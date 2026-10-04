"""Merry-go-round (rotating savings).
Members agree an amount and an order. Each turn, everyone except that turn's recipient pays the amount; once all have paid, the
treasurer pays the whole pot to the recipient. After every member has had a turn the round is complete.

The pot lives in its own money account ('MGR' in the ledger). Loans, expenses and the cash-in-hand figure only ever look at the 'MAIN'
account, so the pot can never be lent out or spent. It can only leave through a payout to that turn's recipient."""
import calendar
import secrets
from datetime import date, datetime, timedelta

import finance as F
from db import audit
from notify import notify, notify_roles
from services import BusinessError, iso, now_utc, parse

FREQ = ('WEEKLY', 'MONTHLY')
MIN_PEOPLE, MAX_PEOPLE = 3, 60
REMIND_HOURS = 12


def _clean(raw, label, lo, hi):
    v = (raw or '').strip()
    if not lo <= len(v) <= hi:
        raise BusinessError(f'{label} must be {lo} to {hi} characters.')
    return v


def due_date(start, frequency, k):
    """Date of turn number k (0 = first). Monthly turns keep the day of the month, or the last day if the month is shorter."""
    if frequency == 'WEEKLY':
        return start + timedelta(days=7 * k)
    m = start.month - 1 + k
    y, mo = start.year + m // 12, m % 12 + 1
    return date(y, mo, min(start.day, calendar.monthrange(y, mo)[1]))


# ---------- the pot ----------
def pot_balance(db, chama_id):
    return int(db.val("SELECT SUM(CASE WHEN direction='IN' THEN amount_cents ELSE -amount_cents END) FROM ledger_transactions "
                      "WHERE chama_id=? AND account='MGR'", (chama_id,), 0) or 0)


def _pot(db, chama_id, kind, direction, cents, user_id, ref_id, desc, now, actor):
    return db.insert('ledger_transactions', chama_id=chama_id, user_id=user_id, kind=kind, direction=direction, amount_cents=cents, ref_type='mgr_payment' if kind != 'MGR_PAYOUT' else 'mgr_slot',
                     ref_id=ref_id, description=(desc or '')[:200], occurred_on=now.date().isoformat(), created_by=actor, created_at=iso(now), account='MGR')


# ---------- reading ----------
def get_round(db, chama_id, round_id):
    r = db.one('SELECT * FROM mgr_rounds WHERE id=? AND chama_id=?', (round_id, chama_id))
    if not r:
        raise BusinessError('Merry-go-round not found.')
    return r


def current_slot(db, round_id):
    return db.one("SELECT s.*, u.name FROM mgr_slots s JOIN users u ON u.id=s.user_id WHERE s.round_id=? AND s.status='PENDING' ORDER BY s.position LIMIT 1", (round_id,))


def slots(db, round_id):
    return db.all('SELECT s.*, u.name FROM mgr_slots s JOIN users u ON u.id=s.user_id WHERE s.round_id=? ORDER BY s.position', (round_id,))


def turn_status(db, rnd):
    """What is happening this turn: the recipient, who has paid, who has not, how much is in the pot for it."""
    slot = current_slot(db, rnd['id'])
    if not slot:
        return None
    people = db.all('SELECT s.user_id, u.name FROM mgr_slots s JOIN users u ON u.id=s.user_id WHERE s.round_id=? AND s.user_id!=? ORDER BY u.name', (rnd['id'], slot['user_id']))
    paid = {p['user_id']: p for p in db.all("SELECT * FROM mgr_payments WHERE slot_id=? AND status='PAID'", (slot['id'],))}
    unpaid = [p for p in people if p['user_id'] not in paid]
    return {'slot': slot, 'payers': len(people), 'paid': [dict(p, name=next(x['name'] for x in people if x['user_id'] == uid), payment_id=p['id']) for uid, p in paid.items()],
            'unpaid': unpaid, 'collected': sum(p['amount_cents'] for p in paid.values()), 'expected': len(people) * rnd['amount_cents']}


def list_rounds(db, chama_id):
    rows = db.all("SELECT * FROM mgr_rounds WHERE chama_id=? ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 40", (chama_id,))
    for r in rows:
        r['people'] = db.val('SELECT COUNT(*) FROM mgr_slots WHERE round_id=?', (r['id'],), 0)
        r['done'] = db.val("SELECT COUNT(*) FROM mgr_slots WHERE round_id=? AND status='PAID_OUT'", (r['id'],), 0)
        r['turn'] = current_slot(db, r['id']) if r['status'] == 'ACTIVE' else None
    return rows


def my_dues(db, chama_id, user_id):
    """Active rounds where this person still has to pay this turn: [(round, slot)]."""
    out = []
    for r in db.all("SELECT * FROM mgr_rounds WHERE chama_id=? AND status='ACTIVE'", (chama_id,)):
        t = turn_status(db, r)
        if t and any(u['user_id'] == user_id for u in t['unpaid']):
            out.append((r, t['slot']))
    return out


# ---------- starting a round ----------
def create_round(db, chama_id, name, amount_cents, frequency, start_date, user_ids, actor, order=None, now=None):
    """order: list of user ids in turn order, or None for a fair random draw (done here, with the system's secure random source)."""
    now = now or now_utc()
    name = _clean(name, 'The name', 3, 60)
    if frequency not in FREQ:
        raise BusinessError('Choose weekly or monthly.')
    try:
        start = date.fromisoformat((start_date or '').strip())
    except ValueError:
        raise BusinessError('Choose a valid start date.')
    if not date.today() - timedelta(days=1) <= start <= date.today() + timedelta(days=366):
        raise BusinessError('The first turn must fall between today and one year from now.')
    ids = list(dict.fromkeys(int(u) for u in user_ids))
    if not MIN_PEOPLE <= len(ids) <= MAX_PEOPLE:
        raise BusinessError(f'A merry-go-round needs {MIN_PEOPLE} to {MAX_PEOPLE} members.')
    with db.tx():
        db.lock('chamas', chama_id)
        active = {r['user_id'] for r in db.all("SELECT user_id FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,))}
        if not set(ids) <= active:
            raise BusinessError('Everyone in a merry-go-round must be an active member of this chama.')
        if order is None:
            order = ids[:]
            secrets.SystemRandom().shuffle(order)
        elif sorted(order) != sorted(ids):
            raise BusinessError('The order must list each chosen member exactly once.')
        rid = db.insert('mgr_rounds', chama_id=chama_id, name=name, amount_cents=amount_cents, frequency=frequency, start_date=start.isoformat(), created_by=actor, created_at=iso(now))
        for k, uid in enumerate(order):
            db.insert('mgr_slots', round_id=rid, chama_id=chama_id, position=k + 1, user_id=uid, due_date=due_date(start, frequency, k).isoformat())
        audit(db, actor, 'MGR_STARTED', 'mgr_round', rid, chama_id, {'people': len(ids), 'amount_cents': amount_cents, 'frequency': frequency})
        link = f'/chamas/{chama_id}/merry-go-round/{rid}'
        for s in slots(db, rid):
            notify(db, s['user_id'], chama_id, f"{name} has started. You are number {s['position']} of {len(ids)}: you pay KES {amount_cents / 100:,.0f} on each of the other {len(ids) - 1} turns and receive the pot on {s['due_date']}.", link, None, now)
        _announce_turn(db, chama_id, get_round(db, chama_id, rid), actor, now)
    return rid


def _announce_turn(db, chama_id, rnd, actor, now):
    """'It is your turn to pay': tell everyone who owes this turn, and tell the recipient what is coming.
    Nobody is skipped, not even the official who caused it: a treasurer who pays out a turn may owe the next one herself."""
    t = turn_status(db, rnd)
    if not t:
        return
    s, link = t['slot'], f"/chamas/{chama_id}/merry-go-round/{rnd['id']}"
    for u in t['unpaid']:
        notify(db, u['user_id'], chama_id, f"Your turn to pay: KES {rnd['amount_cents'] / 100:,.0f} to {s['name']} for {rnd['name']}, due {s['due_date']}.", link, None, now)
    notify(db, s['user_id'], chama_id, f"It is your turn to receive {rnd['name']}: KES {t['expected'] / 100:,.0f} on {s['due_date']} once everyone has paid.", link, None, now)


# ---------- money in ----------
def record_payment(db, chama_id, round_id, user_id, method, reference, paid_on, actor, now=None):
    now = now or now_utc()
    method = F._method(method)
    ref = F._ref(reference)
    on = F.parse_date(paid_on, now.date())
    with db.tx():
        db.lock('chamas', chama_id)
        rnd = get_round(db, chama_id, round_id)
        if rnd['status'] != 'ACTIVE':
            raise BusinessError('This merry-go-round is not running.')
        t = turn_status(db, rnd)
        if not t:
            raise BusinessError('Every turn has been paid out.')
        if user_id == t['slot']['user_id']:
            raise BusinessError(f"{t['slot']['name']} is this turn's recipient and does not pay.")
        if not any(u['user_id'] == user_id for u in t['unpaid']):
            if db.one('SELECT id FROM mgr_slots WHERE round_id=? AND user_id=?', (round_id, user_id)):
                raise BusinessError('That member has already paid for this turn.')
            raise BusinessError('That member is not in this merry-go-round.')
        pid = db.insert('mgr_payments', round_id=round_id, slot_id=t['slot']['id'], chama_id=chama_id, user_id=user_id, amount_cents=rnd['amount_cents'],
                        method=method, reference=ref, paid_on=on, recorded_by=actor, created_at=iso(now))
        who = db.val('SELECT name FROM users WHERE id=?', (user_id,), '')
        _pot(db, chama_id, 'MGR_CONTRIBUTION', 'IN', rnd['amount_cents'], user_id, pid, f"{rnd['name']}: {who} for {t['slot']['name']}", now, actor)
        audit(db, actor, 'MGR_PAYMENT', 'mgr_payment', pid, chama_id, {'round_id': round_id, 'user_id': user_id, 'amount_cents': rnd['amount_cents']})
        link = f'/chamas/{chama_id}/merry-go-round/{round_id}'
        notify(db, user_id, chama_id, f"Your KES {rnd['amount_cents'] / 100:,.0f} for {rnd['name']} (turn of {t['slot']['name']}) was recorded.", link, actor, now)
        if len(t['unpaid']) == 1:  # that was the last one
            notify_roles(db, chama_id, F.FINANCE_ROLES, f"Everyone has paid for {t['slot']['name']}'s turn in {rnd['name']}. The payout of KES {t['expected'] / 100:,.0f} is ready.", link, actor, now)
    return pid


def void_payment(db, chama_id, round_id, payment_id, reason, actor, now=None):
    now = now or now_utc()
    if len((reason or '').strip()) < 3:
        raise BusinessError('Give a reason.')
    with db.tx():
        db.lock('chamas', chama_id)
        p = db.one("SELECT p.*, s.status slot_status FROM mgr_payments p JOIN mgr_slots s ON s.id=p.slot_id WHERE p.id=? AND p.round_id=? AND p.chama_id=? AND p.status='PAID'", (payment_id, round_id, chama_id))
        if not p:
            raise BusinessError('Payment not found or already cancelled.')
        if p['slot_status'] != 'PENDING':
            raise BusinessError('This turn has already been paid out, so the payment cannot be cancelled.')
        _pot(db, chama_id, 'MGR_REVERSAL', 'OUT', p['amount_cents'], p['user_id'], p['id'], 'Cancelled: ' + reason.strip(), now, actor)
        db.execute("UPDATE mgr_payments SET status='CANCELLED', voided_by=?, voided_at=?, void_reason=? WHERE id=?", (actor, iso(now), reason.strip()[:200], p['id']))
        audit(db, actor, 'MGR_PAYMENT_VOIDED', 'mgr_payment', p['id'], chama_id, {'reason': reason})
        notify(db, p['user_id'], chama_id, 'A merry-go-round payment recorded for you was cancelled. Ask the treasurer why.', f'/chamas/{chama_id}/merry-go-round/{round_id}', actor, now)


# ---------- money out ----------
def payout(db, chama_id, round_id, method, reference, actor, now=None):
    """Pay this turn's pot to its recipient. Only when everyone has paid, and never by the recipient themselves."""
    now = now or now_utc()
    method = F._method(method)
    ref = F._ref(reference)
    with db.tx():
        db.lock('chamas', chama_id)
        rnd = get_round(db, chama_id, round_id)
        if rnd['status'] != 'ACTIVE':
            raise BusinessError('This merry-go-round is not running.')
        t = turn_status(db, rnd)
        if not t:
            raise BusinessError('Every turn has been paid out.')
        s = t['slot']
        if actor == s['user_id']:
            raise BusinessError('You cannot pay out your own turn. Another official must do it.')
        if t['unpaid']:
            raise BusinessError('Still waiting for ' + ', '.join(u['name'] for u in t['unpaid']) + '.')
        total = t['collected']
        if pot_balance(db, chama_id) < total:
            raise BusinessError('The pot does not hold that much. Run the validate check.')
        _pot(db, chama_id, 'MGR_PAYOUT', 'OUT', total, s['user_id'], s['id'], f"{rnd['name']}: payout to {s['name']}", now, actor)
        db.execute("UPDATE mgr_slots SET status='PAID_OUT', payout_cents=?, paid_out_at=?, paid_out_by=?, method=?, reference=? WHERE id=?", (total, iso(now), actor, method, ref, s['id']))
        audit(db, actor, 'MGR_PAYOUT', 'mgr_slot', s['id'], chama_id, {'round_id': round_id, 'amount_cents': total, 'to': s['user_id']})
        link = f'/chamas/{chama_id}/merry-go-round/{round_id}'
        notify(db, s['user_id'], chama_id, f"You received KES {total / 100:,.0f} from {rnd['name']}.", link, actor, now)
        if current_slot(db, round_id):
            _announce_turn(db, chama_id, rnd, actor, now)
        else:
            db.execute("UPDATE mgr_rounds SET status='COMPLETED', closed_at=? WHERE id=?", (iso(now), round_id))
            for r in slots(db, round_id):
                notify(db, r['user_id'], chama_id, f"{rnd['name']} is complete. Everyone has had a turn.", link, actor, now)
    return total


def remind(db, chama_id, round_id, actor, now=None):
    """Alert everyone who still owes this turn. At most once every 12 hours so nobody is spammed. Returns how many were alerted."""
    now = now or now_utc()
    with db.tx():
        rnd = get_round(db, chama_id, round_id)
        t = turn_status(db, rnd) if rnd['status'] == 'ACTIVE' else None
        if not t:
            raise BusinessError('This merry-go-round is not running.')
        if not t['unpaid']:
            raise BusinessError('Everyone has already paid for this turn.')
        s = t['slot']
        if s['reminded_at'] and parse(s['reminded_at']) > now - timedelta(hours=REMIND_HOURS):
            raise BusinessError(f'A reminder was already sent in the last {REMIND_HOURS} hours.')
        for u in t['unpaid']:
            notify(db, u['user_id'], chama_id, f"Reminder: pay KES {rnd['amount_cents'] / 100:,.0f} to {s['name']} for {rnd['name']} (due {s['due_date']}).", f'/chamas/{chama_id}/merry-go-round/{round_id}', None, now)
        db.execute('UPDATE mgr_slots SET reminded_at=? WHERE id=?', (iso(now), s['id']))
        audit(db, actor, 'MGR_REMINDER', 'mgr_round', round_id, chama_id, {'count': len(t['unpaid'])})
    return len(t['unpaid'])


def cancel_round(db, chama_id, round_id, reason, actor, now=None):
    now = now or now_utc()
    if len((reason or '').strip()) < 3:
        raise BusinessError('Give a reason.')
    with db.tx():
        db.lock('chamas', chama_id)
        rnd = get_round(db, chama_id, round_id)
        if rnd['status'] != 'ACTIVE':
            raise BusinessError('Only a running merry-go-round can be cancelled.')
        held = db.val("SELECT COUNT(*) FROM mgr_payments p JOIN mgr_slots s ON s.id=p.slot_id WHERE p.round_id=? AND p.status='PAID' AND s.status='PENDING'", (round_id,), 0)
        if held:
            raise BusinessError('Money for the current turn is still in the pot. Cancel those payments first (after returning the money), then cancel the round.')
        db.execute("UPDATE mgr_rounds SET status='CANCELLED', closed_at=? WHERE id=?", (iso(now), round_id))
        audit(db, actor, 'MGR_CANCELLED', 'mgr_round', round_id, chama_id, {'reason': reason})
        for s in slots(db, round_id):
            notify(db, s['user_id'], chama_id, f"{rnd['name']} was cancelled: {reason.strip()[:100]}", f'/chamas/{chama_id}/merry-go-round/{round_id}', actor, now)


def add_member(db, chama_id, round_id, user_id, actor, now=None):
    """Add a Chama member to a round before the first payment is made."""
    now = now or now_utc()
    with db.tx():
        db.lock('chamas', chama_id)
        rnd = get_round(db, chama_id, round_id)
        if rnd['status'] != 'ACTIVE':
            raise BusinessError('Only an active merry-go-round can be changed.')
        if db.val("SELECT COUNT(*) FROM mgr_payments WHERE round_id=?", (round_id,), 0):
            raise BusinessError('Members cannot be added after payments have started for this round. Start a new round instead.')
        count = db.val('SELECT COUNT(*) FROM mgr_slots WHERE round_id=?', (round_id,), 0)
        if count >= MAX_PEOPLE:
            raise BusinessError(f'A merry-go-round can have at most {MAX_PEOPLE} members.')
        active = db.one("SELECT 1 FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, user_id))
        if not active:
            raise BusinessError('Choose an active Chama member.')
        if db.one('SELECT 1 FROM mgr_slots WHERE round_id=? AND user_id=?', (round_id, user_id)):
            raise BusinessError('That member is already in this merry-go-round.')
        position = int(db.val('SELECT COALESCE(MAX(position),0)+1 FROM mgr_slots WHERE round_id=?', (round_id,), 1))
        start = date.fromisoformat(rnd['start_date'])
        due = due_date(start, rnd['frequency'], position - 1)
        sid = db.insert('mgr_slots', round_id=round_id, chama_id=chama_id, position=position, user_id=user_id, due_date=due.isoformat())
        audit(db, actor, 'MGR_MEMBER_ADDED', 'mgr_slot', sid, chama_id, {'round_id': round_id, 'user_id': user_id, 'position': position})
        notify(db, user_id, chama_id, f'You were added to {rnd["name"]}. Your turn is position {position} on {due.isoformat()}.', f'/chamas/{chama_id}/merry-go-round/{round_id}', actor, now)
    return sid
