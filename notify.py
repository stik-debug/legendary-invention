"""In-app notifications. A notification is one row for one person; it never leaves the app (no SMS or email is sent)."""
from services import iso, now_utc


def notify(db, user_id, chama_id, text, link=None, by=None, now=None):
    """Tell one person something. `by` is whoever caused it: nobody needs an alert about their own action."""
    if by is not None and by == user_id:
        return None
    return db.insert('notifications', user_id=user_id, chama_id=chama_id, text=(text or '')[:240], link=link, created_at=iso(now or now_utc()))


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
