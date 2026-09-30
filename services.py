"""Business rules. Everything that touches money, plans or subscriptions lives here so it can be tested without a browser."""
import json
import re
from datetime import datetime, timedelta

from db import IntegrityError, audit

ACCESS_OK = {'TRIAL', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD'}
ROLES = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY', 'MEMBER')
PAYMENT_DONE = {'SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT'}


class BusinessError(Exception):
    """A rule was broken. The message is safe to show to the user."""


class PlanLimitError(BusinessError):
    pass


class SubscriptionInactive(BusinessError):
    pass


def now_utc():
    return datetime.utcnow().replace(microsecond=0)


def iso(d):
    return d.isoformat(sep=' ') if d else None


def parse(s):
    return datetime.fromisoformat(s) if s else None


def setting(db, key, default=0):
    v = db.val('SELECT value FROM settings WHERE key=?', (key,))
    return int(v) if v is not None else default


def kes(cents):
    return f'{(cents or 0) / 100:,.0f}' if (cents or 0) % 100 == 0 else f'{(cents or 0) / 100:,.2f}'


# ---------- validation ----------
def normalize_phone(raw):
    d = re.sub(r'\D', '', raw or '')
    if d.startswith('0') and len(d) == 10:
        d = '254' + d[1:]
    elif len(d) == 9 and d[0] in '17':
        d = '254' + d
    return d if re.fullmatch(r'254[17]\d{8}', d) else None


def valid_email(e):
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]{2,}', e or '')) and len(e) <= 120


# ---------- subscription state machine ----------
def effective_status(sub, now, grace_days):
    """Pure function: what the status should be at `now`.
    TRIAL/ACTIVE until due -> PAST_DUE (first day) -> GRACE_PERIOD -> SUSPENDED.
    SUSPENDED and CANCELLED only change through a payment or an owner action."""
    st = sub['status']
    if st in ('SUSPENDED', 'CANCELLED'):
        return st
    due = parse(sub['due_at'])
    if now <= due:
        return 'TRIAL' if st == 'TRIAL' else 'ACTIVE'
    if now > due + timedelta(days=grace_days):
        return 'SUSPENDED'
    return 'PAST_DUE' if now <= due + timedelta(days=min(1, grace_days)) else 'GRACE_PERIOD'


def sync_subscription(db, chama_id, now=None):
    """Bring the stored status up to date (called on access and by the scheduled sweep)."""
    now = now or now_utc()
    sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (chama_id,))
    if not sub:
        return None
    eff = effective_status(sub, now, setting(db, 'grace_days', 3))
    if eff != sub['status']:
        with db.tx():
            fields = {'status': eff, 'updated_at': iso(now)}
            if eff == 'SUSPENDED':
                fields.update(suspended_at=iso(now), suspend_reason='Subscription payment not received')
            db.execute('UPDATE subscriptions SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?',
                       list(fields.values()) + [sub['id']])
            audit(db, None, 'SUBSCRIPTION_SUSPENDED' if eff == 'SUSPENDED' else 'SUBSCRIPTION_CHANGED',
                  'subscription', sub['id'], chama_id, {'from': sub['status'], 'to': eff, 'automatic': True})
        sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (chama_id,))
    return sub


def sweep_all(db, now=None):
    """Scheduled job entry point. Safe to run as often as you like."""
    n = 0
    for r in db.all("SELECT chama_id FROM subscriptions WHERE status IN ('TRIAL','ACTIVE','PAST_DUE','GRACE_PERIOD')"):
        before = db.val('SELECT status FROM subscriptions WHERE chama_id=?', (r['chama_id'],))
        after = sync_subscription(db, r['chama_id'], now)['status']
        n += before != after
    expire_pending_payments(db, now)
    return n


# ---------- plans ----------
def get_plan(db, plan_id=None, code=None):
    if code:
        return db.one('SELECT * FROM subscription_plans WHERE code=? AND is_active=1', (code,))
    return db.one('SELECT * FROM subscription_plans WHERE id=?', (plan_id,))


def active_member_count(db, chama_id):
    return db.val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,), 0)


def chama_limit(db, chama_id):
    return db.val('SELECT p.max_members FROM subscriptions s JOIN subscription_plans p ON p.id=s.plan_id WHERE s.chama_id=?', (chama_id,), 0)


# ---------- users ----------
def create_user(db, name, email, phone, password_hash, super_admin=False, is_test=False, now=None):
    name, email = (name or '').strip(), (email or '').strip().lower()
    ph = normalize_phone(phone)
    if len(name) < 2:
        raise BusinessError('Enter your full name.')
    if not valid_email(email):
        raise BusinessError('Enter a valid email address.')
    if not ph:
        raise BusinessError('Enter a valid Kenyan phone number, for example 0712345678.')
    if db.val('SELECT COUNT(*) FROM users WHERE email=? OR phone=?', (email, ph)):
        raise BusinessError('An account with that email or phone number already exists.')
    try:
        with db.tx():
            uid = db.insert('users', name=name, email=email, phone=ph, password_hash=password_hash,
                            is_super_admin=int(super_admin), is_test_data=int(is_test), created_at=iso(now or now_utc()))
    except IntegrityError:
        raise BusinessError('An account with that email or phone number already exists.')
    return uid


# ---------- chamas and members ----------
def create_chama(db, user_id, name, description, plan_code, now=None):
    now = now or now_utc()
    name = (name or '').strip()
    if len(name) < 3:
        raise BusinessError('Give your chama a name (at least 3 letters).')
    plan = get_plan(db, code=plan_code)
    if not plan:
        raise BusinessError('Choose a plan.')
    with db.tx():
        cid = db.insert('chamas', name=name[:100], description=(description or '').strip()[:300], created_by=user_id, created_at=iso(now))
        trial_end = now + timedelta(days=setting(db, 'trial_days', 7))
        db.insert('subscriptions', chama_id=cid, plan_id=plan['id'], status='TRIAL', trial_ends_at=iso(trial_end),
                  due_at=iso(trial_end), updated_at=iso(now))
        db.insert('chama_members', chama_id=cid, user_id=user_id, role='CHAMA_ADMIN', status='ACTIVE', joined_at=iso(now))
        audit(db, user_id, 'CHAMA_CREATED', 'chama', cid, cid, {'plan': plan['code']})
    return cid


def add_member(db, chama_id, user_id, role, actor_id, now=None):
    now = now or now_utc()
    if role not in ROLES:
        raise BusinessError('Unknown role.')
    with db.tx():
        db.lock('chamas', chama_id)
        sub = sync_subscription(db, chama_id, now)
        if not sub or sub['status'] not in ACCESS_OK:
            raise SubscriptionInactive('This chama is suspended. Pay the subscription to add members.')
        limit = chama_limit(db, chama_id)
        count = active_member_count(db, chama_id)
        existing = db.one('SELECT * FROM chama_members WHERE chama_id=? AND user_id=?', (chama_id, user_id))
        if existing and existing['status'] == 'ACTIVE':
            raise BusinessError('That person is already a member.')
        if count >= limit:
            raise PlanLimitError(f'Your plan allows {limit} members and you have {count}. Upgrade the plan to add more.')
        if existing:
            db.execute("UPDATE chama_members SET status='ACTIVE', role=?, removed_at=NULL, joined_at=? WHERE id=?", (role, iso(now), existing['id']))
            mid = existing['id']
        else:
            mid = db.insert('chama_members', chama_id=chama_id, user_id=user_id, role=role, status='ACTIVE', joined_at=iso(now))
        audit(db, actor_id, 'MEMBER_ADDED', 'chama_member', mid, chama_id, {'user_id': user_id, 'role': role})
    return mid


def remove_member(db, chama_id, user_id, actor_id, now=None):
    now = now or now_utc()
    with db.tx():
        m = db.one("SELECT * FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, user_id))
        if not m:
            raise BusinessError('Member not found.')
        if m['role'] == 'CHAMA_ADMIN' and db.val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND role='CHAMA_ADMIN' AND status='ACTIVE'", (chama_id,)) <= 1:
            raise BusinessError('A chama must keep at least one administrator.')
        # History is kept: the row is only marked REMOVED.
        db.execute("UPDATE chama_members SET status='REMOVED', removed_at=? WHERE id=?", (iso(now), m['id']))
        audit(db, actor_id, 'MEMBER_REMOVED', 'chama_member', m['id'], chama_id, {'user_id': user_id})


def downgrade_plan(db, chama_id, plan_id, actor_id, now=None):
    """Chama-initiated change: only to an equal or cheaper plan that fits the current membership. Upgrades must be paid for."""
    now = now or now_utc()
    with db.tx():
        sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (chama_id,))
        cur, new = get_plan(db, sub['plan_id']), get_plan(db, plan_id)
        if not new or not new['is_active']:
            raise BusinessError('That plan is not available.')
        if new['price_cents'] > cur['price_cents']:
            raise BusinessError('Upgrading needs a payment. Use "Pay and upgrade".')
        count = active_member_count(db, chama_id)
        if count > new['max_members']:
            raise PlanLimitError(f'You have {count} members but {new["name"]} allows {new["max_members"]}. Remove members first; nobody is removed automatically.')
        db.execute('UPDATE subscriptions SET plan_id=?, updated_at=? WHERE id=?', (new['id'], iso(now), sub['id']))
        audit(db, actor_id, 'PLAN_CHANGED', 'subscription', sub['id'], chama_id, {'from': cur['code'], 'to': new['code']})


# ---------- payments ----------
def create_payment(db, chama_id, plan_id, months, method, provider, phone, actor_id, now=None, reference=None, notes=None, is_test=False):
    now = now or now_utc()
    plan = get_plan(db, plan_id)
    if not plan or not plan['is_active']:
        raise BusinessError('That plan is not available.')
    if months not in (1, 3, 6, 12):
        raise BusinessError('Choose 1, 3, 6 or 12 months.')
    count = active_member_count(db, chama_id)
    if count > plan['max_members']:
        raise PlanLimitError(f'This chama has {count} members, more than the {plan["name"]} limit of {plan["max_members"]}.')
    days = setting(db, 'billing_period_days', 30) * months
    with db.tx():
        pid = db.insert('payments', chama_id=chama_id, plan_id=plan['id'], amount_cents=plan['price_cents'] * months, period_days=days,
                        method=method, provider=provider, status='PENDING', phone=phone, reference=reference, notes=notes,
                        created_by=actor_id, created_at=iso(now), is_test_data=int(is_test))
        audit(db, actor_id, 'PAYMENT_CREATED', 'payment', pid, chama_id, {'amount_cents': plan['price_cents'] * months, 'method': method})
    return pid


def _apply_payment(db, payment_id, now):
    """Extend the subscription for a SUCCESS payment. Runs inside the caller's transaction. Only ever applies once."""
    p = db.one('SELECT * FROM payments WHERE id=?', (payment_id,))
    if p['status'] != 'SUCCESS' or p['applied']:
        return False
    sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (p['chama_id'],))
    was = sub['status']
    due = parse(sub['due_at'])
    base = due if (was in ('TRIAL', 'ACTIVE') and due > now) else now  # early payers do not lose days
    new_due = base + timedelta(days=p['period_days'])
    db.execute("UPDATE subscriptions SET plan_id=?, status='ACTIVE', due_at=?, suspended_at=NULL, suspend_reason=NULL, cancelled_at=NULL, updated_at=? WHERE id=?",
               (p['plan_id'], iso(new_due), iso(now), sub['id']))
    db.execute('UPDATE payments SET applied=1 WHERE id=?', (payment_id,))
    audit(db, p['created_by'], 'SUBSCRIPTION_REACTIVATED' if was in ('SUSPENDED', 'CANCELLED') else 'SUBSCRIPTION_CHANGED',
          'subscription', sub['id'], p['chama_id'], {'from': was, 'to': 'ACTIVE', 'payment_id': payment_id, 'new_due_at': iso(new_due)})
    return True


def process_webhook(db, provider, event):
    """event = {event_id, checkout_id, result_code, amount_cents, receipt}. Returns a status string.
    Safe against duplicates, unknown transactions, wrong amounts and replays."""
    now = now_utc()
    eid = event.get('event_id')
    if not eid or not event.get('checkout_id'):
        return 'INVALID'
    try:
        with db.tx():
            wid = db.insert('payment_webhooks', provider=provider, event_id=eid, payload=json.dumps(event, default=str), received_at=iso(now))
    except IntegrityError:
        row = db.one('SELECT * FROM payment_webhooks WHERE provider=? AND event_id=?', (provider, eid))
        if row and row['processed']:
            return 'DUPLICATE'
        wid = row['id']
    with db.tx():
        p = db.one('SELECT * FROM payments WHERE provider=? AND provider_txn_id=?', (provider, event['checkout_id']))
        if not p:
            db.execute("UPDATE payment_webhooks SET result='UNKNOWN_PAYMENT', processed=1 WHERE id=?", (wid,))
            return 'UNKNOWN'
        if p['status'] != 'PENDING':
            db.execute("UPDATE payment_webhooks SET result='ALREADY_FINAL', payment_id=?, processed=1 WHERE id=?", (p['id'], wid))
            return 'DUPLICATE'
        code = event.get('result_code')
        if code == 0:
            if event.get('amount_cents') != p['amount_cents'] or not event.get('receipt'):
                db.execute("UPDATE payments SET status='FAILED', notes=?, completed_at=? WHERE id=?", ('Callback amount or receipt did not match', iso(now), p['id']))
                audit(db, None, 'PAYMENT_REJECTED', 'payment', p['id'], p['chama_id'], {'reason': 'mismatch'})
                result = 'MISMATCH'
            else:
                try:
                    db.execute("UPDATE payments SET status='SUCCESS', provider_receipt=?, completed_at=? WHERE id=?", (event['receipt'], iso(now), p['id']))
                except IntegrityError:
                    raise BusinessError('Receipt already used')
                _apply_payment(db, p['id'], now)
                result = 'SUCCESS'
        else:
            status = 'CANCELLED' if code == 1032 else 'FAILED'
            db.execute('UPDATE payments SET status=?, completed_at=? WHERE id=?', (status, iso(now), p['id']))
            result = status
        db.execute('UPDATE payment_webhooks SET result=?, payment_id=?, processed=1 WHERE id=?', (result, p['id'], wid))
    return result


def record_manual_payment(db, chama_id, plan_id, months, method_label, reference, paid_on, notes, actor_id, now=None):
    now = now or now_utc()
    if not (reference or '').strip():
        raise BusinessError('A payment reference is required.')
    pid = create_payment(db, chama_id, plan_id, months, 'MANUAL', None, None, actor_id, now, reference.strip()[:60],
                         f'{method_label} | paid {paid_on} | {(notes or "")[:200]}')
    with db.tx():
        db.execute("UPDATE payments SET status='SUCCESS', completed_at=? WHERE id=?", (iso(now), pid))
        _apply_payment(db, pid, now)
        audit(db, actor_id, 'MANUAL_PAYMENT', 'payment', pid, chama_id, {'reference': reference, 'method': method_label})
    return pid


def expire_pending_payments(db, now=None, minutes=15):
    now = now or now_utc()
    cutoff = iso(now - timedelta(minutes=minutes))
    with db.tx():
        db.execute("UPDATE payments SET status='TIMEOUT', completed_at=? WHERE status='PENDING' AND method!='MANUAL' AND created_at<?", (iso(now), cutoff))


# ---------- owner actions ----------
def suspend_chama(db, chama_id, reason, actor_id, now=None):
    now = now or now_utc()
    if not (reason or '').strip():
        raise BusinessError('Please give a reason.')
    with db.tx():
        sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (chama_id,))
        db.execute("UPDATE subscriptions SET status='SUSPENDED', suspended_at=?, suspend_reason=?, updated_at=? WHERE id=?", (iso(now), reason.strip()[:300], iso(now), sub['id']))
        audit(db, actor_id, 'SUBSCRIPTION_SUSPENDED', 'subscription', sub['id'], chama_id, {'reason': reason, 'from': sub['status'], 'manual': True})


def grant_access(db, chama_id, days, actor_id, reactivate=False, now=None):
    """Owner extends a subscription (or reactivates a suspended one) by `days`. History is untouched."""
    now = now or now_utc()
    if not 1 <= int(days) <= 366:
        raise BusinessError('Days must be between 1 and 366.')
    with db.tx():
        sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (chama_id,))
        base = max(parse(sub['due_at']), now) if sub['status'] in ('TRIAL', 'ACTIVE') else now
        new_due = base + timedelta(days=int(days))
        status = 'TRIAL' if sub['status'] == 'TRIAL' else 'ACTIVE'
        db.execute('UPDATE subscriptions SET status=?, due_at=?, suspended_at=NULL, suspend_reason=NULL, cancelled_at=NULL, updated_at=?'
                   + (', trial_ends_at=?' if status == 'TRIAL' else '') + ' WHERE id=?',
                   [status, iso(new_due), iso(now)] + ([iso(new_due)] if status == 'TRIAL' else []) + [sub['id']])
        audit(db, actor_id, 'SUBSCRIPTION_REACTIVATED' if reactivate else 'SUBSCRIPTION_CHANGED', 'subscription', sub['id'], chama_id,
              {'from': sub['status'], 'to': status, 'extended_days': int(days), 'new_due_at': iso(new_due), 'manual': True})


def cancel_subscription(db, chama_id, actor_id, now=None):
    now = now or now_utc()
    with db.tx():
        sub = db.one('SELECT * FROM subscriptions WHERE chama_id=?', (chama_id,))
        db.execute("UPDATE subscriptions SET status='CANCELLED', cancelled_at=?, updated_at=? WHERE id=?", (iso(now), iso(now), sub['id']))
        audit(db, actor_id, 'SUBSCRIPTION_CHANGED', 'subscription', sub['id'], chama_id, {'from': sub['status'], 'to': 'CANCELLED'})


def update_plan(db, plan_id, name, price_kes, max_members, is_active, actor_id):
    try:
        price = round(float(price_kes) * 100)
        mx = int(max_members)
    except (TypeError, ValueError):
        raise BusinessError('Enter valid numbers.')
    if price < 0 or mx < 1 or len((name or '').strip()) < 2:
        raise BusinessError('Enter a valid name, price and member limit.')
    with db.tx():
        db.execute('UPDATE subscription_plans SET name=?, price_cents=?, max_members=?, is_active=? WHERE id=?', (name.strip(), price, mx, int(is_active), plan_id))
        audit(db, actor_id, 'PLAN_CHANGED', 'plan', plan_id, None, {'price_cents': price, 'max_members': mx, 'active': bool(is_active)})


def update_settings(db, trial_days, grace_days, period_days, actor_id):
    try:
        vals = {'trial_days': int(trial_days), 'grace_days': int(grace_days), 'billing_period_days': int(period_days)}
    except (TypeError, ValueError):
        raise BusinessError('Enter whole numbers.')
    if not (0 <= vals['trial_days'] <= 90 and 0 <= vals['grace_days'] <= 30 and 1 <= vals['billing_period_days'] <= 366):
        raise BusinessError('Values are out of range.')
    with db.tx():
        for k, v in vals.items():
            db.execute('UPDATE settings SET value=? WHERE key=?', (str(v), k))
        audit(db, actor_id, 'SETTINGS_CHANGED', 'settings', None, None, vals)


def owner_stats(db, now=None):
    now = now or now_utc()
    month = iso(now.replace(day=1, hour=0, minute=0, second=0))
    by = {r['status']: r['n'] for r in db.all('SELECT status, COUNT(*) n FROM subscriptions GROUP BY status')}
    real = "status='SUCCESS' AND applied=1 AND method!='TEST' AND is_test_data=0"
    return {'chamas': db.val('SELECT COUNT(*) FROM chamas', (), 0), 'users': db.val('SELECT COUNT(*) FROM users', (), 0),
            'by_status': by, 'payments_ok': db.val(f'SELECT COUNT(*) FROM payments WHERE {real}', (), 0),
            'revenue_total': db.val(f'SELECT SUM(amount_cents) FROM payments WHERE {real}', (), 0),
            'revenue_month': db.val(f'SELECT SUM(amount_cents) FROM payments WHERE {real} AND completed_at>=?', (month,), 0)}
