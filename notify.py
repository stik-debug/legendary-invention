"""In-app notifications plus optional SMS delivery through Africa's Talking.
SMS is queued, never sent inline with the user request, so notifications cannot slow or break Chama actions."""
from services import iso, now_utc, normalize_phone
import os
import smtplib
import json
import urllib.request
from email.message import EmailMessage
import threading
import logging
from push_config import get_vapid_keys


def _deliver_web_push(subscriptions, payload, private_key, subject):
    """Send Web Push outside the request path; optional credentials keep local/test installs working."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logging.getLogger(__name__).error('Web push package pywebpush is missing; install requirements.txt.')
        return
    for subscription in subscriptions:
        try:
            webpush(subscription_info=subscription, data=json.dumps(payload), vapid_private_key=private_key,
                    vapid_claims={"sub": subject})
        except Exception as exc:
            # Log failures so hosting logs explain delivery problems; never break the business action.
            logging.getLogger(__name__).warning('Web push delivery failed (%s): %s', type(exc).__name__, str(exc)[:240])
            continue


def send_push_test(db, user_id):
    """Deliver a test push synchronously and return honest per-device results."""
    public_key, private_key = get_vapid_keys(db)
    if not public_key or not private_key:
        return {"ok": False, "error": "Push signing keys are not configured."}
    try:
        from pywebpush import webpush
    except ImportError:
        return {"ok": False, "error": "The server is missing pywebpush. Install requirements.txt and restart the service."}
    rows = db.all('SELECT id,endpoint,subscription_json FROM push_subscriptions WHERE user_id=?', (user_id,))
    if not rows:
        return {"ok": False, "error": "This phone is not registered. Enable device notifications on this phone first."}
    payload = {"title": "ChamaPay test", "body": "Your phone can receive ChamaPay notifications.",
               "url": "/notification-settings", "notification_id": "test-" + str(int(now_utc().timestamp()))}
    sent, failures = 0, []
    for row in rows:
        try:
            webpush(subscription_info=json.loads(row['subscription_json']), data=json.dumps(payload),
                    vapid_private_key=private_key,
                    vapid_claims={"sub": os.environ.get('VAPID_CLAIMS_SUBJECT', 'mailto:support@chamapay.app')})
            sent += 1
        except Exception as exc:
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
            failures.append((row['id'], status_code, type(exc).__name__))
            logging.getLogger(__name__).warning('Push test failed for subscription %s (%s): %s', row['id'], status_code, str(exc)[:240])
            # Push providers mark expired/unsubscribed endpoints with 404 or 410.
            if status_code in (404, 410):
                try:
                    db.execute('DELETE FROM push_subscriptions WHERE id=?', (row['id'],))
                except Exception:
                    pass
    try:
        db.commit()
    except Exception:
        pass
    if sent:
        return {"ok": True, "message": f"Push service accepted the test for {sent} device(s). Check the phone's notification panel." , "sent": sent, "failed": len(failures)}
    detail = failures[0][2] if failures else "UnknownError"
    code = failures[0][1] if failures else None
    hint = f" (HTTP {code})" if code else ""
    return {"ok": False, "error": f"The push service rejected delivery ({detail}{hint}). Re-enable notifications on this phone and check the server logs.", "failed": len(failures)}


def _queue_web_push(db, user_id, text, link, notification_id):
    public_key, private_key = get_vapid_keys(db)
    if not public_key or not private_key:
        logging.getLogger(__name__).warning('Push delivery skipped: VAPID keys are not configured.')
        return
    try:
        pref = db.one('SELECT push FROM notification_preferences WHERE user_id=?', (user_id,))
        if pref is not None and not int(pref.get('push', 1)):
            return
        rows = db.all('SELECT subscription_json FROM push_subscriptions WHERE user_id=?', (user_id,))
        subscriptions = [json.loads(row['subscription_json']) for row in rows]
        if not subscriptions:
            return
        payload = {'title': 'ChamaPay', 'body': (text or 'You have a new update.')[:180],
                   'url': link if isinstance(link, str) and link.startswith('/') and not link.startswith('//') else '/notifications',
                   'notification_id': notification_id}
        threading.Thread(target=_deliver_web_push, args=(subscriptions, payload, private_key,
            os.environ.get('VAPID_CLAIMS_SUBJECT', 'mailto:support@chamapay.app')), daemon=True).start()
    except Exception:
        return


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
    _queue_web_push(db, user_id, text, link, nid)
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



def send_email_now(to_email, subject, body):
    """Send a transactional email using standard SMTP. No provider SDK is required."""
    # Prefer a transactional provider when configured; SMTP remains a fallback.
    resend_key = os.environ.get('RESEND_API_KEY')
    brevo_key = os.environ.get('BREVO_API_KEY')
    sender = os.environ.get('MAIL_FROM') or os.environ.get('SMTP_USERNAME') or 'ChamaPay <no-reply@chamapay.app>'
    if resend_key or brevo_key:
        try:
            if resend_key:
                endpoint = 'https://api.resend.com/emails'
                headers = {'Authorization': 'Bearer ' + resend_key, 'Content-Type': 'application/json'}
                payload = {'from': sender, 'to': [to_email], 'subject': subject, 'text': body}
                provider = 'Resend'
            else:
                endpoint = 'https://api.brevo.com/v3/smtp/email'
                headers = {'api-key': brevo_key, 'Content-Type': 'application/json', 'accept': 'application/json'}
                from_name, from_email = ('ChamaPay', sender)
                if '<' in sender and '>' in sender:
                    from_name, from_email = sender.split('<', 1)[0].strip(), sender.split('<', 1)[1].split('>', 1)[0].strip()
                payload = {'sender': {'name': from_name, 'email': from_email}, 'to': [{'email': to_email}], 'subject': subject, 'textContent': body}
                provider = 'Brevo'
            req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=20) as response:
                if 200 <= response.status < 300:
                    return True, provider + ' accepted message'
                return False, provider + ' returned HTTP ' + str(response.status)
        except Exception as exc:
            return False, type(exc).__name__ + ': ' + str(exc)[:350]
    host=os.environ.get('SMTP_HOST')
    if not host:
        return False, 'Email service is not configured. Set RESEND_API_KEY, BREVO_API_KEY, or SMTP_HOST.'
    try:
        port=int(os.environ.get('SMTP_PORT','587'))
        msg=EmailMessage()
        msg['From']=os.environ.get('MAIL_FROM') or os.environ.get('SMTP_USERNAME') or 'no-reply@chamapay.app'
        msg['To']=to_email
        msg['Subject']=subject
        msg.set_content(body)
        if os.environ.get('SMTP_USE_TLS','1') != '0':
            with smtplib.SMTP(host,port,timeout=20) as smtp:
                smtp.starttls()
                if os.environ.get('SMTP_USERNAME'):
                    smtp.login(os.environ.get('SMTP_USERNAME'),os.environ.get('SMTP_PASSWORD',''))
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host,port,timeout=20) as smtp:
                if os.environ.get('SMTP_USERNAME'):
                    smtp.login(os.environ.get('SMTP_USERNAME'),os.environ.get('SMTP_PASSWORD',''))
                smtp.send_message(msg)
        return True,'sent'
    except Exception as exc:
        return False,f'{type(exc).__name__}: {str(exc)[:450]}'

def queue_email(db,user_id,to_email,subject,body,now=None,dedupe_key=None):
    """Queue a normal email for scheduled/background delivery."""
    try:
        kwargs=dict(user_id=user_id,email=to_email,subject=(subject or '')[:180],body=(body or '')[:5000],created_at=iso(now or now_utc()),status='PENDING',attempts=0)
        if dedupe_key: kwargs['dedupe_key']=dedupe_key[:180]
        return db.insert('email_outbox',**kwargs)
    except Exception:
        return None

def process_email_outbox(database_url,limit=10):
    if not (os.environ.get('SMTP_HOST') or os.environ.get('RESEND_API_KEY') or os.environ.get('BREVO_API_KEY')): return 0
    from db import DB
    db=DB(database_url); sent=0
    try:
        for _ in range(max(1,min(int(limit),25))):
            row=db.one("SELECT * FROM email_outbox WHERE status='PENDING' AND attempts<5 ORDER BY id LIMIT 1")
            if not row: break
            claimed=db.execute("UPDATE email_outbox SET status='PROCESSING',attempts=attempts+1 WHERE id=? AND status='PENDING'",(row['id'],)).rowcount
            if not claimed: continue
            db.commit()
            ok,detail=send_email_now(row['email'],row['subject'],row['body'])
            if ok:
                db.execute("UPDATE email_outbox SET status='SENT',sent_at=?,last_error=NULL WHERE id=?",(iso(now_utc()),row['id'])); db.commit(); sent+=1
            else:
                db.execute("UPDATE email_outbox SET status=?,last_error=? WHERE id=?",('FAILED' if row['attempts']>=4 else 'PENDING',str(detail)[:500],row['id'])); db.commit()
    finally: db.close()
    return sent



def send_whatsapp_now(phone, message):
    """Send a WhatsApp template/message via Meta Cloud API when configured.

    Production messaging rules require an approved template for business-initiated
    messages outside the customer-service window; callers should use template payloads
    for reminders and obtain recipient opt-in first.
    """
    token = os.environ.get('WHATSAPP_ACCESS_TOKEN')
    phone_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
    if not token or not phone_id:
        return False, 'WhatsApp Cloud API is not configured.'
    try:
        phone = normalize_phone(phone)
        if not phone:
            return False, 'Invalid Kenyan phone number.'
        payload = {'messaging_product': 'whatsapp', 'to': phone, 'type': 'text',
                   'text': {'preview_url': False, 'body': (message or '')[:4000]}}
        version = os.environ.get('WHATSAPP_API_VERSION', 'v21.0')
        req = urllib.request.Request('https://graph.facebook.com/' + version + '/' + phone_id + '/messages',
            data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=20) as response:
            if 200 <= response.status < 300:
                return True, 'WhatsApp Cloud API accepted message'
            return False, 'WhatsApp Cloud API HTTP ' + str(response.status)
    except Exception as exc:
        return False, type(exc).__name__ + ': ' + str(exc)[:350]


def queue_whatsapp(db, user_id, phone, message, now=None, dedupe_key=None):
    """Queue WhatsApp only when the app has the API configured; respect opt-in at call site."""
    if not os.environ.get('WHATSAPP_ACCESS_TOKEN') or not os.environ.get('WHATSAPP_PHONE_NUMBER_ID'):
        return None
    try:
        kwargs = dict(user_id=user_id, phone=phone, message=(message or '')[:4000],
                      created_at=iso(now or now_utc()), status='PENDING', attempts=0)
        if dedupe_key:
            kwargs['dedupe_key'] = dedupe_key[:180]
        return db.insert('whatsapp_outbox', **kwargs)
    except Exception:
        return None


def process_whatsapp_outbox(database_url, limit=10):
    if not os.environ.get('WHATSAPP_ACCESS_TOKEN') or not os.environ.get('WHATSAPP_PHONE_NUMBER_ID'):
        return 0
    from db import DB
    db = DB(database_url); sent = 0
    try:
        for _ in range(max(1, min(int(limit), 25))):
            row = db.one("SELECT * FROM whatsapp_outbox WHERE status='PENDING' AND attempts<5 ORDER BY id LIMIT 1")
            if not row: break
            claimed = db.execute("UPDATE whatsapp_outbox SET status='PROCESSING',attempts=attempts+1 WHERE id=? AND status='PENDING'", (row['id'],)).rowcount
            if not claimed: continue
            db.commit()
            ok, detail = send_whatsapp_now(row['phone'], row['message'])
            status = 'SENT' if ok else ('FAILED' if row['attempts'] >= 4 else 'PENDING')
            db.execute("UPDATE whatsapp_outbox SET status=?,last_error=?,sent_at=? WHERE id=?",
                (status, None if ok else str(detail)[:500], iso(now_utc()) if ok else None, row['id']))
            db.commit()
            if ok: sent += 1
    finally:
        db.close()
    return sent


def _sms_response_status(response):
    """Return (accepted, detail) from Africa's Talking's per-recipient response.

    HTTP/API success alone is insufficient: the provider can accept the API request
    while rejecting an individual recipient. Status 101 is Africa's Talking's
    documented successful recipient status.
    """
    if not isinstance(response, dict):
        return False, "Africa's Talking returned an unexpected response."
    data = response.get('SMSMessageData') or {}
    recipients = data.get('Recipients') or []
    if not recipients:
        detail = data.get('Message') or response.get('message') or 'No recipient status was returned.'
        return False, str(detail)[:500]
    failures = []
    for recipient in recipients:
        code = str(recipient.get('statusCode', '')).strip()
        status = str(recipient.get('status', '')).strip().lower()
        if code == '101' or status in ('success', 'sent'):
            continue
        failures.append(f"{recipient.get('number', 'recipient')}: {recipient.get('status', 'Rejected')} (code {code or 'unknown'})")
    if failures:
        return False, '; '.join(failures)[:500]
    return True, data.get('Message') or "Accepted by Africa's Talking."


def send_sms_now(phone, message):
    """Send a security SMS immediately and validate provider recipient status.
    Never report success merely because the SDK call returned without an exception.
    """
    try:
        if not os.environ.get('AT_USERNAME') or not os.environ.get('AT_API_KEY'):
            return False, 'SMS service is not configured. Set AT_USERNAME and AT_API_KEY in Render.'
        normalized = normalize_phone(phone)
        if not normalized:
            return False, 'Invalid Kenyan phone number. Use a valid +254 number.'
        import africastalking
        africastalking.initialize(os.environ['AT_USERNAME'], os.environ['AT_API_KEY'])
        sms = africastalking.SMS
        sender = os.environ.get('AT_SENDER_ID') or None
        if sender:
            response = sms.send((message or '')[:1000], ['+' + normalized], sender_id=sender, enqueue=False)
        else:
            response = sms.send((message or '')[:1000], ['+' + normalized], enqueue=False)
        return _sms_response_status(response)
    except Exception as exc:
        return False, f'{type(exc).__name__}: {str(exc)[:450]}'


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
                    accepted, detail = _sms_response_status(response)
                    if not accepted:
                        db.execute("UPDATE sms_outbox SET status=?, last_error=? WHERE id=?", ('FAILED' if row['attempts'] >= 4 else 'PENDING', str(detail)[:500], row['id']))
                        db.commit()
                        continue
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
