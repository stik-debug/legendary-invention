"""ChamaPay 2.1: security-aware member experience, loan controls, documents, reconciliation,
backups, organizations, notifications preferences, and richer reporting/AI.
M-Pesa integration is intentionally not changed by this module.
"""
import csv
import io
import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

import finance as F
import features as X
import notify as N
import reports as R
import services as S
from db import audit

STAFF = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY', 'AUDITOR')
FIN = F.FINANCE_ROLES
VIEW = F.VIEW_ROLES
ALLOWED_DOCS = {'pdf', 'png', 'jpg', 'jpeg', 'webp', 'txt', 'csv'}
MAX_DOC_BYTES = 8 * 1024 * 1024


def _now():
    return S.now_utc()


def _stamp():
    return S.iso(_now())


def _parse_money(raw):
    return F.parse_kes(raw, 1)


def _doc_root(app):
    root = Path(app.instance_path) / 'uploads'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _member_or_404(db, chama_id, user_id):
    return db.one("""SELECT m.*,u.name,u.phone,u.email FROM chama_members m JOIN users u ON u.id=m.user_id
                    WHERE m.chama_id=? AND m.user_id=? AND m.status='ACTIVE'""", (chama_id, user_id)) or abort(404)


def _can_view_doc(me, doc):
    return me['role'] in STAFF or doc.get('user_id') in (None, me['user_id'])


def _safe_backup(db, chama_id=None):
    tables = ['chamas','chama_members','subscriptions','subscription_plans','contributions','loans','loan_repayments',
              'fines','fine_payments','ledger_transactions','meetings','attendance','announcements','notifications',
              'chama_goals','chama_investments','chama_assets','chama_votes','chama_vote_responses','chama_constitutions',
              'meeting_actions','loan_guarantors','loan_installments','member_documents']
    out = {'format':'chamapay-backup-v1','exported_at':_stamp(),'chama_id':chama_id,'tables':{}}
    for table in tables:
        try:
            if chama_id is None:
                rows = db.all('SELECT * FROM ' + table + ' LIMIT 50000')
            elif table in ('subscription_plans',):
                rows = db.all('SELECT * FROM ' + table)
            elif table == 'chamas':
                rows = db.all('SELECT * FROM chamas WHERE id=?',(chama_id,))
            elif table == 'chama_members':
                rows = db.all('SELECT * FROM chama_members WHERE chama_id=?',(chama_id,))
            elif table == 'subscriptions':
                rows = db.all('SELECT * FROM subscriptions WHERE chama_id=?',(chama_id,))
            elif table == 'contributions':
                rows = db.all('SELECT * FROM contributions WHERE chama_id=?',(chama_id,))
            elif table == 'loans':
                rows = db.all('SELECT * FROM loans WHERE chama_id=?',(chama_id,))
            elif table == 'loan_repayments':
                rows = db.all('SELECT * FROM loan_repayments WHERE chama_id=?',(chama_id,))
            elif table == 'fines':
                rows = db.all('SELECT * FROM fines WHERE chama_id=?',(chama_id,))
            elif table == 'fine_payments':
                rows = db.all('SELECT * FROM fine_payments WHERE chama_id=?',(chama_id,))
            elif table == 'ledger_transactions':
                rows = db.all('SELECT * FROM ledger_transactions WHERE chama_id=?',(chama_id,))
            elif table == 'meetings':
                rows = db.all('SELECT * FROM meetings WHERE chama_id=?',(chama_id,))
            elif table == 'attendance':
                rows = db.all('SELECT * FROM attendance WHERE chama_id=?',(chama_id,))
            elif table == 'announcements':
                rows = db.all('SELECT * FROM announcements WHERE chama_id=?',(chama_id,))
            elif table == 'notifications':
                rows = db.all('SELECT * FROM notifications WHERE chama_id=?',(chama_id,))
            elif table in ('chama_goals','chama_investments','chama_assets','chama_votes','chama_constitutions','meeting_actions','loan_guarantors','loan_installments','member_documents'):
                rows = db.all('SELECT * FROM ' + table + ' WHERE chama_id=?',(chama_id,))
            elif table == 'chama_vote_responses':
                rows = db.all('SELECT r.* FROM chama_vote_responses r JOIN chama_votes v ON v.id=r.vote_id WHERE v.chama_id=?',(chama_id,))
            else:
                rows=[]
            # Never export credentials or secret authentication fields.
            if table == 'member_documents':
                for r in rows:
                    r.pop('stored_name', None)
            out['tables'][table] = rows
        except Exception:
            out['tables'][table] = []
    return out


def _reconcile(db, chama_id):
    ledger_in = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='IN'",(chama_id,),0) or 0)
    ledger_out = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='OUT'",(chama_id,),0) or 0)
    contrib = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0) or 0)
    loan_repay = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM loan_repayments WHERE chama_id=? AND status='PAID'",(chama_id,),0) or 0)
    fines = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM fine_payments WHERE chama_id=? AND status='PAID'",(chama_id,),0) or 0)
    disbursed = int(db.val("SELECT COALESCE(SUM(principal_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','PAID')",(chama_id,),0) or 0)
    expected_in = contrib + loan_repay + fines
    issues=[]
    if ledger_in != expected_in:
        issues.append({'severity':'warning','title':'Money-in reconciliation','detail':f'Ledger IN is KES {ledger_in/100:,.2f}; source payments total KES {expected_in/100:,.2f}.'})
    if ledger_out < disbursed:
        issues.append({'severity':'warning','title':'Loan disbursement reconciliation','detail':f'Ledger OUT is KES {ledger_out/100:,.2f}; active/repaid loan principals total KES {disbursed/100:,.2f}.'})
    orphan = db.val("SELECT COUNT(*) FROM ledger_transactions l LEFT JOIN users u ON u.id=l.user_id WHERE l.chama_id=? AND l.user_id IS NOT NULL AND u.id IS NULL",(chama_id,),0)
    if orphan: issues.append({'severity':'danger','title':'Orphan ledger records','detail':f'{orphan} ledger entries reference a missing user.'})
    # Flag repeated external references within the same transaction family. This is a review
    # signal, not an automatic fraud verdict; legitimate split payments can share a reference.
    duplicate_specs = [
        ('contributions', 'Contribution reference'),
        ('loan_repayments', 'Loan repayment reference'),
        ('fine_payments', 'Fine payment reference'),
    ]
    for table, label in duplicate_specs:
        duplicates = db.all(f"SELECT MIN(reference) AS reference,amount_cents,COUNT(*) AS copies FROM {table} WHERE chama_id=? AND status='PAID' AND reference IS NOT NULL AND TRIM(reference)<>'' GROUP BY UPPER(TRIM(reference)),amount_cents HAVING COUNT(*)>1 ORDER BY copies DESC LIMIT 10",(chama_id,))
        if duplicates:
            examples = ', '.join(f"{r['reference']} ({r['copies']} records, KES {int(r['amount_cents'])/100:,.2f})" for r in duplicates[:3])
            issues.append({'severity':'warning','title':f'Possible duplicate {label.lower()}s','detail':f'{len(duplicates)} repeated reference/amount group(s) found. Review before marking resolved. Examples: {examples}.'})
    return {'ledger_in':ledger_in,'ledger_out':ledger_out,'balance':ledger_in-ledger_out,'contributions':contrib,'repayments':loan_repay,'fines':fines,'loan_disbursed':disbursed,'issues':issues}


def register_plus_routes(app, db, login_required, ctx, owner_required):
    # ---------- safe member-list migration ----------
    @app.route('/chamas/<int:chama_id>/members/import', methods=['GET', 'POST'])
    @login_required
    def member_import(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN',))
        raw_csv = ''
        preview = []
        results = None
        if request.method == 'POST':
            raw_csv = (request.form.get('csv_text') or '').strip()
            uploaded = request.files.get('csv_file')
            if uploaded and uploaded.filename:
                raw_bytes = uploaded.read(256 * 1024 + 1)
                if len(raw_bytes) > 256 * 1024:
                    flash('CSV must be smaller than 256 KB.', 'warning')
                    return render_template('member_import.html', chama=chama, me=me, preview=[], csv_text='')
                try:
                    raw_csv = raw_bytes.decode('utf-8-sig')
                except UnicodeDecodeError:
                    flash('Please save the CSV as UTF-8 and try again.', 'warning')
                    return render_template('member_import.html', chama=chama, me=me, preview=[], csv_text='')
            if not raw_csv or len(raw_csv.encode('utf-8')) > 256 * 1024:
                flash('Upload a CSV or paste CSV rows first. Maximum size is 256 KB.', 'warning')
            else:
                try:
                    reader = csv.DictReader(io.StringIO(raw_csv))
                    headers = {str(h or '').strip().lower() for h in (reader.fieldnames or [])}
                    if not {'name', 'phone'}.issubset(headers):
                        raise ValueError('CSV needs headers named name and phone. Optional header: role.')
                    for line, row in enumerate(reader, start=2):
                        if len(preview) >= 100:
                            raise ValueError('Import is limited to 100 rows at a time.')
                        clean = {str(k or '').strip().lower(): str(v or '').strip() for k, v in row.items()}
                        name, phone = clean.get('name', ''), clean.get('phone', '')
                        role = (clean.get('role') or 'MEMBER').upper().replace(' ', '_')
                        if not name and not phone:
                            continue
                        if len(name) < 2 or not phone:
                            raise ValueError(f'Row {line}: both name and phone are required.')
                        if role not in ('MEMBER', 'TREASURER', 'SECRETARY', 'AUDITOR'):
                            raise ValueError(f'Row {line}: role must be MEMBER, TREASURER, SECRETARY, or AUDITOR. Assign CHAMA_ADMIN manually after import.')
                        preview.append({'line': line, 'name': name[:80], 'phone': phone[:40], 'role': role})
                    if not preview:
                        raise ValueError('No member rows found in the CSV.')
                    if request.form.get('confirm_import') == '1':
                        results = {'added': [], 'errors': []}
                        for row in preview:
                            try:
                                uid, code = S.add_member_by_phone(db(), chama_id, row['phone'], row['name'], row['role'], g.user['id'], generate_password_hash)
                                results['added'].append({'name': row['name'], 'phone': row['phone'], 'role': row['role'], 'code': code, 'already_registered': not bool(code)})
                            except S.BusinessError as exc:
                                results['errors'].append({'name': row['name'], 'phone': row['phone'], 'error': str(exc)})
                        audit(db(), g.user['id'], 'MEMBER_CSV_IMPORT', 'chama', chama_id, chama_id, {'rows': len(preview), 'added': len(results['added']), 'errors': len(results['errors'])})
                        db().commit()
                        raw_csv = ''
                    else:
                        return render_template('member_import.html', chama=chama, me=me, preview=preview, csv_text=raw_csv, results=None)
                except (ValueError, csv.Error) as exc:
                    flash(str(exc), 'warning')
        return render_template('member_import.html', chama=chama, me=me, preview=preview, csv_text=raw_csv, results=results)

    @app.route('/chamas/<int:chama_id>/members/export.csv')
    @login_required
    def member_export_csv(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN', 'TREASURER', 'SECRETARY', 'AUDITOR'))
        rows = db().all("SELECT u.name,u.phone,m.role,m.status,m.joined_at FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? ORDER BY m.status,u.name", (chama_id,))
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['name', 'phone', 'role', 'status', 'joined_at'])
        for row in rows:
            writer.writerow([row.get('name') or '', row.get('phone') or '', row.get('role') or '', row.get('status') or '', row.get('joined_at') or ''])
        audit(db(), g.user['id'], 'MEMBER_LIST_EXPORTED', 'chama', chama_id, chama_id, {'rows': len(rows)})
        db().commit()
        return Response(out.getvalue(), mimetype='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="chamapro-members-{chama_id}.csv"', 'Cache-Control': 'no-store'})

    # ---------- private member dashboard ----------
    @app.route('/chamas/<int:chama_id>/my-dashboard')
    @login_required
    def my_chama_dashboard(chama_id):
        chama, me, sub = ctx(chama_id)
        st = F.member_statement(db(), chama_id, g.user['id'])
        uid=g.user['id']
        fine_paid=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM fine_payments WHERE chama_id=? AND user_id=? AND status='PAID'",(chama_id,uid),0) or 0)
        payouts_received=int(db().val("SELECT COALESCE(SUM(payout_cents),0) FROM member_exits WHERE chama_id=? AND user_id=? AND status='PAID'",(chama_id,uid),0) or 0)
        try:
            dividend_calc=X.dividend_calculation(db(),chama_id,date.today().year)
            dividend_estimate=next((r['dividend_cents'] for r in dividend_calc['rows'] if int(r['user_id'])==int(uid)),0)
        except Exception:
            dividend_estimate=None
        share_balance=st['saved']-st['loan_balance']-st['fine_balance']
        net_position=None if dividend_estimate is None else share_balance+dividend_estimate
        member_money={'fine_paid':fine_paid,'payouts_received':payouts_received,'dividend_estimate':dividend_estimate,
                      'share_balance':share_balance,'net_position':net_position}
        upcoming = db().all("SELECT * FROM meetings WHERE chama_id=? AND status='SCHEDULED' AND held_at>=? ORDER BY held_at LIMIT 3",(chama_id,_stamp()))
        votes = db().all("""SELECT v.*, (SELECT choice FROM chama_vote_responses r WHERE r.vote_id=v.id AND r.user_id=?) my_choice,
                          (SELECT COUNT(*) FROM chama_vote_responses r WHERE r.vote_id=v.id) votes
                          FROM chama_votes v WHERE v.chama_id=? AND v.status='OPEN' ORDER BY v.closes_at LIMIT 5""",(g.user['id'],chama_id))
        group_investments = int(db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_investments WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0)
        attention=[]
        period=date.today().isoformat()[:7]
        if not db().val("SELECT COUNT(*) FROM contributions WHERE chama_id=? AND user_id=? AND period=? AND status='PAID'",(chama_id,g.user['id'],period),0):
            contribution = int(db().val("SELECT contribution_cents FROM chamas WHERE id=?",(chama_id,),0) or 0)
            attention.append(('warning',f'Your {period} contribution is not recorded as paid yet.',url_for('savings',chama_id=chama_id), contribution))
        due_loan=db().one("SELECT id,total_due_cents-paid_cents balance,due_date FROM loans WHERE chama_id=? AND user_id=? AND status='ACTIVE' AND due_date IS NOT NULL ORDER BY due_date LIMIT 1",(chama_id,g.user['id']))
        if due_loan and due_loan['due_date'] <= (date.today()+timedelta(days=7)).isoformat():
            attention.append(('danger' if due_loan['due_date'] < date.today().isoformat() else 'info',f'Loan payment due {due_loan["due_date"]}: KES {due_loan["balance"]/100:,.2f} outstanding.',url_for('loans',chama_id=chama_id), due_loan['balance']))
        open_vote=db().one("SELECT v.id,v.title FROM chama_votes v WHERE v.chama_id=? AND v.status='OPEN' AND NOT EXISTS (SELECT 1 FROM chama_vote_responses r WHERE r.vote_id=v.id AND r.user_id=?) ORDER BY v.id DESC LIMIT 1",(chama_id,g.user['id']))
        if open_vote: attention.append(('info',f'New vote waiting: {open_vote["title"]}',url_for('chama_votes',chama_id=chama_id),0))
        attendance = db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND user_id=? AND status='PRESENT'",(chama_id,g.user['id']),0)
        total_att = db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND user_id=?",(chama_id,g.user['id']),0)
        streak = 0
        for row in db().all("SELECT period FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID' ORDER BY period DESC",(chama_id,g.user['id'])):
            if row['period'] == (date.today().replace(day=1) - timedelta(days=31*streak)).isoformat()[:7]: streak += 1
            elif streak == 0 and row['period'] == date.today().isoformat()[:7]: streak += 1
            else: break
        return render_template('my_dashboard.html',chama=chama,me=me,sub=sub,st=st,upcoming=upcoming,votes=votes,
                               group_investments=group_investments,attendance=attendance,total_att=total_att,streak=streak,attention=attention,member_money=member_money)

    # ---------- documents ----------
    @app.route('/chamas/<int:chama_id>/documents')
    @login_required
    def documents(chama_id):
        chama, me, sub = ctx(chama_id)
        rows = db().all("""SELECT d.*,u.name uploader, t.name subject_name FROM member_documents d
                         LEFT JOIN users u ON u.id=d.uploaded_by LEFT JOIN users t ON t.id=d.user_id
                         WHERE d.chama_id=? AND d.deleted_at IS NULL ORDER BY d.id DESC LIMIT 300""",(chama_id,))
        if me['role'] not in STAFF:
            rows=[r for r in rows if r['user_id'] in (None,me['user_id'])]
        members=db().all("SELECT u.id,u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name",(chama_id,))
        return render_template('documents.html',chama=chama,me=me,rows=rows,members=members,can_upload=me['role'] in STAFF)

    @app.route('/chamas/<int:chama_id>/documents/upload', methods=['POST'])
    @login_required
    def document_upload(chama_id):
        chama, me, sub = ctx(chama_id, roles=STAFF)
        f=request.files.get('document')
        if not f or not f.filename:
            flash('Choose a document first.','danger'); return redirect(url_for('documents',chama_id=chama_id))
        ext=Path(f.filename).suffix.lower().lstrip('.')
        if ext not in ALLOWED_DOCS: flash('Allowed files: PDF, PNG, JPG, WEBP, TXT or CSV.','danger'); return redirect(url_for('documents',chama_id=chama_id))
        data=f.read(MAX_DOC_BYTES+1)
        if len(data)>MAX_DOC_BYTES: flash('That document is larger than 8 MB.','danger'); return redirect(url_for('documents',chama_id=chama_id))
        original=secure_filename(f.filename)[:180] or 'document.'+ext
        stored=secrets.token_hex(16)+'-'+original
        root=_doc_root(app); (root/stored).write_bytes(data)
        uid=request.form.get('user_id',type=int)
        if uid: _member_or_404(db(),chama_id,uid)
        did=db().insert('member_documents',chama_id=chama_id,user_id=uid or None,uploaded_by=me['user_id'],entity_type=(request.form.get('entity_type') or 'CHAMA')[:30],
                        entity_id=request.form.get('entity_id',type=int),original_name=original,stored_name=stored,mime_type=f.mimetype or 'application/octet-stream',size_bytes=len(data),created_at=_stamp())
        audit(db(),me['user_id'],'DOCUMENT_UPLOADED','document',did,chama_id,{'name':original,'size':len(data)}); db().commit()
        flash('Document uploaded securely.','success'); return redirect(url_for('documents',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/documents/<int:doc_id>/download')
    @login_required
    def document_download(chama_id,doc_id):
        chama, me, sub = ctx(chama_id)
        doc=db().one("SELECT * FROM member_documents WHERE id=? AND chama_id=? AND deleted_at IS NULL",(doc_id,chama_id)) or abort(404)
        if not _can_view_doc(me,doc): abort(403)
        path=_doc_root(app)/doc['stored_name']
        if not path.exists(): abort(404)
        return send_file(path,download_name=doc['original_name'],mimetype=doc['mime_type'] or 'application/octet-stream',as_attachment=True)

    @app.route('/chamas/<int:chama_id>/documents/<int:doc_id>/delete', methods=['POST'])
    @login_required
    def document_delete(chama_id,doc_id):
        chama, me, sub = ctx(chama_id,roles=STAFF)
        doc=db().one('SELECT * FROM member_documents WHERE id=? AND chama_id=? AND deleted_at IS NULL',(doc_id,chama_id)) or abort(404)
        db().execute('UPDATE member_documents SET deleted_at=? WHERE id=?',(_stamp(),doc_id)); audit(db(),me['user_id'],'DOCUMENT_DELETED','document',doc_id,chama_id); db().commit()
        try: (_doc_root(app)/doc['stored_name']).unlink(missing_ok=True)
        except OSError: pass
        flash('Document removed.','success'); return redirect(url_for('documents',chama_id=chama_id))

    # ---------- loans: guarantors + schedules ----------
    @app.route('/chamas/<int:chama_id>/loans/<int:loan_id>/details')
    @login_required
    def loan_details(chama_id,loan_id):
        chama,me,sub=ctx(chama_id)
        loan=db().one("SELECT l.*,u.name,u.phone FROM loans l JOIN users u ON u.id=l.user_id WHERE l.id=? AND l.chama_id=?",(loan_id,chama_id)) or abort(404)
        if loan['user_id']!=g.user['id'] and me['role'] not in VIEW: abort(403)
        guarantors=db().all("SELECT g.*,u.name,u.phone FROM loan_guarantors g JOIN users u ON u.id=g.guarantor_user_id WHERE g.loan_id=? ORDER BY g.id",(loan_id,))
        installments=db().all('SELECT * FROM loan_installments WHERE loan_id=? ORDER BY installment_no',(loan_id,))
        members=db().all("SELECT u.id,u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' AND m.user_id<>? ORDER BY u.name",(chama_id,loan['user_id']))
        return render_template('loan_details.html',chama=chama,me=me,loan=loan,guarantors=guarantors,installments=installments,members=members,can_manage=me['role'] in FIN)

    @app.route('/chamas/<int:chama_id>/loans/<int:loan_id>/guarantor', methods=['POST'])
    @login_required
    def loan_guarantor_add(chama_id,loan_id):
        chama,me,sub=ctx(chama_id,roles=FIN)
        loan=db().one('SELECT * FROM loans WHERE id=? AND chama_id=?',(loan_id,chama_id)) or abort(404)
        uid=request.form.get('user_id',type=int); _member_or_404(db(),chama_id,uid)
        if uid==loan['user_id']: flash('The borrower cannot guarantee their own loan.','danger')
        else:
            try:
                gid=db().insert('loan_guarantors',loan_id=loan_id,chama_id=chama_id,guarantor_user_id=uid,status='PENDING',requested_at=_stamp(),note=(request.form.get('note') or '')[:300])
                audit(db(),me['user_id'],'LOAN_GUARANTOR_REQUESTED','loan_guarantor',gid,chama_id,{'loan_id':loan_id,'guarantor_user_id':uid}); db().commit();
                N.notify(db(),uid,chama_id,f'You were asked to guarantee a KES {loan["principal_cents"]/100:,.0f} loan.',f'/chamas/{chama_id}/loans/{loan_id}/details',me['user_id']); db().commit(); flash('Guarantor request sent.','success')
            except Exception: db().rollback(); flash('That member is already a guarantor for this loan.','warning')
        return redirect(url_for('loan_details',chama_id=chama_id,loan_id=loan_id))

    @app.route('/chamas/<int:chama_id>/loans/<int:loan_id>/guarantor/<int:gid>/<action>', methods=['POST'])
    @login_required
    def loan_guarantor_action(chama_id,loan_id,gid,action):
        chama,me,sub=ctx(chama_id)
        gnt=db().one('SELECT * FROM loan_guarantors WHERE id=? AND loan_id=? AND chama_id=?',(gid,loan_id,chama_id)) or abort(404)
        if gnt['guarantor_user_id']!=g.user['id'] and me['role'] not in FIN: abort(403)
        if action not in ('accept','decline'): abort(404)
        db().execute('UPDATE loan_guarantors SET status=?,responded_at=? WHERE id=?',('ACCEPTED' if action=='accept' else 'DECLINED',_stamp(),gid))
        audit(db(),me['user_id'],'LOAN_GUARANTOR_'+action.upper(),'loan_guarantor',gid,chama_id); db().commit()
        flash('Guarantor response recorded.','success'); return redirect(url_for('loan_details',chama_id=chama_id,loan_id=loan_id))

    # ---------- meeting action items ----------
    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/actions', methods=['POST'])
    @login_required
    def meeting_action_add(chama_id,mid):
        chama,me,sub=ctx(chama_id,roles=('CHAMA_ADMIN','SECRETARY'))
        db().one('SELECT id FROM meetings WHERE id=? AND chama_id=?',(mid,chama_id)) or abort(404)
        title=(request.form.get('title') or '').strip()
        if len(title)<3: flash('Give the action a clear title.','danger')
        else:
            uid=request.form.get('owner_user_id',type=int)
            if uid: _member_or_404(db(),chama_id,uid)
            aid=db().insert('meeting_actions',meeting_id=mid,chama_id=chama_id,title=title[:240],owner_user_id=uid or None,due_date=request.form.get('due_date') or None,status='OPEN',created_by=me['user_id'],created_at=_stamp())
            audit(db(),me['user_id'],'MEETING_ACTION_CREATED','meeting_action',aid,chama_id); db().commit(); flash('Action item added.','success')
        return redirect(url_for('meeting_view',chama_id=chama_id,mid=mid))

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/actions/<int:aid>/toggle', methods=['POST'])
    @login_required
    def meeting_action_toggle(chama_id,mid,aid):
        chama,me,sub=ctx(chama_id)
        row=db().one('SELECT * FROM meeting_actions WHERE id=? AND meeting_id=? AND chama_id=?',(aid,mid,chama_id)) or abort(404)
        if me['role'] not in ('CHAMA_ADMIN','SECRETARY') and row['owner_user_id']!=g.user['id']: abort(403)
        status='DONE' if row['status']!='DONE' else 'OPEN'
        db().execute('UPDATE meeting_actions SET status=?,completed_at=? WHERE id=?',(status,_stamp() if status=='DONE' else None,aid)); db().commit()
        return redirect(url_for('meeting_view',chama_id=chama_id,mid=mid))

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/actions/list')
    @login_required
    def meeting_actions(chama_id,mid):
        chama,me,sub=ctx(chama_id)
        db().one('SELECT id FROM meetings WHERE id=? AND chama_id=?',(mid,chama_id)) or abort(404)
        return jsonify(db().all("SELECT a.*,u.name owner FROM meeting_actions a LEFT JOIN users u ON u.id=a.owner_user_id WHERE a.meeting_id=? ORDER BY a.status,a.due_date,a.id",(mid,)))

    # ---------- all functions / More menu ----------
    @app.route('/chamas/<int:chama_id>/more')
    @login_required
    def chama_more(chama_id):
        chama, me, sub = ctx(chama_id)
        return render_template('chama_more.html', chama=chama, me=me, sub=sub)

    # ---------- notifications preferences ----------
    @app.route('/notification-settings', methods=['GET','POST'])
    @login_required
    def notification_settings():
        if request.method=='POST':
            vals={k:int(request.form.get(k)=='1') for k in ('in_app','email','sms','push')}
            vals['in_app']=1
            db().execute("INSERT INTO notification_preferences(user_id,in_app,email,sms,push,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET in_app=excluded.in_app,email=excluded.email,sms=excluded.sms,push=excluded.push,updated_at=excluded.updated_at",(g.user['id'],vals['in_app'],vals['email'],vals['sms'],vals['push'],_stamp()))
            db().commit(); flash('Notification preferences saved.','success')
        pref=db().one('SELECT * FROM notification_preferences WHERE user_id=?',(g.user['id'],)) or {'in_app':1,'email':1,'sms':0,'push':1}
        return render_template('notification_settings.html',pref=pref)

    # ---------- financial reconciliation ----------
    @app.route('/chamas/<int:chama_id>/financial-check')
    @login_required
    def financial_check(chama_id):
        chama,me,sub=ctx(chama_id,roles=VIEW)
        rep=_reconcile(db(),chama_id)
        return render_template('financial_check.html',chama=chama,me=me,rep=rep)

    # ---------- backups ----------
    @app.route('/chamas/<int:chama_id>/backup.json')
    @login_required
    def chama_backup(chama_id):
        chama,me,sub=ctx(chama_id,roles=('CHAMA_ADMIN',))
        payload=_safe_backup(db(),chama_id)
        return Response(json.dumps(payload,default=str,indent=2),mimetype='application/json',headers={'Content-Disposition':f'attachment; filename=chamapay-chama-{chama_id}-backup.json'})

    @app.route('/owner/backup.json')
    @owner_required
    def platform_backup():
        # Platform backup intentionally excludes users/passwords/secrets. It is a data archive, not a credential dump.
        payload={'format':'chamapay-platform-backup-v1','exported_at':_stamp(),'tables':{}}
        for table in ('chamas','subscriptions','subscription_plans','payments','payment_webhooks','audit_logs','support_tickets','organizations','organization_members','organization_chamas','privacy_requests'):
            try: payload['tables'][table]=db().all('SELECT * FROM '+table+' LIMIT 100000')
            except Exception: payload['tables'][table]=[]
        return Response(json.dumps(payload,default=str,indent=2),mimetype='application/json',headers={'Content-Disposition':'attachment; filename=chamapay-platform-backup.json'})

    # ---------- organizations ----------
    @app.route('/organizations', methods=['GET','POST'])
    @login_required
    def organizations():
        if request.method=='POST':
            name=(request.form.get('name') or '').strip()
            if len(name)<3: flash('Organization name is too short.','danger')
            else:
                with db().tx():
                    oid=db().insert('organizations',name=name[:120],created_by=g.user['id'],created_at=_stamp(),status='ACTIVE')
                    db().insert('organization_members',organization_id=oid,user_id=g.user['id'],role='ADMIN',status='ACTIVE',created_at=_stamp())
                    audit(db(),g.user['id'],'ORGANIZATION_CREATED','organization',oid)
                flash('Organization created.','success'); return redirect(url_for('organization_view',organization_id=oid))
        rows=db().all("SELECT o.*,om.role,(SELECT COUNT(*) FROM organization_chamas oc WHERE oc.organization_id=o.id) chamas FROM organizations o JOIN organization_members om ON om.organization_id=o.id WHERE om.user_id=? AND om.status='ACTIVE' ORDER BY o.id DESC",(g.user['id'],))
        return render_template('organizations.html',rows=rows)

    @app.route('/organizations/<int:organization_id>')
    @login_required
    def organization_view(organization_id):
        org=db().one("SELECT o.*,om.role FROM organizations o JOIN organization_members om ON om.organization_id=o.id WHERE o.id=? AND om.user_id=? AND om.status='ACTIVE'",(organization_id,g.user['id'])) or abort(403)
        chamas=db().all("SELECT c.id,c.name,c.created_by,(SELECT COUNT(*) FROM chama_members m WHERE m.chama_id=c.id AND m.status='ACTIVE') members,(SELECT s.status FROM subscriptions s WHERE s.chama_id=c.id) sub_status FROM organization_chamas oc JOIN chamas c ON c.id=oc.chama_id WHERE oc.organization_id=? ORDER BY c.name",(organization_id,))
        owned=db().all("SELECT c.id,c.name FROM chamas c JOIN chama_members m ON m.chama_id=c.id WHERE c.created_by=? AND m.status='ACTIVE' ORDER BY c.name",(g.user['id'],))
        return render_template('organization_view.html',org=org,chamas=chamas,owned=owned)

    @app.route('/organizations/<int:organization_id>/link',methods=['POST'])
    @login_required
    def organization_link(organization_id):
        org=db().one("SELECT o.*,om.role FROM organizations o JOIN organization_members om ON om.organization_id=o.id WHERE o.id=? AND om.user_id=? AND om.role='ADMIN' AND om.status='ACTIVE'",(organization_id,g.user['id'])) or abort(403)
        cid=request.form.get('chama_id',type=int)
        c=db().one('SELECT * FROM chamas WHERE id=? AND created_by=?',(cid,g.user['id'])) or abort(403)
        try:
            db().insert('organization_chamas',organization_id=organization_id,chama_id=cid,linked_by=g.user['id'],linked_at=_stamp()); audit(db(),g.user['id'],'ORGANIZATION_CHAMA_LINKED','organization_chama',cid,cid,{'organization_id':organization_id}); db().commit(); flash('Chama linked to the organization.','success')
        except Exception: db().rollback(); flash('That Chama is already linked.','warning')
        return redirect(url_for('organization_view',organization_id=organization_id))

    # ---------- richer report print view ----------
    @app.route('/chamas/<int:chama_id>/reports/print')
    @login_required
    def reports_print(chama_id):
        chama,me,sub=ctx(chama_id,roles=VIEW)
        period=request.args.get('period') or date.today().isoformat()[:7]
        rep=R.chama_report(db(),chama_id,period)
        recon=_reconcile(db(),chama_id)
        return render_template('report_print.html',chama=chama,me=me,r=rep,recon=recon,period=period)

    # ---------- legacy route aliases kept for older templates/deploys ----------
    @app.route('/chamas/<int:chama_id>/legacy/contribution-schedule', methods=['GET','POST'], endpoint='contribution_schedule')
    @login_required
    def contribution_schedule_alias(chama_id):
        ctx(chama_id, roles=STAFF)
        flash('Contribution schedules are managed from Savings in this version.', 'info')
        return redirect(url_for('savings', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/legacy/member-statement/<int:user_id>', endpoint='member_statement')
    @login_required
    def member_statement_alias(chama_id,user_id):
        return redirect(url_for('statement',chama_id=chama_id,user_id=user_id))

    @app.route('/chamas/<int:chama_id>/legacy/ledger', endpoint='chama_ledger')
    @login_required
    def chama_ledger_alias(chama_id):
        return redirect(url_for('ledger',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/legacy/finance/fine', methods=['POST'], endpoint='finance_fine')
    @login_required
    def finance_fine_alias(chama_id):
        return redirect(url_for('fine_add',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/legacy/finance/loan', methods=['POST'], endpoint='finance_loan')
    @login_required
    def finance_loan_alias(chama_id):
        return redirect(url_for('loan_apply',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/legacy/fine/<int:fine_id>/pay', methods=['POST'], endpoint='fine_pay')
    @login_required
    def fine_pay_alias(chama_id,fine_id):
        return redirect(url_for('fine_action',chama_id=chama_id,fid=fine_id,action='pay'))

    @app.route('/chamas/<int:chama_id>/legacy/loan/<int:loan_id>/repay', methods=['POST'], endpoint='loan_repay')
    @login_required
    def loan_repay_alias(chama_id,loan_id):
        return redirect(url_for('loan_action',chama_id=chama_id,lid=loan_id,action='repay'))

    @app.route('/chamas/<int:chama_id>/legacy/record-contribution', methods=['POST'], endpoint='record_contribution')
    @login_required
    def record_contribution_alias(chama_id):
        return redirect(url_for('savings_add',chama_id=chama_id))

    # ---------- stronger deterministic AI ----------
    @app.route('/chamas/<int:chama_id>/ai/ask',methods=['POST'])
    @login_required
    def ai_ask(chama_id):
        chama,me,sub=ctx(chama_id)
        q=(request.form.get('question') or '').strip()
        if not q: return redirect(url_for('chama_ai',chama_id=chama_id))
        ql=q.lower(); answer='I can answer questions from authorized recorded ChamaPay data. Try savings, contributions, unpaid members, loans, overdue loans, expenses, investments, assets, goals, attendance, meetings, members or financial position.'
        if ('unpaid' in ql or 'not paid' in ql) and ('member' in ql or 'contribution' in ql):
            period=date.today().isoformat()[:7]
            rows=db().all("""SELECT u.name FROM chama_members m JOIN users u ON u.id=m.user_id
                WHERE m.chama_id=? AND m.status='ACTIVE' AND NOT EXISTS
                (SELECT 1 FROM contributions c WHERE c.chama_id=m.chama_id AND c.user_id=m.user_id AND c.period=? AND c.status='PAID')
                ORDER BY u.name LIMIT 100""",(chama_id,period))
            answer=f'{len(rows)} active member(s) have no recorded paid contribution for {period}.' + (" Names: " + ', '.join(r['name'] for r in rows[:12]) + (" and more." if len(rows)>12 else '') if rows else '')
        elif 'saving' in ql or 'contribution' in ql:
            total=db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0); month=db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID' AND period=?",(chama_id,date.today().isoformat()[:7]),0); answer=f'Recorded savings are KES {total/100:,.2f} in total, with KES {month/100:,.2f} recorded for {date.today().isoformat()[:7]}. Voided entries are excluded.'
        elif 'overdue' in ql and 'loan' in ql:
            n=db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND status='ACTIVE' AND due_date IS NOT NULL AND due_date<?",(chama_id,date.today().isoformat()),0); amount=db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status='ACTIVE' AND due_date IS NOT NULL AND due_date<?",(chama_id,date.today().isoformat()),0); answer=f'{n} active loan(s) are past their recorded due date, with KES {amount/100:,.2f} still due across those loans.'
        elif 'loan' in ql and ('balance' in ql or 'outstanding' in ql):
            amount=db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED','PENDING')",(chama_id,),0); answer=f'The recorded outstanding loan obligation is KES {amount/100:,.2f}.'
        elif 'expense' in ql:
            n=db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='OUT' AND kind='EXPENSE'",(chama_id,),0); answer=f'Recorded expenses total KES {n/100:,.2f}.'
        elif 'investment' in ql:
            n=db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_investments WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0); answer=f'Active tracked investments have a current recorded value of KES {n/100:,.2f}.'
        elif 'asset' in ql:
            n=db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_assets WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0); answer=f'Active tracked assets have a current recorded value of KES {n/100:,.2f}.'
        elif 'goal' in ql:
            rows=db().all("SELECT name,current_cents,target_cents FROM chama_goals WHERE chama_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 10",(chama_id,)); answer='Active goals: ' + (', '.join(f"{r['name']} — KES {r['current_cents']/100:,.2f} of KES {r['target_cents']/100:,.2f}" for r in rows) if rows else 'none recorded.')
        elif 'member' in ql:
            n=db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0); answer=f'There are {n} active members in this Chama.'
        elif 'attendance' in ql:
            p=db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND status='PRESENT'",(chama_id,),0); t=db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=?",(chama_id,),0); answer=f'Recorded attendance is {p} present marks out of {t} attendance marks.'
        elif 'meeting' in ql:
            m=db().one("SELECT title,held_at FROM meetings WHERE chama_id=? AND status='SCHEDULED' AND held_at>=? ORDER BY held_at LIMIT 1",(chama_id,_stamp())); answer=f'The next scheduled meeting is {m["title"]} on {m["held_at"]}.' if m else 'There is no upcoming scheduled meeting recorded.'
        elif 'financial position' in ql or 'position' in ql:
            recon=_reconcile(db(),chama_id); inv=db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_investments WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0); assets=db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_assets WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0); answer=f'Ledger cash is KES {recon["balance"]/100:,.2f}; tracked investments and assets add KES {(inv+assets)/100:,.2f}. This is a recorded-data snapshot, not an accounting opinion.'
        return render_template('ai_answer.html',chama=chama,me=me,question=q,answer=answer)

    # ---------- owner support + analytics ----------
    @app.route('/owner/support')
    @owner_required
    def owner_support():
        rows=db().all("""SELECT t.*,u.name user_name,u.email,c.name chama FROM support_tickets t JOIN users u ON u.id=t.user_id LEFT JOIN chamas c ON c.id=t.chama_id ORDER BY CASE t.status WHEN 'OPEN' THEN 0 ELSE 1 END,t.id DESC LIMIT 300""")
        return render_template('owner_support.html',rows=rows)

    @app.route('/owner/support/<int:ticket_id>',methods=['POST'])
    @owner_required
    def owner_support_update(ticket_id):
        status=(request.form.get('status') or 'OPEN')[:20]
        if status not in ('OPEN','IN_PROGRESS','RESOLVED'): abort(400)
        db().execute('UPDATE support_tickets SET status=?,admin_note=?,updated_at=? WHERE id=?',(status,(request.form.get('admin_note') or '')[:2000],_stamp(),ticket_id)); db().commit(); flash('Support ticket updated.','success'); return redirect(url_for('owner_support'))

    @app.route('/owner/analytics')
    @owner_required
    def owner_analytics():
        return render_template('owner_analytics.html',
            users=db().val('SELECT COUNT(*) FROM users',(),0),members=db().val("SELECT COUNT(*) FROM chama_members WHERE status='ACTIVE'",(),0),
            chamas=db().val('SELECT COUNT(*) FROM chamas',(),0),active=db().val("SELECT COUNT(*) FROM subscriptions WHERE status IN ('TRIAL','ACTIVE','PAST_DUE','GRACE_PERIOD')",(),0),
            revenue=db().val("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE status='SUCCESS' AND applied=1 AND is_test_data=0",(),0),
            open_support=db().val("SELECT COUNT(*) FROM support_tickets WHERE status!='RESOLVED'",(),0),
            actions=db().val("SELECT COUNT(*) FROM meeting_actions WHERE status='OPEN'",(),0))
