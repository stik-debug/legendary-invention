"""Product additions that sit on top of the existing ChamaPay ledger."""
import json
from datetime import date
from io import BytesIO
from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for
from urllib.parse import quote
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import features as X
import finance as F
import services as S
from db import audit

STAFF=('CHAMA_ADMIN','TREASURER','SECRETARY','AUDITOR')
VIEW=('CHAMA_ADMIN','TREASURER','SECRETARY','AUDITOR')


def _pdf(title, lines):
    out=BytesIO(); c=canvas.Canvas(out,pagesize=A4); w,h=A4; y=h-54
    c.setTitle(title); c.setFont('Helvetica-Bold',16); c.drawString(48,y,title); y-=28
    c.setFont('Helvetica',10)
    for line in lines:
        text=str(line)
        for chunk in [text[i:i+105] for i in range(0,len(text),105)] or ['']:
            if y<54: c.showPage(); y=h-54; c.setFont('Helvetica',10)
            c.drawString(48,y,chunk); y-=15
    c.save(); out.seek(0); return out


def register(app, db, ctx, login_required, owner_required):
    @app.route('/chamas/<int:chama_id>/dividends')
    @login_required
    def dividends(chama_id):
        chama,me,sub=ctx(chama_id,roles=VIEW)
        year=request.args.get('year',type=int) or date.today().year
        try: calc=X.dividend_calculation(db(),chama_id,year)
        except S.BusinessError as e: flash(str(e),'warning'); calc=X.dividend_calculation(db(),chama_id,date.today().year)
        return render_template('dividends.html',chama=chama,me=me,calc=calc)

    @app.route('/chamas/<int:chama_id>/member-exit/<int:user_id>',methods=['GET','POST'])
    @login_required
    def member_exit(chama_id,user_id):
        chama,me,sub=ctx(chama_id,roles=('CHAMA_ADMIN','TREASURER'))
        try: q=X.member_exit_quote(db(),chama_id,user_id)
        except S.BusinessError: abort(404)
        if request.method=='POST':
            try:
                eid=X.exit_member(db(),chama_id,user_id,g.user['id'],request.form.get('method'),request.form.get('reference'))
                flash('Member exited and their share payout was recorded.','success'); return redirect(url_for('chama_home',chama_id=chama_id))
            except S.BusinessError as e: flash(str(e),'warning')
        return render_template('member_exit.html',chama=chama,me=me,q=q,methods=F.METHODS)

    @app.route('/chamas/<int:chama_id>/audit-log')
    @login_required
    def chama_audit_log(chama_id):
        chama,me,sub=ctx(chama_id,roles=VIEW)
        rows=db().all("SELECT a.*,u.name actor_name FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id WHERE a.chama_id=? ORDER BY a.id DESC LIMIT 250",(chama_id,))
        for r in rows:
            try: r['metadata_obj']=json.loads(r['metadata'] or '{}')
            except Exception: r['metadata_obj']={}
        return render_template('audit_log.html',chama=chama,me=me,rows=rows)

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/share')
    @login_required
    def meeting_share(chama_id,mid):
        chama,me,sub=ctx(chama_id)
        m=db().one('SELECT * FROM meetings WHERE id=? AND chama_id=?',(mid,chama_id)) or abort(404)
        base=request.url_root.rstrip('/')
        target=f'{base}{url_for("meeting_view",chama_id=chama_id,mid=mid)}'
        return redirect('https://wa.me/?text='+quote(f'ChamaPay meeting: {m["title"]}\n{m["held_at"]}\n{target}'))

    @app.route('/chamas/<int:chama_id>/statement/<int:user_id>.pdf')
    @login_required
    def statement_pdf(chama_id,user_id):
        chama,me,sub=ctx(chama_id)
        if user_id!=g.user['id'] and me['role'] not in VIEW: abort(403)
        who=db().one('SELECT u.id,u.name,u.email FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.user_id=?',(chama_id,user_id)) or abort(404)
        st=F.member_statement(db(),chama_id,user_id)
        lines=[f'Chama: {chama["name"]}',f'Member: {who["name"]}',f'Generated: {S.now_utc()}','',f'Total saved: KES {st["saved"]/100:,.2f}',f'Loan balance: KES {st["loan_balance"]/100:,.2f}',f'Fines owed: KES {st["fine_balance"]/100:,.2f}','', 'CONTRIBUTIONS']
        lines += [f'{c["paid_on"]} | KES {c["amount_cents"]/100:,.2f} | {c["method"]} | {c.get("reference") or "—"} | {c["status"]}' for c in st['contributions']]
        lines += ['','LOANS'] + [f'{l["applied_at"][:10]} | Principal KES {l["principal_cents"]/100:,.2f} | Due KES {l["total_due_cents"]/100:,.2f} | Paid KES {l["paid_cents"]/100:,.2f} | {l["status"]}' for l in st['loans']]
        lines += ['','FINES'] + [f'{f["created_at"][:10]} | KES {f["amount_cents"]/100:,.2f} | Paid KES {f["paid_cents"]/100:,.2f} | {f["status"]} | {f["reason"]}' for f in st['fines']]
        return send_file(_pdf('ChamaPay Member Statement',lines),mimetype='application/pdf',as_attachment=True,download_name=f'statement-{user_id}.pdf')

    @app.route('/chamas/<int:chama_id>/money-receipt/<kind>/<int:item_id>.pdf')
    @login_required
    def receipt_pdf(chama_id,kind,item_id):
        chama,me,sub=ctx(chama_id)
        table_map={'contribution':("SELECT c.*,u.name member FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.id=? AND c.chama_id=?",'Contribution'),'repayment':("SELECT r.*,u.name member FROM loan_repayments r JOIN users u ON u.id=r.user_id WHERE r.id=? AND r.chama_id=?",'Loan repayment'),'fine':("SELECT p.*,u.name member FROM fine_payments p JOIN users u ON u.id=p.user_id WHERE p.id=? AND p.chama_id=?",'Fine payment')}
        if kind not in table_map: abort(404)
        row=db().one(table_map[kind][0],(item_id,chama_id)) or abort(404)
        if row['user_id']!=me['user_id'] and me['role'] not in STAFF: abort(403)
        proof=f'CP-{kind[:3].upper()}-{item_id:06d}'
        lines=['CHAMAPAY FINANCIAL RECEIPT','',f'Chama: {chama["name"]}',f'Type: {table_map[kind][1]}',f'Member: {row["member"]}',f'Amount: KES {row["amount_cents"]/100:,.2f}',f'Date: {row.get("paid_on") or row.get("created_at")}',f'Method: {row.get("method") or "—"}',f'Reference: {row.get("reference") or "—"}',f'Proof ID: {proof}','', 'This proof confirms what ChamaPay currently records. It is not a bank statement or external payment-provider confirmation.']
        return send_file(_pdf('ChamaPay Receipt',lines),mimetype='application/pdf',as_attachment=True,download_name=f'{proof}.pdf')
