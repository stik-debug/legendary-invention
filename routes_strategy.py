"""Strategy, decision support and continuity tools. All financial outputs are estimates or read-only snapshots."""
import base64, hashlib, hmac, io, json, secrets, zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from flask import abort, flash, g, make_response, redirect, render_template, request, send_file, url_for
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
import textwrap
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
import textwrap
import services as S
from db import audit, IntegrityError

STAFF = ('CHAMA_ADMIN','TREASURER','SECRETARY','AUDITOR')
WRITERS = ('CHAMA_ADMIN','TREASURER','SECRETARY')

def _money_cents(value, default=0):
    try:
        d=Decimal(str(value if value is not None else default))
        if not d.is_finite() or d < 0: raise InvalidOperation
        return int((d*100).quantize(Decimal('1')))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Enter valid non-negative money amounts.')

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

def _scenario(db, cid, assumptions):
    # Ledger totals use posted entries only. This is a planning estimate, not a cash guarantee.
    today=date.today(); start=(today-timedelta(days=90)).isoformat(); end=(today+timedelta(days=1)).isoformat()
    cash=int(db.val("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0) FROM ledger_transactions WHERE chama_id=?",(cid,),0) or 0)
    recent=db.all("SELECT direction,amount_cents FROM ledger_transactions WHERE chama_id=? AND occurred_on>=? AND occurred_on<? AND direction IN ('IN','OUT')",(cid,start,end))
    other_inflow=sum(int(r['amount_cents']) for r in db.all("SELECT amount_cents FROM ledger_transactions WHERE chama_id=? AND occurred_on>=? AND occurred_on<? AND direction='IN' AND COALESCE(ref_type,'')!='contribution'",(cid,start,end)))
    contribution_total=int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<?",(cid,start,end),0) or 0)
    monthly_contributions=int(contribution_total/3); monthly_other_in=int(other_inflow/3)
    outflow=sum(int(r['amount_cents']) for r in recent if r['direction']=='OUT')
    monthly_out=int(outflow/3)
    outstanding=int(db.val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(cid,),0) or 0)
    contribution_factor=Decimal(100-int(assumptions['contribution_drop_pct']))/Decimal(100)
    default_factor=Decimal(assumptions['default_pct'])/Decimal(100)
    projected_in=monthly_other_in+int(Decimal(monthly_contributions)*contribution_factor)
    risk_loss=int(Decimal(outstanding)*default_factor)
    projected_cash=cash+projected_in-monthly_out-int(assumptions['planned_payout_cents'])-int(assumptions['new_loan_cents'])-risk_loss
    result={'current_ledger_balance_cents':cash,'average_monthly_contributions_cents':monthly_contributions,'average_monthly_other_inflow_cents':monthly_other_in,'average_monthly_outflow_cents':monthly_out,
      'projected_monthly_inflow_cents':projected_in,'outstanding_loan_exposure_cents':outstanding,'stress_default_loss_cents':risk_loss,
      'planned_payout_cents':int(assumptions['planned_payout_cents']),'proposed_loan_cents':int(assumptions['new_loan_cents']),
      'estimated_30_day_balance_cents':projected_cash,'estimated_only':True,
      'warning':'Planning estimate based on the last 90 days of paid contributions and posted ledger entries. It assumes contributions change by the selected percentage; it excludes unrecorded cash, uncertain repayments, future costs and timing differences.'}
    return result

def _fairness_flags(db,cid):
    flags=[]
    dups=db.all("SELECT user_id,period,COUNT(*) n,SUM(amount_cents) total FROM contributions WHERE chama_id=? AND status='PAID' GROUP BY user_id,period HAVING COUNT(*)>1 ORDER BY n DESC LIMIT 30",(cid,))
    for r in dups:
        flags.append({'level':'Review','title':'Multiple paid contribution entries in one period','detail':f"Member ID {r['user_id']} has {r['n']} paid entries for {r['period']}. This may be legitimate split payments; verify references before taking action."})
    loan_total=int(db.val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(cid,),0) or 0)
    if loan_total:
        top=db.one("SELECT user_id,SUM(total_due_cents-paid_cents) exposure FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL GROUP BY user_id ORDER BY exposure DESC LIMIT 1",(cid,))
        if top and int(top['exposure'] or 0)*100 >= loan_total*40:
            member=db.one('SELECT name FROM users WHERE id=?',(top['user_id'],))
            pct=round(int(top['exposure'])*100/loan_total)
            flags.append({'level':'Review','title':'Borrowing concentration is high','detail':f"One member accounts for about {pct}% of the chama's outstanding loan exposure. Review the loan policy and guarantees; this is a risk indicator, not a misconduct finding."})
    fine_variance=db.all("SELECT LOWER(TRIM(reason)) reason_key,COUNT(*) n,MIN(amount_cents) lo,MAX(amount_cents) hi FROM fines WHERE chama_id=? AND status NOT IN ('CANCELLED','WAIVED') GROUP BY LOWER(TRIM(reason)) HAVING COUNT(*)>=2 AND MAX(amount_cents)>MIN(amount_cents)*1.25 ORDER BY n DESC LIMIT 20",(cid,))
    for r in fine_variance:
        flags.append({'level':'Review','title':'Similar fine reasons have different amounts','detail':f"The recorded fine amounts for '{r['reason_key'] or 'unspecified reason'}' vary by more than 25%. Check whether an approved rule explains the difference."})
    if not flags:
        flags.append({'level':'Clear','title':'No current rule-based flags','detail':'The checks in this page did not find the patterns they test. This is not a guarantee that all records are correct or fair.'})
    return flags

def register_strategy_routes(app, db, ctx, login_required):
    @app.route('/chamas/<int:chama_id>/strategy', methods=['GET','POST'])
    @login_required
    def chama_strategy(chama_id):
        chama,me,sub=ctx(chama_id)
        simulation=None; saved=False
        if request.method=='POST':
            if me['role'] not in WRITERS: abort(403)
            try:
                drop=int(request.form.get('contribution_drop_pct','0'))
                default_pct=int(request.form.get('default_pct','0'))
                if not 0<=drop<=100 or not 0<=default_pct<=100: raise ValueError('Percentages must be between 0 and 100.')
                assumptions={'contribution_drop_pct':drop,'default_pct':default_pct,
                    'planned_payout_cents':_money_cents(request.form.get('planned_payout','0')),
                    'new_loan_cents':_money_cents(request.form.get('new_loan','0'))}
                simulation=_scenario(db(),chama_id,assumptions)
                if request.form.get('save_scenario')=='1':
                    name=(request.form.get('scenario_name') or '').strip()[:120]
                    if len(name)<3: raise ValueError('Give the scenario a name of at least 3 characters.')
                    db().begin()
                    sid=db().insert('chama_scenarios',chama_id=chama_id,name=name,assumptions_json=_json(assumptions),results_json=_json(simulation),created_by=me['user_id'],created_at=S.iso(S.now_utc()))
                    audit(db(),me['user_id'],'SCENARIO_SAVED','scenario',sid,chama_id,{'name':name}); db().commit(); saved=True
            except (ValueError,InvalidOperation) as e:
                db().rollback(); flash(str(e) or 'Please check the scenario inputs.','danger')
            except Exception:
                db().rollback(); flash('Scenario could not be saved. No financial records were changed.','danger')
        saved_rows=db().all('SELECT s.*,u.name creator FROM chama_scenarios s LEFT JOIN users u ON u.id=s.created_by WHERE s.chama_id=? ORDER BY s.id DESC LIMIT 20',(chama_id,)) if me['role'] in STAFF else []
        for row in saved_rows:
            row['assumptions_obj']=json.loads(row['assumptions_json'] or '{}'); row['results_obj']=json.loads(row['results_json'] or '{}')
        return render_template('strategy.html',chama=chama,me=me,simulation=simulation,saved=saved,saved_rows=saved_rows,flags=_fairness_flags(db(),chama_id))

    @app.route('/chamas/<int:chama_id>/promises', methods=['GET','POST'])
    @login_required
    def chama_promises(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            if me['role'] not in WRITERS: abort(403)
            title=(request.form.get('title') or '').strip(); mid=request.form.get('meeting_id',type=int); owner=request.form.get('owner_user_id',type=int)
            meeting=db().one('SELECT id FROM meetings WHERE id=? AND chama_id=?',(mid,chama_id)) if mid else None
            if len(title)<3 or not meeting:
                flash('Choose a meeting and enter a clear commitment.','danger')
            else:
                if owner and not db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'",(chama_id,owner),0): abort(400)
                due=(request.form.get('due_date') or '').strip() or None
                db().begin()
                aid=db().insert('meeting_actions',meeting_id=mid,chama_id=chama_id,title=title[:240],owner_user_id=owner or None,due_date=due,status='OPEN',created_by=me['user_id'],created_at=S.iso(S.now_utc()))
                audit(db(),me['user_id'],'PROMISE_TRACKER_CREATED','meeting_action',aid,chama_id,{'meeting_id':mid,'owner_user_id':owner,'due_date':due}); db().commit()
                flash('Commitment recorded and assigned.','success'); return redirect(url_for('chama_promises',chama_id=chama_id))
        rows=db().all("SELECT a.*,u.name owner,m.title meeting_title,m.held_at FROM meeting_actions a LEFT JOIN users u ON u.id=a.owner_user_id JOIN meetings m ON m.id=a.meeting_id WHERE a.chama_id=? ORDER BY CASE WHEN a.status='DONE' THEN 1 ELSE 0 END, a.due_date,a.id DESC LIMIT 200",(chama_id,))
        for r in rows: r['overdue']=bool(r.get('due_date') and r['due_date']<date.today().isoformat() and r['status']!='DONE')
        meetings=db().all('SELECT id,title,held_at FROM meetings WHERE chama_id=? ORDER BY held_at DESC LIMIT 100',(chama_id,)) if me['role'] in WRITERS else []
        members=db().all("SELECT u.id,u.name FROM users u JOIN chama_members m ON m.user_id=u.id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name",(chama_id,)) if me['role'] in WRITERS else []
        return render_template('promises.html',chama=chama,me=me,rows=rows,meetings=meetings,members=members)

    @app.route('/chamas/<int:chama_id>/promises/<int:action_id>/toggle',methods=['POST'])
    @login_required
    def chama_promise_toggle(chama_id,action_id):
        chama,me,sub=ctx(chama_id)
        row=db().one('SELECT * FROM meeting_actions WHERE id=? AND chama_id=?',(action_id,chama_id)) or abort(404)
        if me['role'] not in WRITERS and row['owner_user_id']!=me['user_id']: abort(403)
        status='DONE' if row['status']!='DONE' else 'OPEN'
        db().begin()
        db().execute('UPDATE meeting_actions SET status=?,completed_at=? WHERE id=?',(status,S.iso(S.now_utc()) if status=='DONE' else None,action_id))
        audit(db(),me['user_id'],'PROMISE_STATUS_CHANGED','meeting_action',action_id,chama_id,{'status':status}); db().commit()
        return redirect(url_for('chama_promises',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/handover',methods=['GET','POST'])
    @login_required
    def chama_handover(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            action=(request.form.get('action') or '').strip()
            if action=='propose':
                if me['role'] not in ('CHAMA_ADMIN','TREASURER'): abort(403)
                successor=request.form.get('successor_id',type=int)
                target=db().one("SELECT user_id FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'",(chama_id,successor)) if successor else None
                if not target or successor==me['user_id']:
                    flash('Choose another active member as the proposed successor.','danger')
                elif db().val("SELECT COUNT(*) FROM chama_handovers WHERE chama_id=? AND status='PENDING_ACCEPTANCE'",(chama_id,),0):
                    flash('A handover is already awaiting acceptance. Resolve it before starting another.','warning')
                else:
                    core={'chama_id':chama_id,'created_at':S.iso(S.now_utc()),
                      'active_members':int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0),
                      'paid_contributions_cents':int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0) or 0),
                      'outstanding_loans_cents':int(db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(chama_id,),0) or 0),
                      'open_actions':int(db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN'",(chama_id,),0) or 0)}
                    digest=hashlib.sha256(_json(core).encode('utf-8')).hexdigest()
                    db().begin()
                    try:
                        hid=db().insert('chama_handovers',chama_id=chama_id,initiated_by=me['user_id'],proposed_successor=successor,snapshot_sha256=digest,
                          notes=(request.form.get('notes') or '').strip()[:1000] or None,status='PENDING_ACCEPTANCE',created_at=S.iso(S.now_utc()))
                    except IntegrityError:
                        db().rollback(); flash('A handover was started by another official. Review the pending handover instead.','warning')
                        return redirect(url_for('chama_handover',chama_id=chama_id))
                    audit(db(),me['user_id'],'HANDOVER_PROPOSED','handover',hid,chama_id,{'successor_id':successor,'snapshot_sha256':digest}); db().commit()
                    flash('Handover proposed. The successor must review and accept; this does not automatically change their role.','success')
                    return redirect(url_for('chama_handover',chama_id=chama_id))
            elif action=='accept':
                hid=request.form.get('handover_id',type=int)
                hand=db().one("SELECT * FROM chama_handovers WHERE id=? AND chama_id=? AND proposed_successor=? AND status='PENDING_ACCEPTANCE'",(hid,chama_id,me['user_id']))
                if not hand: abort(403)
                db().begin()
                db().execute("UPDATE chama_handovers SET status='ACCEPTED',accepted_at=? WHERE id=? AND status='PENDING_ACCEPTANCE'",(S.iso(S.now_utc()),hid))
                audit(db(),me['user_id'],'HANDOVER_ACCEPTED','handover',hid,chama_id,{'snapshot_sha256':hand['snapshot_sha256']}); db().commit()
                flash('Handover receipt accepted. This acknowledgement does not transfer permissions or certify every financial entry.','success')
                return redirect(url_for('chama_handover',chama_id=chama_id))
        if me['role'] in STAFF:
            rows=db().all("SELECT h.*,a.name initiator,s.name successor FROM chama_handovers h JOIN users a ON a.id=h.initiated_by JOIN users s ON s.id=h.proposed_successor WHERE h.chama_id=? ORDER BY h.id DESC LIMIT 30",(chama_id,))
        else:
            rows=db().all("SELECT h.*,a.name initiator,s.name successor FROM chama_handovers h JOIN users a ON a.id=h.initiated_by JOIN users s ON s.id=h.proposed_successor WHERE h.chama_id=? AND (h.proposed_successor=? OR h.initiated_by=?) ORDER BY h.id DESC LIMIT 30",(chama_id,me['user_id'],me['user_id']))
        members=db().all("SELECT u.id,u.name,m.role FROM users u JOIN chama_members m ON m.user_id=u.id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name",(chama_id,)) if me['role'] in ('CHAMA_ADMIN','TREASURER') else []
        can_export=me['role'] in STAFF or bool(db().val("SELECT COUNT(*) FROM chama_handovers WHERE chama_id=? AND proposed_successor=? AND status='PENDING_ACCEPTANCE'",(chama_id,me['user_id']),0))
        return render_template('handover.html',chama=chama,me=me,rows=rows,members=members,can_export=can_export)

    @app.route('/chamas/<int:chama_id>/financial-audit.pdf')
    @login_required
    def monthly_financial_audit_pdf(chama_id):
        chama,me,sub=ctx(chama_id)
        if me['role'] not in STAFF: abort(403)
        period=(request.args.get('period') or date.today().strftime('%Y-%m')).strip()
        try:
            month=date.fromisoformat(period+'-01')
            if month.strftime('%Y-%m') != period: raise ValueError
        except ValueError: abort(400)
        next_month=date(month.year+1,1,1) if month.month==12 else date(month.year,month.month+1,1)
        start=month.isoformat(); end=next_month.isoformat()
        # Reconciles records already in ChamaPay; external bank/M-Pesa statements are not independently verified here.
        contrib=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<?",(chama_id,start,end),0) or 0)
        repayments=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM loan_repayments WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<?",(chama_id,start,end),0) or 0)
        fine_paid=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM fine_payments WHERE chama_id=? AND status='PAID' AND voided_at IS NULL AND paid_on>=? AND paid_on<?",(chama_id,start,end),0) or 0)
        ledger_in=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='IN' AND occurred_on>=? AND occurred_on<?",(chama_id,start,end),0) or 0)
        ledger_out=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='OUT' AND occurred_on>=? AND occurred_on<?",(chama_id,start,end),0) or 0)
        source_in=contrib+repayments+fine_paid
        duplicates=db().all("SELECT MIN(reference) reference,amount_cents,COUNT(*) copies FROM contributions WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<? AND reference IS NOT NULL AND TRIM(reference)<>'' GROUP BY UPPER(TRIM(reference)),amount_cents HAVING COUNT(*)>1 ORDER BY copies DESC LIMIT 20",(chama_id,start,end))
        snapshot={'report_type':'monthly_chamapay_reconciliation','chama_id':chama_id,'chama_name':chama['name'],'period':period,'generated_at':S.iso(S.now_utc()),'source':'ChamaPay records only; external statements are not independently verified by this report','contributions_cents':contrib,'loan_repayments_cents':repayments,'fine_payments_cents':fine_paid,'source_payments_total_cents':source_in,'ledger_in_cents':ledger_in,'ledger_out_cents':ledger_out,'inflow_difference_cents':ledger_in-source_in,'possible_duplicate_contribution_references':[dict(r) for r in duplicates]}
        snapshot_json=_json(snapshot); digest=hashlib.sha256(snapshot_json.encode('utf-8')).hexdigest(); ref='CPAUD-'+period.replace('-','')+'-'+secrets.token_hex(4).upper()
        db().begin(); db().insert('chama_financial_passports',chama_id=chama_id,report_ref=ref,snapshot_json=snapshot_json,snapshot_sha256=digest,created_by=me['user_id'],created_at=snapshot['generated_at'])
        signature=hmac.new(str(app.config['SECRET_KEY']).encode('utf-8'),f'{ref}.{digest}'.encode('utf-8'),hashlib.sha256).hexdigest()
        token=base64.urlsafe_b64encode(f'{ref}.{digest}.{signature}'.encode('utf-8')).decode('ascii').rstrip('=')
        verify_url=request.url_root.rstrip('/')+url_for('verify_financial_passport',token=token)
        audit(db(),me['user_id'],'MONTHLY_FINANCIAL_AUDIT_EXPORTED','financial_audit',ref,chama_id,{'period':period,'sha256':digest}); db().commit()
        buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=A4); _,h=A4; y=h-50; c.setTitle('ChamaPay Monthly Reconciliation Report')
        lines=['ChamaPay Monthly Reconciliation Report',f'Group: {chama["name"]}',f'Period: {period}',f'Generated: {snapshot["generated_at"]}',f'Report reference: {ref}',f'SHA-256: {digest}','',f'Paid contributions: KES {contrib/100:,.2f}',f'Paid loan repayments: KES {repayments/100:,.2f}',f'Paid fines: KES {fine_paid/100:,.2f}',f'Source payment total: KES {source_in/100:,.2f}',f'Ledger money in: KES {ledger_in/100:,.2f}',f'Ledger money out: KES {ledger_out/100:,.2f}',f'Inflow difference (ledger minus sources): KES {(ledger_in-source_in)/100:,.2f}',f'Possible duplicate contribution reference groups: {len(duplicates)}','', 'Integrity verification:', verify_url, '', 'Important limits:', 'This is a tamper-evident snapshot of ChamaPay records, not an independent audit or proof of bank/M-Pesa balances.', 'Compare against the original bank or provider statement and investigate differences before approving accounts.', 'Repeated references are review flags, not proof of fraud.']
        for line in lines:
            wrapped=textwrap.wrap(str(line),width=96,break_long_words=True,break_on_hyphens=False) or ['']
            for part in wrapped:
                if y<55: c.showPage(); y=h-50
                c.setFont('Helvetica-Bold' if line==lines[0] else 'Helvetica',14 if line==lines[0] else 9)
                c.drawString(45,y,part); y-=20 if line==lines[0] else 13
        c.save(); buf.seek(0)
        return send_file(buf,mimetype='application/pdf',as_attachment=True,download_name=f'chamapay-monthly-audit-{chama_id}-{period}.pdf')

    @app.route('/verify/financial-passport/<token>')
    def verify_financial_passport(token):
        try:
            raw=base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)).decode('utf-8')
            report_ref,digest,signature=raw.split('.',2)
        except Exception:
            abort(404)
        expected=hmac.new(str(app.config['SECRET_KEY']).encode('utf-8'),f'{report_ref}.{digest}'.encode('utf-8'),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected): abort(404)
        row=db().one('SELECT p.*,c.name chama_name FROM chama_financial_passports p JOIN chamas c ON c.id=p.chama_id WHERE p.report_ref=? AND p.snapshot_sha256=?',(report_ref,digest))
        if not row or hashlib.sha256(row['snapshot_json'].encode('utf-8')).hexdigest()!=digest: abort(404)
        snapshot=json.loads(row['snapshot_json'])
        return render_template('verify_passport.html',valid=True,ref=report_ref,digest=digest,snapshot=snapshot,chama_name=row['chama_name'],created_at=row['created_at'])

    @app.route('/chamas/<int:chama_id>/financial-passport.pdf')
    @login_required
    def chama_financial_passport(chama_id):
        chama,me,sub=ctx(chama_id,roles=STAFF)
        members=int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0)
        savings=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0) or 0)
        loans=int(db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(chama_id,),0) or 0)
        assets=int(db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_assets WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0)
        investments=int(db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_investments WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0)
        cash=int(db().val("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0) FROM ledger_transactions WHERE chama_id=?",(chama_id,),0) or 0)
        audits=int(db().val('SELECT COUNT(*) FROM audit_logs WHERE chama_id=?',(chama_id,),0) or 0)
        open_actions=int(db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN'",(chama_id,),0) or 0)
        generated=S.iso(S.now_utc())
        snapshot={'chama_name':chama['name'],'generated_at':generated,'active_members':members,'paid_contributions_lifetime_cents':savings,
          'outstanding_loan_balance_cents':loans,'ledger_balance_snapshot_cents':cash,'tracked_assets_cents':assets,
          'tracked_investments_cents':investments,'audit_events_recorded':audits,'open_meeting_commitments':open_actions}
        snapshot_json=_json(snapshot); digest=hashlib.sha256(snapshot_json.encode('utf-8')).hexdigest(); passport_id='CPFP-'+secrets.token_hex(6).upper()
        db().begin()
        db().insert('chama_financial_passports',chama_id=chama_id,report_ref=passport_id,snapshot_json=snapshot_json,snapshot_sha256=digest,created_by=me['user_id'],created_at=generated)
        signature=hmac.new(str(app.config['SECRET_KEY']).encode('utf-8'),f'{passport_id}.{digest}'.encode('utf-8'),hashlib.sha256).hexdigest()
        token=base64.urlsafe_b64encode(f'{passport_id}.{digest}.{signature}'.encode('utf-8')).decode('ascii').rstrip('=')
        verify_url=request.url_root.rstrip('/')+url_for('verify_financial_passport',token=token)
        lines=['ChamaPay Financial Passport',f'Chama: {chama["name"]}',f'Generated: {generated}',f'Report reference: {passport_id}',f'Snapshot SHA-256: {digest}',f'Verification URL: {verify_url}','',f'Active members: {members}',f'Recorded paid contributions lifetime: KES {savings/100:,.2f}',f'Outstanding approved/active loan balance: KES {loans/100:,.2f}',f'Ledger balance snapshot: KES {cash/100:,.2f}',f'Tracked assets: KES {assets/100:,.2f}',f'Tracked investments: KES {investments/100:,.2f}',f'Audit events recorded: {audits}',f'Open meeting commitments: {open_actions}','', 'Interpretation and limits:', 'The verification URL confirms this report snapshot was issued by this ChamaPay installation and has not been altered. It is not a credit rating, bank statement, independent audit, guarantee of solvency or lender approval.', 'Figures may be incomplete if records have not been reconciled. The recipient should verify source documents and current balances.']
        buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=A4); w,h=A4; y=h-52; c.setTitle('ChamaPay Financial Passport')
        expanded=[]
        for line in lines:
            expanded.extend(textwrap.wrap(str(line),width=96,break_long_words=True,break_on_hyphens=False) or [''])
        for i,line in enumerate(expanded):
            if y<110: c.showPage(); y=h-52
            c.setFont('Helvetica-Bold' if i==0 else 'Helvetica', 14 if i==0 else 9)
            c.drawString(48,y,line); y-=20 if i==0 else 13
        qr=QrCodeWidget(verify_url); bounds=qr.getBounds(); qw=bounds[2]-bounds[0]; qh=bounds[3]-bounds[1]
        drawing=Drawing(84,84,transform=[84/max(qw,1),0,0,84/max(qh,1),0,0]); drawing.add(qr)
        if y<100: c.showPage(); y=h-80
        renderPDF.draw(drawing,c,48,max(25,y-84)); c.setFont('Helvetica',9); c.drawString(145,max(55,y-45),'Scan QR code to verify report origin and integrity.')
        c.save(); buf.seek(0)
        audit(db(),me['user_id'],'FINANCIAL_PASSPORT_EXPORTED','financial_passport',passport_id,chama_id,{'members':members})
        db().commit()
        return send_file(buf,mimetype='application/pdf',as_attachment=True,download_name=f'chamapay-financial-passport-{chama_id}.pdf')

    @app.route('/chamas/<int:chama_id>/continuity-vault.zip')
    @login_required
    def chama_continuity_vault(chama_id):
        chama,me,sub=ctx(chama_id)
        if me['role'] not in ('CHAMA_ADMIN','TREASURER','AUDITOR') and not db().val("SELECT COUNT(*) FROM chama_handovers WHERE chama_id=? AND proposed_successor=? AND status='PENDING_ACCEPTANCE'",(chama_id,me['user_id']),0):
            abort(403)
        snapshot={
          'format':'ChamaPay continuity snapshot v1','generated_at':S.iso(S.now_utc()),
          'chama':{'id':chama['id'],'name':chama['name'],'created_at':chama.get('created_at')},
          'active_members':db().all("SELECT u.name,m.role,m.joined_at FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name",(chama_id,)),
          'financial_totals':{'paid_contributions_cents':int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0) or 0),'ledger_balance_cents':int(db().val("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0) FROM ledger_transactions WHERE chama_id=?",(chama_id,),0) or 0),'outstanding_loans_cents':int(db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(chama_id,),0) or 0)},
          'loans':db().all("SELECT u.name member,l.principal_cents,l.total_due_cents,l.paid_cents,l.status,l.due_date FROM loans l JOIN users u ON u.id=l.user_id WHERE l.chama_id=? AND l.status IN ('PENDING','APPROVED','ACTIVE') ORDER BY l.due_date,l.id",(chama_id,)),
          'constitution':db().one('SELECT monthly_contribution_cents,joining_fee_cents,loan_interest_bps,loan_multiplier,loan_months,late_fine_cents,contribution_due_day,attendance_requirement,voting_requirement,updated_at FROM chama_constitutions WHERE chama_id=?',(chama_id,)),
          'constitution_history':db().all('SELECT version_no,snapshot_json,changed_at FROM chama_constitution_history WHERE chama_id=? ORDER BY version_no',(chama_id,)),
          'goals':db().all('SELECT name,description,target_cents,current_cents,status,deadline FROM chama_goals WHERE chama_id=? ORDER BY id',(chama_id,)),
          'assets':db().all('SELECT name,category,purchase_price_cents,current_value_cents,purchase_date,status FROM chama_assets WHERE chama_id=? ORDER BY id',(chama_id,)),
          'investments':db().all('SELECT name,type,purchase_price_cents,current_value_cents,income_cents,purchase_date,status FROM chama_investments WHERE chama_id=? ORDER BY id',(chama_id,)),
          'open_commitments':db().all("SELECT a.title,u.name owner,a.due_date,a.status,m.title meeting FROM meeting_actions a LEFT JOIN users u ON u.id=a.owner_user_id JOIN meetings m ON m.id=a.meeting_id WHERE a.chama_id=? AND a.status!='DONE' ORDER BY a.due_date,a.id",(chama_id,)),
          'recent_audit_events':db().all('SELECT action,entity_type,entity_id,metadata,created_at FROM audit_logs WHERE chama_id=? ORDER BY id DESC LIMIT 500',(chama_id,))}
        raw=_json(snapshot).encode('utf-8'); digest=hashlib.sha256(raw).hexdigest()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('continuity-snapshot.json',raw)
            z.writestr('README.txt','ChamaPay continuity snapshot\nRead-only export. It does not restore itself automatically. Verify records against source documents before importing into another system. No passwords, OTPs or session tokens are included.\nSHA-256: '+digest+'\n')
        out.seek(0); db().begin(); audit(db(),me['user_id'],'CONTINUITY_VAULT_EXPORTED','continuity_vault',chama_id,chama_id,{'sha256':digest,'bytes':len(raw)}); db().commit()
        return send_file(out,mimetype='application/zip',as_attachment=True,download_name=f'chamapay-continuity-{chama_id}-{date.today().isoformat()}.zip')
