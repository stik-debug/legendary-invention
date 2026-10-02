from datetime import timedelta

from flask import abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import recovery as RC
import services as S


def register(app, db, ctx, login_required, owner_required):
    def blocked(key):
        db().execute('DELETE FROM login_attempts WHERE at<?', (S.iso(S.now_utc() - timedelta(minutes=10)),)); db().commit()
        return db().val('SELECT COUNT(*) FROM login_attempts WHERE key=?', (key,), 0) >= 6

    @app.route('/forgot', methods=['GET', 'POST'])
    def forgot():
        if request.method == 'POST':
            f = request.form
            key = f"reset|{S.normalize_phone(f.get('phone')) or '-'}|{request.remote_addr}"
            ip_key = f'reset-ip|{request.remote_addr}'
            if blocked(key) or blocked(ip_key):
                flash('Too many attempts. Wait 10 minutes and try again.', 'danger')
                return render_template('forgot.html'), 429
            if f.get('password') != f.get('confirm'):
                flash('Passwords do not match.', 'danger')
                return render_template('forgot.html')
            try:
                RC.reset_password(db(), f.get('phone'), f.get('code'), f.get('password'), generate_password_hash, check_password_hash)
                flash('Your password was changed. Log in with the new password.', 'success')
                return redirect(url_for('login'))
            except S.BusinessError as e:
                for k in (key, ip_key):
                    db().execute('INSERT INTO login_attempts(key,at) VALUES(?,?)', (k, S.iso(S.now_utc())))
                db().commit()
                flash(str(e), 'danger')
        return render_template('forgot.html')

    @app.route('/chamas/<int:chama_id>/members/<int:user_id>/reset-code', methods=['POST'])
    @login_required
    def member_reset_code(chama_id, user_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        try:
            code = RC.issue_reset_code(db(), g.user['id'], user_id, generate_password_hash, chama_id=chama_id)
            flash(f'Reset code: {code}. Give it to them in person or by phone. It works once, for {RC.CODE_MINUTES} minutes, at the "Forgot password" page.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chama_home', chama_id=chama_id))

    @app.route('/owner/users')
    @owner_required
    def owner_users():
        q = (request.args.get('q') or '').strip()[:80]
        rows = []
        if q:
            like = '%' + q.lower() + '%'
            ph = S.normalize_phone(q)
            rows = db().all("""SELECT id, name, email, phone, is_super_admin, claimed, is_active FROM users
                WHERE lower(name) LIKE ? OR lower(email) LIKE ? OR phone=? ORDER BY name LIMIT 20""", (like, like, ph or '-'))
            for r in rows:
                r['chamas'] = db().all("SELECT c.name FROM chama_members m JOIN chamas c ON c.id=m.chama_id WHERE m.user_id=? AND m.status='ACTIVE'", (r['id'],))
        return render_template('owner_users.html', q=q, rows=rows)

    @app.route('/owner/users/<int:user_id>/reset-code', methods=['POST'])
    @owner_required
    def owner_reset_code(user_id):
        try:
            code = RC.issue_reset_code(db(), g.user['id'], user_id, generate_password_hash)
            flash(f'Reset code: {code}. Give it only to the verified person. It works once, for {RC.CODE_MINUTES} minutes, at the "Forgot password" page.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('owner_users', q=request.form.get('q', '')))
