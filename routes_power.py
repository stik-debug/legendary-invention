"""ChamaPower: auditable collective projects, member votes, supplier quotes and delivery updates.

This module records proposals and evidence only. It does not transfer money, verify suppliers
automatically, or treat project budgets/quotes as ledger balances.
"""
from datetime import datetime, timezone
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
            (SELECT COUNT(*) FROM chama_power_votes v WHERE v.project_id=p.id AND v.choice='YES') yes_votes,
            (SELECT COUNT(*) FROM chama_power_votes v WHERE v.project_id=p.id AND v.choice='NO') no_votes,
            (SELECT COUNT(*) FROM chama_power_votes v WHERE v.project_id=p.id AND v.choice='ABSTAIN') abstain_votes,
            (SELECT choice FROM chama_power_votes v WHERE v.project_id=p.id AND v.user_id=?) my_vote,
            (SELECT COUNT(*) FROM chama_power_quotes q WHERE q.project_id=p.id) quote_count
            FROM chama_power_projects p LEFT JOIN users u ON u.id=p.created_by
            WHERE p.chama_id=? ORDER BY CASE p.status WHEN 'UNDER_REVIEW' THEN 0 WHEN 'APPROVED' THEN 1 WHEN 'IN_PROGRESS' THEN 2 ELSE 3 END,
            p.updated_at DESC,p.id DESC LIMIT 100""", (uid, cid))
        for p in projects:
            p['status_label'] = STATUS_LABELS.get(p['status'], p['status'])
            p['quotes'] = db().all('SELECT * FROM chama_power_quotes WHERE project_id=? AND chama_id=? ORDER BY amount_cents,id', (p['id'],cid))
            p['updates'] = db().all("""SELECT x.*,u.name actor FROM chama_power_updates x
                LEFT JOIN users u ON u.id=x.user_id WHERE x.project_id=? AND x.chama_id=? ORDER BY x.id DESC LIMIT 10""",(p['id'],cid))
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
            yes=int(db().val("SELECT COUNT(*) FROM chama_power_votes WHERE project_id=? AND choice='YES'",(project_id,),0) or 0)
            no=int(db().val("SELECT COUNT(*) FROM chama_power_votes WHERE project_id=? AND choice='NO'",(project_id,),0) or 0)
            if yes < 1 or yes <= no:
                flash('Approval requires at least one YES vote and more YES than NO votes.','danger')
                return redirect(url_for('chama_power',chama_id=cid))
        notes=(request.form.get('outcome_notes') or '').strip()[:2000]
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
