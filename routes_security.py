"""Owner two-factor login: second login step and the owner's security page."""
import time
from datetime import timedelta

from flask import abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import services as S
import twofactor as T
from db import audit


def register(app, db, owner_required, safe_next):
    def blocked(key):
        db().execute('DELETE FROM login_attempts WHERE at<?', (S.iso(S.now_utc() - timedelta(minutes=10)),)); db().commit()
        return db().val('SELECT COUNT(*) FROM login_attempts WHERE key=?', (key,), 0) >= 6

    @app.route('/login/2fa', methods=['GET', 'POST'])
    def login_2fa():
        uid, at = session.get('pre2fa'), session.get('pre2fa_at', 0)
        if not uid or time.time() - at > 300:
            session.pop('pre2fa', None)
            flash('Please log in again.', 'warning')
            return redirect(url_for('login'))
        if request.method == 'POST':
            key = f'2fa|{uid}|{request.remote_addr}'
            if blocked(key):
                flash('Too many wrong codes. Wait 10 minutes and log in again.', 'danger')
                return render_template('login_2fa.html'), 429
            u = db().one('SELECT * FROM users WHERE id=? AND is_active=1 AND is_super_admin=1', (uid,))
            if u and T.check_login(db(), u, request.form.get('code')):
                nxt = session.get('pre2fa_next')
                session.clear(); session['uid'] = u['id']; session['ep'] = u['session_epoch']; session.permanent = True
                audit(db(), u['id'], 'LOGIN', 'user', u['id'], None, {'two_factor': True}); db().commit()
                return redirect(nxt or url_for('owner_home'))
            db().execute('INSERT INTO login_attempts(key,at) VALUES(?,?)', (key, S.iso(S.now_utc()))); db().commit()
            flash('That code is not right or was already used.', 'danger')
        return render_template('login_2fa.html')

    @app.route('/owner/security')
    @owner_required
    def owner_security():
        u = db().one('SELECT * FROM users WHERE id=?', (g.user['id'],))
        return render_template('owner_security.html', u=u, left=T.recovery_left(u),
                               required=bool(app.config.get('REQUIRE_OWNER_2FA')), setup=None, codes=None)

    @app.route('/owner/security/start', methods=['POST'])
    @owner_required
    def owner_2fa_start():
        if g.user['totp_enabled']:
            flash('Two-factor login is already on.', 'info')
            return redirect(url_for('owner_security'))
        secret = T.begin_setup(db(), g.user['id'])
        u = db().one('SELECT * FROM users WHERE id=?', (g.user['id'],))
        return render_template('owner_security.html', u=u, left=0, required=bool(app.config.get('REQUIRE_OWNER_2FA')),
                               setup={'secret': secret, 'uri': T.otpauth_uri(secret, g.user['email'])}, codes=None)

    @app.route('/owner/security/confirm', methods=['POST'])
    @owner_required
    def owner_2fa_confirm():
        try:
            codes = T.confirm_setup(db(), g.user['id'], request.form.get('code'))
        except S.BusinessError as e:
            u = db().one('SELECT * FROM users WHERE id=?', (g.user['id'],))
            flash(str(e), 'danger')
            if u['totp_secret'] and not u['totp_enabled']:
                return render_template('owner_security.html', u=u, left=0, required=bool(app.config.get('REQUIRE_OWNER_2FA')),
                                       setup={'secret': u['totp_secret'], 'uri': T.otpauth_uri(u['totp_secret'], u['email'])}, codes=None)
            return redirect(url_for('owner_security'))
        u = db().one('SELECT * FROM users WHERE id=?', (g.user['id'],))
        return render_template('owner_security.html', u=u, left=len(codes), required=bool(app.config.get('REQUIRE_OWNER_2FA')), setup=None, codes=codes)

    @app.route('/owner/security/codes', methods=['POST'])
    @owner_required
    def owner_2fa_codes():
        try:
            codes = T.new_recovery_codes(db(), g.user['id'], request.form.get('code'))
        except S.BusinessError as e:
            flash(str(e), 'danger')
            return redirect(url_for('owner_security'))
        u = db().one('SELECT * FROM users WHERE id=?', (g.user['id'],))
        return render_template('owner_security.html', u=u, left=len(codes), required=bool(app.config.get('REQUIRE_OWNER_2FA')), setup=None, codes=codes)

    @app.route('/owner/security/disable', methods=['POST'])
    @owner_required
    def owner_2fa_disable():
        if app.config.get('REQUIRE_OWNER_2FA'):
            flash('Two-factor login is required on this server, so it cannot be switched off here.', 'warning')
            return redirect(url_for('owner_security'))
        try:
            T.disable(db(), g.user['id'], request.form.get('password'), request.form.get('code'), check_password_hash)
            flash('Two-factor login is off.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'danger')
        return redirect(url_for('owner_security'))
