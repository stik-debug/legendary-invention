"""Password reset: one-time code flow without SMS/email delivery."""
import secrets

from flask import flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import services as S
from db import audit


def register(app, db):
    @app.route('/password-reset', methods=['GET', 'POST'])
    def password_reset_request():
        if request.method == 'POST':
            ident = (request.form.get('login') or '').strip().lower()
            phone = S.normalize_phone(ident)
            u = db().one('SELECT * FROM users WHERE is_active=1 AND claimed=1 AND (email=? OR phone=?)',
                         (ident, phone or '-'))
            if not u:
                flash('No active account found with that email or phone.', 'warning')
                return render_template('password_reset_request.html')
            code = ''.join(secrets.choice('0123456789') for _ in range(8))
            with db().tx():
                db().execute('UPDATE users SET reset_code_hash=?, reset_code_at=? WHERE id=?',
                             (generate_password_hash(code), S.iso(S.now_utc()), u['id']))
                audit(db(), None, 'PASSWORD_RESET_REQUESTED', 'user', u['id'])
            flash(f'Your one-time reset code is {code}. Enter it below to set a new password.', 'success')
            return redirect(url_for('password_reset_code', user_id=u['id']))
        return render_template('password_reset_request.html')

    @app.route('/password-reset/<int:user_id>', methods=['GET', 'POST'])
    def password_reset_code(user_id):
        u = db().one('SELECT * FROM users WHERE id=? AND is_active=1 AND claimed=1', (user_id,))
        if not u or not u['reset_code_hash']:
            flash('That reset code is no longer valid.', 'danger')
            return redirect(url_for('password_reset_request'))
        if not u['reset_code_at'] or (S.now_utc() - S.parse(u['reset_code_at'])).total_seconds() > 3600:
            with db().tx():
                db().execute('UPDATE users SET reset_code_hash=NULL, reset_code_at=NULL WHERE id=?', (user_id,))
            flash('This reset code has expired. Request a new one.', 'warning')
            return redirect(url_for('password_reset_request'))

        if request.method == 'POST':
            code = (request.form.get('code') or '').strip()
            password = request.form.get('password') or ''
            confirm = request.form.get('confirm') or ''

            if len(password) < 8:
                flash('Password must be at least 8 characters.', 'danger')
            elif password != confirm:
                flash('Passwords do not match.', 'danger')
            elif not check_password_hash(u['reset_code_hash'], code):
                flash('That reset code is not correct.', 'danger')
            else:
                with db().tx():
                    db().execute('UPDATE users SET password_hash=?, reset_code_hash=NULL, reset_code_at=NULL WHERE id=?',
                                 (generate_password_hash(password), user_id))
                    audit(db(), user_id, 'PASSWORD_RESET_COMPLETED', 'user', user_id)
                flash('Password reset complete. You can now log in.', 'success')
                return redirect(url_for('login'))

        return render_template('password_reset_code.html', user_id=user_id)
