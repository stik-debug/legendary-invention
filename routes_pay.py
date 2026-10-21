import io

from flask import abort, flash, g, redirect, render_template, request, url_for, Response

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

    @app.route('/chamas/<int:chama_id>/pay/<int:pid>/receipt')
    @login_required
    def pay_receipt(chama_id, pid):
        chama, me, sub = ctx(chama_id)
        p = db().one('SELECT * FROM chama_payments WHERE id=? AND chama_id=?', (pid, chama_id))
        if not p or (p['user_id'] != g.user['id'] and me['role'] not in STAFF):
            abort(404)
        if p['status'] != 'SUCCESS' or not p['applied']:
            abort(404)
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from xml.sax.saxutils import escape
            buf = io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=22*mm, leftMargin=22*mm, topMargin=22*mm, bottomMargin=20*mm, title='ChamaPay Payment Receipt')
            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle(name='ReceiptTitle', parent=styles['Title'], alignment=TA_CENTER, textColor=colors.HexColor('#123B35'), fontSize=23, leading=28, spaceAfter=5))
            styles.add(ParagraphStyle(name='ReceiptSub', parent=styles['Normal'], alignment=TA_CENTER, textColor=colors.HexColor('#54716B'), fontSize=10, spaceAfter=18))
            styles.add(ParagraphStyle(name='ReceiptMuted', parent=styles['Normal'], textColor=colors.HexColor('#64748b'), fontSize=8))
            user = db().one('SELECT name FROM users WHERE id=?', (p['user_id'],))
            purpose = P.PURPOSES.get(p['purpose'], str(p['purpose']).replace('_',' ').title())
            date_value = p['completed_at'] or p['created_at'] or ''
            story = [Paragraph('ChamaPay', styles['ReceiptTitle']), Paragraph('OFFICIAL PAYMENT RECEIPT', styles['ReceiptSub']), Paragraph('Payment confirmed and recorded', ParagraphStyle('ReceiptConfirmed', parent=styles['Heading2'], alignment=TA_CENTER, textColor=colors.HexColor('#16734A'), spaceAfter=18))]
            data = [['Chama / Group', escape(str(chama['name']))], ['Member', escape(str(user['name'] if user else 'Member'))], ['Amount paid', 'KES {:,.2f}'.format(int(p['amount_cents'])/100)], ['Payment purpose', escape(str(purpose))], ['Confirmation code', escape(str(p['receipt'] or 'Recorded internally'))], ['Payment status', 'PAID - CONFIRMED'], ['Recorded on', escape(str(date_value))], ['Receipt reference', 'CHP-{}-{}'.format(chama_id, pid)]]
            table = Table(data, colWidths=[48*mm, 105*mm], hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#F0F7F5')),('TEXTCOLOR',(0,0),(0,-1),colors.HexColor('#34534D')),('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTNAME',(0,2),(-1,2),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),10),('TEXTCOLOR',(1,2),(1,2),colors.HexColor('#123B35')),('FONTSIZE',(1,2),(1,2),13),('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#D9E7E3')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),11),('BOTTOMPADDING',(0,0),(-1,-1),11)]))
            story += [table, Spacer(1,18), Paragraph('This receipt confirms that the payment shown above has been marked successful in ChamaPay. Keep it for your records. If any detail looks incorrect, contact your chama treasurer or administrator.', styles['Normal']), Spacer(1,16), Paragraph('Generated by ChamaPay. This document is not an M-Pesa statement or proof of a bank transfer beyond the recorded confirmation shown above.', styles['ReceiptMuted'])]
            doc.build(story); pdf = buf.getvalue()
        except Exception:
            app.logger.exception('Could not generate payment receipt'); abort(500)
        response = Response(pdf, mimetype='application/pdf')
        response.headers['Content-Disposition'] = 'attachment; filename="ChamaPay-Receipt-{}.pdf"'.format(pid)
        response.headers['Cache-Control'] = 'no-store'
        return response

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
