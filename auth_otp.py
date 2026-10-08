"""SMS OTP security flows for ChamaPay.

OTP is intentionally NOT part of ordinary login. It is used only for sensitive
account actions such as signup phone verification, password recovery, phone
changes and explicit security challenges.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

import services as S
from db import audit
import notify as N

OTP_MINUTES = 5
RESEND_SECONDS = 60
MAX_ATTEMPTS = 5
MAX_REQUESTS = 5
REQUEST_WINDOW_MINUTES = 15
GENERIC = 'If that number is registered, a verification code has been sent.'


def _hash(app, code):
    return hmac.new(app.config['SECRET_KEY'].encode('utf-8'), code.encode('utf-8'), hashlib.sha256).hexdigest()


def _new_code():
    return f'{secrets.randbelow(1_000_000):06d}'


def _invalidate(db, user_id, phone, purpose):
    db.execute("UPDATE auth_otps SET status='SUPERSEDED' WHERE status='PENDING' AND purpose=? AND (user_id=? OR phone=?)",
               (purpose, user_id if user_id is not None else -1, phone))


def request_otp(app, db, user_id, phone, purpose, ip=None, now=None, force=True):
    """Create a single-use OTP and queue its SMS. Returns (ok, message, otp_id).
    Plain OTP is never stored or logged.
    """
    now = now or S.now_utc()
    phone = S.normalize_phone(phone)
    if not phone:
        return False, 'Enter a valid Kenyan phone number.', None

    recent = db.val("SELECT COUNT(*) FROM auth_otps WHERE phone=? AND purpose=? AND created_at>=?",
                    (phone, purpose, S.iso(now - timedelta(minutes=REQUEST_WINDOW_MINUTES))), 0)
    if recent >= MAX_REQUESTS:
        return False, 'Too many verification requests. Please wait 15 minutes and try again.', None

    last = db.one("SELECT created_at FROM auth_otps WHERE phone=? AND purpose=? ORDER BY id DESC LIMIT 1", (phone, purpose))
    if last and (now - S.parse(last['created_at'])).total_seconds() < RESEND_SECONDS:
        return False, 'Please wait 60 seconds before requesting another code.', None

    code = _new_code()
    expires = now + timedelta(minutes=OTP_MINUTES)
    with db.tx():
        _invalidate(db, user_id, phone, purpose)
        oid = db.insert('auth_otps', user_id=user_id, phone=phone, purpose=purpose,
                        code_hash=_hash(app, code), expires_at=S.iso(expires), attempts=0,
                        created_at=S.iso(now), request_ip=(ip or '')[:80], status='PENDING')
        # OTP delivery deliberately bypasses notification preferences: this is an
        # account-security message, not a marketing/notification preference. Never
        # persist the plaintext OTP in the general-purpose SMS outbox.
        db.insert('sms_outbox', user_id=user_id, phone='+' + phone,
                  message='[SECURITY OTP SENT IMMEDIATELY; MESSAGE REDACTED]',
                  created_at=S.iso(now), status='PENDING', attempts=0,
                  dedupe_key=f'otp:{oid}')

    # Security OTPs are sent immediately. Do not claim success while the message
    # is merely sitting in an outbox/background worker.
    message = f'ChamaPay verification code: {code}. It expires in {OTP_MINUTES} minutes. Do not share this code with anyone.'
    sent, result = N.send_sms_now(phone, message)
    if not sent:
        with db.tx():
            db.execute("UPDATE auth_otps SET status='FAILED' WHERE id=?", (oid,))
            db.execute("UPDATE sms_outbox SET status='FAILED', attempts=attempts+1, last_error=? WHERE dedupe_key=?",
                       (str(result)[:500], f'otp:{oid}'))
        return False, 'We could not send the verification SMS. Please check the SMS service configuration and try again.', None
    with db.tx():
        db.execute("UPDATE sms_outbox SET status='SENT', attempts=attempts+1, sent_at=?, last_error=NULL WHERE dedupe_key=?",
                   (S.iso(S.now_utc()), f'otp:{oid}'))
    return True, 'Verification code sent.', oid


def verify_otp(app, db, otp_id, code, purpose, now=None):
    now = now or S.now_utc()
    row = db.one('SELECT * FROM auth_otps WHERE id=? AND purpose=?', (otp_id, purpose))
    if not row or row['status'] != 'PENDING' or not row['expires_at'] or S.parse(row['expires_at']) < now:
        if row and row['status'] == 'PENDING':
            db.execute("UPDATE auth_otps SET status='EXPIRED' WHERE id=?", (otp_id,)); db.commit()
        return None
    if int(row.get('attempts') or 0) >= MAX_ATTEMPTS:
        db.execute("UPDATE auth_otps SET status='LOCKED' WHERE id=?", (otp_id,)); db.commit()
        return None
    code = ''.join((code or '').split())
    if not code or not hmac.compare_digest(row['code_hash'], _hash(app, code)):
        with db.tx():
            new_attempts = int(row.get('attempts') or 0) + 1
            db.execute("UPDATE auth_otps SET attempts=?, status=? WHERE id=?",
                       (new_attempts, 'LOCKED' if new_attempts >= MAX_ATTEMPTS else 'PENDING', otp_id))
        return None
    with db.tx():
        db.execute("UPDATE auth_otps SET status='USED', used_at=? WHERE id=?", (S.iso(now), otp_id))
    return row


def mark_phone_verified(db, user_id, now=None):
    with db.tx():
        db.execute('UPDATE users SET phone_verified_at=? WHERE id=?', (S.iso(now or S.now_utc()), user_id))
        audit(db, user_id, 'PHONE_VERIFIED', 'user', user_id)


def invalidate_for_user(db, user_id, purpose=None):
    if purpose:
        db.execute("UPDATE auth_otps SET status='SUPERSEDED' WHERE user_id=? AND purpose=? AND status='PENDING'", (user_id, purpose))
    else:
        db.execute("UPDATE auth_otps SET status='SUPERSEDED' WHERE user_id=? AND status='PENDING'", (user_id,))
