"""Community growth features: provider marketplace, welfare funds and chama collaboration.
All workflows are record-keeping only; they do not move money or certify providers automatically.
"""
from datetime import datetime, timezone
import re
from flask import abort, flash, g, redirect, render_template, request, url_for
import finance as F
import services as S
from db import audit

ADMIN_ROLES = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY')
CATEGORIES = ('Bookkeeping', 'Legal support', 'Insurance', 'Training', 'Financial education', 'Other')
POST_KINDS = ('DISCUSSION', 'TEMPLATE', 'EVENT', 'PROJECT')
# Starter profanity filter, backed up by community reporting and moderator review.
PROFANITY = re.compile(r'(?i)(?<![a-z0-9])(?:fuck(?:er|ing|ed|s)?|shit(?:ty|ting|s)?|bitch(?:es)?|asshole(?:s)?|motherfucker|cunt(?:s)?|bastard(?:s)?|dickhead(?:s)?|nigger(?:s)?|faggot(?:s)?|pussy|whore(?:s)?|slut(?:s)?)(?![a-z0-9])')

def respectful_text(value, label, minimum, maximum):
    value=clean(value,label,minimum,maximum)
    if PROFANITY.search(value):
        raise S.BusinessError(f'{label} contains language that is not allowed. Please rewrite it respectfully.')
    if re.search(r'(.)\1{9,}', value):
        raise S.BusinessError(f'{label} looks like spam. Please write a clear message.')
    return value

def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def clean(value, label, minimum=2, maximum=1500):
    value=(value or '').strip()
    if not minimum <= len(value) <= maximum:
        raise S.BusinessError(f'{label} must be {minimum}–{maximum} characters.')
    return value

def register(app, db, ctx, login_required, owner_required):
    @app.route('/marketplace')
    @login_required
    def marketplace():
        category=(request.args.get('category') or '').strip()
        if category not in CATEGORIES: category=''
        sql="SELECT * FROM marketplace_providers WHERE status='VERIFIED'"
        args=[]
        if category: sql+=' AND category=?'; args.append(category)
        sql+=' ORDER BY category,name LIMIT 100'
        rows=db().all(sql,args)
        mine=db().all('SELECT * FROM marketplace_providers WHERE user_id=? ORDER BY id DESC LIMIT 30',(g.user['id'],))
        return render_template('marketplace.html', rows=rows, mine=mine, categories=CATEGORIES, category=category)

    @app.route('/marketplace/providers', methods=['POST'])
    @login_required
    def marketplace_add():
        try:
            name=clean(request.form.get('name'),'Provider name',2,100)
            category=request.form.get('category','')
            if category not in CATEGORIES: raise S.BusinessError('Choose a valid service category.')
            desc=clean(request.form.get('description'),'Description',10,1200)
            email=(request.form.get('contact_email') or '').strip()[:160] or None
            phone=(request.form.get('contact_phone') or '').strip()[:40] or None
            county=(request.form.get('county') or '').strip()[:100] or None
            website=(request.form.get('website') or '').strip()[:250] or None
            credentials=(request.form.get('credentials') or '').strip()[:1000] or None
            if website and not website.startswith('https://'): raise S.BusinessError('Website must start with https://.')
            with db().tx():
                pid=db().insert('marketplace_providers',user_id=g.user['id'],name=name,category=category,description=desc,county=county,
                    contact_email=email,contact_phone=phone,website=website,credentials=credentials,status='PENDING',created_at=stamp())
                audit(db(),g.user['id'],'MARKETPLACE_PROVIDER_SUBMITTED','marketplace_provider',pid,None,{'category':category})
            flash('Provider submitted for review. It will not appear as verified until an administrator approves it.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('marketplace'))

    @app.route('/marketplace/<int:provider_id>/interest', methods=['POST'])
    @login_required
    def marketplace_interest(provider_id):
        provider=db().one("SELECT id FROM marketplace_providers WHERE id=? AND status='VERIFIED'",(provider_id,))
        if not provider: abort(404)
        chama_id=request.form.get('chama_id',type=int)
        if chama_id:
            ctx(chama_id)
        message=(request.form.get('message') or '').strip()[:500] or None
        try:
            with db().tx():
                db().insert('marketplace_interests',provider_id=provider_id,user_id=g.user['id'],chama_id=chama_id,message=message,created_at=stamp())
            flash('Interest recorded. Contact the provider directly using their listed details.','success')
        except Exception:
            flash('You have already registered interest in this provider for this chama.','info')
        return redirect(url_for('marketplace'))

    @app.route('/owner/marketplace')
    @owner_required
    def marketplace_review():
        rows=db().all("SELECT p.*,u.name submitter FROM marketplace_providers p JOIN users u ON u.id=p.user_id WHERE p.status='PENDING' ORDER BY p.id LIMIT 100")
        return render_template('marketplace_review.html',rows=rows)

    @app.route('/owner/marketplace/<int:provider_id>/<action>', methods=['POST'])
    @owner_required
    def marketplace_decide(provider_id,action):
        if action not in ('verify','reject'): abort(404)
        row=db().one("SELECT * FROM marketplace_providers WHERE id=? AND status='PENDING'",(provider_id,))
        if not row: abort(404)
        status='VERIFIED' if action=='verify' else 'REJECTED'
        with db().tx():
            db().execute('UPDATE marketplace_providers SET status=?,reviewed_at=?,reviewed_by=? WHERE id=? AND status=\'PENDING\'',(status,stamp(),g.user['id'],provider_id))
            audit(db(),g.user['id'],'MARKETPLACE_PROVIDER_'+status,'marketplace_provider',provider_id,None,{'category':row['category']})
        flash('Provider review saved. Verification is a platform review, not a guarantee of service quality.','success')
        return redirect(url_for('marketplace_review'))

    @app.route('/chamas/<int:chama_id>/welfare')
    @login_required
    def welfare(chama_id):
        chama,me,sub=ctx(chama_id)
        funds=db().all("SELECT f.*,u.name creator,(SELECT COUNT(*) FROM welfare_requests r WHERE r.fund_id=f.id) request_count FROM welfare_funds f LEFT JOIN users u ON u.id=f.created_by WHERE f.chama_id=? ORDER BY f.id DESC LIMIT 100",(chama_id,))
        requests=db().all("SELECT r.*,u.name requester,f.name fund_name FROM welfare_requests r JOIN users u ON u.id=r.user_id JOIN welfare_funds f ON f.id=r.fund_id WHERE r.chama_id=? AND (r.user_id=? OR ? IN ('CHAMA_ADMIN','TREASURER','SECRETARY')) ORDER BY r.id DESC LIMIT 100",(chama_id,g.user['id'],me['role']))
        return render_template('welfare.html',chama=chama,me=me,funds=funds,requests=requests,can=me['role'] in ADMIN_ROLES)

    @app.route('/chamas/<int:chama_id>/welfare/create',methods=['POST'])
    @login_required
    def welfare_create(chama_id):
        chama,me,sub=ctx(chama_id,roles=ADMIN_ROLES)
        try:
            name=clean(request.form.get('name'),'Fund name',3,100); purpose=clean(request.form.get('purpose'),'Purpose',10,1000)
            eligibility=clean(request.form.get('eligibility'),'Eligibility rules',5,1000)
            target=F.parse_kes(request.form.get('target') or '0',0); budget=F.parse_kes(request.form.get('budget') or '0',0)
            if target<0 or budget<0: raise S.BusinessError('Amounts cannot be negative.')
            with db().tx():
                fid=db().insert('welfare_funds',chama_id=chama_id,name=name,purpose=purpose,target_cents=target,budget_cents=budget,eligibility=eligibility,status='OPEN',created_by=g.user['id'],created_at=stamp())
                audit(db(),g.user['id'],'WELFARE_FUND_CREATED','welfare_fund',fid,chama_id,{'name':name})
            flash('Welfare fund created. Requests and approvals are tracked separately from the financial ledger.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('welfare',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/welfare/<int:fund_id>/request',methods=['POST'])
    @login_required
    def welfare_request(chama_id,fund_id):
        ctx(chama_id)
        fund=db().one("SELECT * FROM welfare_funds WHERE id=? AND chama_id=? AND status='OPEN'",(fund_id,chama_id))
        if not fund: abort(404)
        try:
            amount=F.parse_kes(request.form.get('amount') or '',1)
            reason=clean(request.form.get('reason'),'Request reason',10,1000)
            with db().tx():
                rid=db().insert('welfare_requests',fund_id=fund_id,chama_id=chama_id,user_id=g.user['id'],amount_cents=amount,reason=reason,status='PENDING',created_at=stamp())
                audit(db(),g.user['id'],'WELFARE_REQUESTED','welfare_request',rid,chama_id,{'fund_id':fund_id,'amount_cents':amount})
            flash('Welfare request submitted for review.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('welfare',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/welfare/requests/<int:request_id>/<action>',methods=['POST'])
    @login_required
    def welfare_decide(chama_id,request_id,action):
        ctx(chama_id,roles=ADMIN_ROLES)
        if action not in ('approve','decline'): abort(404)
        note=(request.form.get('note') or '').strip()[:500] or None
        row=db().one("SELECT * FROM welfare_requests WHERE id=? AND chama_id=? AND status='PENDING'",(request_id,chama_id))
        if not row: abort(404)
        status='APPROVED' if action=='approve' else 'DECLINED'
        with db().tx():
            db().execute("UPDATE welfare_requests SET status=?,decision_note=?,decided_by=?,decided_at=? WHERE id=? AND status='PENDING'",(status,note,g.user['id'],stamp(),request_id))
            audit(db(),g.user['id'],'WELFARE_REQUEST_'+status,'welfare_request',request_id,chama_id,{'fund_id':row['fund_id']})
        flash('Request '+status.lower()+'. Approval records the decision but does not transfer money.','success')
        return redirect(url_for('welfare',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/welfare/<int:fund_id>/close',methods=['POST'])
    @login_required
    def welfare_close(chama_id,fund_id):
        ctx(chama_id,roles=ADMIN_ROLES)
        with db().tx(): db().execute("UPDATE welfare_funds SET status='CLOSED',closed_at=? WHERE id=? AND chama_id=? AND status='OPEN'",(stamp(),fund_id,chama_id)); audit(db(),g.user['id'],'WELFARE_FUND_CLOSED','welfare_fund',fund_id,chama_id,{})
        flash('Fund closed to new requests. Historical records remain available.','success')
        return redirect(url_for('welfare',chama_id=chama_id))

    @app.route('/collaboration')
    @login_required
    def collaboration():
        rows=db().all("SELECT p.*,u.name author,c.name chama,(SELECT COUNT(*) FROM collaboration_replies r WHERE r.post_id=p.id AND r.status='ACTIVE') reply_count FROM collaboration_posts p JOIN users u ON u.id=p.user_id LEFT JOIN chamas c ON c.id=p.chama_id WHERE p.status='ACTIVE' AND p.visibility='COMMUNITY' ORDER BY p.id DESC LIMIT 100")
        for p in rows:
            p['replies']=db().all("SELECT r.*,u.name author FROM collaboration_replies r JOIN users u ON u.id=r.user_id WHERE r.post_id=? AND r.status='ACTIVE' ORDER BY r.id ASC LIMIT 100",(p['id'],))
        return render_template('collaboration.html',rows=rows,kinds=POST_KINDS)

    @app.route('/collaboration/post',methods=['POST'])
    @login_required
    def collaboration_post():
        try:
            kind=request.form.get('kind','DISCUSSION')
            if kind not in POST_KINDS: raise S.BusinessError('Choose a valid post type.')
            title=respectful_text(request.form.get('title'),'Title',3,120)
            body=respectful_text(request.form.get('body'),'Details',10,1800)
            chama_id=request.form.get('chama_id',type=int)
            if chama_id: ctx(chama_id,roles=ADMIN_ROLES)
            county=(request.form.get('county') or '').strip()[:100] or None
            with db().tx():
                pid=db().insert('collaboration_posts',chama_id=chama_id,user_id=g.user['id'],kind=kind,title=title,body=body,county=county,visibility='COMMUNITY',status='ACTIVE',created_at=stamp())
                audit(db(),g.user['id'],'COLLABORATION_POST_CREATED','collaboration_post',pid,chama_id,{'kind':kind})
            flash('Your post is live. Keep the discussion respectful and protect private information.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('collaboration'))

    @app.route('/collaboration/<int:post_id>/edit',methods=['GET','POST'])
    @login_required
    def collaboration_post_edit(post_id):
        post=db().one("SELECT * FROM collaboration_posts WHERE id=? AND user_id=? AND status='ACTIVE' AND visibility='COMMUNITY'",(post_id,g.user['id']))
        if not post:
            # Do not disclose whether another user's post exists.
            abort(404)
        if request.method == 'POST':
            try:
                kind=request.form.get('kind',post['kind'])
                if kind not in POST_KINDS: raise S.BusinessError('Choose a valid post type.')
                title=respectful_text(request.form.get('title'),'Title',3,120)
                body=respectful_text(request.form.get('body'),'Details',10,1800)
                county=(request.form.get('county') or '').strip()[:100] or None
                with db().tx():
                    db().execute("UPDATE collaboration_posts SET kind=?,title=?,body=?,county=? WHERE id=? AND user_id=? AND status='ACTIVE'",(kind,title,body,county,post_id,g.user['id']))
                    audit(db(),g.user['id'],'COLLABORATION_POST_EDITED','collaboration_post',post_id,post['chama_id'],{'kind':kind})
                flash('Your discussion was updated.','success')
                return redirect(url_for('collaboration')+'#post-'+str(post_id))
            except S.BusinessError as e:
                flash(str(e),'warning')
        return render_template('collaboration_edit.html',post=post,kinds=POST_KINDS)

    @app.route('/collaboration/<int:post_id>/delete',methods=['POST'])
    @login_required
    def collaboration_post_delete(post_id):
        post=db().one("SELECT * FROM collaboration_posts WHERE id=? AND user_id=? AND status='ACTIVE' AND visibility='COMMUNITY'",(post_id,g.user['id']))
        if not post: abort(404)
        with db().tx():
            # Soft-delete: preserve the original post and replies for audit/moderation history.
            db().execute("UPDATE collaboration_posts SET status='DELETED' WHERE id=? AND user_id=? AND status='ACTIVE'",(post_id,g.user['id']))
            audit(db(),g.user['id'],'COLLABORATION_POST_DELETED','collaboration_post',post_id,post['chama_id'],{'title':post['title']})
        flash('Your discussion was removed. Its history is retained securely for moderation and audit purposes.','success')
        return redirect(url_for('collaboration'))

    @app.route('/collaboration/<int:post_id>/reply',methods=['POST'])
    @login_required
    def collaboration_reply(post_id):
        try:
            post=db().one("SELECT id FROM collaboration_posts WHERE id=? AND status='ACTIVE' AND visibility='COMMUNITY'",(post_id,))
            if not post: abort(404)
            body=respectful_text(request.form.get('body'),'Reply',2,1200)
            with db().tx():
                rid=db().insert('collaboration_replies',post_id=post_id,user_id=g.user['id'],body=body,status='ACTIVE',created_at=stamp())
                audit(db(),g.user['id'],'COLLABORATION_REPLY_CREATED','collaboration_reply',rid,None,{'post_id':post_id})
            flash('Reply added to the discussion.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('collaboration')+'#post-'+str(post_id))

    @app.route('/collaboration/<int:post_id>/report',methods=['POST'])
    @login_required
    def collaboration_report(post_id):
        post=db().one("SELECT id FROM collaboration_posts WHERE id=? AND status='ACTIVE' AND visibility='COMMUNITY'",(post_id,))
        if not post: abort(404)
        reply_id=request.form.get('reply_id',type=int)
        if reply_id and not db().one("SELECT id FROM collaboration_replies WHERE id=? AND post_id=? AND status='ACTIVE'",(reply_id,post_id)): abort(404)
        reason=request.form.get('reason','OTHER')
        allowed=('ABUSIVE','PROFANITY','SPAM','PRIVATE_INFO','HARASSMENT','OTHER')
        if reason not in allowed: reason='OTHER'
        details=(request.form.get('details') or '').strip()[:500] or None
        with db().tx():
            db().insert('collaboration_reports',post_id=post_id,reply_id=reply_id,reporter_id=g.user['id'],reason=reason,details=details,status='OPEN',created_at=stamp())
            audit(db(),g.user['id'],'COLLABORATION_CONTENT_REPORTED','collaboration_post',post_id,None,{'reply_id':reply_id,'reason':reason})
        flash('Report sent to the moderation queue. Thank you for helping keep the community safe.','success')
        return redirect(url_for('collaboration')+'#post-'+str(post_id))

    @app.route('/owner/community-moderation')
    @owner_required
    def collaboration_moderation():
        reports=db().all("SELECT r.*,p.title,u.name reporter,pu.name post_author,ru.name reply_author FROM collaboration_reports r JOIN collaboration_posts p ON p.id=r.post_id JOIN users u ON u.id=r.reporter_id JOIN users pu ON pu.id=p.user_id LEFT JOIN collaboration_replies rr ON rr.id=r.reply_id LEFT JOIN users ru ON ru.id=rr.user_id WHERE r.status='OPEN' ORDER BY r.id ASC LIMIT 200")
        return render_template('collaboration_moderation.html',reports=reports)

    @app.route('/owner/community-moderation/<int:report_id>/<action>',methods=['POST'])
    @owner_required
    def collaboration_moderation_action(report_id,action):
        if action not in ('dismiss','hide'): abort(404)
        report=db().one("SELECT * FROM collaboration_reports WHERE id=? AND status='OPEN'",(report_id,))
        if not report: abort(404)
        with db().tx():
            if action=='hide':
                if report['reply_id']:
                    db().execute("UPDATE collaboration_replies SET status='HIDDEN' WHERE id=?",(report['reply_id'],))
                else:
                    db().execute("UPDATE collaboration_posts SET status='HIDDEN' WHERE id=?",(report['post_id'],))
            db().execute("UPDATE collaboration_reports SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",('DISMISSED' if action=='dismiss' else 'ACTIONED',g.user['id'],stamp(),report_id))
            audit(db(),g.user['id'],'COLLABORATION_REPORT_'+action.upper(),'collaboration_report',report_id,None,{'post_id':report['post_id'],'reply_id':report['reply_id']})
        flash('Moderation decision saved. Hidden content remains in the database for audit purposes.','success')
        return redirect(url_for('collaboration_moderation'))
