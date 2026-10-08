"""Meetings, attendance and announcements. Officials run them, every member can read them."""
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse

import finance as F
from db import audit
from notify import notify_chama
from services import BusinessError, iso, now_utc

COMMS_ROLES = ('CHAMA_ADMIN', 'SECRETARY')
ATTENDANCE = ('PRESENT', 'ABSENT', 'APOLOGY')


def _text(raw, label, lo, hi):
    v = (raw or '').strip()
    if not lo <= len(v) <= hi:
        raise BusinessError(f'{label} must be {lo} to {hi} characters.' if lo else f'{label} can be up to {hi} characters.')
    return v


def parse_when(raw):
    """Browser datetime-local value ('2026-10-04T15:00') -> '2026-10-04 15:00'. Shown exactly as typed (East Africa time)."""
    try:
        d = datetime.fromisoformat((raw or '').strip().replace('T', ' '))
    except ValueError:
        raise BusinessError('Choose a valid date and time.')
    if not now_utc() - timedelta(days=1100) < d < now_utc() + timedelta(days=1100):
        raise BusinessError('That date is too far away.')
    return d.strftime('%Y-%m-%d %H:%M')


# ---------- meetings ----------
def _online(mode, link):
    """('none' | 'link') -> (provider, room, url). Links must be plain https."""
    mode = (mode or 'none').strip().lower()
    if mode == 'none':
        return None, None, None
    if mode == 'link':
        u = (link or '').strip()
        p = urlparse(u)
        if len(u) > 300 or p.scheme != 'https' or '.' not in (p.hostname or '') or p.username or p.password or any(c.isspace() for c in u):
            raise BusinessError('Paste the full meeting link, starting with https:// (Zoom, Google Meet, Teams...).')
        return 'LINK', None, u
    raise BusinessError('Choose how the meeting will be held.')


def online_url(m):
    return m.get('online_url') if m.get('online_provider') == 'LINK' else None


def join_open(m, now=None):
    """Join button shows 30 minutes before the start until 4 hours after (meeting times are East Africa time, UTC+3)."""
    if not online_url(m) or m['status'] == 'CANCELLED':
        return False
    eat = (now or now_utc()) + timedelta(hours=3)
    start = datetime.strptime(m['held_at'], '%Y-%m-%d %H:%M')
    return start - timedelta(minutes=30) <= eat <= start + timedelta(hours=4)


def join_target(db, chama_id, meeting_id, user, now=None):
    """Records that this member opened the online room (a hint for the attendance sheet) and returns where to send them."""
    now = now or now_utc()
    m = get_meeting(db, chama_id, meeting_id)
    if not join_open(m, now):
        raise BusinessError('The online room is not open yet. It opens 30 minutes before the start.')
    url = online_url(m)
    with db.tx():
        if not db.val('SELECT COUNT(*) FROM meeting_joins WHERE meeting_id=? AND user_id=?', (meeting_id, user['id']), 0):
            db.insert('meeting_joins', meeting_id=meeting_id, chama_id=chama_id, user_id=user['id'], joined_at=iso(now))
    return url


def joined_ids(db, meeting_id):
    return {r['user_id'] for r in db.all('SELECT user_id FROM meeting_joins WHERE meeting_id=?', (meeting_id,))}


def create_meeting(db, chama_id, title, when, venue, agenda, absent_fine_cents, actor, now=None, online='none', link=None):
    now = now or now_utc()
    provider, room, url = _online(online, link)
    title, venue, agenda = _text(title, 'The title', 3, 100), _text(venue, 'The venue', 0, 100), _text(agenda, 'The agenda', 0, 2000)
    when = parse_when(when)
    with db.tx():
        mid = db.insert('meetings', chama_id=chama_id, title=title, venue=venue or None, held_at=when, agenda=agenda or None,
                        absent_fine_cents=int(absent_fine_cents or 0), created_by=actor, created_at=iso(now),
                        online_provider=provider, online_room=room, online_url=url)
        audit(db, actor, 'MEETING_CREATED', 'meeting', mid, chama_id, {'title': title, 'when': when})
        notify_chama(db, chama_id, f'New {"online " if provider else ""}meeting: {title}, {when}.', f'/chamas/{chama_id}/meetings/{mid}', actor, now)
    return mid


def get_meeting(db, chama_id, meeting_id):
    m = db.one('SELECT * FROM meetings WHERE id=? AND chama_id=?', (meeting_id, chama_id))
    if not m:
        raise BusinessError('Meeting not found.')
    return m


def save_minutes(db, chama_id, meeting_id, minutes, actor, now=None):
    minutes = _text(minutes, 'The minutes', 0, 8000)
    with db.tx():
        m = get_meeting(db, chama_id, meeting_id)
        if m['status'] == 'CANCELLED':
            raise BusinessError('This meeting was cancelled.')
        db.execute('UPDATE meetings SET minutes=? WHERE id=?', (minutes or None, meeting_id))
        audit(db, actor, 'MINUTES_SAVED', 'meeting', meeting_id, chama_id)


def cancel_meeting(db, chama_id, meeting_id, actor, now=None):
    with db.tx():
        m = get_meeting(db, chama_id, meeting_id)
        if m['status'] != 'SCHEDULED':
            raise BusinessError('Only a meeting that has not been held can be cancelled.')
        db.execute("UPDATE meetings SET status='CANCELLED' WHERE id=?", (meeting_id,))
        audit(db, actor, 'MEETING_CANCELLED', 'meeting', meeting_id, chama_id)
        notify_chama(db, chama_id, f"Meeting cancelled: {m['title']}.", f'/chamas/{chama_id}/meetings/{meeting_id}', actor, now)


def mark_attendance(db, chama_id, meeting_id, statuses, actor, now=None):
    """statuses: {user_id: 'PRESENT'|'ABSENT'|'APOLOGY'}. Marks the meeting as held. Members marked ABSENT get the meeting's
    absence fine once; changing someone to PRESENT later does not remove the fine (an administrator can waive it).
    Returns (people marked, fines created)."""
    now = now or now_utc()
    fines = marked = 0
    with db.tx():
        m = get_meeting(db, chama_id, meeting_id)
        if m['status'] == 'CANCELLED':
            raise BusinessError('This meeting was cancelled.')
        active = {r['user_id'] for r in db.all("SELECT user_id FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,))}
        for uid, st in statuses.items():
            if uid not in active or st not in ATTENDANCE:
                raise BusinessError('Attendance can only be marked for current members as present, absent or with apology.')
        for uid, st in statuses.items():
            row = db.one('SELECT * FROM attendance WHERE meeting_id=? AND user_id=?', (meeting_id, uid))
            fine_id = row['fine_id'] if row else None
            if st == 'ABSENT' and m['absent_fine_cents'] > 0 and not fine_id:
                fine_id = F.create_fine(db, chama_id, uid, m['absent_fine_cents'], f"Absent from meeting: {m['title']}", None, actor, now)
                fines += 1
            if row:
                db.execute('UPDATE attendance SET status=?, fine_id=?, marked_by=?, marked_at=? WHERE id=?', (st, fine_id, actor, iso(now), row['id']))
            else:
                db.insert('attendance', meeting_id=meeting_id, chama_id=chama_id, user_id=uid, status=st, fine_id=fine_id, marked_by=actor, marked_at=iso(now))
            marked += 1
        db.execute("UPDATE meetings SET status='HELD' WHERE id=?", (meeting_id,))
        audit(db, actor, 'ATTENDANCE_MARKED', 'meeting', meeting_id, chama_id, {'marked': marked, 'fines': fines})
    return marked, fines


def attendance_sheet(db, chama_id, meeting_id):
    """Every current member with their mark (None if not marked yet)."""
    return db.all("""SELECT u.id, u.name, a.status FROM chama_members m JOIN users u ON u.id=m.user_id
        LEFT JOIN attendance a ON a.user_id=u.id AND a.meeting_id=? WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name""", (meeting_id, chama_id))


def my_attendance(db, chama_id, user_id):
    return {r['meeting_id']: r['status'] for r in db.all('SELECT meeting_id, status FROM attendance WHERE chama_id=? AND user_id=?', (chama_id, user_id))}


def attendance_rates(db, chama_id):
    """Per member: meetings marked, present count, percentage (apologies count as excused, not present)."""
    return db.all("""SELECT u.id, u.name, COUNT(a.id) marked, COALESCE(SUM(CASE WHEN a.status='PRESENT' THEN 1 ELSE 0 END),0) present,
        COALESCE(SUM(CASE WHEN a.status='APOLOGY' THEN 1 ELSE 0 END),0) apologies
        FROM chama_members m JOIN users u ON u.id=m.user_id LEFT JOIN attendance a ON a.user_id=u.id AND a.chama_id=m.chama_id
        WHERE m.chama_id=? AND m.status='ACTIVE' GROUP BY u.id, u.name ORDER BY u.name""", (chama_id,))


# ---------- announcements ----------
def post_notice(db, chama_id, title, body, pinned, actor, now=None):
    now = now or now_utc()
    title, body = _text(title, 'The title', 3, 100), _text(body, 'The message', 3, 3000)
    with db.tx():
        nid = db.insert('announcements', chama_id=chama_id, title=title, body=body, pinned=int(bool(pinned)), created_by=actor, created_at=iso(now))
        audit(db, actor, 'ANNOUNCEMENT_POSTED', 'announcement', nid, chama_id)
        notify_chama(db, chama_id, f'Notice: {title}', f'/chamas/{chama_id}/notices', actor, now)
    return nid


def _notice(db, chama_id, notice_id):
    n = db.one('SELECT * FROM announcements WHERE id=? AND chama_id=? AND deleted_at IS NULL', (notice_id, chama_id))
    if not n:
        raise BusinessError('Notice not found.')
    return n


def delete_notice(db, chama_id, notice_id, actor, now=None):
    with db.tx():
        _notice(db, chama_id, notice_id)
        db.execute('UPDATE announcements SET deleted_at=? WHERE id=?', (iso(now or now_utc()), notice_id))
        audit(db, actor, 'ANNOUNCEMENT_REMOVED', 'announcement', notice_id, chama_id)


def toggle_pin(db, chama_id, notice_id, actor):
    with db.tx():
        n = _notice(db, chama_id, notice_id)
        db.execute('UPDATE announcements SET pinned=? WHERE id=?', (0 if n['pinned'] else 1, notice_id))
        audit(db, actor, 'ANNOUNCEMENT_PINNED' if not n['pinned'] else 'ANNOUNCEMENT_UNPINNED', 'announcement', notice_id, chama_id)


def list_notices(db, chama_id, limit=25, offset=0):
    return db.all("""SELECT a.*, u.name author FROM announcements a LEFT JOIN users u ON u.id=a.created_by
        WHERE a.chama_id=? AND a.deleted_at IS NULL ORDER BY a.pinned DESC, a.id DESC LIMIT ? OFFSET ?""", (chama_id, limit, offset))
