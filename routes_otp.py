"""User-facing email OTP flows. SMS is not required for account verification."""
from datetime import timedelta
from flask import flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import email_otp as OTP
import recovery as RC
import services as S
from db import audit

def safe_next_local(value):
    value=(value or '').strip()
    return value if value.startswith('/') and not value.startswith('//') else ''

def register(app, db, login_required):
    def email_on(): return bool(app.config.get('EMAIL_OTP_ENABLED'))
    def too_many(key):
        db().execute('DELETE FROM login_attempts WHERE at<?',(S.iso(S.now_utc()-timedelta(minutes=10)),)); db().commit()
        return db().val('SELECT COUNT(*) FROM login_attempts WHERE key=?',(key,),0)>=6
    def note_fail(key):
        db().execute('INSERT INTO login_attempts(key,at) VALUES(?,?)',(key,S.iso(S.now_utc()))); db().commit()

    @app.route('/otp/resend',methods=['POST'])
    def otp_resend():
        flow=session.get('otp_flow') or {}
        if not flow.get('purpose') or not flow.get('user_id') or not flow.get('email'):
            flash('There is no verification in progress.','warning'); return redirect(url_for('login'))
        ok,msg,oid=OTP.request_otp(app,db(),flow['user_id'],flow['email'],flow['purpose'],request.remote_addr)
        if ok:
            flow['otp_id']=oid; session['otp_flow']=flow; flash('A new verification code has been sent to your email.','success')
        else: flash(msg,'warning')
        return redirect(url_for('otp_verify'))

    @app.route('/otp/verify',methods=['GET','POST'])
    def otp_verify():
        flow=session.get('otp_flow') or {}
        if not flow.get('purpose') or not flow.get('otp_id'): return redirect(url_for('login'))
        if request.method=='POST':
            row=OTP.verify_otp(app,db(),flow['otp_id'],request.form.get('code'),flow['purpose'])
            if not row:
                flash('That code is not correct, expired, or has been used. You have up to 5 attempts per code.','danger')
                return render_template('otp_verify.html',flow=flow)
            uid=int(row['user_id']); u=db().one('SELECT * FROM users WHERE id=? AND is_active=1',(uid,)); purpose=flow['purpose']
            if not u:
                session.pop('otp_flow',None); flash('That verification is no longer valid.','danger'); return redirect(url_for('login'))
            if purpose in ('signup','login_email_verify'):
                OTP.mark_verified(db,uid)
                with db().tx(): audit(db,uid,'USER_REGISTERED' if purpose=='signup' else 'LOGIN_EMAIL_VERIFIED','user',uid)
                session.clear(); session['uid']=uid; session['ep']=u['session_epoch']; session.permanent=True
                return redirect(safe_next_local(flow.get('next')) or url_for('dashboard'))
            if purpose=='forgot_password':
                session['otp_flow']={'purpose':'forgot_password_verified','user_id':uid,'email':u['email']}; return redirect(url_for('forgot'))
            if purpose=='phone_change_current':
                session['otp_flow']={'purpose':'phone_change_new','user_id':uid,'email':u['email'],'current_verified':True}; return redirect(url_for('phone_change'))
            if purpose=='phone_change_new':
                new_phone=S.normalize_phone(flow.get('new_phone'))
                if not new_phone: session.pop('otp_flow',None); flash('The new phone number is no longer valid.','danger'); return redirect(url_for('phone_change'))
                if db().val('SELECT COUNT(*) FROM users WHERE phone=? AND id!=?',(new_phone,uid),0):
                    session.pop('otp_flow',None); flash('That phone number is already linked to another account.','danger'); return redirect(url_for('phone_change'))
                with db().tx():
                    db().execute('UPDATE users SET phone=?,phone_verified_at=? WHERE id=?',(new_phone,S.iso(S.now_utc()),uid)); audit(db,uid,'PHONE_CHANGED','user',uid,None,{'email_otp':True})
                session.pop('otp_flow',None); flash('Your phone number has been changed and verified.','success'); return redirect(url_for('security_privacy'))
            if purpose=='password_change':
                session['otp_flow']={'purpose':'password_change_verified','user_id':uid}; return redirect(url_for('password_change'))
            flash('Verification complete.','success'); session.pop('otp_flow',None); return redirect(url_for('security_privacy'))
        return render_template('otp_verify.html',flow=flow)

    @app.route('/security/phone',methods=['GET','POST'])
    @login_required
    def phone_change():
        if not email_on():
            key=f'phonechg|{g.user["id"]}'
            if request.method=='POST':
                new=S.normalize_phone(request.form.get('new_phone'))
                if too_many(key): flash('Too many attempts. Wait 10 minutes and try again.','danger')
                elif not g.user['password_hash'] or not check_password_hash(g.user['password_hash'],request.form.get('current_password','')): note_fail(key); flash('Your current password is not correct.','danger')
                elif not new: flash('Enter a valid Kenyan phone number.','danger')
                elif db().val('SELECT COUNT(*) FROM users WHERE phone=? AND id!=?',(new,g.user['id']),0): flash('That phone number is already linked to another account.','danger')
                else:
                    with db().tx(): db().execute('UPDATE users SET phone=?,phone_verified_at=NULL WHERE id=?',(new,g.user['id'])); audit(db,g.user['id'],'PHONE_CHANGED','user',g.user['id'])
                    flash('Your phone number has been changed.','success'); return redirect(url_for('security_privacy'))
            return render_template('phone_change.html',stage='simple',flow={})
        flow=session.get('otp_flow') or {}
        if request.method=='POST':
            if flow.get('purpose')=='phone_change_new' and flow.get('current_verified'):
                new=S.normalize_phone(request.form.get('new_phone'))
                if not new: flash('Enter a valid Kenyan phone number.','danger'); return render_template('phone_change.html',stage='new',flow=flow)
                if db().val('SELECT COUNT(*) FROM users WHERE phone=? AND id!=?',(new,g.user['id']),0): flash('That phone number is already linked to another account.','danger'); return render_template('phone_change.html',stage='new',flow=flow)
                ok,msg,oid=OTP.request_otp(app,db(),g.user['id'],g.user['email'],'phone_change_new',request.remote_addr)
                if ok: session['otp_flow']={'purpose':'phone_change_new','user_id':g.user['id'],'email':g.user['email'],'new_phone':new,'otp_id':oid}; return redirect(url_for('otp_verify'))
                flash(msg,'warning')
            else:
                ok,msg,oid=OTP.request_otp(app,db(),g.user['id'],g.user['email'],'phone_change_current',request.remote_addr)
                if ok: session['otp_flow']={'purpose':'phone_change_current','user_id':g.user['id'],'email':g.user['email'],'otp_id':oid}; flash('We sent a verification code to your email.','success'); return redirect(url_for('otp_verify'))
                flash(msg,'warning')
        stage='new' if flow.get('purpose')=='phone_change_new' and flow.get('current_verified') else 'current'
        return render_template('phone_change.html',stage=stage,flow=flow)

    @app.route('/security/password',methods=['GET','POST'])
    @login_required
    def password_change():
        if not email_on():
            key=f'pwchg|{g.user["id"]}'
            if request.method=='POST':
                password=request.form.get('password') or ''; confirm=request.form.get('confirm') or ''
                if too_many(key): flash('Too many attempts. Wait 10 minutes and try again.','danger')
                elif not g.user['password_hash'] or not check_password_hash(g.user['password_hash'],request.form.get('current_password','')): note_fail(key); flash('Your current password is not correct.','danger')
                elif len(password)<8: flash('Password must be at least 8 characters.','danger')
                elif password!=confirm: flash('Passwords do not match.','danger')
                else:
                    with db().tx(): db().execute('UPDATE users SET password_hash=?,session_epoch=session_epoch+1 WHERE id=?',(generate_password_hash(password),g.user['id'])); audit(db,g.user['id'],'PASSWORD_CHANGED','user',g.user['id'],None,{'email_otp':False})
                    session.clear(); flash('Your password was changed. Please log in again.','success'); return redirect(url_for('login'))
            return render_template('password_change.html',verified=False,simple=True)
        flow=session.get('otp_flow') or {}
        if request.method=='POST':
            if flow.get('purpose')=='password_change_verified' and flow.get('user_id')==g.user['id']:
                password=request.form.get('password') or ''; confirm=request.form.get('confirm') or ''
                if len(password)<8: flash('Password must be at least 8 characters.','danger')
                elif password!=confirm: flash('Passwords do not match.','danger')
                else:
                    with db().tx(): db().execute('UPDATE users SET password_hash=?,session_epoch=session_epoch+1 WHERE id=?',(generate_password_hash(password),g.user['id'])); audit(db,g.user['id'],'PASSWORD_CHANGED','user',g.user['id'],None,{'email_otp':True})
                    session.clear(); flash('Your password was changed. Please log in again.','success'); return redirect(url_for('login'))
            else:
                ok,msg,oid=OTP.request_otp(app,db(),g.user['id'],g.user['email'],'password_change',request.remote_addr)
                if ok: session['otp_flow']={'purpose':'password_change','user_id':g.user['id'],'email':g.user['email'],'otp_id':oid}; flash('We sent a verification code to your email.','success'); return redirect(url_for('otp_verify'))
                flash(msg,'warning')
        return render_template('password_change.html',verified=(flow.get('purpose')=='password_change_verified'))

    @app.route('/forgot',methods=['GET','POST'])
    def forgot():
        if not email_on():
            if request.method=='POST':
                f=request.form; key=f'forgot|{S.normalize_phone(f.get("phone")) or "-"}|{request.remote_addr}'
                if too_many(key): return render_template('forgot.html',stage='code'),429
                if (f.get('password') or '')!=(f.get('confirm') or ''): flash('Passwords do not match.','danger')
                else:
                    try: RC.reset_password(db(),f.get('phone'),f.get('code'),f.get('password'),generate_password_hash,check_password_hash); session.clear(); flash('Your password was changed. You can now log in.','success'); return redirect(url_for('login'))
                    except S.BusinessError as e: note_fail(key); flash(str(e),'danger')
            return render_template('forgot.html',stage='code')
        flow=session.get('otp_flow') or {}
        if request.method=='POST':
            action=request.form.get('action') or 'request'
            if action=='verify_password' and flow.get('purpose')=='forgot_password_verified':
                password=request.form.get('password') or ''; confirm=request.form.get('confirm') or ''
                if len(password)<8: flash('Password must be at least 8 characters.','danger')
                elif password!=confirm: flash('Passwords do not match.','danger')
                else:
                    uid=flow['user_id']
                    with db().tx(): db().execute('UPDATE users SET password_hash=?,reset_hash=NULL,reset_expires=NULL,reset_fails=0,session_epoch=session_epoch+1 WHERE id=?',(generate_password_hash(password),uid)); audit(db,uid,'PASSWORD_RESET_DONE','user',uid,None,{'email_otp':True})
                    session.clear(); flash('Your password was reset. You can now log in.','success'); return redirect(url_for('login'))
            else:
                email=(request.form.get('email') or '').strip().lower(); u=db().one('SELECT id,email FROM users WHERE email=? AND is_active=1 AND claimed=1',(email or '-',))
                if u:
                    ok,msg,oid=OTP.request_otp(app,db(),u['id'],u['email'],'forgot_password',request.remote_addr)
                    if ok: session['otp_flow']={'purpose':'forgot_password','user_id':u['id'],'email':u['email'],'otp_id':oid}; flash(OTP.GENERIC,'success'); return redirect(url_for('otp_verify'))
                flash(OTP.GENERIC,'success')
            return render_template('forgot.html',stage='request')
        if flow.get('purpose')=='forgot_password_verified': return render_template('forgot.html',stage='reset')
        return render_template('forgot.html',stage='request')
