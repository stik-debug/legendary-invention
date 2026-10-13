"""Trust workflows: reconciliation, member obligations, reminders and checked financial-history imports."""
import csv, io, json, hashlib
from datetime import date, datetime
from flask import abort, flash, g, redirect, render_template, request, session, url_for
import finance as F
import services as S

FIN = F.FINANCE_ROLES

def register(app, db, ctx, login_required):
    @app.route('/chamas/<int:chama_id>/trust')
    @login_required
    def trust_center(chama_id):
        chama, me, sub = ctx(chama_id, roles=FIN)
        period = date.today().isoformat()[:7]
        members = F.month_status(db(), chama_id, period)
        ledger_in = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='IN'", (chama_id,), 0) or 0)
        ledger_out = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND direction='OUT'", (chama_id,), 0) or 0)
        ledger_balance = ledger_in-ledger_out
        checks=[]
        # Source-to-ledger integrity for records whose ledger references are explicit.
        for table, ref_type, label, status_clause in [
            ('contributions','contribution','Contributions',"status='PAID'"),
            ('loan_repayments','loan_repayment','Loan repayments',"status='PAID'"),
            ('fine_payments','fine_payment','Fine payments',"status='PAID'"),
        ]:
            rows=db().all(f"SELECT id,amount_cents FROM {table} WHERE chama_id=? AND {status_clause}",(chama_id,))
            missing=[]; mismatched=[]
            for row in rows:
                ledger=db().one("SELECT amount_cents FROM ledger_transactions WHERE chama_id=? AND ref_type=? AND ref_id=? AND direction='IN' ORDER BY id LIMIT 1",(chama_id,ref_type,row['id']))
                if not ledger: missing.append(row['id'])
                elif int(ledger['amount_cents']) != int(row['amount_cents']): mismatched.append(row['id'])
            checks.append({'label':label,'source_count':len(rows),'missing_count':len(missing),'mismatch_count':len(mismatched),'examples':missing[:8] + (['amount mismatch IDs: '+','.join(map(str,mismatched[:5]))] if mismatched else [])})
        duplicates=db().all("SELECT reference,COUNT(*) n FROM (SELECT upper(trim(reference)) reference FROM contributions WHERE chama_id=? AND status='PAID' AND reference IS NOT NULL AND trim(reference)<>'' UNION ALL SELECT upper(trim(reference)) reference FROM loan_repayments WHERE chama_id=? AND status='PAID' AND reference IS NOT NULL AND trim(reference)<>'' UNION ALL SELECT upper(trim(reference)) reference FROM fine_payments WHERE chama_id=? AND status='PAID' AND reference IS NOT NULL AND trim(reference)<>'') refs GROUP BY reference HAVING COUNT(*)>1",(chama_id,chama_id,chama_id))
        pending=db().val("SELECT COUNT(*) FROM payment_requests WHERE chama_id=? AND status='PENDING'",(chama_id,),0)
        open_actions=db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN'",(chama_id,),0)
        overdue_actions=db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN' AND due_date IS NOT NULL AND due_date<?",(chama_id,date.today().isoformat()),0)
        statement_balance=request.args.get('statement_balance',type=int)
        statement_ledger=request.args.get('statement_ledger',type=int)
        statement_delta=(statement_balance-statement_ledger) if statement_balance is not None and statement_ledger is not None else None
        return render_template('trust_center.html',chama=chama,me=me,members=members,checks=checks,duplicates=duplicates,
            ledger_in=ledger_in,ledger_out=ledger_out,ledger_balance=ledger_balance,cash_balance=F.cash_balance(db(),chama_id),today=date.today().isoformat(),statement_ledger=statement_ledger,
            pending=pending,open_actions=open_actions,overdue_actions=overdue_actions,statement_balance=statement_balance,statement_delta=statement_delta)

    @app.route('/chamas/<int:chama_id>/trust/statement-check', methods=['POST'])
    @login_required
    def trust_statement_check(chama_id):
        chama, me, sub = ctx(chama_id, roles=FIN)
        try:
            actual=F.parse_kes(request.form.get('statement_balance_kes'),0)
            as_of=datetime.strptime(request.form.get('as_of',''),'%Y-%m-%d').date().isoformat()
            ledger=int(db().val("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND occurred_on<=?",(chama_id,as_of),0) or 0)
            delta=actual-ledger
            from db import audit
            audit(db(),g.user['id'],'EXTERNAL_CASH_RECONCILIATION','chama',chama_id,chama_id,{'as_of':as_of,'reported_cash_and_bank_cents':actual,'ledger_cash_cents':ledger,'difference_cents':delta})
            db().commit()
            flash('Statement comparison saved to the audit log. Investigate any difference before changing ledger records.','success' if delta==0 else 'warning')
            return redirect(url_for('trust_center',chama_id=chama_id,statement_balance=actual,statement_ledger=ledger,as_of=as_of))
        except (S.BusinessError,ValueError,TypeError):
            flash('Enter a valid KES total and statement date.','warning')
            return redirect(url_for('trust_center',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/trust/remind', methods=['POST'])
    @login_required
    def trust_remind(chama_id):
        chama, me, sub = ctx(chama_id, roles=FIN)
        from trust_jobs import daily_obligation_reminders
        sent=daily_obligation_reminders(db(),chama_id)
        flash(f'Created {sent} in-app reminder(s). Reminders are not proof of payment; officials must verify references.', 'success')
        return redirect(url_for('trust_center',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/financial-import', methods=['GET','POST'])
    @login_required
    def financial_import(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN','TREASURER'))
        if request.method=='POST':
            raw=request.form.get('csv_text','')
            upload=request.files.get('csv_file')
            if upload and upload.filename:
                raw=upload.read(256*1024+1).decode('utf-8-sig','replace')
            if len(raw.encode('utf-8'))>256*1024:
                flash('CSV is too large (maximum 256 KB).','warning'); return redirect(url_for('financial_import',chama_id=chama_id))
            if request.form.get('confirm_import')=='1':
                staging_id=session.get('financial_import_preview_id')
                staged=db().one("SELECT * FROM financial_import_staging WHERE id=? AND chama_id=? AND user_id=? AND status='PREVIEW' AND expires_at>?",(staging_id,chama_id,g.user['id'],S.iso(S.now_utc()))) if staging_id else None
                if not staged:
                    flash('Import preview expired or changed. Upload and preview the file again.','warning'); return redirect(url_for('financial_import',chama_id=chama_id))
                rows=json.loads(staged['payload']); saved_hash=staged['payload_sha256']
                if not rows or saved_hash!=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest():
                    flash('Import preview failed its integrity check. Upload and preview the file again.','warning'); return redirect(url_for('financial_import',chama_id=chama_id))
                errors=[]; added=0
                try:
                    # Re-validate all rows before changing the ledger; the transaction rolls back on any error.
                    with db().tx():
                        for idx,r in enumerate(rows, start=2):
                            phone_match=S.normalize_phone(r['member']) or r['member']
                            uid=db().val("SELECT u.id FROM users u JOIN chama_members m ON m.user_id=u.id WHERE m.chama_id=? AND m.status='ACTIVE' AND (u.phone=? OR lower(u.name)=lower(?)) LIMIT 1",(chama_id,phone_match,r['member']),None)
                            if r['type']=='CONTRIBUTION':
                                if not uid: raise ValueError(f'Row {idx}: member {r["member"]} was not found by phone or exact name.')
                                F.record_contribution(db(),chama_id,int(uid),int(r['amount_cents']),r['date'],r['method'],r['reference'],('Historical import: '+r['description'])[:200],g.user['id'])
                            elif r['type'] in ('EXPENSE','OTHER_INCOME'):
                                F.record_ledger_entry(db(),chama_id,r['type'],int(r['amount_cents']),r['description'],r['date'],g.user['id'])
                            elif r['type']=='OPENING_BALANCE':
                                if db().val("SELECT COUNT(*) FROM ledger_transactions WHERE chama_id=? AND kind='OPENING_BALANCE'",(chama_id,),0): raise ValueError('An opening balance is already recorded. Do not import it twice.')
                                if any(x['type']=='OPENING_BALANCE' for x in rows[:idx-2]): raise ValueError('Only one opening balance row is allowed per import.')
                                db().insert('ledger_transactions',chama_id=chama_id,user_id=None,kind='OPENING_BALANCE',direction='IN',amount_cents=int(r['amount_cents']),ref_type='opening_balance',ref_id=None,description=r['description'][:200],occurred_on=r['date'],created_by=g.user['id'],created_at=S.iso(S.now_utc()),account='MAIN')
                            else:
                                raise ValueError(f'Row {idx}: unsupported transaction type.')
                            added+=1
                        from finance import audit
                        audit(db(),g.user['id'],'FINANCIAL_HISTORY_IMPORTED','financial_import',None,chama_id,{'rows':added,'sha256':saved_hash})
                    db().execute("UPDATE financial_import_staging SET status='COMMITTED' WHERE id=?",(staged['id'],))
                    db().commit()
                    session.pop('financial_import_preview_id',None)
                    flash(f'Imported {added} reconciled historical transaction row(s). Verify the reconciliation report before relying on opening balances.', 'success')
                    return redirect(url_for('trust_center',chama_id=chama_id))
                except Exception as e:
                    db().rollback()
                    flash(str(e)[:240] if isinstance(e, (ValueError,S.BusinessError)) else 'Import failed; no rows were committed. Correct the CSV and preview it again.', 'warning')
                    return redirect(url_for('financial_import',chama_id=chama_id))
            try:
                reader=csv.DictReader(io.StringIO(raw))
                required={'type','date','amount_kes','description'}
                if not reader.fieldnames or not required.issubset({x.strip().lower() for x in reader.fieldnames}):
                    raise ValueError('Required CSV headers: type,date,amount_kes,description. Contributions also require member; reference is strongly recommended.')
                normalized=[]
                for idx,row in enumerate(reader,start=2):
                    row={str(k or '').strip().lower():str(v or '').strip() for k,v in row.items()}
                    if not any(row.values()): continue
                    typ=row.get('type','').upper()
                    if typ not in ('CONTRIBUTION','EXPENSE','OTHER_INCOME','OPENING_BALANCE'): raise ValueError(f'Row {idx}: type must be CONTRIBUTION, EXPENSE, OTHER_INCOME, or OPENING_BALANCE.')
                    d=datetime.strptime(row.get('date',''),'%Y-%m-%d').date().isoformat()
                    amount=F.parse_kes(row.get('amount_kes'),100)
                    desc=row.get('description','')
                    if len(desc)<3: raise ValueError(f'Row {idx}: description must be at least 3 characters.')
                    member=row.get('member','')
                    if typ=='CONTRIBUTION' and not member: raise ValueError(f'Row {idx}: contribution needs a member phone or exact name.')
                    if typ=='OPENING_BALANCE' and not desc.lower().startswith('opening balance as of '): raise ValueError(f'Row {idx}: description must start with Opening balance as of and include the statement cutoff date.')
                    ref=(row.get('reference') or '').strip().upper()[:40] or None
                    if ref:
                        try: F._ensure_unique_payment_reference(db(),chama_id,ref)
                        except S.BusinessError as exc: raise ValueError(f'Row {idx}: {exc}')
                    normalized.append({'type':typ,'date':d,'amount_cents':amount,'description':desc[:200],'member':member,'reference':ref,'method':(row.get('method') or 'Cash').strip().title()[:30]})
                    if len(normalized)>500: raise ValueError('Maximum 500 transaction rows per import.')
                if not normalized: raise ValueError('No transaction rows found.')
                opening_rows=[i for i,r in enumerate(normalized) if r['type']=='OPENING_BALANCE']
                if opening_rows and opening_rows != [0]: raise ValueError('If included, the single opening balance must be the first transaction row. Import only transactions after its cutoff date.')
                # Detect duplicate references within this import before showing the commit screen.
                refs=[r['reference'] for r in normalized if r['reference']]
                if len(refs)!=len(set(refs)): raise ValueError('Duplicate payment references found inside this CSV. Resolve them before importing.')
                payload=json.dumps(normalized,sort_keys=True)
                digest=hashlib.sha256(payload.encode()).hexdigest()
                db().execute("UPDATE financial_import_staging SET status='EXPIRED' WHERE chama_id=? AND user_id=? AND status='PREVIEW'",(chama_id,g.user['id']))
                staging_id=db().insert('financial_import_staging',chama_id=chama_id,user_id=g.user['id'],payload=payload,payload_sha256=digest,status='PREVIEW',created_at=S.iso(S.now_utc()),expires_at=S.iso(S.now_utc()+__import__('datetime').timedelta(minutes=30)))
                db().commit()
                session['financial_import_preview_id']=staging_id
                return render_template('financial_import.html',chama=chama,me=me,preview=normalized,csv_text=raw,hash=digest)
            except (ValueError,TypeError,S.BusinessError) as e:
                flash(str(e),'warning')
        return render_template('financial_import.html',chama=chama,me=me,preview=None,csv_text='')
