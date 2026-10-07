from flask import abort, flash, g, redirect, render_template, request, url_for

import chama_pay as P
import finance as F
import services as S

STAFF = F.FINANCE_ROLES


def register(app, db, ctx, login_required):
    def back(chama_id, endpoint='pay', **kw):
        return redirect(url_for(endpoint, chama_id=chama_id, **kw))

    def what(raw):
        purpose, _, tid = (raw or '').partition(':')
        return purpose, (int(tid) if tid.isdigit() else None)

    @app.route('/chamas/<int:chama_id>/pay')
    @login_required
    def pay(chama_id):
        return redirect(url_for('universal_pay_page', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/pay/claim', methods=['POST'])
    @login_required
    def pay_claim(chama_id):
        ctx(chama_id)
        purpose, tid = what(request.form.get('what'))
        message = request.form.get('message')
        raw_amount = (request.form.get('amount') or '').strip()
        try:
            cents = F.parse_kes(raw_amount, 100) if raw_amount else None
            pid = P.claim(db(), chama_id, g.user['id'], purpose, tid, cents, request.form.get('code'), message=message)
            row = db().one('SELECT status, verified FROM chama_payments WHERE id=?', (pid,))
            if row['status'] == 'SUCCESS':
                flash('Recorded. Thank you.' if row['verified'] == 'TRUSTED' else "Checked against the chama's M-Pesa records and recorded. Thank you.", 'success')
            elif row['status'] == 'REVIEW':
                flash('Received. An official will look at it.', 'success')
            else:
                flash("Received. It is recorded as soon as the chama's M-Pesa records show it, or when an official confirms it.", 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return back(chama_id)

    @app.route('/chamas/<int:chama_id>/pay/records', methods=['GET', 'POST'])
    @login_required
    def pay_records(chama_id):
        chama, me, sub = ctx(chama_id, roles=STAFF)
        if request.method == 'POST':
            try:
                r = P.add_records(db(), chama_id, g.user['id'], request.form.get('text'))
                msg = f"{r['added']} added"
                if r['duplicates']:
                    msg += f", {r['duplicates']} already there"
                if r['skipped']:
                    msg += f", {r['skipped']} without an amount (ignored)"
                msg += f". {r['verified']} waiting payment(s) verified and recorded."
                if r['attention']:
                    msg += f" {r['attention']} need your attention under Pay."
                flash(msg, 'success')
            except S.BusinessError as e:
                flash(str(e), 'warning')
            return back(chama_id, 'pay_records')
        return render_template('pay_records.html', chama=chama, me=me, rows=P.records(db(), chama_id), names=P.PURPOSES)

    @app.route('/chamas/<int:chama_id>/pay/<int:pid>')
    @login_required
    def pay_status(chama_id, pid):
        chama, me, sub = ctx(chama_id)
        p = db().one('SELECT * FROM chama_payments WHERE id=? AND chama_id=?', (pid, chama_id))
        if not p or (p['user_id'] != g.user['id'] and me['role'] not in STAFF):
            abort(404)
        return render_template('pay_status.html', chama=chama, me=me, p=p, names=P.PURPOSES, mine=p['user_id'] == g.user['id'])

    @app.route('/chamas/<int:chama_id>/pay/<int:pid>/<action>', methods=['POST'])
    @login_required
    def pay_decide(chama_id, pid, action):
        ctx(chama_id, roles=STAFF)
        if action not in ('approve', 'reject'):
            abort(404)
        try:
            P.decide(db(), chama_id, pid, action == 'approve', g.user['id'], request.form.get('reason'))
            flash('Payment recorded.' if action == 'approve' else 'Payment rejected. The member was told why.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return back(chama_id)

    @app.route('/chamas/<int:chama_id>/pay/settings', methods=['GET', 'POST'])
    @login_required
    def pay_settings(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN',))
        if request.method == 'POST':
            try:
                P.save_config(db(), chama_id, request.form, g.user['id'])
                flash('Payment settings saved.', 'success')
                return back(chama_id)
            except S.BusinessError as e:
                flash(str(e), 'warning')
        return render_template('pay_settings.html', chama=chama, me=me, cfg=P.get_config(db(), chama_id), mode=P.check_mode(db(), chama_id), payment_methods=P.PAYMENT_METHODS)
