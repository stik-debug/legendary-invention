"""ChamaPower: auditable collective projects, member votes, supplier quotes and delivery updates.

This module records proposals and evidence only. It does not transfer money, verify suppliers
automatically, or treat project budgets/quotes as ledger balances.
"""
from datetime import date, datetime, timezone
from math import ceil
from flask import abort, flash, g, redirect, render_template, request, url_for
import finance as F
from db import audit

STAFF = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY')
CATEGORIES = ('Bulk purchasing', 'Land & property', 'Farming', 'Equipment', 'Business project',
              'Emergency fund', 'Education', 'Other')
STATUS_LABELS = {
    'UNDER_REVIEW': 'Open for member votes',
    'APPROVED': 'Approved by group',
    'IN_PROGRESS': 'In progress',
    'COMPLETED': 'Completed',
    'REJECTED': 'Not approved',
}


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _cents(raw, allow_zero=False):
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    try:
        amount = Decimal((raw or '').strip())
        if not amount.is_finite() or amount < (Decimal('0') if allow_zero else Decimal('0.01')):
            raise ValueError
        return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, AttributeError):
        raise ValueError('Enter a valid KES amount.')


def register(app, db, ctx, login_required):
    @app.route('/chamas/<int:chama_id>/power', methods=['GET', 'POST'])
    @login_required
    def chama_power(chama_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        staff = me['role'] in STAFF
        if request.method == 'POST':
            title = (request.form.get('title') or '').strip()[:140]
            category = (request.form.get('category') or '').strip()
            description = (request.form.get('description') or '').strip()[:3000]
            deadline = (request.form.get('deadline') or '').strip() or None
            try:
                if deadline:
                    try:
                        date.fromisoformat(deadline)
                    except ValueError:
                        raise ValueError('Enter a valid target date.')
                target = _cents(request.form.get('target', '0'), allow_zero=True)
                if len(title) < 4:
                    raise ValueError('Give the project a clear title (at least 4 characters).')
                if category not in CATEGORIES:
                    raise ValueError('Choose a valid project category.')
                if len(description) < 20:
                    raise ValueError('Describe the opportunity, who benefits, and what needs to be checked (at least 20 characters).')
                now = _stamp()
                db().begin()
                pid = db().insert('chama_power_projects', chama_id=cid, title=title, category=category,
                    description=description, target_cents=target, deadline=deadline, status='UNDER_REVIEW',
                    created_by=uid, created_at=now, updated_at=now)
                audit(db(), uid, 'CHAMAPOWER_PROJECT_PROPOSED', 'chama_power_project', pid, cid,
                      {'title': title, 'category': category, 'target_cents': target})
                db().commit()
                flash('Project proposal recorded. Members can now review and vote; no money has moved.', 'success')
                return redirect(url_for('chama_power', chama_id=cid))
            except ValueError as e:
                db().rollback()
                flash(str(e), 'danger')
            except Exception:
                db().rollback()
                flash('The proposal could not be saved. No financial records were changed.', 'danger')

        projects = db().all("""SELECT p.*, u.name proposer,
            (SELECT COUNT(*) FROM chama_power_votes v JOIN chama_members cm ON cm.user_id=v.user_id AND cm.chama_id=v.chama_id AND cm.status='ACTIVE' WHERE v.project_id=p.id AND v.choice='YES') yes_votes,
            (SELECT COUNT(*) FROM chama_power_votes v JOIN chama_members cm ON cm.user_id=v.user_id AND cm.chama_id=v.chama_id AND cm.status='ACTIVE' WHERE v.project_id=p.id AND v.choice='NO') no_votes,
            (SELECT COUNT(*) FROM chama_power_votes v JOIN chama_members cm ON cm.user_id=v.user_id AND cm.chama_id=v.chama_id AND cm.status='ACTIVE' WHERE v.project_id=p.id AND v.choice='ABSTAIN') abstain_votes,
            (SELECT COUNT(*) FROM chama_members cm WHERE cm.chama_id=p.chama_id AND cm.status='ACTIVE') active_members,
            (SELECT choice FROM chama_power_votes v WHERE v.project_id=p.id AND v.user_id=?) my_vote,
            (SELECT COUNT(*) FROM chama_power_quotes q WHERE q.project_id=p.id) quote_count
            FROM chama_power_projects p LEFT JOIN users u ON u.id=p.created_by
            WHERE p.chama_id=? ORDER BY CASE p.status WHEN 'UNDER_REVIEW' THEN 0 WHEN 'APPROVED' THEN 1 WHEN 'IN_PROGRESS' THEN 2 ELSE 3 END,
            p.updated_at DESC,p.id DESC LIMIT 100""", (uid, cid))
        constitution = db().one('SELECT voting_requirement FROM chama_constitutions WHERE chama_id=?', (cid,)) or {'voting_requirement': 50}
        quorum_pct = max(0, min(100, int(constitution.get('voting_requirement', 50) or 0)))
        for p in projects:
            p['votes_cast'] = int(p.get('yes_votes') or 0) + int(p.get('no_votes') or 0) + int(p.get('abstain_votes') or 0)
            p['required_votes'] = ceil(int(p.get('active_members') or 0) * quorum_pct / 100)
            p['quorum_pct'] = quorum_pct
            p['status_label'] = STATUS_LABELS.get(p['status'], p['status'])
            p['quotes'] = db().all('SELECT * FROM chama_power_quotes WHERE project_id=? AND chama_id=? ORDER BY amount_cents,id', (p['id'],cid))
            p['updates'] = db().all("""SELECT x.*,u.name actor FROM chama_power_updates x
                LEFT JOIN users u ON u.id=x.user_id WHERE x.project_id=? AND x.chama_id=? ORDER BY x.id DESC LIMIT 10""",(p['id'],cid))
            p['comments'] = db().all("""SELECT x.*,u.name author FROM chama_power_comments x
                LEFT JOIN users u ON u.id=x.user_id WHERE x.project_id=? AND x.chama_id=? ORDER BY x.id ASC LIMIT 100""",(p['id'],cid))
            p['milestones'] = db().all("SELECT * FROM chama_power_milestones WHERE project_id=? AND chama_id=? ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, due_date, id", (p['id'],cid))
        return render_template('chama_power.html', chama=chama, me=me, staff=staff, projects=projects,
                               categories=CATEGORIES)

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/vote', methods=['POST'])
    @login_required
    def chama_power_vote(chama_id, project_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        project = db().one("SELECT * FROM chama_power_projects WHERE id=? AND chama_id=?", (project_id,cid))
        if not project: abort(404)
        if project['status'] != 'UNDER_REVIEW': 
            flash('Voting is closed for this project.', 'info')
            return redirect(url_for('chama_power', chama_id=cid))
        choice = (request.form.get('choice') or '').upper()
        if choice not in ('YES','NO','ABSTAIN'): abort(400)
        now = _stamp()
        try:
            db().begin()
            old = db().one('SELECT id FROM chama_power_votes WHERE project_id=? AND user_id=?', (project_id,uid))
            if old:
                db().execute('UPDATE chama_power_votes SET choice=?,created_at=? WHERE id=?', (choice,now,old['id']))
            else:
                db().insert('chama_power_votes', project_id=project_id, chama_id=cid, user_id=uid, choice=choice, created_at=now)
            audit(db(),uid,'CHAMAPOWER_VOTE_CAST','chama_power_project',project_id,cid,{'choice':choice})
            db().commit()
            flash('Your vote has been recorded. You can change it while voting is open.', 'success')
        except Exception:
            db().rollback()
            flash('Your vote could not be recorded. Please try again.', 'danger')
        return redirect(url_for('chama_power', chama_id=cid))

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/quote', methods=['POST'])
    @login_required
    def chama_power_quote(chama_id, project_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        if me['role'] not in STAFF: abort(403)
        project = db().one('SELECT * FROM chama_power_projects WHERE id=? AND chama_id=?',(project_id,cid))
        if not project: abort(404)
        if project['status'] in ('COMPLETED','REJECTED'): abort(400)
        supplier = (request.form.get('supplier_name') or '').strip()[:160]
        contact = (request.form.get('supplier_contact') or '').strip()[:180]
        scope = (request.form.get('scope') or '').strip()[:1200]
        validity = (request.form.get('validity') or '').strip()[:100]
        evidence = (request.form.get('evidence_reference') or '').strip()[:240]
        try:
            amount = _cents(request.form.get('amount'))
            if len(supplier)<2 or len(scope)<5: raise ValueError('Enter the supplier name and describe what the quote covers.')
            now = _stamp()
            db().begin()
            qid=db().insert('chama_power_quotes',project_id=project_id,chama_id=cid,supplier_name=supplier,
                supplier_contact=contact or None,amount_cents=amount,scope=scope,validity=validity or None,
                evidence_reference=evidence or None,verification_status='UNVERIFIED',added_by=uid,created_at=now)
            audit(db(),uid,'CHAMAPOWER_QUOTE_ADDED','chama_power_quote',qid,cid,
                  {'project_id':project_id,'amount_cents':amount,'verification_status':'UNVERIFIED'})
            db().commit()
            flash('Quote saved for comparison. It is marked unverified until independently checked.', 'success')
        except ValueError as e:
            db().rollback(); flash(str(e),'danger')
        except Exception:
            db().rollback(); flash('Quote could not be saved.','danger')
        return redirect(url_for('chama_power',chama_id=cid))

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/status', methods=['POST'])
    @login_required
    def chama_power_status(chama_id, project_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        if me['role'] not in STAFF: abort(403)
        p=db().one('SELECT * FROM chama_power_projects WHERE id=? AND chama_id=?',(project_id,cid))
        if not p: abort(404)
        new_status=(request.form.get('status') or '').upper()
        allowed = {
            'UNDER_REVIEW': ('APPROVED','REJECTED'),
            'APPROVED': ('IN_PROGRESS','REJECTED'),
            'IN_PROGRESS': ('COMPLETED',),
        }
        if new_status not in allowed.get(p['status'], ()): abort(400)
        if new_status == 'APPROVED':
            yes=int(db().val("SELECT COUNT(*) FROM chama_power_votes v JOIN chama_members cm ON cm.user_id=v.user_id AND cm.chama_id=v.chama_id AND cm.status='ACTIVE' WHERE v.project_id=? AND v.choice='YES'",(project_id,),0) or 0)
            no=int(db().val("SELECT COUNT(*) FROM chama_power_votes v JOIN chama_members cm ON cm.user_id=v.user_id AND cm.chama_id=v.chama_id AND cm.status='ACTIVE' WHERE v.project_id=? AND v.choice='NO'",(project_id,),0) or 0)
            abstain=int(db().val("SELECT COUNT(*) FROM chama_power_votes v JOIN chama_members cm ON cm.user_id=v.user_id AND cm.chama_id=v.chama_id AND cm.status='ACTIVE' WHERE v.project_id=? AND v.choice='ABSTAIN'",(project_id,),0) or 0)
            active=int(db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(cid,),0) or 0)
            constitution=db().one('SELECT voting_requirement FROM chama_constitutions WHERE chama_id=?',(cid,)) or {'voting_requirement':50}
            pct=max(0,min(100,int(constitution.get('voting_requirement',50) or 0)))
            required=ceil(active*pct/100)
            if yes < 1 or yes <= no or yes+no+abstain < required:
                flash(f'Approval requires at least one YES vote, more YES than NO, and quorum of {required} votes ({pct}% of active members). Current participation: {yes+no+abstain}.','danger')
                return redirect(url_for('chama_power',chama_id=cid))
        notes=(request.form.get('outcome_notes') or '').strip()[:2000]
        if new_status == 'COMPLETED' and len(notes) < 10:
            flash('Add a meaningful completion note (at least 10 characters).', 'danger')
            return redirect(url_for('chama_power',chama_id=cid))
        now=_stamp()
        try:
            db().begin()
            db().execute('UPDATE chama_power_projects SET status=?,approved_by=CASE WHEN ?=? THEN ? ELSE approved_by END, outcome_notes=CASE WHEN ?=? THEN ? ELSE outcome_notes END, updated_at=? WHERE id=? AND chama_id=?',
                         (new_status,new_status,'APPROVED',uid,new_status,'COMPLETED',notes,now,project_id,cid))
            audit(db(),uid,'CHAMAPOWER_STATUS_CHANGED','chama_power_project',project_id,cid,
                  {'from':p['status'],'to':new_status,'outcome_notes':notes if new_status=='COMPLETED' else None})
            db().commit()
            flash('Project status updated. This records a group decision; it does not transfer funds or confirm a purchase.', 'success')
        except Exception:
            db().rollback(); flash('Project status could not be updated.','danger')
        return redirect(url_for('chama_power',chama_id=cid))

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/update', methods=['POST'])
    @login_required
    def chama_power_update(chama_id, project_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        if me['role'] not in STAFF: abort(403)
        p=db().one('SELECT * FROM chama_power_projects WHERE id=? AND chama_id=?',(project_id,cid))
        if not p: abort(404)
        if p['status'] not in ('APPROVED','IN_PROGRESS','COMPLETED'): abort(400)
        body=(request.form.get('body') or '').strip()[:2000]
        kind=(request.form.get('update_type') or 'PROGRESS').upper()
        if kind not in ('PROGRESS','DELIVERY','RECEIPT','DECISION','MAINTENANCE'): abort(400)
        evidence=(request.form.get('evidence_reference') or '').strip()[:240]
        try:
            amount=_cents(request.form.get('amount','0'),allow_zero=True)
            if len(body)<5: raise ValueError('Write a useful progress update.')
            now=_stamp()
            db().begin()
            xid=db().insert('chama_power_updates',project_id=project_id,chama_id=cid,user_id=uid,
                update_type=kind,body=body,amount_cents=amount,evidence_reference=evidence or None,created_at=now)
            db().execute('UPDATE chama_power_projects SET updated_at=? WHERE id=? AND chama_id=?',(now,project_id,cid))
            audit(db(),uid,'CHAMAPOWER_UPDATE_ADDED','chama_power_update',xid,cid,
                  {'project_id':project_id,'update_type':kind,'amount_cents':amount,'evidence_reference':evidence or None})
            db().commit()
            flash('Project evidence/update recorded. Amounts here are not ledger transactions.', 'success')
        except ValueError as e:
            db().rollback(); flash(str(e),'danger')
        except Exception:
            db().rollback(); flash('The update could not be saved.','danger')
        return redirect(url_for('chama_power',chama_id=cid))


    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/comment', methods=['POST'])
    @login_required
    def chama_power_comment(chama_id, project_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        p = db().one('SELECT id,status FROM chama_power_projects WHERE id=? AND chama_id=?', (project_id,cid))
        if not p: abort(404)
        body = (request.form.get('body') or '').strip()
        if len(body) < 3 or len(body) > 1500:
            flash('Comments must be between 3 and 1,500 characters.', 'danger')
            return redirect(url_for('chama_power', chama_id=cid))
        now = _stamp()
        try:
            db().begin()
            comment_id = db().insert('chama_power_comments', project_id=project_id, chama_id=cid,
                user_id=uid, body=body, created_at=now)
            audit(db(), uid, 'CHAMAPOWER_COMMENT_ADDED', 'chama_power_comment', comment_id, cid,
                  {'project_id': project_id})
            db().commit()
            flash('Your project comment was recorded.', 'success')
        except Exception:
            db().rollback()
            flash('Comment could not be saved. Please try again.', 'danger')
        return redirect(url_for('chama_power', chama_id=cid))

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/milestone', methods=['POST'])
    @login_required
    def chama_power_milestone(chama_id, project_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        if me['role'] not in STAFF: abort(403)
        p = db().one('SELECT * FROM chama_power_projects WHERE id=? AND chama_id=?', (project_id,cid))
        if not p: abort(404)
        if p['status'] not in ('APPROVED','IN_PROGRESS'): abort(400)
        title = (request.form.get('title') or '').strip()[:160]
        due = (request.form.get('due_date') or '').strip() or None
        try:
            if len(title) < 3: raise ValueError('Milestone title must be at least 3 characters.')
            if due:
                try:
                    date.fromisoformat(due)
                except ValueError:
                    raise ValueError('Enter a valid milestone date.')
            now = _stamp()
            db().begin()
            mid = db().insert('chama_power_milestones', project_id=project_id, chama_id=cid,
                title=title, due_date=due, status='OPEN', created_by=uid, created_at=now, updated_at=now)
            audit(db(), uid, 'CHAMAPOWER_MILESTONE_ADDED', 'chama_power_milestone', mid, cid,
                  {'project_id': project_id, 'due_date': due})
            db().commit()
            flash('Milestone added to the project plan.', 'success')
        except ValueError as e:
            db().rollback(); flash(str(e) or 'Enter a valid date.', 'danger')
        except Exception:
            db().rollback(); flash('Milestone could not be saved.', 'danger')
        return redirect(url_for('chama_power', chama_id=cid))

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/milestone/<int:milestone_id>/complete', methods=['POST'])
    @login_required
    def chama_power_milestone_complete(chama_id, project_id, milestone_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        if me['role'] not in STAFF: abort(403)
        milestone = db().one('SELECT * FROM chama_power_milestones WHERE id=? AND project_id=? AND chama_id=?',
                             (milestone_id, project_id, cid))
        if not milestone: abort(404)
        if milestone['status'] == 'COMPLETED':
            flash('This milestone is already completed.', 'info')
            return redirect(url_for('chama_power', chama_id=cid))
        note = (request.form.get('completion_note') or '').strip()[:1000]
        if len(note) < 5:
            flash('Add a short completion note (at least 5 characters).', 'danger')
            return redirect(url_for('chama_power', chama_id=cid))
        now = _stamp()
        try:
            db().begin()
            db().execute("UPDATE chama_power_milestones SET status='COMPLETED',completion_note=?,completed_by=?,updated_at=? WHERE id=? AND project_id=? AND chama_id=?",
                         (note, uid, now, milestone_id, project_id, cid))
            audit(db(), uid, 'CHAMAPOWER_MILESTONE_COMPLETED', 'chama_power_milestone', milestone_id, cid,
                  {'project_id': project_id, 'completion_note': note})
            db().commit()
            flash('Milestone completion recorded.', 'success')
        except Exception:
            db().rollback(); flash('Milestone could not be updated.', 'danger')
        return redirect(url_for('chama_power', chama_id=cid))

    @app.route('/chamas/<int:chama_id>/power/<int:project_id>/quote/<int:quote_id>/review', methods=['POST'])
    @login_required
    def chama_power_quote_review(chama_id, project_id, quote_id):
        chama, me, sub = ctx(chama_id)
        cid, uid = chama_id, g.user['id']
        if me['role'] not in STAFF: abort(403)
        quote = db().one('SELECT * FROM chama_power_quotes WHERE id=? AND project_id=? AND chama_id=?',
                         (quote_id, project_id, cid))
        if not quote: abort(404)
        decision = (request.form.get('decision') or '').upper()
        note = (request.form.get('verification_note') or '').strip()[:1200]
        if decision not in ('VERIFIED','FLAGGED') or len(note) < 15:
            flash('Choose a review outcome and record what was checked (at least 15 characters).', 'danger')
            return redirect(url_for('chama_power', chama_id=cid))
        now = _stamp()
        try:
            db().begin()
            db().execute('UPDATE chama_power_quotes SET verification_status=?,verification_note=?,verified_by=?,verified_at=? WHERE id=? AND project_id=? AND chama_id=?',
                         (decision, note, uid, now, quote_id, project_id, cid))
            audit(db(), uid, 'CHAMAPOWER_QUOTE_REVIEWED', 'chama_power_quote', quote_id, cid,
                  {'project_id': project_id, 'decision': decision, 'verification_note': note})
            db().commit()
            if decision == 'VERIFIED':
                flash('Quote review recorded. This means your official checked the stated evidence; it is not a guarantee of supplier reliability.', 'success')
            else:
                flash('Quote flagged for follow-up. Do not rely on it until concerns are resolved.', 'warning')
        except Exception:
            db().rollback(); flash('Quote review could not be saved.', 'danger')
        return redirect(url_for('chama_power', chama_id=cid))
