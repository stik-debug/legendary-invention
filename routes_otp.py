"""User-facing SMS OTP flows. Ordinary login never uses this module."""
from flask import flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import auth_otp as OTP
import services as S
from db import audit


def safe_next_local(value):
    value = (value or '').strip()
    if not value.startswith('/') or value.startswith('//'):
        return ''
    return value


def register(app, db, login_required):
    @app.route('/otp/resend', methods=['POST'])
    def otp_resend():
        flow = session.get('otp_flow') or {}
        if not flow.get('purpose') or not flow.get('user_id') or not flow.get('phone'):
            flash('There is no verification in progress.', 'warning')
            return redirect(url_for('login'))
        ok, msg, oid = OTP.request_otp(app, db(), flow['user_id'], flow['phone'], flow['purpose'], request.remote_addr)
        if ok:
            flow['otp_id'] = oid
            session['otp_flow'] = flow
            flash('A new verification code has been sent.', 'success')
        else:
            flash(msg, 'warning')
        return redirect(url_for('otp_verify'))

    @app.route('/otp/verify', methods=['GET', 'POST'])
    def otp_verify():
        flow = session.get('otp_flow') or {}
        if not flow.get('purpose') or not flow.get('otp_id'):
            return redirect(url_for('login'))
        if request.method == 'POST':
            row = OTP.verify_otp(app, db(), flow['otp_id'], request.form.get('code'), flow['purpose'])
            if not row:
                flash('That code is not correct, expired, or has been used. You have up to 5 attempts per code.', 'danger')
                return render_template('otp_verify.html', flow=flow)
            uid = int(row['user_id'])
            u = db().one('SELECT * FROM users WHERE id=? AND is_active=1', (uid,))
            purpose = flow['purpose']
            if not u:
                session.pop('otp_flow', None)
                flash('That verification is no longer valid.', 'danger')
                return redirect(url_for('login'))

            if purpose == 'signup':
                OTP.mark_phone_verified(db(), uid)
                with db().tx():
                    audit(db(), uid, 'USER_REGISTERED', 'user', uid)
                # Welcome SMS is a normal notification and may respect SMS preferences.
                try:
                    import notify as N
                    N.queue_sms(db(), uid, 'Welcome to ChamaPay! Your account is ready. Keep this number active for important Chama alerts.')
                    db().commit()
                except Exception:
                    pass
                session.clear(); session['uid'] = uid; session['ep'] = u['session_epoch']; session.permanent = True
                return redirect(url_for('dashboard'))

            if purpose == 'forgot_password':
                session['otp_flow'] = {'purpose':'forgot_password_verified', 'user_id':uid, 'phone':u['phone']}
                return redirect(url_for('forgot'))

            if purpose == 'phone_change_current':
                session['otp_flow'] = {'purpose':'phone_change_new', 'user_id':uid, 'phone':flow.get('new_phone') or '', 'current_verified':True}
                return redirect(url_for('phone_change'))

            if purpose == 'phone_change_new':
                new_phone = S.normalize_phone(flow.get('phone'))
                if not new_phone:
                    session.pop('otp_flow', None)
                    flash('The new phone number is no longer valid. Start again.', 'danger')
                    return redirect(url_for('phone_change'))
                if db().val('SELECT COUNT(*) FROM users WHERE phone=? AND id!=?', (new_phone, uid), 0):
                    session.pop('otp_flow', None)
                    flash('That phone number is already linked to another account.', 'danger')
                    return redirect(url_for('phone_change'))
                with db().tx():
                    db().execute('UPDATE users SET phone=?, phone_verified_at=? WHERE id=?', (new_phone, S.iso(S.now_utc()), uid))
                    audit(db(), uid, 'PHONE_CHANGED', 'user', uid, None, {'phone_verified':True})
                session.pop('otp_flow', None)
                flash('Your phone number has been changed and verified.', 'success')
                return redirect(url_for('security_privacy'))

            if purpose == 'password_change':
                session['otp_flow'] = {'purpose':'password_change_verified', 'user_id':uid}
                return redirect(url_for('password_change'))

            if purpose == 'login_phone_verify':
                OTP.mark_phone_verified(db(), uid)
                nxt = safe_next_local(flow.get('next'))
                session.clear(); session['uid'] = uid; session['ep'] = u['session_epoch']; session.permanent = True
                audit(db(), uid, 'LOGIN_PHONE_VERIFIED', 'user', uid)
                db().commit()
                return redirect(nxt or url_for('owner_home' if u['is_super_admin'] else 'dashboard'))

            flash('Verification complete.', 'success')
            session.pop('otp_flow', None)
            return redirect(url_for('security_privacy'))
        return render_template('otp_verify.html', flow=flow)

    @app.route('/security/phone', methods=['GET', 'POST'])
    @login_required
    def phone_change():
        flow = session.get('otp_flow') or {}
        if request.method == 'POST':
            if flow.get('purpose') == 'phone_change_new' and flow.get('current_verified'):
                new_phone = S.normalize_phone(request.form.get('new_phone'))
                if not new_phone:
                    flash('Enter a valid Kenyan phone number.', 'danger')
                    return render_template('phone_change.html', stage='new', flow=flow)
                if db().val('SELECT COUNT(*) FROM users WHERE phone=? AND id!=?', (new_phone, g.user['id']), 0):
                    flash('That phone number is already linked to another account.', 'danger')
                    return render_template('phone_change.html', stage='new', flow=flow)
                ok, msg, oid = OTP.request_otp(app, db(), g.user['id'], new_phone, 'phone_change_new', request.remote_addr)
                if ok:
                    session['otp_flow'] = {'purpose':'phone_change_new', 'user_id':g.user['id'], 'phone':new_phone, 'current_verified':True, 'otp_id':oid}
                    return redirect(url_for('otp_verify'))
                flash(msg, 'warning')
            else:
                ok, msg, oid = OTP.request_otp(app, db(), g.user['id'], g.user['phone'], 'phone_change_current', request.remote_addr)
                if ok:
                    session['otp_flow'] = {'purpose':'phone_change_current', 'user_id':g.user['id'], 'phone':g.user['phone'], 'new_phone':'', 'otp_id':oid}
                    flash('We sent a verification code to your current phone.', 'success')
                    return redirect(url_for('otp_verify'))
                flash(msg, 'warning')
        stage = 'new' if flow.get('purpose') == 'phone_change_new' and flow.get('current_verified') else 'current'
        return render_template('phone_change.html', stage=stage, flow=flow)

    @app.route('/security/password', methods=['GET', 'POST'])
    @login_required
    def password_change():
        flow = session.get('otp_flow') or {}
        if request.method == 'POST':
            if flow.get('purpose') == 'password_change_verified' and flow.get('user_id') == g.user['id']:
                password = request.form.get('password') or ''
                confirm = request.form.get('confirm') or ''
                if len(password) < 8:
                    flash('Password must be at least 8 characters.', 'danger')
                elif password != confirm:
                    flash('Passwords do not match.', 'danger')
                else:
                    with db().tx():
                        db().execute('UPDATE users SET password_hash=?, session_epoch=session_epoch+1 WHERE id=?', (generate_password_hash(password), g.user['id']))
                        audit(db(), g.user['id'], 'PASSWORD_CHANGED', 'user', g.user['id'], None, {'sms_otp':True})
                    session.clear()
                    flash('Your password was changed. Please log in again.', 'success')
                    return redirect(url_for('login'))
            else:
                ok, msg, oid = OTP.request_otp(app, db(), g.user['id'], g.user['phone'], 'password_change', request.remote_addr)
                if ok:
                    session['otp_flow'] = {'purpose':'password_change', 'user_id':g.user['id'], 'phone':g.user['phone'], 'otp_id':oid}
                    flash('We sent a verification code to your phone.', 'success')
                    return redirect(url_for('otp_verify'))
                flash(msg, 'warning')
        return render_template('password_change.html', verified=(flow.get('purpose') == 'password_change_verified'))

    @app.route('/forgot', methods=['GET', 'POST'])
    def forgot():
        flow = session.get('otp_flow') or {}
        if request.method == 'POST':
            action = request.form.get('action') or 'request'
            if action == 'verify_password' and flow.get('purpose') == 'forgot_password_verified':
                password = request.form.get('password') or ''
                confirm = request.form.get('confirm') or ''
                if len(password) < 8:
                    flash('Password must be at least 8 characters.', 'danger')
                elif password != confirm:
                    flash('Passwords do not match.', 'danger')
                else:
                    uid = flow.get('user_id')
                    with db().tx():
                        db().execute('UPDATE users SET password_hash=?, reset_hash=NULL, reset_expires=NULL, reset_fails=0, session_epoch=session_epoch+1 WHERE id=?', (generate_password_hash(password), uid))
                        audit(db(), uid, 'PASSWORD_RESET_DONE', 'user', uid, None, {'sms_otp':True})
                    session.clear()
                    flash('Your password was reset. You can now log in.', 'success')
                    return redirect(url_for('login'))
            else:
                phone = S.normalize_phone(request.form.get('phone'))
                u = db().one('SELECT id,phone FROM users WHERE phone=? AND is_active=1 AND claimed=1', (phone or '-',))
                if u:
                    ok, msg, oid = OTP.request_otp(app, db(), u['id'], u['phone'], 'forgot_password', request.remote_addr)
                    if ok:
                        session['otp_flow'] = {'purpose':'forgot_password', 'user_id':u['id'], 'phone':u['phone'], 'otp_id':oid}
                        # Same response either way to prevent account enumeration.
                        flash(OTP.GENERIC, 'success')
                        return redirect(url_for('otp_verify'))
                    flash(msg, 'warning')
                else:
                    # Same response whether the number exists or not.
                    flash(OTP.GENERIC, 'success')
                return render_template('forgot.html', stage='request')
        if flow.get('purpose') == 'forgot_password_verified':
            return render_template('forgot.html', stage='reset')
        return render_template('forgot.html', stage='request')
