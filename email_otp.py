"""Email OTP delivery. Plain codes are never stored."""
import hashlib, hmac, secrets
from datetime import timedelta
import services as S
import notify as N

OTP_MINUTES=5; RESEND_SECONDS=60; MAX_ATTEMPTS=5; MAX_REQUESTS=5; REQUEST_WINDOW_MINUTES=15
GENERIC='If that email is registered, a verification code has been sent.'

def _hash(app, code): return hmac.new(app.config['SECRET_KEY'].encode(), code.encode(), hashlib.sha256).hexdigest()
def _new_code(): return f'{secrets.randbelow(1_000_000):06d}'

def request_otp(app, db, user_id, email, purpose, ip=None, now=None):
    now=now or S.now_utc(); email=(email or '').strip().lower()
    if not S.valid_email(email): return False,'Enter a valid email address.',None
    recent=db.val("SELECT COUNT(*) FROM email_otps WHERE email=? AND purpose=? AND created_at>=?",(email,purpose,S.iso(now-timedelta(minutes=REQUEST_WINDOW_MINUTES))),0)
    if recent>=MAX_REQUESTS: return False,'Too many verification requests. Please wait 15 minutes and try again.',None
    last=db.one("SELECT created_at FROM email_otps WHERE email=? AND purpose=? ORDER BY id DESC LIMIT 1",(email,purpose))
    if last and (now-S.parse(last['created_at'])).total_seconds()<RESEND_SECONDS: return False,'Please wait 60 seconds before requesting another code.',None
    code=_new_code(); expires=now+timedelta(minutes=OTP_MINUTES)
    with db.tx():
        db.execute("UPDATE email_otps SET status='SUPERSEDED' WHERE status='PENDING' AND purpose=? AND (user_id=? OR email=?)",(purpose,user_id or -1,email))
        oid=db.insert('email_otps',user_id=user_id,email=email,purpose=purpose,code_hash=_hash(app,code),expires_at=S.iso(expires),attempts=0,created_at=S.iso(now),request_ip=(ip or '')[:80],status='PENDING')
    subject='Your ChamaPay verification code'
    body=f'Your ChamaPay verification code is {code}. It expires in {OTP_MINUTES} minutes. Do not share this code with anyone.'
    sent,result=N.send_email_now(email,subject,body)
    if not sent:
        db.execute("UPDATE email_otps SET status='FAILED' WHERE id=?",(oid,)); db.commit()
        return False,'We could not send the verification email. Please check the email service configuration.',None
    return True,'Verification code sent to your email.',oid

def verify_otp(app,db,otp_id,code,purpose,now=None):
    now=now or S.now_utc(); row=db.one('SELECT * FROM email_otps WHERE id=? AND purpose=?',(otp_id,purpose))
    if not row or row['status']!='PENDING' or not row['expires_at'] or S.parse(row['expires_at'])<now:
        if row and row['status']=='PENDING': db.execute("UPDATE email_otps SET status='EXPIRED' WHERE id=?",(otp_id,)); db.commit()
        return None
    attempts=int(row.get('attempts') or 0)
    if attempts>=MAX_ATTEMPTS: db.execute("UPDATE email_otps SET status='LOCKED' WHERE id=?",(otp_id,)); db.commit(); return None
    code=''.join((code or '').split())
    if not code or not hmac.compare_digest(row['code_hash'],_hash(app,code)):
        with db.tx():
            n=attempts+1; db.execute('UPDATE email_otps SET attempts=?,status=? WHERE id=?',(n,'LOCKED' if n>=MAX_ATTEMPTS else 'PENDING',otp_id))
        return None
    with db.tx(): db.execute("UPDATE email_otps SET status='USED',used_at=? WHERE id=?",(S.iso(now),otp_id))
    return row

def mark_verified(db,user_id,now=None):
    with db.tx():
        db.execute('UPDATE users SET email_verified_at=? WHERE id=?',(S.iso(now or S.now_utc()),user_id))
        from db import audit; audit(db,user_id,'EMAIL_VERIFIED','user',user_id)
