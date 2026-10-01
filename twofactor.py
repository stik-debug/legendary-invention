"""Two-factor login for the platform owner: standard TOTP (RFC 6238, 6 digits, 30 seconds, works with Google Authenticator,
Microsoft Authenticator, Authy and similar apps) plus one-time recovery codes. Standard library only.
Honest limits: the authenticator secret is stored in the database as-is (it must be readable to check codes), so protect database access."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

from db import audit
from services import BusinessError

STEP = 30
RECOVERY_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def code_at(secret, step):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    o = mac[-1] & 15
    return f'{(struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}'


def verify_code(secret, code, last_step=0, now=None):
    """Returns the matching time step (accepts one step either side for clock drift), or None. A step can only be used once."""
    code = ''.join((code or '').split())  # people type '287 082'; spaces are fine, anything else is not
    if len(code) != 6 or not code.isdigit() or not code.isascii():
        return None
    cur = int((now if now is not None else time.time()) // STEP)
    for step in (cur, cur - 1, cur + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret, account, issuer='ChamaPay'):
    return f'otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period={STEP}'


def _hash(code):
    return hashlib.sha256(code.strip().lower().replace(' ', '').encode()).hexdigest()


def _new_recovery(n=8):
    codes = [''.join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10)) for _ in range(n)]
    return [c[:5] + '-' + c[5:] for c in codes]


def _store_recovery(db, user_id, codes):
    db.execute('UPDATE users SET totp_recovery=? WHERE id=?', (json.dumps([_hash(c.replace('-', '')) for c in codes]), user_id))


def begin_setup(db, user_id):
    """Create a fresh secret that is NOT active until the owner proves their app works (confirm_setup)."""
    secret = new_secret()
    with db.tx():
        db.execute('UPDATE users SET totp_secret=?, totp_enabled=0, totp_last_step=0, totp_recovery=NULL WHERE id=? AND is_super_admin=1', (secret, user_id))
    return secret


def confirm_setup(db, user_id, code, now=None):
    u = db.one('SELECT * FROM users WHERE id=? AND is_super_admin=1', (user_id,))
    if not u or not u['totp_secret'] or u['totp_enabled']:
        raise BusinessError('Start the setup again.')
    step = verify_code(u['totp_secret'], code, 0, now)
    if step is None:
        raise BusinessError('That code is not right. Check the time on your phone and try the newest code.')
    codes = _new_recovery()
    with db.tx():
        db.execute('UPDATE users SET totp_enabled=1, totp_last_step=? WHERE id=?', (step, user_id))
        _store_recovery(db, user_id, codes)
        audit(db, user_id, 'OWNER_2FA_ENABLED', 'user', user_id)
    return codes


def check_login(db, user, code, now=None):
    """Accepts an authenticator code or an unused recovery code. Recovery codes are consumed."""
    if not user['totp_enabled'] or not user['totp_secret']:
        return False
    step = verify_code(user['totp_secret'], code, user['totp_last_step'] or 0, now)
    if step is not None:
        with db.tx():
            db.execute('UPDATE users SET totp_last_step=? WHERE id=?', (step, user['id']))
        return True
    h = _hash((code or '').replace('-', ''))
    stored = json.loads(user['totp_recovery'] or '[]')
    if h in stored:
        stored.remove(h)
        with db.tx():
            db.execute('UPDATE users SET totp_recovery=? WHERE id=?', (json.dumps(stored), user['id']))
            audit(db, user['id'], 'OWNER_RECOVERY_CODE_USED', 'user', user['id'], None, {'left': len(stored)})
        return True
    return False


def recovery_left(user):
    return len(json.loads(user['totp_recovery'] or '[]'))


def new_recovery_codes(db, user_id, code, now=None):
    u = db.one('SELECT * FROM users WHERE id=?', (user_id,))
    if not check_login(db, u, code, now):
        raise BusinessError('Enter a current code from your authenticator app.')
    codes = _new_recovery()
    with db.tx():
        _store_recovery(db, user_id, codes)
        audit(db, user_id, 'OWNER_RECOVERY_CODES_RENEWED', 'user', user_id)
    return codes


def disable(db, user_id, password, code, pw_checker, now=None):
    u = db.one('SELECT * FROM users WHERE id=?', (user_id,))
    if not pw_checker(u['password_hash'], password or ''):
        raise BusinessError('Wrong password.')
    if not check_login(db, db.one('SELECT * FROM users WHERE id=?', (user_id,)), code, now):
        raise BusinessError('Enter a current code from your authenticator app, or an unused recovery code.')
    with db.tx():
        db.execute('UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_recovery=NULL, totp_last_step=0 WHERE id=?', (user_id,))
        audit(db, user_id, 'OWNER_2FA_DISABLED', 'user', user_id)


def force_disable(db, email):
    """Emergency recovery from the server (OWNER_RESET_2FA=1). Returns True if an owner was changed."""
    with db.tx():
        cur = db.execute('UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_recovery=NULL, totp_last_step=0 WHERE is_super_admin=1 AND email=?', ((email or '').strip().lower(),))
        if cur.rowcount:
            audit(db, None, 'OWNER_2FA_RESET_FROM_SERVER', 'user', None)
    return bool(cur.rowcount)
