
from datetime import date, datetime, timedelta
import math
import json
from flask import render_template, request, redirect, url_for, flash, abort, jsonify, g
import services as S
from db import audit
from governance import calculate_vote_result

STAFF = ('CHAMA_ADMIN','TREASURER','SECRETARY','AUDITOR')

def register_upgrade_routes(app, db, login_required, ctx):
    def staff_ctx(cid):
        return ctx(cid, roles=STAFF)

    def admin_ctx(cid):
        return ctx(cid, roles=('CHAMA_ADMIN',))

    @app.route('/chamas/<int:chama_id>/command')
    @login_required
    def command_center(chama_id):
        # The Command Center is the private owner console. A user must be
        # the original creator of this Chama, not merely an administrator.
        chama, me, sub = ctx(chama_id)
        if int(chama['created_by']) != int(me['user_id']):
            abort(403)
        members = db().all("""SELECT u.id,u.name,u.phone,m.role,m.joined_at FROM chama_members m JOIN users u ON u.id=m.user_id
                              WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name""",(chama_id,))
        total_members=len(members)
        cash = db().val("""SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0)
                           FROM ledger_transactions WHERE chama_id=?""",(chama_id,),0)
        savings = db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0)
        loans = db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED','PENDING')",(chama_id,),0)
        investments = db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_investments WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0)
        assets = db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_assets WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0)
        welfare = db().val("SELECT COALESCE(SUM(amount_cents),0) FROM ledger_transactions WHERE chama_id=? AND account='WELFARE' AND direction='IN'",(chama_id,),0)
        pending_loans=db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND status='PENDING'",(chama_id,),0)
        overdue=db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND status='ACTIVE' AND due_date IS NOT NULL AND due_date < ?",(chama_id,date.today().isoformat()),0)
        period=date.today().isoformat()[:7]
        paid=db().val("SELECT COUNT(DISTINCT user_id) FROM contributions WHERE chama_id=? AND period=? AND status='PAID'",(chama_id,period),0)
        health_parts={
            'Savings consistency': round((paid/max(total_members,1))*100),
            'Loan repayment': round((db().val("SELECT COALESCE(SUM(paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','REPAID')",(chama_id,),0)/max(db().val("SELECT COALESCE(SUM(total_due_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','REPAID')",(chama_id,),0),1))*100),
            'Member participation': round((db().val("SELECT COUNT(DISTINCT user_id) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0)/max(total_members,1))*100) if total_members else 0,
            'Meeting attendance': round((db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND status='PRESENT'",(chama_id,),0)/max(db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=?",(chama_id,),0),1))*100),
            'Contribution consistency': round((paid/max(total_members,1))*100),
            'Financial transparency': 100 if db().val("SELECT COUNT(*) FROM audit_logs WHERE chama_id=?",(chama_id,),0)>0 else 70,
        }
        health=round(sum(health_parts.values())/len(health_parts))
        alerts=[]
        if pending_loans: alerts.append(('warning',f'{pending_loans} loan application(s) awaiting approval',url_for('loans',chama_id=chama_id)))
        if overdue: alerts.append(('danger',f'{overdue} overdue loan(s)',url_for('loans',chama_id=chama_id)))
        if total_members-paid: alerts.append(('warning',f'{total_members-paid} member(s) have not paid this month',url_for('savings',chama_id=chama_id)))
        if db().val("SELECT COUNT(*) FROM chama_votes WHERE chama_id=? AND status='OPEN'",(chama_id,),0): alerts.append(('info','Open vote(s) need member attention',url_for('chama_votes',chama_id=chama_id)))
        return render_template('command_center.html',chama=chama,me=me,sub=sub,members=members,total_members=total_members,
            cash=cash,savings=savings,loans=loans,investments=investments,assets=assets,welfare=welfare,paid=paid,health=health,
            health_parts=health_parts,alerts=alerts,pending_loans=pending_loans,overdue=overdue,goals=db().all("SELECT * FROM chama_goals WHERE chama_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 5",(chama_id,)),
            activities=db().all("""SELECT l.*,u.name FROM ledger_transactions l LEFT JOIN users u ON u.id=l.user_id WHERE l.chama_id=? ORDER BY l.id DESC LIMIT 10""",(chama_id,)))

    @app.route('/chamas/<int:chama_id>/goals', methods=['GET','POST'])
    @login_required
    def chama_goals(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            if me['role'] not in STAFF: abort(403)
            f=request.form
            target=int(float(f.get('target','0') or 0)*100)
            if target<=0: flash('Goal target must be greater than zero.','danger')
            else:
                db().insert('chama_goals',chama_id=chama_id,name=f.get('name','').strip()[:120],description=f.get('description','')[:500],
                            target_cents=target,current_cents=0,status='ACTIVE',deadline=f.get('deadline') or None,created_by=me['user_id'],created_at=S.iso(S.now_utc()))
                audit(db(),me['user_id'],'GOAL_CREATED','goal',None,chama_id,{'name':f.get('name')}); db().commit(); flash('Goal created.','success')
            return redirect(url_for('chama_goals',chama_id=chama_id))
        rows=db().all("SELECT g.*,u.name creator FROM chama_goals g LEFT JOIN users u ON u.id=g.created_by WHERE g.chama_id=? ORDER BY g.status,g.id DESC",(chama_id,))
        return render_template('goals.html',chama=chama,me=me,goals=rows)

    @app.route('/chamas/<int:chama_id>/goals/<int:goal_id>/fund', methods=['POST'])
    @login_required
    def goal_fund(chama_id,goal_id):
        chama,me,sub=ctx(chama_id)
        if me['role'] not in STAFF: abort(403)
        amount=int(float(request.form.get('amount','0') or 0)*100)
        goal=db().one("SELECT * FROM chama_goals WHERE id=? AND chama_id=?",(goal_id,chama_id)) or abort(404)
        new=min(goal['target_cents'],goal['current_cents']+amount)
        db().execute("UPDATE chama_goals SET current_cents=?, status=? WHERE id=?",(new,'COMPLETED' if new>=goal['target_cents'] else 'ACTIVE',goal_id))
        audit(db(),me['user_id'],'GOAL_FUNDED','goal',goal_id,chama_id,{'amount_cents':amount}); db().commit()
        flash('Goal progress updated.','success'); return redirect(url_for('chama_goals',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/investments', methods=['GET','POST'])
    @login_required
    def investments(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            if me['role'] not in STAFF: abort(403)
            f=request.form
            purchase=int(float(f.get('purchase_price','0') or 0)*100); current=int(float(f.get('current_value','0') or 0)*100)
            if purchase<=0 or current<0: flash('Enter valid investment values.','danger')
            else:
                iid=db().insert('chama_investments',chama_id=chama_id,name=f.get('name','').strip()[:150],type=f.get('type','Other')[:50],
                    purchase_date=f.get('purchase_date') or date.today().isoformat(),purchase_price_cents=purchase,current_value_cents=current,
                    income_cents=int(float(f.get('income','0') or 0)*100),notes=f.get('notes','')[:1000],status='ACTIVE',created_by=me['user_id'],created_at=S.iso(S.now_utc()))
                audit(db(),me['user_id'],'INVESTMENT_CREATED','investment',iid,chama_id); db().commit(); flash('Investment added.','success')
            return redirect(url_for('investments',chama_id=chama_id))
        rows=db().all("SELECT * FROM chama_investments WHERE chama_id=? ORDER BY status,id DESC",(chama_id,))
        totals={'purchase':sum(x['purchase_price_cents'] for x in rows if x['status']=='ACTIVE'),'value':sum(x['current_value_cents'] for x in rows if x['status']=='ACTIVE'),'income':sum(x['income_cents'] for x in rows if x['status']=='ACTIVE')}
        return render_template('investments.html',chama=chama,me=me,rows=rows,totals=totals)

    @app.route('/chamas/<int:chama_id>/assets', methods=['GET','POST'])
    @login_required
    def assets(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            if me['role'] not in STAFF: abort(403)
            f=request.form
            purchase=int(float(f.get('purchase_price','0') or 0)*100); current=int(float(f.get('current_value','0') or 0)*100)
            if not f.get('name') or current<0: flash('Enter valid asset information.','danger')
            else:
                aid=db().insert('chama_assets',chama_id=chama_id,name=f.get('name','')[:150],category=f.get('category','Other')[:50],
                    purchase_price_cents=purchase,current_value_cents=current,purchase_date=f.get('purchase_date') or date.today().isoformat(),
                    location=f.get('location','')[:200],ownership=f.get('ownership','')[:300],notes=f.get('notes','')[:1000],
                    status='ACTIVE',created_by=me['user_id'],created_at=S.iso(S.now_utc()))
                audit(db(),me['user_id'],'ASSET_CREATED','asset',aid,chama_id); db().commit(); flash('Asset added.','success')
            return redirect(url_for('assets',chama_id=chama_id))
        rows=db().all("SELECT * FROM chama_assets WHERE chama_id=? ORDER BY status,id DESC",(chama_id,))
        return render_template('assets.html',chama=chama,me=me,rows=rows,total=sum(x['current_value_cents'] for x in rows if x['status']=='ACTIVE'))

    @app.route('/chamas/<int:chama_id>/votes', methods=['GET','POST'])
    @login_required
    def chama_votes(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            if me['role'] not in STAFF: abort(403)
            f=request.form
            title=(f.get('title') or '').strip()
            if not title:
                flash('A proposal title is required.','warning')
                return redirect(url_for('chama_votes',chama_id=chama_id))
            try:
                quorum=max(1,min(100,int(f.get('quorum_pct','50'))))
                threshold=max(1,min(100,int(f.get('approval_threshold_pct','50'))))
            except (TypeError,ValueError):
                flash('Quorum and approval threshold must be whole percentages from 1 to 100.','warning')
                return redirect(url_for('chama_votes',chama_id=chama_id))
            close_value=f.get('closes_at') or None
            scenario_id=f.get('scenario_id',type=int)
            if scenario_id and not db().val('SELECT COUNT(*) FROM chama_scenarios WHERE id=? AND chama_id=?',(scenario_id,chama_id),0):
                flash('That planning scenario does not belong to this chama.','warning')
                return redirect(url_for('chama_votes',chama_id=chama_id))
            vid=db().insert('chama_votes',chama_id=chama_id,title=title[:180],description=f.get('description','')[:2000],scenario_id=scenario_id or None,
                opens_at=S.iso(S.now_utc()),closes_at=close_value,status='OPEN',quorum_pct=quorum,
                approval_threshold_pct=threshold,result='PENDING',created_by=me['user_id'],created_at=S.iso(S.now_utc()))
            audit(db(),me['user_id'],'VOTE_CREATED','vote',vid,chama_id,{'quorum_pct':quorum,'approval_threshold_pct':threshold,'scenario_id':scenario_id}); db().commit(); flash('Vote opened with its quorum, approval rule and any linked planning scenario recorded.','success')
            return redirect(url_for('chama_votes',chama_id=chama_id))
        now=S.iso(S.now_utc())
        # Close expired ballots and calculate the outcome using the rule fixed when each vote opened.
        expiring=db().all("SELECT * FROM chama_votes WHERE chama_id=? AND status='OPEN' AND closes_at IS NOT NULL AND closes_at<=?",(chama_id,now))
        for v in expiring:
            eligible=int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0)
            votes=int(db().val('SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=?',(v['id'],),0) or 0)
            yes=int(db().val("SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=? AND choice='YES'",(v['id'],),0) or 0)
            no=int(db().val("SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=? AND choice='NO'",(v['id'],),0) or 0)
            abstain=int(db().val("SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=? AND choice='ABSTAIN'",(v['id'],),0) or 0)
            outcome=calculate_vote_result(eligible,votes,yes,no,abstain,v.get('quorum_pct',50),v.get('approval_threshold_pct',50))
            result=outcome['result']
            db().execute("UPDATE chama_votes SET status='CLOSED',result=? WHERE id=? AND status='OPEN'",(result,v['id']))
            audit(db(),me['user_id'],'VOTE_AUTO_CLOSED','vote',v['id'],chama_id,outcome)
        db().commit()
        rows=db().all("""SELECT v.*,u.name creator,sc.name scenario_name,sc.results_json scenario_results_json,sc.assumptions_json scenario_assumptions_json,(SELECT COUNT(*) FROM chama_vote_responses r WHERE r.vote_id=v.id AND r.choice='YES') yes_count,
                        (SELECT COUNT(*) FROM chama_vote_responses r WHERE r.vote_id=v.id AND r.choice='NO') no_count,
                        (SELECT COUNT(*) FROM chama_vote_responses r WHERE r.vote_id=v.id AND r.choice='ABSTAIN') abstain_count,
                        (SELECT choice FROM chama_vote_responses r WHERE r.vote_id=v.id AND r.user_id=?) my_choice,
                        (SELECT COUNT(*) FROM chama_members cm WHERE cm.chama_id=v.chama_id AND cm.status='ACTIVE') eligible_count
                        FROM chama_votes v LEFT JOIN users u ON u.id=v.created_by LEFT JOIN chama_scenarios sc ON sc.id=v.scenario_id AND sc.chama_id=v.chama_id WHERE v.chama_id=? ORDER BY v.id DESC""",(me['user_id'],chama_id))
        for v in rows:
            try: v['scenario_result']=json.loads(v.get('scenario_results_json') or '{}')
            except Exception: v['scenario_result']={}
            try: v['scenario_assumptions']=json.loads(v.get('scenario_assumptions_json') or '{}')
            except Exception: v['scenario_assumptions']={}
            v['votes_cast']=int(v['yes_count'])+int(v['no_count'])+int(v['abstain_count'])
            v['quorum_needed']=(int(v['eligible_count'])*int(v.get('quorum_pct') or 50)+99)//100
            v['yes_threshold_needed']=((int(v['yes_count'])+int(v['no_count']))*int(v.get('approval_threshold_pct') or 50)+99)//100
        scenarios=db().all('SELECT id,name FROM chama_scenarios WHERE chama_id=? ORDER BY id DESC LIMIT 50',(chama_id,)) if me['role'] in STAFF else []
        return render_template('votes.html',chama=chama,me=me,rows=rows,scenarios=scenarios)

    @app.route('/chamas/<int:chama_id>/votes/<int:vote_id>/close', methods=['POST'])
    @login_required
    def close_vote(chama_id,vote_id):
        chama,me,sub=ctx(chama_id,roles=('CHAMA_ADMIN','TREASURER','SECRETARY'))
        v=db().one('SELECT * FROM chama_votes WHERE id=? AND chama_id=?',(vote_id,chama_id)) or abort(404)
        if v['status']=='OPEN':
            eligible=int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0)
            votes=int(db().val('SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=?',(vote_id,),0) or 0)
            yes=int(db().val("SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=? AND choice='YES'",(vote_id,),0) or 0)
            no=int(db().val("SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=? AND choice='NO'",(vote_id,),0) or 0)
            abstain=int(db().val("SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=? AND choice='ABSTAIN'",(vote_id,),0) or 0)
            outcome=calculate_vote_result(eligible,votes,yes,no,abstain,v.get('quorum_pct',50),v.get('approval_threshold_pct',50))
            result=outcome['result']
            db().execute("UPDATE chama_votes SET status='CLOSED',result=? WHERE id=? AND status='OPEN'",(result,vote_id))
            audit(db(),me['user_id'],'VOTE_CLOSED','vote',vote_id,chama_id,outcome)
            db().commit(); flash('Vote closed. Result: '+result.replace('_',' ').lower()+'.','success')
        return redirect(url_for('chama_votes',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/votes/<int:vote_id>', methods=['POST'])
    @login_required
    def vote(chama_id,vote_id):
        chama,me,sub=ctx(chama_id)
        v=db().one("SELECT * FROM chama_votes WHERE id=? AND chama_id=?",(vote_id,chama_id)) or abort(404)
        if v['status']!='OPEN': flash('This vote is closed.','warning')
        else:
            choice=request.form.get('choice')
            if choice not in ('YES','NO','ABSTAIN'): flash('Invalid vote.','danger')
            else:
                db().execute("INSERT INTO chama_vote_responses(vote_id,user_id,choice,created_at) VALUES(?,?,?,?) ON CONFLICT(vote_id,user_id) DO UPDATE SET choice=excluded.choice,created_at=excluded.created_at",
                             (vote_id,me['user_id'],choice,S.iso(S.now_utc())))
                audit(db(),me['user_id'],'VOTE_CAST','vote',vote_id,chama_id,{'choice':choice}); db().commit(); flash('Your vote was recorded.','success')
        return redirect(url_for('chama_votes',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/constitution', methods=['GET','POST'])
    @login_required
    def constitution(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            if me['role']!='CHAMA_ADMIN': abort(403)
            f=request.form
            fields=['monthly_contribution','joining_fee','loan_interest','loan_multiplier','loan_months','late_fine','attendance_requirement','voting_requirement']
            try: contribution_due_day=int(f.get('contribution_due_day') or 0)
            except (TypeError,ValueError): contribution_due_day=-1
            vals={x:f.get(x,'') for x in fields}
            try:
                numeric={k:float(vals[k] or 0) for k in fields}
                if any(not math.isfinite(v) for v in numeric.values()): raise ValueError
                if any(numeric[k]<0 for k in ('monthly_contribution','joining_fee','loan_interest','late_fine')) or not 0<=contribution_due_day<=28: raise ValueError
                if not numeric['loan_multiplier'].is_integer() or not numeric['loan_months'].is_integer() or not numeric['attendance_requirement'].is_integer() or not numeric['voting_requirement'].is_integer(): raise ValueError
                if not 1<=int(numeric['loan_multiplier'])<=10 or not 1<=int(numeric['loan_months'])<=60: raise ValueError
                if not 0<=numeric['attendance_requirement']<=100 or not 0<=numeric['voting_requirement']<=100 or numeric['loan_interest']>100: raise ValueError
            except (TypeError,ValueError,OverflowError):
                flash('Check the rules: money and interest cannot be negative, loan interest must be at most 100%, loan multiplier 1–10, repayment 1–60 months, due day 0 (off) or 1–28, and attendance/voting 0–100%. No changes were saved.','danger')
                return redirect(url_for('constitution',chama_id=chama_id))
            db().begin()
            current=db().one("SELECT * FROM chama_constitutions WHERE chama_id=?",(chama_id,))
            if current:
                import json
                version=int(db().val('SELECT COALESCE(MAX(version_no),0)+1 FROM chama_constitution_history WHERE chama_id=?',(chama_id,),1) or 1)
                snapshot={k:current.get(k) for k in ('monthly_contribution_cents','joining_fee_cents','loan_interest_bps','loan_multiplier','loan_months','late_fine_cents','contribution_due_day','attendance_requirement','voting_requirement','updated_by','updated_at')}
                db().insert('chama_constitution_history',chama_id=chama_id,version_no=version,snapshot_json=json.dumps(snapshot,sort_keys=True),changed_by=me['user_id'],changed_at=S.iso(S.now_utc()))
            db().execute("""UPDATE chama_constitutions SET monthly_contribution_cents=?,joining_fee_cents=?,loan_interest_bps=?,loan_multiplier=?,loan_months=?,
                            late_fine_cents=?,contribution_due_day=?,attendance_requirement=?,voting_requirement=?,updated_by=?,updated_at=? WHERE chama_id=?""",
                        (int(float(vals['monthly_contribution'] or 0)*100),int(float(vals['joining_fee'] or 0)*100),int(float(vals['loan_interest'] or 0)*100),
                         int(vals['loan_multiplier'] or 1),int(vals['loan_months'] or 1),int(float(vals['late_fine'] or 0)*100),contribution_due_day,
                         int(float(vals['attendance_requirement'] or 0)),int(float(vals['voting_requirement'] or 0)),me['user_id'],S.iso(S.now_utc()),chama_id))
            audit(db(),me['user_id'],'CONSTITUTION_UPDATED','constitution',chama_id,chama_id); db().commit(); flash('Chama constitution saved. Previous rules were preserved in version history.','success')
            return redirect(url_for('constitution',chama_id=chama_id))
        row=db().one("SELECT * FROM chama_constitutions WHERE chama_id=?",(chama_id,))
        history=db().all('SELECT version_no,snapshot_json,changed_at FROM chama_constitution_history WHERE chama_id=? ORDER BY version_no DESC LIMIT 20',(chama_id,))
        return render_template('constitution.html',chama=chama,me=me,c=row,history=history)

    @app.route('/chamas/<int:chama_id>/members/<int:user_id>/passport')
    @login_required
    def member_passport(chama_id,user_id):
        chama,me,sub=ctx(chama_id)
        target=db().one("""SELECT u.*,m.role,m.joined_at,m.status FROM users u JOIN chama_members m ON m.user_id=u.id
                           WHERE m.chama_id=? AND m.user_id=? AND m.status='ACTIVE'""",(chama_id,user_id)) or abort(404)
        if user_id!=me['user_id'] and me['role'] not in STAFF: abort(403)
        savings=db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID'",(chama_id,user_id),0)
        loan=db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND user_id=? AND status IN ('ACTIVE','APPROVED')",(chama_id,user_id),0)
        contribs=db().val("SELECT COUNT(*) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID'",(chama_id,user_id),0)
        present=db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND user_id=? AND status='PRESENT'",(chama_id,user_id),0)
        totalmeet=db().val("SELECT COUNT(*) FROM attendance WHERE chama_id=? AND user_id=?",(chama_id,user_id),0)
        # Transparent participation indicator: attendance and contribution consistency only.
        # It is not a credit score and must not automatically change lending terms.
        from datetime import date, timedelta
        today=date.today()
        joined_raw=(target.get('joined_at') or today.isoformat())[:10]
        try: joined=date.fromisoformat(joined_raw)
        except ValueError: joined=today
        window_start=max(joined, today.replace(day=1)-timedelta(days=365))
        expected_months=max(1, min(12, (today.year-window_start.year)*12+today.month-window_start.month+1))
        periods=int(db().val("SELECT COUNT(DISTINCT period) FROM contributions WHERE chama_id=? AND user_id=? AND status='PAID' AND period>=?",(chama_id,user_id,window_start.strftime('%Y-%m')),0) or 0)
        attendance_pct=round((int(present or 0)*100/int(totalmeet)) if totalmeet else 0)
        contribution_pct=round(min(100,periods*100/expected_months))
        dimensions=[]
        if totalmeet: dimensions.append(attendance_pct)
        constitution=db().one('SELECT monthly_contribution_cents FROM chama_constitutions WHERE chama_id=?',(chama_id,))
        if constitution and int(constitution.get('monthly_contribution_cents') or 0)>0: dimensions.append(contribution_pct)
        participation_score=round(sum(dimensions)/len(dimensions)) if dimensions else None
        if participation_score is None: participation_badge='Getting started'
        elif participation_score>=85: participation_badge='Gold'
        elif participation_score>=65: participation_badge='Silver'
        else: participation_badge='Bronze'
        score_details={'attendance_pct':attendance_pct if totalmeet else None,'contribution_pct':contribution_pct if constitution and int(constitution.get('monthly_contribution_cents') or 0)>0 else None,'months_assessed':expected_months,'paid_periods':periods}
        return render_template('passport.html',chama=chama,me=me,target=target,savings=savings,loan=loan,contribs=contribs,present=present,totalmeet=totalmeet,participation_score=participation_score,participation_badge=participation_badge,score_details=score_details)

    @app.route('/chamas/<int:chama_id>/activity')
    @login_required
    def activity(chama_id):
        chama,me,sub=ctx(chama_id)
        rows=db().all("""SELECT l.*,u.name FROM ledger_transactions l LEFT JOIN users u ON u.id=l.user_id
                         WHERE l.chama_id=? ORDER BY l.id DESC LIMIT 100""",(chama_id,))
        return render_template('activity.html',chama=chama,me=me,rows=rows)

    @app.route('/chamas/<int:chama_id>/ai')
    @login_required
    def chama_ai(chama_id):
        chama,me,sub=ctx(chama_id)
        stats={
            'members':db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0),
            'savings':db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID'",(chama_id,),0),
            'loans':db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('ACTIVE','APPROVED')",(chama_id,),0),
            'investments':db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_investments WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0),
            'assets':db().val("SELECT COALESCE(SUM(current_value_cents),0) FROM chama_assets WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0),
            'goals':db().all("SELECT name,target_cents,current_cents FROM chama_goals WHERE chama_id=? AND status='ACTIVE'",(chama_id,))
        }
        answer=None
        q=(request.args.get('q') or '').strip()
        if q:
            ql=q.lower()
            if 'contribut' in ql and ('who' in ql or 'paid' in ql):
                period=date.today().isoformat()[:7]
                answer={'title':'Contribution check','text':f"{stats['members']-db().val('SELECT COUNT(DISTINCT user_id) FROM contributions WHERE chama_id=? AND period=? AND status=\'PAID\'',(chama_id,period),0)} member(s) have not recorded a contribution for {period}."}
            elif 'save' in ql:
                answer={'title':'Savings','text':f"Recorded contributions total KES {stats['savings']/100:,.2f}."}
            elif 'loan' in ql:
                answer={'title':'Loans','text':f"Outstanding approved/active loan balance is KES {stats['loans']/100:,.2f}."}
            elif 'invest' in ql:
                answer={'title':'Investments','text':f"Tracked investments are currently valued at KES {stats['investments']/100:,.2f}."}
            elif 'asset' in ql or 'wealth' in ql:
                answer={'title':'Group wealth','text':f"Tracked investments and assets total KES {(stats['investments']+stats['assets'])/100:,.2f}, before cash and other balances."}
            elif 'cash' in ql or 'ledger balance' in ql or 'available balance' in ql:
                cash=int(db().val("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0) FROM ledger_transactions WHERE chama_id=?",(chama_id,),0) or 0)
                answer={'title':'Recorded ledger balance','text':f"The net balance of posted ledger entries is KES {cash/100:,.2f}. This is a ledger snapshot, not a bank-confirmed available balance."}
            elif 'overdue' in ql or 'late loan' in ql:
                count=int(db().val("SELECT COUNT(*) FROM loans WHERE chama_id=? AND status='ACTIVE' AND due_date IS NOT NULL AND due_date<?",(chama_id,date.today().isoformat()),0) or 0)
                answer={'title':'Overdue loans','text':f"There are {count} active loan(s) with a recorded due date before today. Check each repayment history before contacting members."}
            elif 'promise' in ql or 'action item' in ql or 'commitment' in ql:
                open_count=int(db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN'",(chama_id,),0) or 0)
                overdue_count=int(db().val("SELECT COUNT(*) FROM meeting_actions WHERE chama_id=? AND status='OPEN' AND due_date IS NOT NULL AND due_date<?",(chama_id,date.today().isoformat()),0) or 0)
                answer={'title':'Meeting commitments','text':f"{open_count} commitment(s) remain open; {overdue_count} are past their due date. Open the Promise Tracker to review owners and meeting decisions."}
            elif 'goal' in ql or 'target' in ql:
                goals=db().all("SELECT name,target_cents,current_cents FROM chama_goals WHERE chama_id=? AND status='ACTIVE'",(chama_id,))
                remaining=sum(max(0,int(x['target_cents'])-int(x['current_cents'])) for x in goals)
                answer={'title':'Active goals','text':f"There are {len(goals)} active group goal(s), with a combined recorded remaining target of KES {remaining/100:,.2f}."}
            elif 'fair' in ql or 'dispute' in ql or 'consisten' in ql:
                answer={'title':'Fairness review','text':'Open Strategy & Continuity for explainable consistency checks. Flags are prompts to review records and group rules, not findings of wrongdoing.'}
            elif 'scenario' in ql or 'afford' in ql or 'what if' in ql or 'forecast' in ql:
                answer={'title':'What-if planning','text':'Open Strategy & Continuity to model a contribution drop, a stressed loan-default rate, a planned payout and a proposed new loan. The result is an estimate, not a promise of available cash.'}
            else:
                answer={'title':'ChamaPay Copilot','text':'I can explain recorded savings, loan balances, ledger balance, overdue loans, group wealth, goals and meeting commitments. For scenario planning and consistency checks, open Strategy & Continuity. Answers are based on recorded data, not external bank verification.'}
        return render_template('ai.html',chama=chama,me=me,stats=stats,answer=answer,q=q)


    @app.route('/help', methods=['GET','POST'])
    @login_required
    def help_center():
        if request.method=='POST':
            f=request.form
            subject=(f.get('subject') or '').strip()[:160]
            body=(f.get('body') or '').strip()[:3000]
            if not subject or not body:
                flash('Please provide a subject and message.','danger')
            else:
                cid=request.form.get('chama_id',type=int)
                db().insert('support_tickets',user_id=g.user['id'],chama_id=cid,subject=subject,body=body,status='OPEN',
                            created_at=S.iso(S.now_utc()),updated_at=S.iso(S.now_utc()))
                db().commit()
                flash('Support request sent.','success')
                return redirect(url_for('help_center'))
        tickets=db().all("SELECT t.*,c.name chama FROM support_tickets t LEFT JOIN chamas c ON c.id=t.chama_id WHERE t.user_id=? ORDER BY t.id DESC",(g.user['id'],))
        chamas=db().all("SELECT c.id,c.name FROM chamas c JOIN chama_members m ON m.chama_id=c.id WHERE m.user_id=? AND m.status='ACTIVE' ORDER BY c.name",(g.user['id'],))
        return render_template('help.html',tickets=tickets,chamas=chamas)

    @app.route('/search')
    @login_required
    def global_search():
        q=(request.args.get('q') or '').strip()
        results=[]
        if q:
            like='%'+q.lower()+'%'
            results=db().all("""SELECT 'member' AS kind,u.id AS user_id,u.name AS title,u.phone AS detail,m.chama_id AS chama_id,c.name AS chama
                FROM users u JOIN chama_members m ON m.user_id=u.id JOIN chamas c ON c.id=m.chama_id
                WHERE m.status='ACTIVE' AND (LOWER(u.name) LIKE ? OR LOWER(u.phone) LIKE ?)
                  AND m.chama_id IN (SELECT chama_id FROM chama_members WHERE user_id=? AND status='ACTIVE')
                LIMIT 50""",(like,like,g.user['id']))
        return render_template('search.html',q=q,results=results)

    @app.route('/chamas/<int:chama_id>/upgrade-receipt/<int:payment_id>')
    @login_required
    def upgrade_receipt(chama_id,payment_id):
        chama,me,sub=ctx(chama_id,allow_inactive=True)
        p=db().one("SELECT * FROM payments WHERE id=? AND chama_id=?",(payment_id,chama_id)) or abort(404)
        if p['created_by']!=me['user_id'] and me['role'] not in STAFF: abort(403)
        return render_template('receipt.html',chama=chama,p=p,me=me)
