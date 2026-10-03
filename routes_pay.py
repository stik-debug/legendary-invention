import hmac
import os

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

import chama_pay as P
import finance as F
import services as S
from providers import MpesaPaymentProvider

STAFF = F.FINANCE_ROLES


def register(app, db, ctx, login_required):
    def simulate():
        p = app.config['PROVIDER']
        return (not app.config['IS_PRODUCTION']) and not (p and p.name == 'MPESA')

    def base_url():
        return (os.environ.get('PUBLIC_URL') or request.url_root).rstrip('/')

    def back(chama_id, endpoint='pay', **kw):
        return redirect(url_for(endpoint, chama_id=chama_id, **kw))

    def what(raw):
        purpose, _, tid = (raw or '').partition(':')
        return purpose, (int(tid) if tid.isdigit() else None)

    @app.route('/chamas/<int:chama_id>/pay')
    @login_required
    def pay(chama_id):
        chama, me, sub = ctx(chama_id)
        staff = me['role'] in STAFF
        return render_template('pay.html', chama=chama, me=me, channel=P.channel(db(), chama_id), cfg=P.get_config(db(), chama_id), d=P.dues(db(), chama_id, g.user['id']),
                               staff=staff, is_admin=me['role'] == 'CHAMA_ADMIN', queue=P.queue(db(), chama_id) if staff else [], names=P.PURPOSES, sim=simulate())

    @app.route('/chamas/<int:chama_id>/pay/start', methods=['POST'])
    @login_required
    def pay_start(chama_id):
        ctx(chama_id)
        purpose, tid = request.form.get('purpose'), request.form.get('target_id', type=int)
        try:
            pid = P.initiate(db(), chama_id, g.user['id'], purpose, tid, F.parse_kes(request.form.get('amount'), 100), request.form.get('phone'), base_url(), simulate())
        except S.BusinessError as e:
            flash(str(e), 'warning')
            return back(chama_id)
        flash('Check your phone and enter your M-Pesa PIN.', 'info')
        return back(chama_id, 'pay_status', pid=pid)

    @app.route('/chamas/<int:chama_id>/pay/claim', methods=['POST'])
    @login_required
    def pay_claim(chama_id):
        ctx(chama_id)
        purpose, tid = what(request.form.get('what'))
        try:
            P.claim(db(), chama_id, g.user['id'], purpose, tid, F.parse_kes(request.form.get('amount'), 100), request.form.get('code'))
            flash('Sent. An official will check it against the M-Pesa statement and confirm.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return back(chama_id)

    @app.route('/chamas/<int:chama_id>/pay/<int:pid>')
    @login_required
    def pay_status(chama_id, pid):
        chama, me, sub = ctx(chama_id)
        p = db().one('SELECT * FROM chama_payments WHERE id=? AND chama_id=?', (pid, chama_id))
        if not p or (p['user_id'] != g.user['id'] and me['role'] not in STAFF):
            abort(404)
        return render_template('pay_status.html', chama=chama, me=me, p=p, names=P.PURPOSES, sim=simulate(), mine=p['user_id'] == g.user['id'])

    @app.route('/chamas/<int:chama_id>/pay/<int:pid>/check', methods=['POST'])
    @login_required
    def pay_check(chama_id, pid):
        ctx(chama_id)
        try:
            P.check_pending(db(), chama_id, pid, g.user['id'], simulate())
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return back(chama_id, 'pay_status', pid=pid)

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
                P.save_config(db(), chama_id, request.form, g.user['id'], app.config['IS_PRODUCTION'])
                flash('Payment settings saved.', 'success')
                return back(chama_id)
            except S.BusinessError as e:
                flash(str(e), 'warning')
        return render_template('pay_settings.html', chama=chama, me=me, cfg=P.get_config(db(), chama_id), ready=P.encryption_ready(), prod=app.config['IS_PRODUCTION'])

    # Safaricom calls this. The per-chama secret in the URL is the gate; the payment is then verified against our own record.
    @app.route('/webhooks/chama-mpesa/<int:chama_id>/<secret>', methods=['POST'])
    def chama_mpesa_webhook(chama_id, secret):
        want = P.callback_secret(chama_id)
        if not want or not hmac.compare_digest(secret, want):
            abort(403)
        ev = MpesaPaymentProvider.parse_callback(request.get_json(silent=True) or {})
        try:
            P.settle(db(), chama_id, ev.get('checkout_id'), ev.get('result_code', -1), ev.get('amount_cents'), ev.get('receipt'))
        except Exception:
            app.logger.exception('chama payment webhook failed')
        return jsonify(ResultCode=0, ResultDesc='Accepted')

    @app.route('/dev/chama-pay/<int:chama_id>/<int:pid>/<outcome>', methods=['POST'])
    @login_required
    def dev_chama_pay(chama_id, pid, outcome):
        if not simulate():
            abort(404)
        ctx(chama_id)
        p = db().one('SELECT * FROM chama_payments WHERE id=? AND chama_id=? AND user_id=?', (pid, chama_id, g.user['id']))
        code = {'success': 0, 'failed': 1, 'cancelled': 1032}.get(outcome)
        if not p or code is None:
            abort(404)
        P.settle(db(), chama_id, p['checkout_id'], code, p['amount_cents'], 'SIM' + p['checkout_id'][-9:])
        flash(f'Simulated: {outcome}.', 'info')
        return back(chama_id, 'pay_status', pid=pid)
