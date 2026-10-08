"""Account deletion for a data-subject request.

A chama's records (contributions, loans, fines, the ledger) belong to the whole group and must stay balanced, so an
account is never hard-deleted. It is ANONYMISED instead: name, phone, email, password, 2FA and sign-in sessions are
wiped, the person leaves their chamas, and what remains in the group's books is attributed to "Deleted member".

It is refused while the person still owes or is owed something that needs a human to settle first."""
from db import audit
from services import BusinessError, iso, now_utc

OPEN_LOAN = ('PENDING', 'APPROVED', 'ACTIVE')


def deletion_blockers(db, user_id):
    """Plain-language reasons this account cannot be anonymised yet (empty list = fine to proceed)."""
    u = db.one('SELECT * FROM users WHERE id=?', (user_id,))
    if not u:
        return ['Account not found.']
    if u['is_super_admin']:
        return ['The owner account cannot be deleted from here.']
    if not u['is_active'] and str(u['email']).endswith('@deleted.invalid'):
        return ['This account has already been deleted.']
    out = []
    marks = ','.join('?' * len(OPEN_LOAN))
    n = db.val(f'SELECT COUNT(*) FROM loans WHERE user_id=? AND status IN ({marks})', (user_id, *OPEN_LOAN), 0)
    if n:
        out.append(f'{n} open loan(s) must be repaid or closed first.')
    n = db.val("SELECT COUNT(*) FROM fines WHERE user_id=? AND status NOT IN ('PAID','WAIVED','CANCELLED')", (user_id,), 0)
    if n:
        out.append(f'{n} unpaid fine(s) must be paid or waived first.')
    n = db.val(f'SELECT COUNT(*) FROM loan_guarantors g JOIN loans l ON l.id=g.loan_id WHERE g.guarantor_user_id=? AND l.status IN ({marks})',
               (user_id, *OPEN_LOAN), 0)
    if n:
        out.append(f'They guarantee {n} open loan(s); the guarantee must be released first.')
    rows = db.all("SELECT c.name FROM chama_members m JOIN chamas c ON c.id=m.chama_id WHERE m.user_id=? AND m.status='ACTIVE' AND m.role='CHAMA_ADMIN'", (user_id,))
    if rows:
        out.append('They are the chairperson/administrator of: ' + ', '.join(r['name'] for r in rows) + '. Hand the role to someone else first.')
    return out


def anonymise_user(db, actor_id, user_id, now=None):
    """Wipe the person's details and sign them out everywhere. Raises BusinessError if something must be settled first."""
    now = now or now_utc()
    blockers = deletion_blockers(db, user_id)
    if blockers:
        raise BusinessError(' '.join(blockers))
    with db.tx():
        db.execute("""UPDATE users SET name='Deleted member', email=?, phone=?, password_hash='', is_active=0, claimed=0,
                      claim_code_hash=NULL, reset_hash=NULL, reset_expires=NULL, phone_verified_at=NULL,
                      totp_secret=NULL, totp_enabled=0, totp_recovery=NULL, session_epoch=session_epoch+1 WHERE id=?""",
                   (f'deleted-{user_id}@deleted.invalid', f'deleted-{user_id}', user_id))
        db.execute("UPDATE chama_members SET status='REMOVED', removed_at=? WHERE user_id=? AND status='ACTIVE'", (iso(now), user_id))
        for table in ('notification_preferences', 'sms_outbox', 'email_outbox', 'auth_otps', 'email_otps', 'notifications'):
            db.execute(f'DELETE FROM {table} WHERE user_id=?', (user_id,))
        audit(db, actor_id, 'ACCOUNT_ANONYMISED', 'user', user_id)
