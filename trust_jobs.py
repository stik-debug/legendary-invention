"""Daily idempotent in-app reminders for contributions, loan installments and meeting actions."""
from datetime import date
import finance as F
import services as S
from notify import notify as send_notification, queue_opted_in_whatsapp

def daily_obligation_reminders(db, chama_id=None):
    today_date=date.today(); today=today_date.isoformat(); period=today[:7]; sent=0
    chamas=db.all('SELECT id FROM chamas ORDER BY id') if chama_id is None else [{'id':int(chama_id)}]
    for c in chamas:
        cid=int(c['id'])
        # Constitution-based late contribution fines are disabled unless an administrator explicitly sets due day 1–28 and a positive fine.
        rule=db.one('SELECT monthly_contribution_cents,late_fine_cents,contribution_due_day FROM chama_constitutions WHERE chama_id=?',(cid,))
        if rule and int(rule.get('contribution_due_day') or 0) in range(1,29) and today_date.day>int(rule['contribution_due_day']) and int(rule.get('late_fine_cents') or 0)>0 and int(rule.get('monthly_contribution_cents') or 0)>0:
            expected=int(rule['monthly_contribution_cents']); fine_cents=int(rule['late_fine_cents'])
            configured=int(db.val('SELECT contribution_cents FROM chamas WHERE id=?',(cid,),0) or 0)
            if configured != expected:
                # Two existing settings screens can hold the contribution target; never auto-fine if they disagree.
                continue
            members=db.all("SELECT m.user_id,m.joined_at,c.created_by FROM chama_members m JOIN chamas c ON c.id=m.chama_id WHERE m.chama_id=? AND m.status='ACTIVE'",(cid,))
            for member in members:
                joined=(member.get('joined_at') or '')[:10]
                if joined.startswith(period) and joined[8:10].isdigit() and int(joined[8:10])>int(rule['contribution_due_day']): continue
                paid=int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND user_id=? AND period=? AND status='PAID'",(cid,member['user_id'],period),0) or 0)
                reason=f'Late contribution for {period}'
                exists=db.val("SELECT COUNT(*) FROM fines WHERE chama_id=? AND user_id=? AND reason=?",(cid,member['user_id'],reason),0)
                if paid<expected and not exists:
                    F.create_fine(db,cid,member['user_id'],fine_cents,reason,None,member['created_by'],S.now_utc())
                    queue_opted_in_whatsapp(db,member['user_id'],f'ChamaPay notice: a KES {fine_cents/100:,.0f} fine was recorded under your group constitution for the unpaid {period} contribution. If you have paid, contact the treasurer to reconcile your record.',dedupe_key=f'constitution-late-fine:{cid}:{member["user_id"]}:{period}')
                    sent+=1
        for m in F.month_status(db,cid,period):
            if m['status']!='PAID':
                text=f"Payment reminder: your {period} chama contribution is {('partly paid' if m['status']=='PARTIAL' else 'not yet recorded')}. If you have paid, ask the treasurer to verify your reference."
                link=f"/chamas/{cid}/statement"
                exists=db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND chama_id=? AND text=? AND created_at LIKE ?",(m['id'],cid,text,today+'%'),0)
                if not exists:
                    send_notification(db,m['id'],cid,text,link=link); queue_opted_in_whatsapp(db,m['id'],text,dedupe_key=f'obligation-contribution:{cid}:{m["id"]}:{period}'); sent+=1
        actions=db.all("SELECT a.*,m.title meeting_title FROM meeting_actions a JOIN meetings m ON m.id=a.meeting_id WHERE a.chama_id=? AND a.status='OPEN' AND a.due_date IS NOT NULL AND a.due_date<=? AND a.owner_user_id IS NOT NULL",(cid,today))
        for a in actions:
            text=f"Action reminder: '{a['title']}' from {a['meeting_title']} is due or overdue."
            exists=db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND chama_id=? AND text=? AND created_at LIKE ?",(a['owner_user_id'],cid,text,today+'%'),0)
            if not exists:
                send_notification(db,a['owner_user_id'],cid,text,link=f"/chamas/{cid}/promises"); queue_opted_in_whatsapp(db,a['owner_user_id'],text,dedupe_key=f'obligation-action:{a["id"]}:{today}'); sent+=1
        installments=db.all("SELECT i.id,i.due_date,i.amount_cents,i.paid_cents,l.user_id FROM loan_installments i JOIN loans l ON l.id=i.loan_id WHERE l.chama_id=? AND l.status='ACTIVE' AND i.status!='PAID' AND i.due_date<=? ORDER BY i.due_date,i.id",(cid,today))
        for inst in installments:
            remaining=max(0,int(inst['amount_cents'])-int(inst['paid_cents']))
            text=f"Loan installment reminder: KES {remaining/100:,.2f} is due/overdue since {inst['due_date']}. If you have paid, ask the treasurer to verify your reference."
            exists=db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND chama_id=? AND text=? AND created_at LIKE ?",(inst['user_id'],cid,text,today+'%'),0)
            if not exists:
                send_notification(db,inst['user_id'],cid,text,link=f"/chamas/{cid}/loans"); queue_opted_in_whatsapp(db,inst['user_id'],text,dedupe_key=f'obligation-installment:{inst["id"]}:{today}'); sent+=1
    db.commit()
    return sent
