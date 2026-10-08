"""ChamaPay additions: member exits, dividends, audit views, PDFs and reminder helpers."""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from db import audit
from services import BusinessError, iso, now_utc
import finance as F
import notify as N


def member_exit_quote(db, chama_id, user_id):
    m = db.one("SELECT m.*,u.name,u.email FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.user_id=? AND m.status='ACTIVE'", (chama_id, user_id))
    if not m:
        raise BusinessError('Member not found or already exited.')
    savings = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID'", (chama_id,user_id), 0) or 0)
    loan = int(db.val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id,user_id), 0) or 0)
    pending = int(db.val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND user_id=? AND status IN ('PENDING','APPROVED')", (chama_id,user_id), 0) or 0)
    fine = int(db.val("SELECT COALESCE(SUM(amount_cents-paid_cents),0) FROM fines WHERE chama_id=? AND user_id=? AND status IN ('UNPAID','PARTIAL')", (chama_id,user_id), 0) or 0)
    payout = savings - loan - fine
    return {'member':m,'savings':savings,'loan_balance':loan,'fine_balance':fine,'pending_loans':pending,'payout':payout}


def exit_member(db, chama_id, user_id, actor, method, reference=None, now=None):
    now = now or now_utc()
    method = (method or '').strip()
    if method not in F.METHODS:
        raise BusinessError('Choose Cash, M-Pesa or Bank for the share payout.')
    q = member_exit_quote(db, chama_id, user_id)
    m = q['member']
    if m['role'] == 'CHAMA_ADMIN' and db.val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND role='CHAMA_ADMIN' AND status='ACTIVE'", (chama_id,), 0) <= 1:
        raise BusinessError('A chama must keep at least one administrator.')
    if q['pending_loans']:
        raise BusinessError('This member has a loan request still awaiting a decision. Decide it before exit.')
    if q['payout'] < 0:
        raise BusinessError(f'This member owes more than their savings by KES {abs(q["payout"])/100:,.2f}. Settle the balance before exit.')
    ref = (reference or '').strip().upper()[:80] or None
    with db.tx():
        db.lock('chamas', chama_id)
        q = member_exit_quote(db, chama_id, user_id)
        current = db.one("SELECT * FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id,user_id))
        if not current:
            raise BusinessError('Member has already exited.')
        # Any outstanding fine is settled from the member's share before the final payout.
        remaining = q['fine_balance']
        for fine in db.all("SELECT * FROM fines WHERE chama_id=? AND user_id=? AND status IN ('UNPAID','PARTIAL') ORDER BY id", (chama_id,user_id)):
            if remaining <= 0: break
            due = fine['amount_cents'] - fine['paid_cents']
            take = min(due, remaining)
            pid = db.insert('fine_payments', fine_id=fine['id'], chama_id=chama_id, user_id=user_id, amount_cents=take,
                            paid_on=now.date().isoformat(), method='Share offset', reference=ref, created_by=actor, created_at=iso(now))
            paid = fine['paid_cents'] + take
            db.execute("UPDATE fines SET paid_cents=?, status=? WHERE id=?", (paid, 'PAID' if paid >= fine['amount_cents'] else 'PARTIAL', fine['id']))
            F._ledger(db, chama_id, 'FINE_PAYMENT', 'IN', take, user_id, 'fine_payment', pid, 'Fine settled from exit share', now.date().isoformat(), actor, now)
            remaining -= take
        payout = q['payout']
        if payout:
            exit_id = db.insert('member_exits', chama_id=chama_id,user_id=user_id,savings_cents=q['savings'],loan_balance_cents=q['loan_balance'],fine_balance_cents=q['fine_balance'],payout_cents=payout,method=method,reference=ref,status='PAID',approved_by=actor,paid_at=iso(now),created_at=iso(now))
            F._ledger(db, chama_id, 'MEMBER_SHARE_PAYOUT', 'OUT', payout, user_id, 'member_exit', exit_id, 'Member share payout', now.date().isoformat(), actor, now)
        else:
            exit_id = db.insert('member_exits', chama_id=chama_id,user_id=user_id,savings_cents=q['savings'],loan_balance_cents=q['loan_balance'],fine_balance_cents=q['fine_balance'],payout_cents=0,method=method,reference=ref,status='PAID',approved_by=actor,paid_at=iso(now),created_at=iso(now))
        db.execute("UPDATE chama_members SET status='REMOVED', removed_at=? WHERE id=?", (iso(now), current['id']))
        audit(db, actor, 'MEMBER_EXITED', 'member_exit', exit_id, chama_id, {'user_id':user_id,'payout_cents':payout,'method':method})
        N.notify(db, user_id, chama_id, f'Your chama exit was recorded. Share payout: KES {payout/100:,.2f}.', f'/chamas/{chama_id}/statement', actor, now)
    return exit_id


def _year_range(year):
    try: y = int(year)
    except (TypeError, ValueError): raise BusinessError('Choose a valid year.')
    if y < 2000 or y > datetime.utcnow().year + 1: raise BusinessError('Choose a valid year.')
    return y, f'{y:04d}-01-01', f'{y+1:04d}-01-01'


def dividend_calculation(db, chama_id, year):
    y, start, end = _year_range(year)
    members = db.all("SELECT u.id,u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND (m.status='ACTIVE' OR m.removed_at>=?) ORDER BY u.name", (chama_id,start))
    contributions = {r['user_id']: int(r['amount'] or 0) for r in db.all("SELECT user_id,SUM(amount_cents) amount FROM contributions WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<? GROUP BY user_id", (chama_id,start,end))}
    fine_pool = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM fine_payments WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<?", (chama_id,start,end),0) or 0)
    # Allocate loan repayments to principal first; anything beyond principal is interest earned.
    interest_pool = 0
    for loan in db.all("SELECT * FROM loans WHERE chama_id=? AND disbursed_at IS NOT NULL", (chama_id,)):
        prior = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM loan_repayments WHERE loan_id=? AND status='PAID' AND paid_on<?", (loan['id'],start),0) or 0)
        principal_left = max(0, loan['principal_cents'] - prior)
        used = 0
        for r in db.all("SELECT amount_cents FROM loan_repayments WHERE loan_id=? AND status='PAID' AND paid_on>=? AND paid_on<? ORDER BY paid_on,id", (loan['id'],start,end)):
            amt = int(r['amount_cents'])
            principal_take = min(principal_left, amt)
            principal_left -= principal_take
            used += amt - principal_take
        interest_pool += min(used, int(loan['interest_cents']))
    pool = interest_pool + fine_pool
    total_contrib = sum(contributions.values())
    rows=[]; allocated=0
    if total_contrib:
        exact=[]
        for m in members:
            c=contributions.get(m['id'],0)
            numerator=pool*c
            base=numerator//total_contrib
            rem=numerator%total_contrib
            exact.append([m,base,rem,c])
        allocated=sum(x[1] for x in exact)
        leftover=pool-allocated
        exact.sort(key=lambda x:(x[2],x[0]['id']),reverse=True)
        for i in range(leftover): exact[i][1] += 1
        exact.sort(key=lambda x:x[0]['name'].lower())
        rows=[{'user_id':m['id'],'name':m['name'],'contribution_cents':c,'dividend_cents':base} for m,base,rem,c in exact]
    else:
        rows=[{'user_id':m['id'],'name':m['name'],'contribution_cents':0,'dividend_cents':0} for m in members]
    return {'year':y,'interest_pool':interest_pool,'fine_pool':fine_pool,'pool':pool,'total_contributions':total_contrib,'rows':rows,'allocated':sum(r['dividend_cents'] for r in rows)}


def meeting_share_url(base_url, chama_id, meeting_id, title, when):
    text = f'ChamaPay meeting: {title}\n{when}\n{base_url}/chamas/{chama_id}/meetings/{meeting_id}'
    return 'https://wa.me/?text=' + quote(text)


def meeting_reminders(db, app_base=None, now=None):
    now=now or now_utc()
    sent=0
    meetings=db.all("SELECT * FROM meetings WHERE status='SCHEDULED'")
    for m in meetings:
        try: start=datetime.strptime(m['held_at'],'%Y-%m-%d %H:%M')
        except Exception: continue
        # Meeting times are stored as East Africa local time. Compare against UTC+3.
        start_utc=start-timedelta(hours=3)
        if not (now+timedelta(hours=23) <= start_utc <= now+timedelta(hours=25)): continue
        if db.val('SELECT COUNT(*) FROM meeting_reminders_sent WHERE meeting_id=? AND reminder_type=?',(m['id'],'24H'),0): continue
        members=db.all("SELECT u.id,u.email,u.name FROM chama_members cm JOIN users u ON u.id=cm.user_id WHERE cm.chama_id=? AND cm.status='ACTIVE'",(m['chama_id'],))
        with db.tx():
            for u in members:
                N.notify(db,u['id'],m['chama_id'],f'Reminder: {m["title"]} is tomorrow at {m["held_at"]}.',f'/chamas/{m["chama_id"]}/meetings/{m["id"]}',None,now)
                pref=db.one('SELECT email FROM notification_preferences WHERE user_id=?',(u['id'],))
                if (pref is None or int(pref.get('email',1))) and u.get('email'):
                    N.queue_email(db,u['id'],u['email'],f'Reminder: {m["title"]}',f'Your Chama meeting is tomorrow at {m["held_at"]}. Open ChamaPay for the agenda and meeting details.')
            db.insert('meeting_reminders_sent',meeting_id=m['id'],reminder_type='24H',sent_at=iso(now))
        sent+=1
    return sent
