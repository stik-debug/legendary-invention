"""Password reset without email or SMS. A trusted person issues a one-time 8-digit code; the person who forgot types it on /forgot.
Who may issue one:
  * the ChamaPay owner, for anyone except an owner account;
  * a chama administrator, ONLY for an ordinary officer or member who belongs to no other chama and is not an administrator.
    (Otherwise a chairperson of one chama could take over someone's account, and with it their other chamas.)
The code works once, expires after 60 minutes, locks after 5 wrong tries, and is stored hashed.
A successful reset also logs the account out everywhere else."""
from datetime import timedelta

from db import audit
from services import BusinessError, _new_code, iso, now_utc, parse

CODE_MINUTES = 60
MAX_FAILS = 5
GENERIC = 'That phone number or code is not right, or the code has expired. Ask for a new code.'


def issue_reset_code(db, actor_id, user_id, hasher, chama_id=None, now=None):
    """chama_id=None means the owner is acting. Returns the plain code (shown once, never stored)."""
    now = now or now_utc()
    with db.tx():
        t = db.one('SELECT * FROM users WHERE id=? AND is_active=1', (user_id,))
        if not t:
            raise BusinessError('Person not found.')
        if t['is_super_admin']:
            raise BusinessError('The owner account is recovered from the server settings, not from here.')
        if t['id'] == actor_id:
            raise BusinessError('Ask another official to reset your password.')
        if not t['claimed']:
            raise BusinessError('This person has not registered yet. Give them a new join code instead.')
        if chama_id is not None:
            m = db.one("SELECT role FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, user_id))
            if not m:
                raise BusinessError('That person is not an active member of this chama.')
            if m['role'] == 'CHAMA_ADMIN':
                raise BusinessError('Another administrator\'s password can only be reset by the ChamaPay owner.')
            if db.val("SELECT COUNT(*) FROM chama_members WHERE user_id=? AND status='ACTIVE' AND chama_id!=?", (user_id, chama_id), 0):
                raise BusinessError('This person is in more than one chama, so only the ChamaPay owner can reset their password. Ask them to contact support.')
        code = _new_code()
        db.execute('UPDATE users SET reset_hash=?, reset_expires=?, reset_fails=0 WHERE id=?', (hasher(code), iso(now + timedelta(minutes=CODE_MINUTES)), user_id))
        audit(db, actor_id, 'PASSWORD_RESET_CODE_ISSUED', 'user', user_id, chama_id, {'by': 'owner' if chama_id is None else 'chama_admin'})
    return code


def reset_password(db, phone, code, new_password, hasher, checker, now=None):
    """Every failure gives the same message, so nobody can use this page to find out who has an account."""
    from services import normalize_phone
    now = now or now_utc()
    if len(new_password or '') < 8:
        raise BusinessError('Password must be at least 8 characters.')
    code = ''.join((code or '').split())
    u = db.one('SELECT * FROM users WHERE phone=? AND is_active=1', (normalize_phone(phone) or '-',))
    if not u or not u['reset_hash'] or not u['reset_expires'] or parse(u['reset_expires']) < now or (u['reset_fails'] or 0) >= MAX_FAILS:
        raise BusinessError(GENERIC)
    if not code or not checker(u['reset_hash'], code):
        with db.tx():
            db.execute('UPDATE users SET reset_fails=reset_fails+1 WHERE id=?', (u['id'],))
        raise BusinessError(GENERIC)
    with db.tx():
        db.execute('UPDATE users SET password_hash=?, reset_hash=NULL, reset_expires=NULL, reset_fails=0, claim_fails=0, session_epoch=session_epoch+1 WHERE id=?',
                   (hasher(new_password), u['id']))
        audit(db, u['id'], 'PASSWORD_RESET_DONE', 'user', u['id'])
    return u['id']
