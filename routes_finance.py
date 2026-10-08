import re
from datetime import date

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for
from urllib.parse import quote

import finance as F
import services as S

PAGE = 25
FIN, VIEW, FINE = F.FINANCE_ROLES, F.VIEW_ROLES, F.FINE_ROLES


def register(app, db, ctx, login_required):
    def page_no():
        return max(1, request.args.get('page', 1, type=int))

    def done(chama_id, endpoint, fn, ok, **kw):
        """Run a business action; show the rule's message if it refuses."""
        try:
            fn()
            flash(ok, 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for(endpoint, chama_id=chama_id, **kw))

    def kes_field(name='amount', minimum=100):
        return F.parse_kes(request.form.get(name), minimum)

    def date_field(name='paid_on'):
        return F.parse_date(request.form.get(name))

    def int_field(name):
        try:
            return int(request.form.get(name))
        except (TypeError, ValueError):
            abort(400)

    # ---------- chama settings ----------
    @app.route('/chamas/<int:chama_id>/settings', methods=['POST'])
    @login_required
    def chama_settings(chama_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        return done(chama_id, 'chama_home', lambda: F.update_chama_settings(
            db(), chama_id, F.parse_kes(request.form.get('contribution'), 0), request.form.get('rate'), request.form.get('multiplier'), g.user['id']), 'Settings saved.')

    # ---------- savings ----------
    @app.route('/chamas/<int:chama_id>/savings')
    @login_required
    def savings(chama_id):
        chama, me, sub = ctx(chama_id)
        period = request.args.get('period', '')
        period = period if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) else date.today().isoformat()[:7]
        status = F.month_status(db(), chama_id, period)
        rows = db().all("""SELECT c.*, u.name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.chama_id=?
            ORDER BY c.paid_on DESC, c.id DESC LIMIT ? OFFSET ?""", (chama_id, PAGE, (page_no() - 1) * PAGE))
        total = db().val('SELECT COUNT(*) FROM contributions WHERE chama_id=?', (chama_id,), 0)
        return render_template('savings.html', chama=chama, me=me, status=status, period=period, rows=rows, page=page_no(), pages=max(1, -(-total // PAGE)),
                               can=me['role'] in FIN, today=date.today().isoformat(), methods=F.METHODS,
                               paid_count=sum(1 for s in status if s['status'] == 'PAID'), collected=sum(s['paid'] for s in status))

    @app.route('/chamas/<int:chama_id>/savings/add', methods=['POST'])
    @login_required
    def savings_add(chama_id):
        ctx(chama_id, roles=FIN)
        return done(chama_id, 'savings', lambda: F.record_contribution(
            db(), chama_id, int_field('user_id'), kes_field(), date_field(), request.form.get('method'), request.form.get('reference'),
            request.form.get('notes'), g.user['id']), 'Contribution recorded.')

    @app.route('/chamas/<int:chama_id>/savings/<int:cid>/void', methods=['POST'])
    @login_required
    def savings_void(chama_id, cid):
        ctx(chama_id, roles=FIN)
        return done(chama_id, 'savings', lambda: F.void_contribution(db(), chama_id, cid, request.form.get('reason'), g.user['id']), 'Contribution cancelled. The ledger was corrected.')

    # ---------- loans ----------
    @app.route('/chamas/<int:chama_id>/loans')
    @login_required
    def loans(chama_id):
        chama, me, sub = ctx(chama_id)
        staff = me['role'] in FIN
        where, args = ('l.chama_id=?', [chama_id]) if (staff or me['role'] == 'SECRETARY') else ('l.chama_id=? AND l.user_id=?', [chama_id, g.user['id']])
        rows = db().all(f"SELECT l.*, u.name FROM loans l JOIN users u ON u.id=l.user_id WHERE {where} ORDER BY l.id DESC LIMIT 60", args)
        reps = {}
        for r in db().all("SELECT r.* FROM loan_repayments r JOIN loans l ON l.id=r.loan_id WHERE r.chama_id=? AND r.status='PAID' ORDER BY r.id DESC LIMIT 300", (chama_id,)):
            reps.setdefault(r['loan_id'], []).append(r)
        open_loan = db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND user_id=? AND status IN ('PENDING','APPROVED','ACTIVE')", (chama_id, g.user['id']), 0)
        guarantor_members = db().all("SELECT u.id,u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' AND m.user_id<>? ORDER BY u.name", (chama_id,g.user['id']))
        return render_template('loans.html', chama=chama, me=me, loans=rows, reps=reps, staff=staff, methods=F.METHODS, today=date.today().isoformat(),
                               limit=F.max_loan(db(), chama_id, g.user['id']), open_loan=open_loan, cash=F.cash_balance(db(), chama_id), guarantor_members=guarantor_members)

    @app.route('/chamas/<int:chama_id>/loans/apply', methods=['POST'])
    @login_required
    def loan_apply(chama_id):
        ctx(chama_id)
        return done(chama_id, 'loans', lambda: F.apply_loan(db(), chama_id, g.user['id'], kes_field(minimum=10000), request.form.get('purpose'), guarantor_ids=request.form.getlist('guarantor_id')),
                    'Loan request sent to your officials.')

    @app.route('/chamas/<int:chama_id>/loans/<int:lid>/<action>', methods=['POST'])
    @login_required
    def loan_action(chama_id, lid, action):
        ctx(chama_id, roles=FIN)
        actor = g.user['id']
        if action in ('approve', 'reject'):
            return done(chama_id, 'loans', lambda: F.decide_loan(db(), chama_id, lid, action == 'approve', actor), 'Loan ' + ('approved.' if action == 'approve' else 'declined.'))
        if action == 'disburse':
            return done(chama_id, 'loans', lambda: F.disburse_loan(db(), chama_id, lid, actor), 'Loan paid out and recorded in the ledger.')
        if action == 'repay':
            return done(chama_id, 'loans', lambda: F.repay_loan(db(), chama_id, lid, kes_field(minimum=100), date_field(), request.form.get('method'),
                                                                  request.form.get('reference'), actor), 'Repayment recorded.')
        abort(404)

    @app.route('/chamas/<int:chama_id>/repayments/<int:rid>/void', methods=['POST'])
    @login_required
    def repayment_void(chama_id, rid):
        ctx(chama_id, roles=FIN)
        return done(chama_id, 'loans', lambda: F.void_repayment(db(), chama_id, rid, request.form.get('reason'), g.user['id']), 'Repayment cancelled.')

    # ---------- fines ----------
    @app.route('/chamas/<int:chama_id>/fines')
    @login_required
    def fines(chama_id):
        chama, me, sub = ctx(chama_id)
        see_all = me['role'] in VIEW
        where, args = ('f.chama_id=?', [chama_id]) if see_all else ('f.chama_id=? AND f.user_id=?', [chama_id, g.user['id']])
        rows = db().all(f"SELECT f.*, u.name FROM fines f JOIN users u ON u.id=f.user_id WHERE {where} ORDER BY f.id DESC LIMIT 80", args)
        members = db().all("SELECT u.id, u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name", (chama_id,))
        pays = {}
        for p in db().all("SELECT * FROM fine_payments WHERE chama_id=? AND status='PAID' ORDER BY id DESC LIMIT 300", (chama_id,)):
            pays.setdefault(p['fine_id'], []).append(p)
        return render_template('fines.html', chama=chama, me=me, fines=rows, members=members, pays=pays, can_create=me['role'] in FINE, can_pay=me['role'] in FIN,
                               is_admin=me['role'] == 'CHAMA_ADMIN', methods=F.METHODS, today=date.today().isoformat())

    @app.route('/chamas/<int:chama_id>/fines/add', methods=['POST'])
    @login_required
    def fine_add(chama_id):
        ctx(chama_id, roles=FINE)
        due = request.form.get('due_on', '').strip()
        return done(chama_id, 'fines', lambda: F.create_fine(db(), chama_id, int_field('user_id'), kes_field(minimum=100), request.form.get('reason'),
                                                             (F.parse_date(due) if due else None), g.user['id']), 'Fine created.')

    @app.route('/chamas/<int:chama_id>/fines/<int:fid>/<action>', methods=['POST'])
    @login_required
    def fine_action(chama_id, fid, action):
        if action == 'waive':
            ctx(chama_id, roles=('CHAMA_ADMIN',))
            return done(chama_id, 'fines', lambda: F.waive_fine(db(), chama_id, fid, g.user['id']), 'Fine waived.')
        if action == 'pay':
            ctx(chama_id, roles=FIN)
            return done(chama_id, 'fines', lambda: F.pay_fine(db(), chama_id, fid, kes_field(minimum=100), date_field(), request.form.get('method'),
                                                               request.form.get('reference'), g.user['id']), 'Fine payment recorded.')
        abort(404)

    @app.route('/chamas/<int:chama_id>/fine-payments/<int:pid>/void', methods=['POST'])
    @login_required
    def fine_payment_void(chama_id, pid):
        ctx(chama_id, roles=FIN)
        return done(chama_id, 'fines', lambda: F.void_fine_payment(db(), chama_id, pid, request.form.get('reason'), g.user['id']), 'Payment cancelled.')

    # ---------- ledger ----------
    @app.route('/chamas/<int:chama_id>/ledger')
    @login_required
    def ledger(chama_id):
        chama, me, sub = ctx(chama_id)
        rows = db().all("""SELECT l.*, u.name FROM ledger_transactions l LEFT JOIN users u ON u.id=l.user_id WHERE l.chama_id=? AND l.account='MAIN'
            ORDER BY l.id DESC LIMIT ? OFFSET ?""", (chama_id, PAGE, (page_no() - 1) * PAGE))
        total = db().val("SELECT COUNT(*) FROM ledger_transactions WHERE chama_id=? AND account='MAIN'", (chama_id,), 0)
        money_in, money_out = F.cash_totals(db(), chama_id)
        return render_template('ledger.html', chama=chama, me=me, rows=rows, page=page_no(), pages=max(1, -(-total // PAGE)), balance=F.cash_balance(db(), chama_id),
                               money_in=money_in, money_out=money_out, can=me['role'] in FIN, can_export=me['role'] in VIEW, today=date.today().isoformat())

    @app.route('/chamas/<int:chama_id>/ledger/add', methods=['POST'])
    @login_required
    def ledger_add(chama_id):
        ctx(chama_id, roles=FIN)
        return done(chama_id, 'ledger', lambda: F.record_ledger_entry(db(), chama_id, request.form.get('kind'), kes_field(), request.form.get('description'),
                                                                        date_field('on'), g.user['id']), 'Entry recorded.')

    @app.route('/chamas/<int:chama_id>/ledger.csv')
    @login_required
    def ledger_csv(chama_id):
        chama, me, sub = ctx(chama_id, roles=VIEW)
        rows = db().all("SELECT l.id, l.occurred_on, l.kind, l.direction, l.amount_cents, u.name, l.description FROM ledger_transactions l "
                        "LEFT JOIN users u ON u.id=l.user_id WHERE l.chama_id=? AND l.account='MAIN' ORDER BY l.id LIMIT 20000", (chama_id,))
        body = F.to_csv(['Entry', 'Date', 'Type', 'Direction', 'Amount KES', 'Member', 'Description'],
                        [[r['id'], r['occurred_on'], r['kind'], r['direction'], f"{r['amount_cents'] / 100:.2f}", r['name'], r['description']] for r in rows])
        return Response(body, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=ledger-{chama_id}.csv'})

    # ---------- statements ----------
    def target(chama_id, me, user_id):
        if user_id != g.user['id'] and me['role'] not in VIEW:
            abort(403)
        row = db().one("SELECT u.id, u.name, u.phone FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.user_id=?", (chama_id, user_id))
        return row or abort(404)

    @app.route('/chamas/<int:chama_id>/statement')
    @app.route('/chamas/<int:chama_id>/statement/<int:user_id>')
    @login_required
    def statement(chama_id, user_id=None):
        chama, me, sub = ctx(chama_id)
        who = target(chama_id, me, user_id or g.user['id'])
        share_text = quote(f'ChamaPay statement for {who["name"]}: {request.url_root.rstrip("/")}{url_for("statement", chama_id=chama_id, user_id=who["id"])}')
        whatsapp_share = 'https://wa.me/?text=' + share_text
        return render_template('statement.html', chama=chama, me=me, who=who, st=F.member_statement(db(), chama_id, who['id']), whatsapp_share=whatsapp_share)

    @app.route('/chamas/<int:chama_id>/statement/<int:user_id>.csv')
    @login_required
    def statement_csv(chama_id, user_id):
        chama, me, sub = ctx(chama_id)
        who = target(chama_id, me, user_id)
        st = F.member_statement(db(), chama_id, who['id'])
        rows = [['Contribution', c['paid_on'], c['status'], f"{c['amount_cents'] / 100:.2f}", c['method'], c['reference']] for c in st['contributions']]
        rows += [['Loan', l['applied_at'][:10], l['status'], f"{l['total_due_cents'] / 100:.2f}", f"paid {l['paid_cents'] / 100:.2f}", l['purpose']] for l in st['loans']]
        rows += [['Fine', f['created_at'][:10], f['status'], f"{f['amount_cents'] / 100:.2f}", f"paid {f['paid_cents'] / 100:.2f}", f['reason']] for f in st['fines']]
        body = F.to_csv(['Type', 'Date', 'Status', 'Amount KES', 'Detail', 'Note'], rows)
        return Response(body, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=statement-{who["id"]}.csv'})

    # ---------- chat ----------
    @app.route('/chamas/<int:chama_id>/chat')
    @login_required
    def chat(chama_id):
        chama, me, sub = ctx(chama_id)
        before = request.args.get('before', type=int)
        msgs = F.list_messages(db(), chama_id, before=before)
        if not before:
            F.mark_read(db(), chama_id, g.user['id'])
        return render_template('chat.html', chama=chama, me=me, msgs=msgs, last=(msgs[-1]['id'] if msgs else 0), older=(msgs[0]['id'] if len(msgs) == 50 else None))

    @app.route('/chamas/<int:chama_id>/chat/send', methods=['POST'])
    @login_required
    def chat_send(chama_id):
        ctx(chama_id)
        try:
            F.send_message(db(), chama_id, g.user['id'], request.form.get('body'))
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chat', chama_id=chama_id) + '#end')

    @app.route('/chamas/<int:chama_id>/chat/poll')
    @login_required
    def chat_poll(chama_id):
        ctx(chama_id)
        after = request.args.get('after', 0, type=int)
        rows = F.list_messages(db(), chama_id, after=after, limit=50)
        if rows:
            F.mark_read(db(), chama_id, g.user['id'])
        return jsonify(messages=[{'id': m['id'], 'sender': m['sender'], 'mine': m['sender_id'] == g.user['id'], 'body': m['body'], 'at': m['created_at']} for m in rows])
