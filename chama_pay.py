"""In-app chama payments.

Money goes straight from the member to the CHAMA's own paybill: ChamaPay never holds it.
Two channels:
  DARAJA  the chama's own Daraja app. The member gets an M-Pesa PIN prompt, and the contribution / fine / loan repayment is recorded
          automatically when Safaricom confirms (verified server-side, once only).
  MANUAL  the member pays the chama's paybill/till/phone themselves, enters the M-Pesa code, and an official approves it.
Everything is recorded through the same finance rules and ledger as a payment typed in by the treasurer."""
import base64
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken

import finance as F
from db import IntegrityError, audit
from notify import notify, notify_roles
from services import BusinessError, iso, now_utc
from services import parse as parse_time

PURPOSES = {'CONTRIBUTION': 'Contribution', 'FINE': 'Fine', 'LOAN': 'Loan repayment'}
MAX_CENTS = 250_000 * 100  # M-Pesa's own limit per transaction
STK_WAIT = timedelta(minutes=10)


# ---------- secrets ----------
def _secret():
    return os.environ.get('CHAMA_SECRETS_KEY', '')


def encryption_ready():
    return len(_secret()) >= 32


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_secret().encode()).digest()))


def enc(v):
    return _fernet().encrypt(v.encode()).decode()


def dec(v):
    try:
        return _fernet().decrypt((v or '').encode()).decode()
    except (InvalidToken, ValueError):
        return ''


def callback_secret(chama_id):
    """Per-chama secret in the callback URL. Derived, so nothing extra is stored."""
    return hmac.new(_secret().encode(), f'chama-cb-{chama_id}'.encode(), hashlib.sha256).hexdigest()[:32] if encryption_ready() else ''


def norm_phone(raw):
    d = re.sub(r'\D', '', raw or '')
    if d.startswith('0'):
        d = '254' + d[1:]
    elif len(d) == 9 and d[0] in '71':
        d = '254' + d
    if not re.fullmatch(r'254[17]\d{8}', d):
        raise BusinessError('Enter a valid M-Pesa number such as 0712345678.')
    return d


# ---------- settings ----------
def get_config(db, chama_id):
    return db.one('SELECT * FROM chama_pay_config WHERE chama_id=?', (chama_id,))


def channel(db, chama_id):
    c = get_config(db, chama_id)
    if not c:
        return None
    if c['mode'] == 'DARAJA' and c['shortcode'] and c['key_enc'] and c['secret_enc'] and c['passkey_enc'] and encryption_ready():
        return 'DARAJA'
    return 'MANUAL' if c['instructions'] else None


def save_config(db, chama_id, f, actor, production, now=None):
    now = now or now_utc()
    mode = f.get('mode')
    if mode not in ('MANUAL', 'DARAJA'):
        raise BusinessError('Choose how members will pay.')
    instr = (f.get('instructions') or '').strip()[:500]
    old = get_config(db, chama_id)
    vals = {'mode': mode, 'instructions': instr or None, 'updated_by': actor, 'updated_at': iso(now)}
    if mode == 'MANUAL' and len(instr) < 5:
        raise BusinessError('Tell members how to pay: your paybill or till number and the account name.')
    if mode == 'DARAJA':
        if not encryption_ready():
            raise BusinessError('Automatic payments are not switched on for this server yet. The ChamaPay owner must set CHAMA_SECRETS_KEY.')
        code = (f.get('shortcode') or '').strip()
        env = f.get('env') or 'sandbox'
        if not re.fullmatch(r'\d{5,7}', code):
            raise BusinessError('Enter your paybill number (5 to 7 digits).')
        if env not in ('sandbox', 'production') or (production and env != 'production'):
            raise BusinessError('A live chama must use production credentials.')
        for field, col in (('key', 'key_enc'), ('secret', 'secret_enc'), ('passkey', 'passkey_enc')):
            new = (f.get(field) or '').strip()
            if new:
                vals[col] = enc(new)
            elif not (old and old.get(col)):
                raise BusinessError('Enter your Daraja consumer key, consumer secret and passkey.')
        vals.update(shortcode=code, env=env)
    with db.tx():
        if old:
            db.execute('UPDATE chama_pay_config SET ' + ','.join(f'{k}=?' for k in vals) + ' WHERE chama_id=?', list(vals.values()) + [chama_id])
        else:
            db.insert('chama_pay_config', chama_id=chama_id, **vals)
        audit(db, actor, 'PAY_CONFIG_SAVED', 'chama', chama_id, chama_id, {'mode': mode})


# ---------- Daraja (the chama's own app) ----------
class Daraja:
    def __init__(self, c):
        self.code, self.env = c['shortcode'], c['env']
        self.key, self.secret, self.passkey = dec(c['key_enc']), dec(c['secret_enc']), dec(c['passkey_enc'])
        self.base = 'https://api.safaricom.co.ke' if self.env == 'production' else 'https://sandbox.safaricom.co.ke'

    def _call(self, path, body, headers):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())

    def _auth(self):
        cred = base64.b64encode(f'{self.key}:{self.secret}'.encode()).decode()
        tok = self._call('/oauth/v1/generate?grant_type=client_credentials', None, {'Authorization': 'Basic ' + cred})['access_token']
        return {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}

    def _stamp(self):
        ts = (now_utc() + timedelta(hours=3)).strftime('%Y%m%d%H%M%S')  # Safaricom expects East Africa time
        return ts, base64.b64encode((self.code + self.passkey + ts).encode()).decode()

    def stk_push(self, phone, cents, ref, callback, desc):
        try:
            ts, pw = self._stamp()
            d = self._call('/mpesa/stkpush/v1/processrequest', {
                'BusinessShortCode': self.code, 'Password': pw, 'Timestamp': ts, 'TransactionType': 'CustomerPayBillOnline',
                'Amount': cents // 100, 'PartyA': phone, 'PartyB': self.code, 'PhoneNumber': phone, 'CallBackURL': callback,
                'AccountReference': ref[:12], 'TransactionDesc': desc[:13]}, self._auth())
            if d.get('ResponseCode') == '0':
                return {'ok': True, 'checkout_id': d['CheckoutRequestID']}
            return {'ok': False, 'error': d.get('errorMessage') or d.get('ResponseDescription') or 'M-Pesa refused the request.'}
        except Exception:
            return {'ok': False, 'error': 'Could not reach M-Pesa. Please try again.'}

    def stk_query(self, checkout_id):
        ts, pw = self._stamp()
        try:
            return self._call('/mpesa/stkpushquery/v1/query', {'BusinessShortCode': self.code, 'Password': pw, 'Timestamp': ts, 'CheckoutRequestID': checkout_id}, self._auth())
        except urllib.error.HTTPError as e:  # "still being processed" comes back as an HTTP error with a JSON body
            try:
                return json.loads(e.read() or b'{}')
            except ValueError:
                return {}


class Simulated:
    """Development only: no money moves. Outcomes are triggered with the dev buttons."""
    def stk_push(self, phone, cents, ref, callback, desc):
        return {'ok': True, 'checkout_id': 'SIM-' + uuid.uuid4().hex[:14].upper()}

    def stk_query(self, checkout_id):
        return {}


def _client(c, simulate):
    return Simulated() if simulate else Daraja(c)


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


# ---------- automatic (STK push) ----------
def initiate(db, chama_id, user_id, purpose, target_id, cents, phone, base_url, simulate, now=None):
    now = now or now_utc()
    if channel(db, chama_id) != 'DARAJA':
        raise BusinessError('Automatic M-Pesa payment is not set up for this chama.')
    phone = norm_phone(phone)
    tid, cap = _target(db, chama_id, user_id, purpose, target_id)
    _check_amount(cents, cap)
    with db.tx():
        db.lock('chamas', chama_id)
        if db.val("SELECT COUNT(*) FROM chama_payments WHERE chama_id=? AND user_id=? AND purpose=? AND status='PENDING' AND created_at>?",
                  (chama_id, user_id, purpose, iso(now - timedelta(minutes=3))), 0):
            raise BusinessError('A payment is already waiting for your M-Pesa PIN. Finish it, or wait a few minutes.')
        pid = db.insert('chama_payments', chama_id=chama_id, user_id=user_id, purpose=purpose, target_id=tid, amount_cents=cents, phone=phone,
                        channel='STK', status='PENDING', created_at=iso(now))
    cb = f'{base_url}/webhooks/chama-mpesa/{chama_id}/{callback_secret(chama_id)}'
    res = _client(get_config(db, chama_id), simulate).stk_push(phone, cents, f'CHAMA{chama_id}', cb, 'ChamaPay')
    with db.tx():
        if res['ok']:
            db.execute('UPDATE chama_payments SET checkout_id=? WHERE id=?', (res['checkout_id'], pid))
        else:
            db.execute("UPDATE chama_payments SET status='FAILED', result_desc=?, completed_at=? WHERE id=?", (res['error'][:200], iso(now), pid))
    if not res['ok']:
        raise BusinessError(res['error'])
    return pid


def settle(db, chama_id, checkout_id, code, amount_cents, receipt, now=None):
    """One M-Pesa result (callback or status check). Safe to call any number of times: only the first one counts."""
    now = now or now_utc()
    row = db.one('SELECT id FROM chama_payments WHERE chama_id=? AND checkout_id=?', (chama_id, checkout_id)) if checkout_id else None
    if not row:
        return 'unknown'
    try:
        with db.tx():
            db.lock('chamas', chama_id)
            p = db.one('SELECT * FROM chama_payments WHERE id=?', (row['id'],))
            if p['status'] != 'PENDING':
                return 'duplicate'
            if code != 0:
                st = {1032: 'CANCELLED', 1037: 'TIMEOUT'}.get(code, 'FAILED')
                db.execute('UPDATE chama_payments SET status=?, result_desc=?, completed_at=? WHERE id=?', (st, f'M-Pesa result {code}', iso(now), p['id']))
                notify(db, p['user_id'], chama_id, f'Your M-Pesa payment of {_label(p)} was not completed.', f'/chamas/{chama_id}/pay/{p["id"]}', None, now)
                return st.lower()
            if amount_cents is not None and amount_cents != p['amount_cents']:  # forged or wrong-amount callback
                db.execute("UPDATE chama_payments SET status='FAILED', result_desc='Amount did not match', completed_at=? WHERE id=?", (iso(now), p['id']))
                audit(db, None, 'PAY_AMOUNT_MISMATCH', 'chama_payment', p['id'], chama_id, {'expected': p['amount_cents'], 'got': amount_cents})
                return 'rejected'
            rid = _apply(db, p, (receipt or 'CHK' + checkout_id[-9:]).upper(), p['user_id'], now)
            db.execute("UPDATE chama_payments SET status='SUCCESS', applied=1, applied_ref_id=?, receipt=?, completed_at=? WHERE id=?",
                       (rid, receipt.upper() if receipt else None, iso(now), p['id']))
            audit(db, p['user_id'], 'PAY_APPLIED', 'chama_payment', p['id'], chama_id, {'purpose': p['purpose'], 'amount_cents': p['amount_cents']})
            notify(db, p['user_id'], chama_id, f'Your M-Pesa payment of {_label(p)} was received and recorded.', f'/chamas/{chama_id}/statement', None, now)
            notify_roles(db, chama_id, F.FINANCE_ROLES, f'{_label(p)} paid in the app.', f'/chamas/{chama_id}/pay', p['user_id'], now)
            return 'applied'
    except (BusinessError, IntegrityError) as e:  # the money may have arrived but cannot be recorded automatically: an official decides
        with db.tx():
            db.execute("UPDATE chama_payments SET status='REVIEW', result_desc=?, completed_at=? WHERE id=? AND status='PENDING'", (str(e)[:200], iso(now), row['id']))
            notify_roles(db, chama_id, F.FINANCE_ROLES, 'An M-Pesa payment arrived but could not be recorded automatically. Please review it.', f'/chamas/{chama_id}/pay', None, now)
        return 'review'


def check_pending(db, chama_id, pid, user_id, simulate, now=None):
    """The payer asks M-Pesa directly (covers a lost callback). Never marks success without M-Pesa saying so."""
    now = now or now_utc()
    p = db.one('SELECT * FROM chama_payments WHERE id=? AND chama_id=? AND user_id=?', (pid, chama_id, user_id))
    if not p:
        raise BusinessError('Payment not found.')
    if p['status'] == 'PENDING' and p['checkout_id']:
        try:
            q = _client(get_config(db, chama_id), simulate).stk_query(p['checkout_id']) or {}
        except Exception:
            q = {}
        code = q.get('ResultCode')
        if code not in (None, ''):
            settle(db, chama_id, p['checkout_id'], int(code), None, None, now)
        elif now - parse_time(p['created_at']) > STK_WAIT:
            settle(db, chama_id, p['checkout_id'], 1037, None, None, now)
    return db.one('SELECT * FROM chama_payments WHERE id=?', (pid,))


# ---------- manual claims ----------
def claim(db, chama_id, user_id, purpose, target_id, cents, code, now=None):
    now = now or now_utc()
    if not channel(db, chama_id):
        raise BusinessError('Payment details are not set up for this chama yet.')
    code = (code or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{8,12}', code):
        raise BusinessError('Enter the M-Pesa confirmation code from your SMS, for example QRT1234ABC.')
    tid, cap = _target(db, chama_id, user_id, purpose, target_id)
    _check_amount(cents, cap)
    try:
        with db.tx():
            pid = db.insert('chama_payments', chama_id=chama_id, user_id=user_id, purpose=purpose, target_id=tid, amount_cents=cents, channel='CLAIM',
                            status='CLAIMED', receipt=code, created_at=iso(now))
            who = db.val('SELECT name FROM users WHERE id=?', (user_id,))
            notify_roles(db, chama_id, F.FINANCE_ROLES, f'{who} says they paid {_label({"amount_cents": cents, "purpose": purpose})} ({code}). Check your M-Pesa statement, then approve.',
                         f'/chamas/{chama_id}/pay', user_id, now)
    except IntegrityError:
        raise BusinessError('That M-Pesa code has already been submitted.')
    return pid


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
