"""ChamaPay V22: polish, trust, intelligence and privacy improvements.
No M-Pesa integration is added here. Financial actions remain server-authorized.
"""
import json
from datetime import date, timedelta
from flask import abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from io import BytesIO
import finance as F
import services as S
from db import audit

STAFF = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY', 'AUDITOR')


def _stamp():
    return S.iso(S.now_utc())


def _health(db, cid, total_members):
    period = date.today().isoformat()[:7]
    paid = int(db.val("SELECT COUNT(DISTINCT user_id) FROM contributions WHERE chama_id=? AND period=? AND status='PAID'", (cid, period), 0) or 0)
    contrib_score = round((paid / max(total_members, 1)) * 100) if total_members else 0
    due = int(db.val("SELECT COALESCE(SUM(total_due_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','REPAID','PAID')", (cid,), 0) or 0)
    repaid = int(db.val("SELECT COALESCE(SUM(paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','REPAID','PAID')", (cid,), 0) or 0)
    loan_score = round(min(100, (repaid / max(due, 1)) * 100)) if due else 100
    attendance_total = int(db.val("SELECT COUNT(*) FROM attendance WHERE chama_id=?", (cid,), 0) or 0)
    attendance_present = int(db.val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND status='PRESENT'", (cid,), 0) or 0)
    attendance_score = round((attendance_present / max(attendance_total, 1)) * 100) if attendance_total else 100
    open_actions = int(db.val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN'", (cid,), 0) or 0)
    overdue = int(db.val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND status='ACTIVE' AND due_date IS NOT NULL AND due_date<?", (cid, date.today().isoformat()), 0) or 0)
    transparency = 100 if int(db.val("SELECT COUNT(*) FROM audit_logs WHERE chama_id=?", (cid,), 0) or 0) else 70
    parts = {
        'Contribution consistency': contrib_score,
        'Loan repayment': loan_score,
        'Meeting participation': attendance_score,
        'Financial transparency': transparency,
    }
    penalties = min(25, overdue * 5 + min(open_actions, 5))
    score = max(0, round(sum(parts.values()) / len(parts)) - penalties)
    label = 'Excellent' if score >= 85 else 'Healthy' if score >= 70 else 'Needs attention' if score >= 50 else 'At risk'
    return score, label, parts, {'paid_members': paid, 'total_members': total_members, 'overdue_loans': overdue, 'open_actions': open_actions}


def register_v22_routes(app, db, login_required, ctx, owner_required):
    @app.route('/chamas/<int:chama_id>/money-timeline')
    @login_required
    def money_timeline(chama_id):
        chama, me, sub = ctx(chama_id)
        uid = g.user['id']
        rows = []
        for r in db().all("""SELECT id, amount_cents amount, paid_on occurred_on, method, reference, status, 'Contribution' kind
                           FROM contributions WHERE chama_id=? AND user_id=? ORDER BY id DESC LIMIT 120""", (chama_id, uid)):
            r['direction'] = 'IN'; r['title'] = 'Contribution'; rows.append(r)
        for r in db().all("""SELECT r.id, r.amount_cents amount, r.paid_on occurred_on, r.method, r.reference, r.status,
                                  'Loan repayment' kind, l.id loan_id
                           FROM loan_repayments r JOIN loans l ON l.id=r.loan_id
                           WHERE r.chama_id=? AND r.user_id=? ORDER BY r.id DESC LIMIT 120""", (chama_id, uid)):
            r['direction'] = 'IN'; r['title'] = 'Loan repayment'; rows.append(r)
        for r in db().all("""SELECT p.id, p.amount_cents amount, p.paid_on occurred_on, p.method, p.reference, p.status,
                                  'Fine payment' kind, f.id fine_id
                           FROM fine_payments p JOIN fines f ON f.id=p.fine_id
                           WHERE p.chama_id=? AND p.user_id=? ORDER BY p.id DESC LIMIT 120""", (chama_id, uid)):
            r['direction'] = 'IN'; r['title'] = 'Fine payment'; rows.append(r)
        rows.sort(key=lambda x: (x.get('occurred_on') or '', x.get('id') or 0), reverse=True)
        return render_template('money_timeline.html', chama=chama, me=me, sub=sub, rows=rows[:150])

    @app.route('/chamas/<int:chama_id>/money-receipt/<kind>/<int:item_id>')
    @login_required
    def money_receipt(chama_id, kind, item_id):
        chama, me, sub = ctx(chama_id)
        table_map = {
            'contribution': ("SELECT c.*,u.name member FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.id=? AND c.chama_id=?", 'Contribution'),
            'repayment': ("SELECT r.*,u.name member FROM loan_repayments r JOIN users u ON u.id=r.user_id WHERE r.id=? AND r.chama_id=?", 'Loan repayment'),
            'fine': ("SELECT p.*,u.name member FROM fine_payments p JOIN users u ON u.id=p.user_id WHERE p.id=? AND p.chama_id=?", 'Fine payment'),
        }
        if kind not in table_map: abort(404)
        row = db().one(table_map[kind][0], (item_id, chama_id)) or abort(404)
        if row['user_id'] != me['user_id'] and me['role'] not in STAFF: abort(403)
        proof_id = f"CP-{kind[:3].upper()}-{item_id:06d}"
        return render_template('money_receipt.html', chama=chama, me=me, row=row, kind=table_map[kind][1], proof_id=proof_id)

    @app.route('/chamas/<int:chama_id>/health')
    @login_required
    def chama_health(chama_id):
        chama, me, sub = ctx(chama_id)
        total = int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,), 0) or 0)
        score, label, parts, meta = _health(db(), chama_id, total)
        return render_template('chama_health.html', chama=chama, me=me, sub=sub, health=score, label=label, parts=parts, meta=meta)

    @app.route('/chamas/<int:chama_id>/trust')
    @login_required
    def chama_trust(chama_id):
        chama, me, sub = ctx(chama_id)
        recon = {
            'ledger_in': int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='IN'", (chama_id,), 0) or 0),
            'ledger_out': int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='OUT'", (chama_id,), 0) or 0),
        }
        audits = int(db().val("SELECT COUNT(*) FROM audit_logs WHERE chama_id=?", (chama_id,), 0) or 0)
        members = int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,), 0) or 0)
        return render_template('trust_center.html', chama=chama, me=me, sub=sub, recon=recon, audits=audits, members=members)

    @app.route('/chamas/<int:chama_id>/explain/<metric>')
    @login_required
    def explain_metric(chama_id, metric):
        chama, me, sub = ctx(chama_id)
        if metric == 'savings':
            total = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'", (chama_id,), 0) or 0)
            rows = db().all("SELECT period,COALESCE(SUM(amount_cents),0) amount FROM contributions WHERE chama_id=? AND status='PAID' GROUP BY period ORDER BY period DESC LIMIT 6", (chama_id,))
            return render_template('explain_metric.html', chama=chama, me=me, title='Total savings', value=total, explanation='Sum of recorded PAID contributions. Voided contributions are excluded.', rows=rows, row_label='Month')
        if metric == 'cash':
            incoming = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND direction='IN'", (chama_id,), 0) or 0)
            outgoing = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND direction='OUT'", (chama_id,), 0) or 0)
            return render_template('explain_metric.html', chama=chama, me=me, title='Ledger cash', value=incoming-outgoing, explanation='Recorded ledger money-in minus money-out. This is a system snapshot, not an external bank reconciliation.', rows=[{'period':'Money in','amount':incoming},{'period':'Money out','amount':outgoing}], row_label='Component')
        if metric == 'members':
            total = int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,), 0) or 0)
            return render_template('explain_metric.html', chama=chama, me=me, title='Active members', value=total, explanation='Count of active chama memberships at the time this page was loaded.', rows=[], row_label='')
        abort(404)

    @app.route('/chamas/<int:chama_id>/setup')
    @login_required
    def chama_setup(chama_id):
        chama, me, sub = ctx(chama_id)
        if int(chama['created_by']) != int(me['user_id']): abort(403)
        members = int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'", (chama_id,), 0) or 0)
        constitution = bool(db().val("SELECT COUNT(*) FROM chama_constitutions WHERE chama_id=?", (chama_id,), 0))
        meeting = bool(db().val("SELECT COUNT(*) FROM meetings WHERE chama_id=?", (chama_id,), 0))
        goal = bool(db().val("SELECT COUNT(*) FROM chama_goals WHERE chama_id=? AND status='ACTIVE'", (chama_id,), 0))
        notice = bool(db().val("SELECT COUNT(*) FROM announcements WHERE chama_id=?", (chama_id,), 0))
        steps = [
            ('Members', 'Invite your members and assign roles.', members >= 2, url_for('chama_home', chama_id=chama_id)),
            ('Rules', 'Confirm contribution, loan and attendance rules.', constitution, url_for('constitution', chama_id=chama_id)),
            ('First meeting', 'Schedule your first meeting.', meeting, url_for('meetings', chama_id=chama_id)),
            ('First goal', 'Create a savings goal for the group.', goal, url_for('chama_goals', chama_id=chama_id)),
            ('First notice', 'Send a welcome notice to members.', notice, url_for('notices', chama_id=chama_id)),
        ]
        done = sum(1 for x in steps if x[2]); pct = round(done / len(steps) * 100)
        return render_template('setup_wizard.html', chama=chama, me=me, sub=sub, steps=steps, pct=pct)

    @app.route('/security-privacy')
    @login_required
    def security_privacy():
        u = db().one('SELECT * FROM users WHERE id=?', (g.user['id'],))
        recent = db().all("SELECT action,created_at,entity_type FROM audit_logs WHERE actor_id=? ORDER BY id DESC LIMIT 15", (g.user['id'],))
        requests = db().all("SELECT * FROM privacy_requests WHERE user_id=? ORDER BY id DESC", (g.user['id'],))
        return render_template('security_privacy.html', u=u, recent=recent, requests=requests)

    @app.route('/privacy/request', methods=['POST'])
    @login_required
    def privacy_request():
        kind = (request.form.get('kind') or '').upper()
        if kind not in ('ACCESS', 'CORRECTION', 'DELETION', 'OBJECTION'):
            abort(400)
        reason = (request.form.get('reason') or '').strip()[:1000]
        db().insert('privacy_requests', user_id=g.user['id'], kind=kind, status='OPEN', reason=reason, created_at=_stamp())
        audit(db(), g.user['id'], 'PRIVACY_REQUESTED', 'privacy_request', None, None, {'kind': kind})
        db().commit()
        flash('Your privacy request has been recorded for review.', 'success')
        return redirect(url_for('security_privacy'))

    @app.route('/privacy/export')
    @login_required
    def privacy_export():
        uid = g.user['id']
        data = {'exported_at': _stamp(), 'user': db().one('SELECT id,name,email,phone,created_at FROM users WHERE id=?', (uid,))}
        data['memberships'] = db().all("SELECT m.chama_id,c.name,m.role,m.joined_at,m.status FROM chama_members m JOIN chamas c ON c.id=m.chama_id WHERE m.user_id=?", (uid,))
        data['contributions'] = db().all("SELECT chama_id,amount_cents,paid_on,period,method,reference,status,created_at FROM contributions WHERE user_id=? ORDER BY id DESC", (uid,))
        data['loans'] = db().all("SELECT chama_id,principal_cents,interest_cents,total_due_cents,paid_cents,rate_bps,purpose,status,applied_at,decided_at,disbursed_at FROM loans WHERE user_id=? ORDER BY id DESC", (uid,))
        data['loan_repayments'] = db().all("SELECT chama_id,loan_id,amount_cents,paid_on,method,reference,status,created_at FROM loan_repayments WHERE user_id=? ORDER BY id DESC", (uid,))
        data['fines'] = db().all("SELECT chama_id,amount_cents,paid_cents,reason,due_on,status,created_at FROM fines WHERE user_id=? ORDER BY id DESC", (uid,))
        data['fine_payments'] = db().all("SELECT chama_id,fine_id,amount_cents,paid_on,method,reference,status,created_at FROM fine_payments WHERE user_id=? ORDER BY id DESC", (uid,))
        data['notifications'] = db().all("SELECT chama_id,text,link,created_at,read_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 500", (uid,))
        payload = json.dumps(data, indent=2, default=str).encode('utf-8')
        return send_file(BytesIO(payload), mimetype='application/json', as_attachment=True, download_name='chamapay-my-data.json')

    @app.route('/owner/privacy')
    @owner_required
    def owner_privacy():
        rows = db().all("""SELECT p.*,u.name,u.email FROM privacy_requests p JOIN users u ON u.id=p.user_id
                         ORDER BY CASE p.status WHEN 'OPEN' THEN 0 ELSE 1 END,p.id DESC LIMIT 300""")
        return render_template('owner_privacy.html', rows=rows)

    @app.route('/owner/privacy/<int:request_id>', methods=['POST'])
    @owner_required
    def owner_privacy_update(request_id):
        status=(request.form.get('status') or 'OPEN').upper()
        if status not in ('OPEN','IN_REVIEW','RESOLVED','REJECTED'): abort(400)
        db().execute('UPDATE privacy_requests SET status=?,resolution=?,resolved_at=? WHERE id=?', (status,(request.form.get('resolution') or '')[:2000],_stamp() if status in ('RESOLVED','REJECTED') else None,request_id))
        audit(db(),g.user['id'],'PRIVACY_REQUEST_UPDATED','privacy_request',request_id,None,{'status':status})
        db().commit(); flash('Privacy request updated.','success'); return redirect(url_for('owner_privacy'))

    @app.route('/owner/privacy/<int:request_id>/anonymise', methods=['POST'])
    @owner_required
    def owner_privacy_anonymise(request_id):
        import accounts as AC
        from services import BusinessError
        r = db().one('SELECT * FROM privacy_requests WHERE id=?', (request_id,))
        if not r or r['kind'] != 'DELETION':
            abort(404)
        try:
            AC.anonymise_user(db(), g.user['id'], r['user_id'])
        except BusinessError as e:
            flash(str(e), 'warning'); return redirect(url_for('owner_privacy'))
        db().execute('UPDATE privacy_requests SET status=?,resolution=?,resolved_at=? WHERE id=?',
                     ('RESOLVED', 'Account anonymised. Personal details removed; the group\'s financial records are kept without the person\'s identity.', _stamp(), request_id))
        db().commit(); flash('Account anonymised and the request resolved.', 'success'); return redirect(url_for('owner_privacy'))

    @app.route('/owner/analytics-v22')
    @owner_required
    def owner_analytics_v22():
        month = date.today().strftime('%Y-%m')
        rev_month = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE status='SUCCESS' AND applied=1 AND is_test_data=0 AND created_at LIKE ?", (month+'%',), 0) or 0)
        expiring = int(db().val("SELECT COUNT(*) FROM subscriptions WHERE status IN ('ACTIVE','TRIAL','GRACE_PERIOD') AND due_at IS NOT NULL AND due_at<?", ((date.today()+timedelta(days=7)).isoformat(),), 0) or 0)
        suspended = int(db().val("SELECT COUNT(*) FROM subscriptions WHERE status='SUSPENDED'", (), 0) or 0)
        failed = int(db().val("SELECT COUNT(*) FROM payments WHERE status='FAILED' AND is_test_data=0 AND created_at LIKE ?", (month+'%',), 0) or 0)
        security_events = int(db().val("SELECT COUNT(*) FROM audit_logs WHERE action IN ('LOGIN_FAILED','PRIVACY_REQUESTED') AND created_at LIKE ?", (month+'%',), 0) or 0)
        monthly=[]
        for i in range(5, -1, -1):
            d = date.today().replace(day=1)
            # month arithmetic without external dependencies
            y, m = d.year, d.month-i
            while m <= 0: y -= 1; m += 12
            key = f'{y:04d}-{m:02d}'
            amount = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE status='SUCCESS' AND applied=1 AND is_test_data=0 AND created_at LIKE ?", (key+'%',), 0) or 0)
            monthly.append({'period': key, 'amount': amount})
        return render_template('owner_analytics_v22.html', rev_month=rev_month, expiring=expiring, suspended=suspended, failed=failed,
                               security_events=security_events, monthly=monthly,
                               active_chamas=int(db().val("SELECT COUNT(*) FROM chamas WHERE status='ACTIVE'", (), 0) or 0),
                               active_members=int(db().val("SELECT COUNT(*) FROM chama_members WHERE status='ACTIVE'", (), 0) or 0))
