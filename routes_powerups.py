"""ChamaPro command centre and deterministic, permission-scoped Ask ChamaPro.
Answers are calculated from this chama's database records; this is not a generative AI service.
"""
from datetime import date
from flask import abort, g, render_template, request
import finance as F


def register(app, db, ctx, login_required, owner_required):
    @app.route('/chamas/<int:chama_id>/command-centre')
    @login_required
    def chama_command_centre(chama_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        today = date.today().isoformat()
        role = me['role']
        staff = role in ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY')
        # Every count is sourced from live records. Missing/empty records remain zero, never seeded.
        actions = []
        if staff:
            pending = int(db().val("SELECT COUNT(*) FROM payment_requests WHERE chama_id=? AND status='PENDING'", (cid,), 0) or 0)
            if pending:
                actions.append({'priority':1,'title':f'{pending} payment confirmation(s) awaiting review','detail':'Review references and evidence before confirming any payment.','url':f'/chamas/{cid}/approvals'})
            overdue = int(db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN' AND due_date IS NOT NULL AND due_date<?", (cid,today), 0) or 0)
            if overdue:
                actions.append({'priority':2,'title':f'{overdue} meeting action(s) overdue','detail':'Check the owner, deadline and recorded status.','url':f'/chamas/{cid}/promises'})
            unreconciled = int(db().val("SELECT COUNT(*) FROM contributions c LEFT JOIN ledger_transactions l ON l.chama_id=c.chama_id AND l.ref_type='contribution' AND l.ref_id=c.id AND l.direction='IN' WHERE c.chama_id=? AND c.status='PAID' AND l.id IS NULL", (cid,), 0) or 0)
            if unreconciled:
                actions.append({'priority':3,'title':f'{unreconciled} paid contribution(s) missing a ledger entry','detail':'Investigate the source record and ledger; do not force a balance.','url':f'/chamas/{cid}/trust'})
        # Member-specific open payment requests, when this schema has a requester column.
        try:
            mine_pending = int(db().val("SELECT COUNT(*) FROM payment_requests WHERE chama_id=? AND user_id=? AND status='PENDING'",(cid,uid),0) or 0)
            if mine_pending:
                actions.append({'priority':1,'title':f'{mine_pending} of your payment request(s) awaiting confirmation','detail':'Keep your reference until an authorized official reviews it.','url':f'/chamas/{cid}/pay-status'})
        except Exception:
            pass
        actions.sort(key=lambda x:x['priority'])
        return render_template('command_centre.html',chama=chama,me=me,actions=actions,staff=staff,
            goals=int(db().val("SELECT COUNT(*) FROM chama_goals WHERE chama_id=? AND status='ACTIVE'",(cid,),0) or 0),
            open_actions=int(db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN'",(cid,),0) or 0))

    @app.route('/chamas/<int:chama_id>/ask-chamapro')
    @login_required
    def ask_chamapro(chama_id):
        chama, me, sub = ctx(chama_id)
        q=(request.args.get('q') or '').strip()[:240]
        answer=None
        if q:
            low=q.lower(); cid=chama_id; uid=g.user['id']; year=date.today().year
            if any(k in low for k in ('contributed','contribution','paid this year','my payments')):
                total=int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID' AND substr(paid_on,1,4)=?",(cid,uid,str(year)),0) or 0)
                count=int(db().val("SELECT COUNT(*) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID' AND substr(paid_on,1,4)=?",(cid,uid,str(year)),0) or 0)
                answer={'title':f'Your recorded contributions in {year}','body':f'KES {total/100:,.2f} across {count} paid record(s).','source':'contributions table; status=PAID; current calendar year. This answer excludes unconfirmed requests.'}
            elif any(k in low for k in ('awaiting confirmation','pending payment','unconfirmed payment')):
                if me['role'] in ('CHAMA_ADMIN','TREASURER','SECRETARY'):
                    n=int(db().val("SELECT COUNT(*) FROM payment_requests WHERE chama_id=? AND status='PENDING'",(cid,),0) or 0)
                    answer={'title':'Payment requests awaiting confirmation','body':f'{n} pending request(s) are recorded for this chama.','source':'payment_requests table; status=PENDING. A pending request is not a confirmed payment.'}
                else:
                    n=int(db().val("SELECT COUNT(*) FROM payment_requests WHERE chama_id=? AND user_id=? AND status='PENDING'",(cid,uid),0) or 0)
                    answer={'title':'Your payment requests awaiting confirmation','body':f'{n} of your request(s) are pending.','source':'Only your own payment requests are included.'}
            elif any(k in low for k in ('goal','target','savings target')):
                rows=db().all("SELECT name,target_cents,current_cents,deadline FROM chama_goals WHERE chama_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 20",(cid,))
                if rows:
                    answer={'title':'Active savings goals','body':'\n'.join(f"{r['name']}: KES {int(r['current_cents'] or 0)/100:,.2f} recorded of KES {int(r['target_cents'] or 0)/100:,.2f}"+(f"; deadline {r['deadline']}" if r['deadline'] else '') for r in rows),'source':'Active chama_goals records. Recorded progress is shown; projections are not counted as money held.'}
                else: answer={'title':'No active goals recorded','body':'This chama has no active savings goals in the database.','source':'chama_goals table; status=ACTIVE.'}
            elif any(k in low for k in ('last meeting','meeting decision','minutes','what did we decide')):
                r=db().one("SELECT id,title,held_at,minutes FROM meetings WHERE chama_id=? ORDER BY held_at DESC,id DESC LIMIT 1",(cid,))
                answer=({'title':f"Latest meeting: {r['title']}",'body':(r.get('minutes') or 'No minutes have been recorded for this meeting.'),'source':f"meetings record #{r['id']}; held at {r.get('held_at') or 'date not recorded'}. This is not a search of every prior meeting."} if r else {'title':'No meetings recorded','body':'No meeting records were found for this chama.','source':'meetings table for this chama.'})
            elif any(k in low for k in ('loan','repayment','arrears','outstanding')):
                # Avoid exposing other members' loan-level details to ordinary members.
                if me['role'] in ('CHAMA_ADMIN','TREASURER','SECRETARY'):
                    n=int(db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(cid,),0) or 0)
                    cents=int(db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(cid,),0) or 0)
                    answer={'title':'Recorded outstanding loans','body':f'{n} disbursed active/approved loan(s); recorded outstanding total KES {cents/100:,.2f}.','source':'Loans with a disbursement date and ACTIVE/APPROVED status; total_due minus paid. Review loan records for context.'}
                else:
                    n=int(db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND user_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(cid,uid),0) or 0)
                    cents=int(db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND user_id=? AND status IN ('ACTIVE','APPROVED') AND disbursed_at IS NOT NULL",(cid,uid),0) or 0)
                    answer={'title':'Your recorded outstanding loans','body':f'{n} disbursed active/approved loan(s); recorded outstanding total KES {cents/100:,.2f}.','source':'Only your own disbursed loans are included.'}
            else:
                answer={'title':'I could not safely answer that yet','body':'Try asking about your contributions this year, pending payment confirmations, active savings goals, the latest meeting minutes, or recorded outstanding loans.','source':'Answers are deterministic database queries, not generated guesses. Unavailable information is not invented.'}
        return render_template('ask_chamapro.html',chama=chama,me=me,q=q,answer=answer)
