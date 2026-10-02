from datetime import date

from flask import abort, flash, g, redirect, render_template, request, url_for

import finance as F
import mgr as M
import services as S


def register(app, db, ctx, login_required):
    FIN = F.FINANCE_ROLES

    def done(chama_id, endpoint, fn, ok, **kw):
        try:
            fn()
            flash(ok, 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for(endpoint, chama_id=chama_id, **kw))

    def rnd_or_404(chama_id, rid):
        try:
            return M.get_round(db(), chama_id, rid)
        except S.BusinessError:
            abort(404)

    @app.route('/chamas/<int:chama_id>/merry-go-round')
    @login_required
    def mgr_list(chama_id):
        chama, me, sub = ctx(chama_id)
        members = db().all("SELECT u.id, u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name", (chama_id,))
        mine = {r['id']: s for r, s in M.my_dues(db(), chama_id, g.user['id'])}
        return render_template('mgr_list.html', chama=chama, me=me, rounds=M.list_rounds(db(), chama_id), pot=M.pot_balance(db(), chama_id),
                               can=me['role'] in FIN, members=members, owes=mine, today=date.today().isoformat())

    @app.route('/chamas/<int:chama_id>/merry-go-round/new', methods=['POST'])
    @login_required
    def mgr_new(chama_id):
        ctx(chama_id, roles=FIN)
        f = request.form
        try:
            ids = [int(x) for x in f.getlist('member')]
            order = None
            if f.get('order') == 'listed':
                pos = {}
                for uid in ids:
                    raw = (f.get(f'pos_{uid}') or '').strip()
                    pos[uid] = int(raw) if raw.isdigit() else 10 ** 6
                names = {r['id']: r['name'] for r in db().all('SELECT id, name FROM users WHERE id IN (' + ','.join(str(i) for i in ids or [0]) + ')')}
                order = sorted(ids, key=lambda u: (pos[u], names.get(u, '')))
            rid = M.create_round(db(), chama_id, f.get('name'), F.parse_kes(f.get('amount'), 100), f.get('frequency'), f.get('start'), ids, g.user['id'], order)
        except ValueError:
            flash('Check the members you selected.', 'warning')
            return redirect(url_for('mgr_list', chama_id=chama_id))
        except S.BusinessError as e:
            flash(str(e), 'warning')
            return redirect(url_for('mgr_list', chama_id=chama_id))
        flash('Merry-go-round started. Every member in it was alerted.', 'success')
        return redirect(url_for('mgr_view', chama_id=chama_id, rid=rid))

    @app.route('/chamas/<int:chama_id>/merry-go-round/<int:rid>')
    @login_required
    def mgr_view(chama_id, rid):
        chama, me, sub = ctx(chama_id)
        rnd = rnd_or_404(chama_id, rid)
        turn = M.turn_status(db(), rnd) if rnd['status'] == 'ACTIVE' else None
        return render_template('mgr_view.html', chama=chama, me=me, r=rnd, slots=M.slots(db(), rid), turn=turn, pot=M.pot_balance(db(), chama_id),
                               can=me['role'] in FIN, is_admin=me['role'] == 'CHAMA_ADMIN', can_remind=me['role'] in F.VIEW_ROLES, today=date.today().isoformat(),
                               i_owe=bool(turn and any(u['user_id'] == g.user['id'] for u in turn['unpaid'])))

    @app.route('/chamas/<int:chama_id>/merry-go-round/<int:rid>/pay', methods=['POST'])
    @login_required
    def mgr_pay(chama_id, rid):
        ctx(chama_id, roles=FIN); rnd_or_404(chama_id, rid)
        f = request.form
        try:
            uid = int(f.get('user_id', ''))
        except ValueError:
            abort(400)
        return done(chama_id, 'mgr_view', lambda: M.record_payment(db(), chama_id, rid, uid, f.get('method'), f.get('reference'), f.get('paid_on'), g.user['id']),
                    'Payment recorded in the merry-go-round pot.', rid=rid)

    @app.route('/chamas/<int:chama_id>/merry-go-round/<int:rid>/payments/<int:pid>/void', methods=['POST'])
    @login_required
    def mgr_void(chama_id, rid, pid):
        ctx(chama_id, roles=FIN); rnd_or_404(chama_id, rid)
        return done(chama_id, 'mgr_view', lambda: M.void_payment(db(), chama_id, rid, pid, request.form.get('reason'), g.user['id']), 'Payment cancelled.', rid=rid)

    @app.route('/chamas/<int:chama_id>/merry-go-round/<int:rid>/payout', methods=['POST'])
    @login_required
    def mgr_payout(chama_id, rid):
        ctx(chama_id, roles=FIN); rnd_or_404(chama_id, rid)
        return done(chama_id, 'mgr_view', lambda: M.payout(db(), chama_id, rid, request.form.get('method'), request.form.get('reference'), g.user['id']),
                    'Payout recorded. The next turn has started and everyone who owes was alerted.', rid=rid)

    @app.route('/chamas/<int:chama_id>/merry-go-round/<int:rid>/remind', methods=['POST'])
    @login_required
    def mgr_remind(chama_id, rid):
        ctx(chama_id, roles=F.VIEW_ROLES); rnd_or_404(chama_id, rid)
        try:
            n = M.remind(db(), chama_id, rid, g.user['id'])
            flash(f'Reminder sent to {n} member(s).', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('mgr_view', chama_id=chama_id, rid=rid))

    @app.route('/chamas/<int:chama_id>/merry-go-round/<int:rid>/cancel', methods=['POST'])
    @login_required
    def mgr_cancel(chama_id, rid):
        ctx(chama_id, roles=('CHAMA_ADMIN',)); rnd_or_404(chama_id, rid)
        return done(chama_id, 'mgr_view', lambda: M.cancel_round(db(), chama_id, rid, request.form.get('reason'), g.user['id']), 'Merry-go-round cancelled.', rid=rid)
