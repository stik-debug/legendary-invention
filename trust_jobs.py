"""Daily idempotent in-app reminders for contributions, loan installments and meeting actions."""
from datetime import date
import finance as F
import services as S
from notify import notify as send_notification

def daily_obligation_reminders(db, chama_id=None):
    today=date.today().isoformat(); period=today[:7]; sent=0
    chamas=db.all('SELECT id FROM chamas ORDER BY id') if chama_id is None else [{'id':int(chama_id)}]
    for c in chamas:
        cid=int(c['id'])
        for m in F.month_status(db,cid,period):
            if m['status']!='PAID':
                text=f"Reminder: your {period} chama contribution is {('partly paid' if m['status']=='PARTIAL' else 'not yet recorded')}. If you have paid, ask the treasurer to verify your reference."
                link=f"/chamas/{cid}/statement"
                exists=db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND chama_id=? AND text=? AND created_at LIKE ?",(m['id'],cid,text,today+'%'),0)
                if not exists:
                    send_notification(db,m['id'],cid,text,link=link); sent+=1
        actions=db.all("SELECT a.*,m.title meeting_title FROM meeting_actions a JOIN meetings m ON m.id=a.meeting_id WHERE a.chama_id=? AND a.status='OPEN' AND a.due_date IS NOT NULL AND a.due_date<=? AND a.owner_user_id IS NOT NULL",(cid,today))
        for a in actions:
            text=f"Action reminder: '{a['title']}' from {a['meeting_title']} is due or overdue."
            exists=db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND chama_id=? AND text=? AND created_at LIKE ?",(a['owner_user_id'],cid,text,today+'%'),0)
            if not exists:
                send_notification(db,a['owner_user_id'],cid,text,link=f"/chamas/{cid}/promises"); sent+=1
        installments=db.all("SELECT i.id,i.due_date,i.amount_cents,i.paid_cents,l.user_id FROM loan_installments i JOIN loans l ON l.id=i.loan_id WHERE l.chama_id=? AND l.status='ACTIVE' AND i.status!='PAID' AND i.due_date<=? ORDER BY i.due_date,i.id",(cid,today))
        for inst in installments:
            remaining=max(0,int(inst['amount_cents'])-int(inst['paid_cents']))
            text=f"Loan installment reminder: KES {remaining/100:,.2f} is due/overdue since {inst['due_date']}. If you have paid, ask the treasurer to verify your reference."
            exists=db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND chama_id=? AND text=? AND created_at LIKE ?",(inst['user_id'],cid,text,today+'%'),0)
            if not exists:
                send_notification(db,inst['user_id'],cid,text,link=f"/chamas/{cid}/loans"); sent+=1
    db.commit()
    return sent
