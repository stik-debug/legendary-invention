"""Chama money rules. Amounts are integer cents. Every cash movement writes one ledger row in the same transaction.
Nothing is ever deleted: mistakes are voided, which writes a REVERSAL row."""
import csv
import io
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from db import IntegrityError, audit
from notify import notify, notify_roles
from services import BusinessError, iso, now_utc

MAX_CENTS = 500_000_000  # KES 5,000,000 in one transaction
METHODS = ('Cash', 'M-Pesa', 'Bank')
FINANCE_ROLES = ('CHAMA_ADMIN', 'TREASURER')
VIEW_ROLES = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY')
FINE_ROLES = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY')


# ---------- parsing ----------
def parse_kes(raw, min_cents=100):
    try:
        d = Decimal(str(raw).replace(',', '').strip())
        if not d.is_finite() or d != d.quantize(Decimal('0.01')):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise BusinessError('Enter a valid amount with at most 2 decimal places.')
    cents = int((d * 100).to_integral_value(ROUND_HALF_UP))
    if cents < min_cents or cents > MAX_CENTS:
        raise BusinessError(f'The amount must be between KES {min_cents / 100:,.0f} and KES 5,000,000.')
    return cents


def parse_date(raw, today=None):
    today = today or date.today()
    try:
        d = date.fromisoformat((raw or '').strip())
    except ValueError:
        raise BusinessError('Choose a valid date.')
    if d > today + timedelta(days=1):
        raise BusinessError('The date cannot be in the future.')
    if d < today - timedelta(days=731):
        raise BusinessError('That date is too far in the past.')
    return d.isoformat()


def _n(db, sql, params=()):
    return int(db.val(sql, params, 0) or 0)


def _member(db, chama_id, user_id):
    m = db.one("SELECT * FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, user_id))
    if not m:
        raise BusinessError('Choose a member of this chama.')
    return m


def _method(m):
    if m not in METHODS:
        raise BusinessError('Choose Cash, M-Pesa or Bank.')
    return m


def _ref(r):
    return (r or '').strip().upper()[:40] or None


# ---------- ledger ----------
def cash_balance(db, chama_id):
    return _n(db, "SELECT SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END) "
                  "FROM ledger_transactions WHERE chama_id=?", (chama_id,))


def cash_totals(db, chama_id):
    return (_n(db, "SELECT SUM(amount_cents) FROM ledger_transactions WHERE chama_id=? AND direction='IN'", (chama_id,)),
            _n(db, "SELECT SUM(amount_cents) FROM ledger_transactions WHERE chama_id=? AND direction='OUT'", (chama_id,)))


def _ledger(db, chama_id, kind, direction, cents, user_id, ref_type, ref_id, desc, on, actor, now):
    return db.insert('ledger_transactions', chama_id=chama_id, user_id=user_id, kind=kind, direction=direction, amount_cents=cents,
                     ref_type=ref_type, ref_id=ref_id, description=(desc or '')[:200], occurred_on=on, created_by=actor, created_at=iso(now))


def _reverse(db, chama_id, ref_type, ref_id, cents, user_id, direction, reason, actor, now):
    """Write the opposite of a cash row. Refuses if that would leave the chama with negative cash."""
    delta = -cents if direction == 'OUT' else cents
    if cash_balance(db, chama_id) + delta < 0:
        raise BusinessError('This cannot be cancelled because the money has already been lent out or spent. Record a new entry instead.')
    _ledger(db, chama_id, 'REVERSAL', direction, cents, user_id, ref_type, ref_id, 'Cancelled: ' + reason, now.date().isoformat(), actor, now)


def record_ledger_entry(db, chama_id, kind, cents, desc, on, actor, now=None):
    now = now or now_utc()
    if kind not in ('EXPENSE', 'OTHER_INCOME'):
        raise BusinessError('Unknown entry type.')
    if len((desc or '').strip()) < 3:
        raise BusinessError('Describe what this is for.')
    with db.tx():
        db.lock('chamas', chama_id)
        if kind == 'EXPENSE' and cash_balance(db, chama_id) < cents:
            raise BusinessError('The chama does not have that much cash.')
        lid = _ledger(db, chama_id, kind, 'OUT' if kind == 'EXPENSE' else 'IN', cents, None, 'manual', None, desc.strip(), on, actor, now)
        audit(db, actor, 'EXPENSE_RECORDED' if kind == 'EXPENSE' else 'INCOME_RECORDED', 'ledger', lid, chama_id, {'amount_cents': cents})
    return lid


# ---------- contributions ----------
def record_contribution(db, chama_id, user_id, cents, paid_on, method, reference, notes, actor, now=None):
    now = now or now_utc()
    method, ref = _method(method), _ref(reference)
    try:
        with db.tx():
            db.lock('chamas', chama_id)
            _member(db, chama_id, user_id)
            if ref and _n(db, "SELECT COUNT(*) FROM contributions WHERE chama_id=? AND reference=? AND status='PAID'", (chama_id, ref)):
                raise BusinessError(f'The reference {ref} has already been recorded.')
            cid = db.insert('contributions', chama_id=chama_id, user_id=user_id, amount_cents=cents, paid_on=paid_on, period=paid_on[:7],
                            method=method, reference=ref, notes=(notes or '')[:200], created_by=actor, created_at=iso(now))
            _ledger(db, chama_id, 'CONTRIBUTION', 'IN', cents, user_id, 'contribution', cid, 'Contribution', paid_on, actor, now)
            audit(db, actor, 'CONTRIBUTION_CREATED', 'contribution', cid, chama_id, {'user_id': user_id, 'amount_cents': cents})
            notify(db, user_id, chama_id, f'Your contribution of KES {cents / 100:,.0f} was recorded.', f'/chamas/{chama_id}/statement', actor, now)
    except IntegrityError:
        raise BusinessError(f'The reference {ref} has already been recorded.')
    return cid


def void_contribution(db, chama_id, contribution_id, reason, actor, now=None):
    now = now or now_utc()
    if len((reason or '').strip()) < 3:
        raise BusinessError('Give a reason.')
    with db.tx():
        db.lock('chamas', chama_id)
        c = db.one("SELECT * FROM contributions WHERE id=? AND chama_id=? AND status='PAID'", (contribution_id, chama_id))
        if not c:
            raise BusinessError('Contribution not found or already cancelled.')
        _reverse(db, chama_id, 'contribution', c['id'], c['amount_cents'], c['user_id'], 'OUT', reason.strip(), actor, now)
        db.execute("UPDATE contributions SET status='CANCELLED', voided_by=?, voided_at=?, void_reason=? WHERE id=?", (actor, iso(now), reason.strip()[:200], c['id']))
        audit(db, actor, 'CONTRIBUTION_VOIDED', 'contribution', c['id'], chama_id, {'reason': reason})


def savings(db, chama_id, user_id):
    return _n(db, "SELECT SUM(amount_cents) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID'", (chama_id, user_id))


def month_status(db, chama_id, period):
    """Per active member: paid this month vs expected, status PAID / PARTIAL / PENDING."""
    expected = _n(db, 'SELECT contribution_cents FROM chamas WHERE id=?', (chama_id,))
    paid = {r['user_id']: int(r['t']) for r in db.all("SELECT user_id, SUM(amount_cents) t FROM contributions WHERE chama_id=? AND period=? AND status='PAID' GROUP BY user_id", (chama_id, period))}
    out = []
    for m in db.all("SELECT u.id, u.name, u.phone FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name", (chama_id,)):
        p = paid.get(m['id'], 0)
        out.append({**m, 'paid': p, 'expected': expected, 'status': 'PAID' if expected and p >= expected else ('PARTIAL' if p > 0 else 'PENDING')})
    return out


def update_chama_settings(db, chama_id, contribution_cents, rate_percent, multiplier, actor):
    try:
        rate_bps = int((Decimal(str(rate_percent)) * 100).to_integral_value(ROUND_HALF_UP))
        mult = int(multiplier)
    except (InvalidOperation, ValueError):
        raise BusinessError('Enter valid numbers.')
    if not 0 <= rate_bps <= 10000 or not 1 <= mult <= 10:
        raise BusinessError('Interest must be 0 to 100 percent and the savings multiple 1 to 10.')
    with db.tx():
        db.execute('UPDATE chamas SET contribution_cents=?, loan_rate_bps=?, loan_multiplier=? WHERE id=?', (contribution_cents, rate_bps, mult, chama_id))
        audit(db, actor, 'CHAMA_SETTINGS_CHANGED', 'chama', chama_id, chama_id, {'contribution_cents': contribution_cents, 'rate_bps': rate_bps, 'multiplier': mult})


# ---------- loans ----------
OPEN_LOAN = ('PENDING', 'APPROVED', 'ACTIVE')


def max_loan(db, chama_id, user_id):
    mult = _n(db, 'SELECT loan_multiplier FROM chamas WHERE id=?', (chama_id,))
    return savings(db, chama_id, user_id) * mult


def apply_loan(db, chama_id, user_id, cents, purpose, now=None):
    now = now or now_utc()
    with db.tx():
        db.lock('chamas', chama_id)
        _member(db, chama_id, user_id)
        if _n(db, "SELECT COUNT(*) FROM loans WHERE chama_id=? AND user_id=? AND status IN ('PENDING','APPROVED','ACTIVE')", (chama_id, user_id)):
            raise BusinessError('You already have a loan that is waiting or not fully repaid.')
        limit = max_loan(db, chama_id, user_id)
        if cents > limit:
            raise BusinessError(f'You can borrow up to KES {limit / 100:,.0f} (your savings times the chama multiple).')
        bps = _n(db, 'SELECT loan_rate_bps FROM chamas WHERE id=?', (chama_id,))
        interest = int((Decimal(cents) * bps / 10000).to_integral_value(ROUND_HALF_UP))
        lid = db.insert('loans', chama_id=chama_id, user_id=user_id, principal_cents=cents, interest_cents=interest, total_due_cents=cents + interest,
                        rate_bps=bps, purpose=(purpose or '').strip()[:200], status='PENDING', applied_at=iso(now))
        audit(db, user_id, 'LOAN_APPLIED', 'loan', lid, chama_id, {'principal_cents': cents})
        who = db.val('SELECT name FROM users WHERE id=?', (user_id,), 'A member')
        notify_roles(db, chama_id, FINANCE_ROLES, f'{who} asked for a loan of KES {cents / 100:,.0f}.', f'/chamas/{chama_id}/loans', user_id, now)
    return lid


def _loan(db, chama_id, loan_id):
    loan = db.one('SELECT * FROM loans WHERE id=? AND chama_id=?', (loan_id, chama_id))
    if not loan:
        raise BusinessError('Loan not found.')
    return loan


def decide_loan(db, chama_id, loan_id, approve, actor, now=None):
    now = now or now_utc()
    with db.tx():
        loan = _loan(db, chama_id, loan_id)
        if loan['status'] != 'PENDING':
            raise BusinessError('This loan has already been decided.')
        if loan['user_id'] == actor:
            raise BusinessError('You cannot approve your own loan. Another official must do it.')
        db.execute('UPDATE loans SET status=?, decided_by=?, decided_at=? WHERE id=?', ('APPROVED' if approve else 'REJECTED', actor, iso(now), loan_id))
        audit(db, actor, 'LOAN_APPROVED' if approve else 'LOAN_REJECTED', 'loan', loan_id, chama_id)
        notify(db, loan['user_id'], chama_id, 'Your loan request was ' + ('approved. It will be paid out soon.' if approve else 'declined.'), f'/chamas/{chama_id}/loans', actor, now)


def disburse_loan(db, chama_id, loan_id, actor, now=None):
    now = now or now_utc()
    with db.tx():
        db.lock('chamas', chama_id)
        loan = _loan(db, chama_id, loan_id)
        if loan['status'] != 'APPROVED':
            raise BusinessError('Only an approved loan can be paid out.')
        if loan['user_id'] == actor:
            raise BusinessError('You cannot pay out your own loan.')
        bal = cash_balance(db, chama_id)
        if bal < loan['principal_cents']:
            raise BusinessError(f'The chama only has KES {bal / 100:,.0f} in cash, less than this loan.')
        db.execute("UPDATE loans SET status='ACTIVE', disbursed_by=?, disbursed_at=? WHERE id=?", (actor, iso(now), loan_id))
        _ledger(db, chama_id, 'LOAN_DISBURSEMENT', 'OUT', loan['principal_cents'], loan['user_id'], 'loan', loan_id, 'Loan paid out', now.date().isoformat(), actor, now)
        audit(db, actor, 'LOAN_DISBURSED', 'loan', loan_id, chama_id, {'principal_cents': loan['principal_cents']})
        notify(db, loan['user_id'], chama_id, f"Your loan of KES {loan['principal_cents'] / 100:,.0f} was paid out. You owe KES {loan['total_due_cents'] / 100:,.0f}.", f'/chamas/{chama_id}/loans', actor, now)


def repay_loan(db, chama_id, loan_id, cents, paid_on, method, reference, actor, now=None):
    now = now or now_utc()
    method, ref = _method(method), _ref(reference)
    with db.tx():
        db.lock('chamas', chama_id)
        loan = _loan(db, chama_id, loan_id)
        if loan['status'] != 'ACTIVE':
            raise BusinessError('Only a loan that has been paid out can be repaid.')
        remaining = loan['total_due_cents'] - loan['paid_cents']
        if cents > remaining:
            raise BusinessError(f'The balance is only KES {remaining / 100:,.0f}.')
        rid = db.insert('loan_repayments', loan_id=loan_id, chama_id=chama_id, user_id=loan['user_id'], amount_cents=cents, paid_on=paid_on,
                        method=method, reference=ref, created_by=actor, created_at=iso(now))
        paid = loan['paid_cents'] + cents
        db.execute('UPDATE loans SET paid_cents=?, status=? WHERE id=?', (paid, 'PAID' if paid >= loan['total_due_cents'] else 'ACTIVE', loan_id))
        _ledger(db, chama_id, 'LOAN_REPAYMENT', 'IN', cents, loan['user_id'], 'loan_repayment', rid, 'Loan repayment', paid_on, actor, now)
        audit(db, actor, 'LOAN_REPAYMENT', 'loan', loan_id, chama_id, {'amount_cents': cents, 'repayment_id': rid})
        notify(db, loan['user_id'], chama_id, f'Loan repayment of KES {cents / 100:,.0f} recorded. Balance KES {(remaining - cents) / 100:,.0f}.', f'/chamas/{chama_id}/loans', actor, now)
    return rid


def void_repayment(db, chama_id, repayment_id, reason, actor, now=None):
    now = now or now_utc()
    if len((reason or '').strip()) < 3:
        raise BusinessError('Give a reason.')
    with db.tx():
        db.lock('chamas', chama_id)
        r = db.one("SELECT * FROM loan_repayments WHERE id=? AND chama_id=? AND status='PAID'", (repayment_id, chama_id))
        if not r:
            raise BusinessError('Repayment not found or already cancelled.')
        _reverse(db, chama_id, 'loan_repayment', r['id'], r['amount_cents'], r['user_id'], 'OUT', reason.strip(), actor, now)
        db.execute("UPDATE loan_repayments SET status='CANCELLED', voided_at=?, void_reason=? WHERE id=?", (iso(now), reason.strip()[:200], r['id']))
        db.execute("UPDATE loans SET paid_cents=paid_cents-?, status='ACTIVE' WHERE id=?", (r['amount_cents'], r['loan_id']))
        audit(db, actor, 'LOAN_REPAYMENT_VOIDED', 'loan', r['loan_id'], chama_id, {'repayment_id': r['id']})


# ---------- fines ----------
def create_fine(db, chama_id, user_id, cents, reason, due_on, actor, now=None):
    now = now or now_utc()
    if len((reason or '').strip()) < 3:
        raise BusinessError('Give a reason for the fine.')
    with db.tx():
        _member(db, chama_id, user_id)
        fid = db.insert('fines', chama_id=chama_id, user_id=user_id, amount_cents=cents, reason=reason.strip()[:200], due_on=due_on or None,
                        created_by=actor, created_at=iso(now))
        _ledger(db, chama_id, 'FINE', 'MEMO', cents, user_id, 'fine', fid, reason.strip(), now.date().isoformat(), actor, now)
        audit(db, actor, 'FINE_CREATED', 'fine', fid, chama_id, {'user_id': user_id, 'amount_cents': cents})
        notify(db, user_id, chama_id, f'You were fined KES {cents / 100:,.0f}: {reason.strip()[:80]}', f'/chamas/{chama_id}/fines', actor, now)
    return fid


def _fine(db, chama_id, fine_id):
    f = db.one('SELECT * FROM fines WHERE id=? AND chama_id=?', (fine_id, chama_id))
    if not f:
        raise BusinessError('Fine not found.')
    return f


def pay_fine(db, chama_id, fine_id, cents, paid_on, method, reference, actor, now=None):
    now = now or now_utc()
    method, ref = _method(method), _ref(reference)
    with db.tx():
        db.lock('chamas', chama_id)
        f = _fine(db, chama_id, fine_id)
        if f['status'] not in ('UNPAID', 'PARTIAL'):
            raise BusinessError('This fine is already settled.')
        remaining = f['amount_cents'] - f['paid_cents']
        if cents > remaining:
            raise BusinessError(f'The fine balance is only KES {remaining / 100:,.0f}.')
        pid = db.insert('fine_payments', fine_id=fine_id, chama_id=chama_id, user_id=f['user_id'], amount_cents=cents, paid_on=paid_on, method=method,
                        reference=ref, created_by=actor, created_at=iso(now))
        paid = f['paid_cents'] + cents
        db.execute('UPDATE fines SET paid_cents=?, status=? WHERE id=?', (paid, 'PAID' if paid >= f['amount_cents'] else 'PARTIAL', fine_id))
        _ledger(db, chama_id, 'FINE_PAYMENT', 'IN', cents, f['user_id'], 'fine_payment', pid, 'Fine payment', paid_on, actor, now)
        audit(db, actor, 'FINE_PAYMENT', 'fine', fine_id, chama_id, {'amount_cents': cents, 'payment_id': pid})
        notify(db, f['user_id'], chama_id, f'Fine payment of KES {cents / 100:,.0f} recorded.', f'/chamas/{chama_id}/fines', actor, now)
    return pid


def waive_fine(db, chama_id, fine_id, actor, now=None):
    now = now or now_utc()
    with db.tx():
        f = _fine(db, chama_id, fine_id)
        if f['status'] not in ('UNPAID', 'PARTIAL'):
            raise BusinessError('This fine is already settled.')
        remaining = f['amount_cents'] - f['paid_cents']
        db.execute("UPDATE fines SET status='WAIVED' WHERE id=?", (fine_id,))
        _ledger(db, chama_id, 'FINE_WAIVED', 'MEMO', remaining, f['user_id'], 'fine', fine_id, 'Fine waived', now.date().isoformat(), actor, now)
        audit(db, actor, 'FINE_WAIVED', 'fine', fine_id, chama_id)


def void_fine_payment(db, chama_id, payment_id, reason, actor, now=None):
    now = now or now_utc()
    if len((reason or '').strip()) < 3:
        raise BusinessError('Give a reason.')
    with db.tx():
        db.lock('chamas', chama_id)
        p = db.one("SELECT * FROM fine_payments WHERE id=? AND chama_id=? AND status='PAID'", (payment_id, chama_id))
        if not p:
            raise BusinessError('Payment not found or already cancelled.')
        f = db.one('SELECT * FROM fines WHERE id=?', (p['fine_id'],))
        _reverse(db, chama_id, 'fine_payment', p['id'], p['amount_cents'], p['user_id'], 'OUT', reason.strip(), actor, now)
        db.execute("UPDATE fine_payments SET status='CANCELLED', voided_at=?, void_reason=? WHERE id=?", (iso(now), reason.strip()[:200], p['id']))
        paid = f['paid_cents'] - p['amount_cents']
        status = f['status'] if f['status'] == 'WAIVED' else ('UNPAID' if paid == 0 else 'PARTIAL')
        db.execute('UPDATE fines SET paid_cents=?, status=? WHERE id=?', (paid, status, f['id']))
        audit(db, actor, 'FINE_PAYMENT_VOIDED', 'fine', f['id'], chama_id, {'payment_id': p['id']})


# ---------- statements and exports ----------
def member_statement(db, chama_id, user_id):
    contribs = db.all("SELECT * FROM contributions WHERE chama_id=? AND user_id=? ORDER BY paid_on DESC, id DESC LIMIT 200", (chama_id, user_id))
    loans = db.all('SELECT * FROM loans WHERE chama_id=? AND user_id=? ORDER BY id DESC LIMIT 50', (chama_id, user_id))
    fines = db.all('SELECT * FROM fines WHERE chama_id=? AND user_id=? ORDER BY id DESC LIMIT 50', (chama_id, user_id))
    owed_loans = sum(l['total_due_cents'] - l['paid_cents'] for l in loans if l['status'] == 'ACTIVE')
    owed_fines = sum(f['amount_cents'] - f['paid_cents'] for f in fines if f['status'] in ('UNPAID', 'PARTIAL'))
    return {'contributions': contribs, 'loans': loans, 'fines': fines, 'saved': savings(db, chama_id, user_id),
            'loan_balance': int(owed_loans), 'fine_balance': int(owed_fines),
            'max_loan': max_loan(db, chama_id, user_id)}


def csv_safe(v):
    s = '' if v is None else str(v)
    return "'" + s if s[:1] in ('=', '+', '-', '@', '\t', '\r') else s


def to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([csv_safe(c) for c in r])
    return buf.getvalue()


# ---------- messaging ----------
def send_message(db, chama_id, user_id, body, now=None):
    now = now or now_utc()
    body = (body or '').strip()
    if not body:
        raise BusinessError('Type a message first.')
    if len(body) > 1000:
        raise BusinessError('Messages can be up to 1,000 characters.')
    with db.tx():
        _member(db, chama_id, user_id)
        if _n(db, 'SELECT COUNT(*) FROM messages WHERE chama_id=? AND sender_id=? AND created_at>?', (chama_id, user_id, iso(now - timedelta(minutes=1)))) >= 20:
            raise BusinessError('You are sending too fast. Wait a moment.')
        mid = db.insert('messages', chama_id=chama_id, sender_id=user_id, body=body, created_at=iso(now))
    return mid


def list_messages(db, chama_id, before=None, after=None, limit=50):
    sql = 'SELECT m.id, m.sender_id, u.name sender, m.body, m.created_at FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.chama_id=?'
    args = [chama_id]
    if after is not None:
        rows = db.all(sql + ' AND m.id>? ORDER BY m.id LIMIT ?', args + [after, limit])
    else:
        if before:
            sql += ' AND m.id<?'
            args.append(before)
        rows = list(reversed(db.all(sql + ' ORDER BY m.id DESC LIMIT ?', args + [limit])))
    return rows


def mark_read(db, chama_id, user_id):
    last = _n(db, 'SELECT MAX(id) FROM messages WHERE chama_id=?', (chama_id,))
    with db.tx():
        cur = db.one('SELECT id, last_read_id FROM message_reads WHERE chama_id=? AND user_id=?', (chama_id, user_id))
        if cur:
            if last > cur['last_read_id']:
                db.execute('UPDATE message_reads SET last_read_id=? WHERE id=?', (last, cur['id']))
        else:
            try:
                db.insert('message_reads', chama_id=chama_id, user_id=user_id, last_read_id=last)
            except IntegrityError:
                pass  # a parallel request created it first


def unread_count(db, chama_id, user_id):
    seen = _n(db, 'SELECT last_read_id FROM message_reads WHERE chama_id=? AND user_id=?', (chama_id, user_id))
    return _n(db, 'SELECT COUNT(*) FROM messages WHERE chama_id=? AND id>? AND sender_id!=?', (chama_id, seen, user_id))
