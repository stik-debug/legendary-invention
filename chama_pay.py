"""Paying the chama, kept simple.

Members pay the chama's OWN paybill, till or phone the way they always do. ChamaPay never holds the money and never sends payment prompts.
Then the member pastes the M-Pesa message they received. ChamaPay reads the code and amount and records the contribution, fine or loan
repayment with the same rules and ledger rows as a treasurer entry.

How a pasted message is checked is the chama's choice (Payment settings):
  RECORDS  recorded when its code and amount match what the chama itself received (added by officials under 'M-Pesa records'). Safest.
  TRUST    recorded the moment a readable, recent message is pasted. Officials are told, and can cancel a mistake. When the chama's
           records are added later, each such payment is compared and any mismatch is flagged.
A pasted message is only text, so nothing here can prove it is genuine; the checks above are what protect the chama."""
import re
from datetime import timedelta

import finance as F
import mpesa_sms as SMS
from db import IntegrityError, audit
from notify import notify, notify_roles
from services import BusinessError, iso, now_utc

PURPOSES = {'CONTRIBUTION': 'Contribution', 'FINE': 'Fine', 'LOAN': 'Loan repayment'}
MAX_CENTS = 250_000 * 100  # M-Pesa's own limit per transaction
CHECK_MODES = ('RECORDS', 'TRUST')
TRUST_MAX_AGE_DAYS = 7  # in TRUST mode an older message is left for an official


# ---------- settings ----------
def get_config(db, chama_id):
    return db.one('SELECT * FROM chama_pay_config WHERE chama_id=?', (chama_id,))


def channel(db, chama_id):
    """'MANUAL' once the chairperson has said how members pay the chama, otherwise None."""
    c = get_config(db, chama_id)
    return 'MANUAL' if c and (c['instructions'] or '').strip() else None


def check_mode(db, chama_id):
    c = get_config(db, chama_id)
    return c['check_mode'] if c and c.get('check_mode') in CHECK_MODES else 'RECORDS'


def save_config(db, chama_id, f, actor, now=None):
    now = now or now_utc()
    instr = (f.get('instructions') or '').strip()[:500]
    if len(instr) < 5:
        raise BusinessError('Tell members how to pay: your paybill or till number and the account name.')
    mode = f.get('check_mode') or 'RECORDS'
    if mode not in CHECK_MODES:
        raise BusinessError('Choose how pasted M-Pesa messages are checked.')
    vals = {'mode': 'MANUAL', 'instructions': instr, 'check_mode': mode, 'updated_by': actor, 'updated_at': iso(now)}
    with db.tx():
        if get_config(db, chama_id):
            db.execute('UPDATE chama_pay_config SET ' + ','.join(f'{k}=?' for k in vals) + ' WHERE chama_id=?', list(vals.values()) + [chama_id])
        else:
            db.insert('chama_pay_config', chama_id=chama_id, **vals)
        audit(db, actor, 'PAY_CONFIG_SAVED', 'chama', chama_id, chama_id, {'check_mode': mode})


# ---------- what is being paid ----------
def _target(db, chama_id, user_id, purpose, target_id):
    """(target id, most that can be paid now). A member can only pay their OWN fine / loan."""
    if purpose == 'CONTRIBUTION':
        return None, MAX_CENTS
    if purpose == 'FINE':
        f = db.one('SELECT * FROM fines WHERE id=? AND chama_id=? AND user_id=?', (target_id, chama_id, user_id))
        if not f or f['status'] not in ('UNPAID', 'PARTIAL'):
            raise BusinessError('That fine cannot be paid.')
        return f['id'], f['amount_cents'] - f['paid_cents']
    if purpose == 'LOAN':
        ln = db.one('SELECT * FROM loans WHERE id=? AND chama_id=? AND user_id=?', (target_id, chama_id, user_id))
        if not ln or ln['status'] != 'ACTIVE':
            raise BusinessError('That loan cannot be repaid right now.')
        return ln['id'], ln['total_due_cents'] - ln['paid_cents']
    raise BusinessError('Choose what you are paying for.')


def _check_amount(cents, cap):
    if not 100 <= cents <= min(cap, MAX_CENTS):
        raise BusinessError(f'The amount must be between KES 1 and KES {min(cap, MAX_CENTS) / 100:,.0f}.')


def _apply(db, p, ref, actor, now):
    """Record the money with the same rules (and ledger rows) as a treasurer entry. Returns the new record's id."""
    paid_on = (now + timedelta(hours=3)).date().isoformat()
    a = (db, p['chama_id'])
    if p['purpose'] == 'CONTRIBUTION':
        return F.record_contribution(*a, p['user_id'], p['amount_cents'], paid_on, 'M-Pesa', ref, 'Paid in the app', actor, now)
    if p['purpose'] == 'FINE':
        return F.pay_fine(*a, p['target_id'], p['amount_cents'], paid_on, 'M-Pesa', ref, actor, now)
    return F.repay_loan(*a, p['target_id'], p['amount_cents'], paid_on, 'M-Pesa', ref, actor, now)


def _label(p):
    return f"KES {p['amount_cents'] / 100:,.0f} {PURPOSES[p['purpose']].lower()}"


# ---------- claims: the member pastes the M-Pesa message ----------
def claim(db, chama_id, user_id, purpose, target_id, cents, code, now=None, message=None):
    """The member says they paid the chama and gives the proof: the pasted M-Pesa SMS (or just the code and amount).
    Depending on the chama's setting it is recorded at once, or when the chama's own M-Pesa record of that code is found, or when an official
    approves it. Returns the new payment's id."""
    now = now or now_utc()
    if not channel(db, chama_id):
        raise BusinessError('Payment details are not set up for this chama yet.')
    code = (code or '').strip().upper()
    msg = (message or '').strip()[:2000]
    note, sms, read_ok = None, None, False
    if msg:
        sms = SMS.parse_one(msg)
        if not sms or not sms['amount_cents']:
            raise BusinessError('We could not read that message. Paste the whole M-Pesa SMS, or type the code and the amount instead.')
        if code and code != sms['code']:
            raise BusinessError('The code you typed is not the code in the message.')
        if cents is not None and cents != sms['amount_cents']:
            raise BusinessError(f"The amount you typed (KES {cents / 100:,.0f}) is not the amount in the message (KES {sms['amount_cents'] / 100:,.0f}).")
        today = (now + timedelta(hours=3)).date()
        if sms['date'] and sms['date'] > today.isoformat():
            raise BusinessError('That message is dated in the future. Check that you pasted it correctly.')
        read_ok = bool(sms['date']) and sms['date'] >= (today - timedelta(days=TRUST_MAX_AGE_DAYS)).isoformat()
        code, cents, note = sms['code'], sms['amount_cents'], SMS.summary(sms)
    if not re.fullmatch(r'[A-Z0-9]{8,12}', code):
        raise BusinessError('Enter the M-Pesa confirmation code from your SMS, for example QRT1234ABC.')
    if cents is None:
        raise BusinessError('Enter the amount you paid.')
    tid, cap = _target(db, chama_id, user_id, purpose, target_id)
    _check_amount(cents, cap)
    try:
        with db.tx():
            pid = db.insert('chama_payments', chama_id=chama_id, user_id=user_id, purpose=purpose, target_id=tid, amount_cents=cents, channel='CLAIM',
                            status='CLAIMED', receipt=code, sms_text=note, created_at=iso(now))
    except IntegrityError:
        raise BusinessError('That M-Pesa code has already been submitted.')
    res = verify(db, chama_id, pid, now)
    if res == 'waiting' and read_ok and check_mode(db, chama_id) == 'TRUST':
        res = _finish(db, chama_id, pid, 'TRUSTED', now)
    if res not in ('verified', 'trusted'):
        who = db.val('SELECT name FROM users WHERE id=?', (user_id,))
        with db.tx():
            notify_roles(db, chama_id, F.FINANCE_ROLES, f'{who} says they paid {_label({"amount_cents": cents, "purpose": purpose})} ({code}). Check it, then approve.',
                         f'/chamas/{chama_id}/pay', user_id, now)
    return pid


def _finish(db, chama_id, pid, how, now):
    """Record a waiting claim (how = 'RECORD' matched the chama's records, 'TRUSTED' recorded from the message alone). Returns verified / trusted / review / waiting."""
    try:
        with db.tx():
            db.lock('chamas', chama_id)
            p = db.one("SELECT * FROM chama_payments WHERE id=? AND chama_id=? AND status='CLAIMED'", (pid, chama_id))
            if not p:
                return 'waiting'
            rid = _apply(db, p, p['receipt'], p['user_id'], now)
            db.execute("UPDATE chama_payments SET status='SUCCESS', applied=1, applied_ref_id=?, verified=?, result_desc=NULL, completed_at=? WHERE id=?", (rid, how, iso(now), pid))
            audit(db, p['user_id'], 'PAY_VERIFIED_BY_RECORD' if how == 'RECORD' else 'PAY_RECORDED_FROM_MESSAGE', 'chama_payment', pid, chama_id,
                  {'purpose': p['purpose'], 'amount_cents': p['amount_cents']})
            if how == 'RECORD':
                db.execute('UPDATE mpesa_records SET matched_payment_id=? WHERE chama_id=? AND receipt=?', (pid, chama_id, p['receipt']))
                notify(db, p['user_id'], chama_id, f"Your payment of {_label(p)} was checked against the chama's M-Pesa records and recorded.", f'/chamas/{chama_id}/statement', None, now)
                notify_roles(db, chama_id, F.FINANCE_ROLES, f'{_label(p)} ({p["receipt"]}) was verified against the M-Pesa records and recorded.', f'/chamas/{chama_id}/pay', p['user_id'], now)
            else:
                notify(db, p['user_id'], chama_id, f'Your payment of {_label(p)} was recorded.', f'/chamas/{chama_id}/statement', None, now)
                notify_roles(db, chama_id, F.FINANCE_ROLES, f'{_label(p)} ({p["receipt"]}) was recorded from the member\'s message. Add the M-Pesa record to confirm it.', f'/chamas/{chama_id}/pay', p['user_id'], now)
            return 'verified' if how == 'RECORD' else 'trusted'
    except (BusinessError, IntegrityError) as e:  # the finance rules refuse it (for example the fine is already settled): an official decides
        with db.tx():
            db.execute("UPDATE chama_payments SET status='REVIEW', result_desc=? WHERE id=? AND status='CLAIMED'", (str(e)[:200], pid))
            notify_roles(db, chama_id, F.FINANCE_ROLES, 'A payment could not be recorded automatically. Please review it.', f'/chamas/{chama_id}/pay', None, now)
        return 'review'


# ---------- the chama's own M-Pesa records ----------
def verify(db, chama_id, pid, now=None):
    """Match one waiting claim against the chama's own records of money received. Returns:
      verified    code and amount are in the records: recorded (once only)
      mismatch    the code is in the records with a DIFFERENT amount: not recorded, officials are warned (the message may have been edited)
      needs_other the record was added by the payer themselves: another official must approve
      review      matched, but the finance rules refuse it: an official decides
      waiting     no record of that code yet"""
    now = now or now_utc()
    with db.tx():
        db.lock('chamas', chama_id)
        p = db.one("SELECT * FROM chama_payments WHERE id=? AND chama_id=? AND status='CLAIMED'", (pid, chama_id))
        if not p or not p['receipt']:
            return 'waiting'
        rec = db.one('SELECT * FROM mpesa_records WHERE chama_id=? AND receipt=?', (chama_id, p['receipt']))
        if not rec or rec['matched_payment_id']:
            return 'waiting'
        if rec['amount_cents'] != p['amount_cents']:
            db.execute('UPDATE chama_payments SET result_desc=? WHERE id=?',
                       (f"WARNING: the chama's M-Pesa record for this code says KES {(rec['amount_cents'] or 0) / 100:,.0f}, the member says KES {p['amount_cents'] / 100:,.0f}. The message may have been edited."[:200], pid))
            audit(db, None, 'PAY_RECORD_MISMATCH', 'chama_payment', pid, chama_id, {'record_cents': rec['amount_cents'], 'claimed_cents': p['amount_cents']})
            notify_roles(db, chama_id, F.FINANCE_ROLES, f"A payment claim ({p['receipt']}) does not match the M-Pesa record. Please look at it.", f'/chamas/{chama_id}/pay', p['user_id'], now)
            return 'mismatch'
        if rec['added_by'] == p['user_id']:  # nobody may confirm their own payment with a record they typed in themselves
            db.execute('UPDATE chama_payments SET result_desc=? WHERE id=?', ('Matches an M-Pesa record, but the payer added that record. Another official must approve.', pid))
            return 'needs_other'
    return _finish(db, chama_id, pid, 'RECORD', now)


def add_records(db, chama_id, actor, text, now=None):
    """An official pastes the M-Pesa messages the CHAMA received (or lines like 'QGH7XY12AB 1000'). Each code is saved once.
    Waiting claims for those codes are then checked, and payments already recorded from a message alone are compared with the record."""
    now = now or now_utc()
    items = SMS.parse_many(text)
    if not items:
        raise BusinessError("No M-Pesa messages found. Paste the messages the chama received, one after another, or lines like 'QGH7XY12AB 1000'.")
    added, dup, skipped, codes = 0, 0, 0, []
    for it in items:
        if not it['amount_cents']:
            skipped += 1
            continue
        if db.one('SELECT id FROM mpesa_records WHERE chama_id=? AND receipt=?', (chama_id, it['code'])):
            dup += 1
            continue
        try:
            with db.tx():
                db.insert('mpesa_records', chama_id=chama_id, receipt=it['code'], amount_cents=it['amount_cents'], paid_date=it['date'], paid_time=it['time'],
                          party=SMS.mask(it['party']), added_by=actor, added_at=iso(now))
            added += 1
            codes.append((it['code'], it['amount_cents']))
        except IntegrityError:
            dup += 1
    with db.tx():
        audit(db, actor, 'MPESA_RECORDS_ADDED', 'chama', chama_id, chama_id, {'added': added, 'duplicates': dup, 'unreadable': skipped})
    out = {'added': added, 'duplicates': dup, 'skipped': skipped, 'verified': 0, 'attention': 0}
    for code, cents in codes:
        row = db.one('SELECT id, status, verified, amount_cents FROM chama_payments WHERE chama_id=? AND receipt=?', (chama_id, code))
        if not row:
            continue
        if row['status'] == 'CLAIMED':
            res = verify(db, chama_id, row['id'], now)
            if res == 'verified':
                out['verified'] += 1
            elif res != 'waiting':
                out['attention'] += 1
        elif row['status'] == 'SUCCESS' and row['verified'] == 'TRUSTED':
            with db.tx():
                if row['amount_cents'] == cents:
                    db.execute("UPDATE chama_payments SET verified='RECORD' WHERE id=?", (row['id'],))
                    db.execute('UPDATE mpesa_records SET matched_payment_id=? WHERE chama_id=? AND receipt=?', (row['id'], chama_id, code))
                    out['verified'] += 1
                else:
                    db.execute('UPDATE chama_payments SET result_desc=? WHERE id=?',
                               (f"WARNING: recorded from the member's message as KES {row['amount_cents'] / 100:,.0f}, but the chama's M-Pesa record says KES {cents / 100:,.0f}. Cancel it if the message was edited."[:200], row['id']))
                    audit(db, None, 'PAY_RECORD_MISMATCH', 'chama_payment', row['id'], chama_id, {'record_cents': cents, 'claimed_cents': row['amount_cents']})
                    notify_roles(db, chama_id, F.FINANCE_ROLES, f'A payment recorded from a message ({code}) does not match the M-Pesa record. Please look at it.', f'/chamas/{chama_id}/pay', None, now)
                    out['attention'] += 1
    return out


def records(db, chama_id, limit=60):
    return db.all("""SELECT r.*, u.name matched_name, p.purpose matched_purpose FROM mpesa_records r
        LEFT JOIN chama_payments p ON p.id=r.matched_payment_id LEFT JOIN users u ON u.id=p.user_id
        WHERE r.chama_id=? ORDER BY r.id DESC LIMIT ?""", (chama_id, limit))


def unconfirmed(db, chama_id, limit=30):
    """Payments recorded from a message alone that the chama's records have not confirmed yet (officials can still cancel them)."""
    return db.all("""SELECT p.*, u.name FROM chama_payments p JOIN users u ON u.id=p.user_id
        WHERE p.chama_id=? AND p.status='SUCCESS' AND p.verified='TRUSTED' ORDER BY p.id DESC LIMIT ?""", (chama_id, limit))


def decide(db, chama_id, pid, approve, actor, reason=None, now=None):
    now = now or now_utc()
    with db.tx():
        db.lock('chamas', chama_id)
        p = db.one("SELECT * FROM chama_payments WHERE id=? AND chama_id=? AND status IN ('CLAIMED','REVIEW')", (pid, chama_id))
        if not p:
            raise BusinessError('This payment was already handled.')
        if p['user_id'] == actor:
            raise BusinessError('Another official must approve your own payment.')
        if approve:
            rid = _apply(db, p, p['receipt'], actor, now)
            db.execute("UPDATE chama_payments SET status='SUCCESS', applied=1, applied_ref_id=?, decided_by=?, completed_at=? WHERE id=?", (rid, actor, iso(now), pid))
            audit(db, actor, 'PAY_APPROVED', 'chama_payment', pid, chama_id, {'purpose': p['purpose'], 'amount_cents': p['amount_cents']})
        else:
            if len((reason or '').strip()) < 3:
                raise BusinessError('Give a reason so the member understands.')
            db.execute("UPDATE chama_payments SET status='REJECTED', decided_by=?, result_desc=?, completed_at=? WHERE id=?", (actor, reason.strip()[:200], iso(now), pid))
            audit(db, actor, 'PAY_REJECTED', 'chama_payment', pid, chama_id, {'reason': reason})
            notify(db, p['user_id'], chama_id, f'Your payment of {_label(p)} was not accepted: {reason.strip()[:100]}', f'/chamas/{chama_id}/pay', actor, now)


# ---------- screens ----------
def dues(db, chama_id, user_id, now=None):
    now = now or now_utc()
    period = (now + timedelta(hours=3)).date().isoformat()[:7]
    expected = int(db.val('SELECT contribution_cents FROM chamas WHERE id=?', (chama_id,), 0))
    paid = int(db.val("SELECT SUM(amount_cents) FROM contributions WHERE chama_id=? AND user_id=? AND period=? AND status='PAID'", (chama_id, user_id, period), 0) or 0)
    return {'period': period, 'expected': expected, 'paid': paid, 'left': max(0, expected - paid),
            'fines': db.all("SELECT * FROM fines WHERE chama_id=? AND user_id=? AND status IN ('UNPAID','PARTIAL') ORDER BY id", (chama_id, user_id)),
            'loans': db.all("SELECT * FROM loans WHERE chama_id=? AND user_id=? AND status='ACTIVE' ORDER BY id", (chama_id, user_id)),
            'recent': db.all('SELECT * FROM chama_payments WHERE chama_id=? AND user_id=? ORDER BY id DESC LIMIT 12', (chama_id, user_id))}


def queue(db, chama_id):
    return db.all("SELECT p.*, u.name FROM chama_payments p JOIN users u ON u.id=p.user_id WHERE p.chama_id=? AND p.status IN ('CLAIMED','REVIEW') ORDER BY p.id", (chama_id,))
