"""In-app notifications plus optional SMS delivery through Africa's Talking.
SMS is queued, never sent inline with the user request, so notifications cannot slow or break Chama actions."""
from services import iso, now_utc, normalize_phone
import os


def _queue_sms(db, user_id, text, now=None, dedupe_key=None):
    """Queue SMS after an in-app notification. Never raises into the business operation."""
    try:
        if not os.environ.get('AT_USERNAME') or not os.environ.get('AT_API_KEY'):
            return None
        pref = db.one('SELECT sms FROM notification_preferences WHERE user_id=?', (user_id,))
        # Existing users without a preferences row get SMS by default when AT is configured.
        if pref is not None and not int(pref.get('sms', 0)):
            return None
        phone = db.val('SELECT phone FROM users WHERE id=? AND is_active=1', (user_id,))
        phone = normalize_phone(phone)
        if not phone:
            return None
        kwargs = dict(user_id=user_id, phone='+' + phone, message=(text or '')[:1000],
                      created_at=iso(now or now_utc()), status='PENDING', attempts=0)
        if dedupe_key:
            kwargs['dedupe_key'] = dedupe_key[:180]
        return db.insert('sms_outbox', **kwargs)
    except Exception:
        return None


def notify(db, user_id, chama_id, text, link=None, by=None, now=None):
    """Tell one person something and queue an SMS when enabled/configured. The SMS is never sent inline."""
    if by is not None and by == user_id:
        return None
    nid = db.insert('notifications', user_id=user_id, chama_id=chama_id, text=(text or '')[:240], link=link, created_at=iso(now or now_utc()))
    _queue_sms(db, user_id, text, now, f'notification:{nid}')
    return nid


def _ids(db, chama_id, roles=None):
    sql, args = "SELECT user_id FROM chama_members WHERE chama_id=? AND status='ACTIVE'", [chama_id]
    if roles:
        sql += ' AND role IN (' + ','.join('?' for _ in roles) + ')'
        args += list(roles)
    return [r['user_id'] for r in db.all(sql, args)]


def notify_roles(db, chama_id, roles, text, link=None, by=None, now=None):
    for uid in _ids(db, chama_id, roles):
        notify(db, uid, chama_id, text, link, by, now)


def notify_chama(db, chama_id, text, link=None, by=None, now=None):
    for uid in _ids(db, chama_id):
        notify(db, uid, chama_id, text, link, by, now)


def unread_count(db, user_id):
    return int(db.val('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL', (user_id,), 0))


def recent(db, user_id, limit=60):
    return db.all("""SELECT n.*, c.name chama FROM notifications n LEFT JOIN chamas c ON c.id=n.chama_id
        WHERE n.user_id=? ORDER BY n.id DESC LIMIT ?""", (user_id, limit))


def mark_all_read(db, user_id, now=None):
    with db.tx():
        db.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL', (iso(now or now_utc()), user_id))


def open_one(db, user_id, notification_id, now=None):
    """Mark one as read and return its link (only the owner of the notification can open it)."""
    n = db.one('SELECT * FROM notifications WHERE id=? AND user_id=?', (notification_id, user_id))
    if not n:
        return None
    with db.tx():
        db.execute('UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=?', (iso(now or now_utc()), n['id']))
    return n['link']


def queue_sms(db, user_id, text, now=None, dedupe_key=None):
    """Public helper for SMS-only events such as account welcome messages."""
    return _queue_sms(db, user_id, text, now, dedupe_key)


def send_sms_now(phone, message):
    """Send a security SMS immediately and return (ok, error).
    Used only for security-critical OTPs so the user is not told a code was sent
    before Africa's Talking has accepted the request.
    """
    try:
        if not os.environ.get('AT_USERNAME') or not os.environ.get('AT_API_KEY'):
            return False, 'SMS service is not configured. Set AT_USERNAME and AT_API_KEY in Render.'
        import africastalking
        africastalking.initialize(os.environ['AT_USERNAME'], os.environ['AT_API_KEY'])
        sms = africastalking.SMS
        sender = os.environ.get('AT_SENDER_ID') or None
        if sender:
            response = sms.send((message or '')[:1000], ['+' + normalize_phone(phone)], sender_id=sender, enqueue=False)
        else:
            response = sms.send((message or '')[:1000], ['+' + normalize_phone(phone)], enqueue=False)
        return True, response
    except Exception as exc:
        return False, str(exc)[:500]


def process_sms_outbox(database_url, limit=8):
    """Deliver a small batch outside the request path. Safe to call from a background worker/thread."""
    if not os.environ.get('AT_USERNAME') or not os.environ.get('AT_API_KEY'):
        return 0
    try:
        import africastalking
        from db import DB
        db = DB(database_url)
        sent = 0
        try:
            africastalking.initialize(os.environ['AT_USERNAME'], os.environ['AT_API_KEY'])
            sms = africastalking.SMS
            for _ in range(max(1, min(int(limit), 25))):
                row = db.one("SELECT * FROM sms_outbox WHERE status='PENDING' AND attempts < 5 ORDER BY id LIMIT 1")
                if not row:
                    break
                # Claim the row atomically so multiple Gunicorn workers cannot normally send it twice.
                claimed = db.execute("UPDATE sms_outbox SET status='PROCESSING', attempts=attempts+1 WHERE id=? AND status='PENDING'", (row['id'],)).rowcount
                if not claimed:
                    continue
                db.commit()
                try:
                    sender = os.environ.get('AT_SENDER_ID') or None
                    if sender:
                        response = sms.send(row['message'], [row['phone']], sender_id=sender, enqueue=True)
                    else:
                        response = sms.send(row['message'], [row['phone']], enqueue=True)
                    db.execute("UPDATE sms_outbox SET status='SENT', sent_at=?, last_error=NULL WHERE id=?", (iso(now_utc()), row['id']))
                    db.commit()
                    sent += 1
                except Exception as exc:
                    db.execute("UPDATE sms_outbox SET status=?, last_error=? WHERE id=?", ('FAILED' if row['attempts'] >= 4 else 'PENDING', str(exc)[:500], row['id']))
                    db.commit()
        finally:
            db.close()
        return sent
    except Exception:
        return 0
