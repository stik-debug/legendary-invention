"""Community growth features: provider marketplace, welfare funds and chama collaboration.
All workflows are record-keeping only; they do not move money or certify providers automatically.
"""
from datetime import datetime, timezone
from uuid import uuid4
import re
from flask import abort, flash, g, redirect, render_template, request, url_for
import finance as F
import services as S
import notify as N
from db import audit

ADMIN_ROLES = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY')
CATEGORIES = ('Bookkeeping', 'Legal support', 'Insurance', 'Training', 'Financial education', 'Other')
POST_KINDS = ('DISCUSSION', 'TEMPLATE', 'EVENT', 'PROJECT')
# Starter profanity filter, backed up by community reporting and moderator review.
PROFANITY = re.compile(r'(?i)(?<![a-z0-9])(?:fuck(?:er|ing|ed|s)?|shit(?:ty|ting|s)?|bitch(?:es)?|asshole(?:s)?|motherfucker|cunt(?:s)?|bastard(?:s)?|dickhead(?:s)?|nigger(?:s)?|faggot(?:s)?|pussy|whore(?:s)?|slut(?:s)?)(?![a-z0-9])')
MISLEADING_CLAIMS = re.compile(r'(?i)\b(?:guaranteed?\s+(?:returns?|profits?|income|earnings)|risk[- ]free\s+(?:investment|returns?|profits?)|100\s*%\s*(?:profit|returns?)|double\s+your\s+money|no[- ]risk\s+(?:investment|returns?)|assured\s+(?:investment\s+)?returns?)\b')

def respectful_text(value, label, minimum, maximum):
    value=clean(value,label,minimum,maximum)
    if PROFANITY.search(value):
        raise S.BusinessError(f'{label} contains language that is not allowed. Please rewrite it respectfully.')
    if re.search(r'(.)\1{9,}', value):
        raise S.BusinessError(f'{label} looks like spam. Please write a clear message.')
    if MISLEADING_CLAIMS.search(value):
        raise S.BusinessError(f'{label} appears to promise guaranteed financial returns or risk-free profits. Remove the claim and state verifiable facts instead.')
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
        query=(request.args.get('q') or '').strip()[:100]
        county=(request.args.get('county') or '').strip()[:100]
        sort=(request.args.get('sort') or 'recommended').strip()
        order_by={'recommended':'category,name','name':'name COLLATE NOCASE','newest':'id DESC'}.get(sort,'category,name')
        sql="""SELECT p.*,
            (SELECT COUNT(*) FROM marketplace_reviews r WHERE r.provider_id=p.id AND r.status='PUBLISHED') review_count,
            (SELECT ROUND(AVG(r.rating),1) FROM marketplace_reviews r WHERE r.provider_id=p.id AND r.status='PUBLISHED') average_rating
            FROM marketplace_providers p WHERE p.status='VERIFIED'"""
        args=[]
        if category: sql+=' AND category=?'; args.append(category)
        if county: sql+=' AND county LIKE ?'; args.append('%'+county+'%')
        if query:
            sql+=' AND (name LIKE ? OR description LIKE ? OR category LIKE ? OR county LIKE ?)'
            term='%'+query+'%'; args.extend([term,term,term,term])
        sql+=' ORDER BY '+order_by+' LIMIT 100'
        rows=db().all(sql,args)
        for provider_row in rows:
            provider_row['recent_reviews']=db().all("SELECT r.*,u.name reviewer_name FROM marketplace_reviews r JOIN users u ON u.id=r.user_id WHERE r.provider_id=? AND r.status='PUBLISHED' ORDER BY r.id DESC LIMIT 3",(provider_row['id'],))
        mine=db().all('SELECT * FROM marketplace_providers WHERE user_id=? ORDER BY id DESC LIMIT 30',(g.user['id'],))
        counts=db().one("SELECT COUNT(*) total, COUNT(DISTINCT category) categories FROM marketplace_providers WHERE status='VERIFIED'")
        counties=db().all("SELECT DISTINCT county FROM marketplace_providers WHERE status='VERIFIED' AND county IS NOT NULL AND TRIM(county)<>'' ORDER BY county LIMIT 100")
        return render_template('marketplace.html', rows=rows, mine=mine, categories=CATEGORIES, category=category,
            query=query, county=county, sort=sort, counties=counties, counts=counts)

    @app.route('/marketplace/providers', methods=['POST'])
    @login_required
    def marketplace_add():
        try:
            name=clean(request.form.get('name'),'Provider name',2,100)
            category=request.form.get('category','')
            if category not in CATEGORIES: raise S.BusinessError('Choose a valid service category.')
            desc=respectful_text(request.form.get('description'),'Description',10,1200)
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
        provider=db().one("SELECT id,user_id,name FROM marketplace_providers WHERE id=? AND status='VERIFIED'",(provider_id,))
        if not provider: abort(404)
        chama_id=request.form.get('chama_id',type=int)
        if chama_id:
            ctx(chama_id)
        message=(request.form.get('message') or '').strip()[:500] or None
        try:
            with db().tx():
                db().insert('marketplace_interests',provider_id=provider_id,user_id=g.user['id'],chama_id=chama_id,message=message,created_at=stamp())
                N.notify(db(), provider['user_id'], chama_id, f'Someone is interested in your marketplace service: {provider["name"]}.', '/marketplace', g.user['id'])
            flash('Interest recorded. Contact the provider directly using their listed details.','success')
        except Exception:
            flash('You have already registered interest in this provider for this chama.','info')
        return redirect(url_for('marketplace'))

    @app.route('/marketplace/quote-request', methods=['POST'])
    @login_required
    def marketplace_quote_request_many():
        raw_ids=request.form.getlist('provider_ids')
        provider_ids=[]
        for raw_id in raw_ids[:8]:
            try: provider_ids.append(int(raw_id))
            except (TypeError,ValueError): continue
        provider_ids=list(dict.fromkeys(provider_ids))
        if not provider_ids:
            flash('Choose at least one provider to request comparable quotes.','warning')
            return redirect(url_for('marketplace'))
        placeholders=','.join('?' for _ in provider_ids)
        providers=db().all(f"SELECT id,user_id FROM marketplace_providers WHERE status='VERIFIED' AND id IN ({placeholders})",provider_ids)
        providers=[p for p in providers if int(p['user_id']) != int(g.user['id'])]
        if not providers:
            flash('Choose reviewed provider listings other than your own.','warning')
            return redirect(url_for('marketplace'))
        try:
            title=clean(request.form.get('title'),'Request title',4,120)
            details=respectful_text(request.form.get('details'),'Work description',10,1500)
            budget_hint=(request.form.get('budget_hint') or '').strip()[:100] or None
            deadline=(request.form.get('deadline') or '').strip()[:30] or None
            chama_id=request.form.get('chama_id',type=int)
            if chama_id: ctx(chama_id)
            comparison_key=str(uuid4()) if len(providers)>1 else None
            with db().tx():
                for provider in providers:
                    rid=db().insert('marketplace_quote_requests',provider_id=provider['id'],user_id=g.user['id'],chama_id=chama_id,
                        title=title,details=details,budget_hint=budget_hint,deadline=deadline,status='OPEN',comparison_key=comparison_key,
                        created_at=stamp(),updated_at=stamp())
                    audit(db(),g.user['id'],'MARKETPLACE_QUOTE_REQUESTED','marketplace_quote_request',rid,chama_id,{'provider_id':provider['id'],'comparison_group':bool(comparison_key)})
                    N.notify(db(), provider['user_id'], chama_id, f'New quote request: {title}.', '/marketplace/quotes', g.user['id'])
            flash(f'Quote request sent to {len(providers)} provider(s). Their offers will be compared side by side.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('marketplace_quotes'))

    @app.route('/marketplace/<int:provider_id>/quote-request', methods=['POST'])
    @login_required
    def marketplace_quote_request(provider_id):
        provider=db().one("SELECT id,user_id,name FROM marketplace_providers WHERE id=? AND status='VERIFIED'",(provider_id,))
        if not provider: abort(404)
        if int(provider['user_id']) == int(g.user['id']):
            flash('You cannot request a quote from your own listing.','warning')
            return redirect(url_for('marketplace'))
        try:
            title=clean(request.form.get('title'),'Request title',4,120)
            details=clean(request.form.get('details'),'Work description',10,1500)
            budget_hint=(request.form.get('budget_hint') or '').strip()[:100] or None
            deadline=(request.form.get('deadline') or '').strip()[:30] or None
            chama_id=request.form.get('chama_id',type=int)
            if chama_id: ctx(chama_id)
            with db().tx():
                rid=db().insert('marketplace_quote_requests',provider_id=provider_id,user_id=g.user['id'],chama_id=chama_id,
                    title=title,details=details,budget_hint=budget_hint,deadline=deadline,status='OPEN',
                    created_at=stamp(),updated_at=stamp())
                audit(db(),g.user['id'],'MARKETPLACE_QUOTE_REQUESTED','marketplace_quote_request',rid,chama_id,{'provider_id':provider_id})
                N.notify(db(), provider['user_id'], chama_id, f'New quote request: {title}.', '/marketplace/quotes', g.user['id'])
            flash('Quote request sent. Compare the written offer and terms before accepting.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('marketplace_quotes'))

    @app.route('/marketplace/quotes')
    @login_required
    def marketplace_quotes():
        requested=db().all("""SELECT q.*,p.name provider_name,p.category,u.name requester_name,
            (SELECT COUNT(*) FROM marketplace_quotes x WHERE x.request_id IN (SELECT q2.id FROM marketplace_quote_requests q2 WHERE q2.comparison_key=q.comparison_key) OR (q.comparison_key IS NULL AND x.request_id=q.id)) quote_count
            FROM marketplace_quote_requests q JOIN marketplace_providers p ON p.id=q.provider_id
            JOIN users u ON u.id=q.user_id WHERE q.user_id=? ORDER BY q.id DESC LIMIT 100""",(g.user['id'],))
        received=db().all("""SELECT q.*,p.name provider_name,requester.name requester_name,
            (SELECT COUNT(*) FROM marketplace_quotes x WHERE x.request_id IN (SELECT q2.id FROM marketplace_quote_requests q2 WHERE q2.comparison_key=q.comparison_key) OR (q.comparison_key IS NULL AND x.request_id=q.id)) quote_count
            FROM marketplace_quote_requests q JOIN marketplace_providers p ON p.id=q.provider_id
            JOIN users requester ON requester.id=q.user_id WHERE p.user_id=? ORDER BY q.id DESC LIMIT 100""",(g.user['id'],))
        return render_template('marketplace_quotes.html',requested=requested,received=received)

    @app.route('/marketplace/quote-requests/<int:request_id>/respond', methods=['POST'])
    @login_required
    def marketplace_quote_respond(request_id):
        req=db().one("""SELECT q.*,p.user_id provider_owner,p.name provider_name FROM marketplace_quote_requests q
            JOIN marketplace_providers p ON p.id=q.provider_id WHERE q.id=?""",(request_id,))
        if not req or int(req['provider_owner']) != int(g.user['id']): abort(404)
        if req['status'] not in ('OPEN','QUOTED'): abort(400)
        try:
            amount=F.parse_kes(request.form.get('amount',''))
            scope=respectful_text(request.form.get('scope'),'Quote scope',10,1500)
            timeline=clean(request.form.get('timeline'),'Timeline',2,120)
            validity=(request.form.get('validity') or '').strip()[:30] or None
            with db().tx():
                existing=db().one('SELECT id FROM marketplace_quotes WHERE request_id=? AND provider_id=?',(request_id,req['provider_id']))
                if existing:
                    db().execute("UPDATE marketplace_quotes SET amount_cents=?,scope=?,timeline=?,validity=?,status='SUBMITTED',created_at=? WHERE id=?",
                        (amount,scope,timeline,validity,stamp(),existing['id']))
                    quote_id=existing['id']
                else:
                    quote_id=db().insert('marketplace_quotes',request_id=request_id,provider_id=req['provider_id'],amount_cents=amount,
                        scope=scope,timeline=timeline,validity=validity,status='SUBMITTED',created_at=stamp())
                db().execute("UPDATE marketplace_quote_requests SET status='QUOTED',updated_at=? WHERE id=? AND status IN ('OPEN','QUOTED')",(stamp(),request_id))
                audit(db(),g.user['id'],'MARKETPLACE_QUOTE_SUBMITTED','marketplace_quote',quote_id,req['chama_id'],{'request_id':request_id})
                N.notify(db(), req['user_id'], req['chama_id'], f'{req["provider_name"]} submitted a quote for {req["title"]}.', f'/marketplace/quote-requests/{request_id}/compare', g.user['id'])
            flash('Written quote saved. The requester can compare it with other offers.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        return redirect(url_for('marketplace_quotes'))

    @app.route('/marketplace/quote-requests/<int:request_id>/compare')
    @login_required
    def marketplace_quote_compare(request_id):
        req=db().one("""SELECT q.*,p.name provider_name,p.user_id provider_owner,u.name requester_name
            FROM marketplace_quote_requests q JOIN marketplace_providers p ON p.id=q.provider_id
            JOIN users u ON u.id=q.user_id WHERE q.id=?""",(request_id,))
        if not req: abort(404)
        group_rows=db().all('SELECT id,status,user_id,provider_id FROM marketplace_quote_requests WHERE comparison_key=?' if req['comparison_key'] else 'SELECT id,status,user_id,provider_id FROM marketplace_quote_requests WHERE id=?', (req['comparison_key'],) if req['comparison_key'] else (request_id,))
        is_requester=int(req['user_id'])==int(g.user['id'])
        owns_group_provider=any(int(db().val('SELECT user_id FROM marketplace_providers WHERE id=?',(item['provider_id'],),-1))==int(g.user['id']) for item in group_rows)
        if not is_requester and not owns_group_provider: abort(404)
        states=[item['status'] for item in group_rows]
        req['comparison_status']=next((state for state in ('COMPLETED','COMPLETION_PENDING','ACCEPTED') if state in states), 'QUOTED' if 'QUOTED' in states else ('CANCELLED' if states and all(x=='CANCELLED' for x in states) else 'OPEN'))
        quotes=db().all("""SELECT x.*,p.name provider_name,p.category,p.county,p.user_id provider_owner,
            (SELECT ROUND(AVG(r.rating),1) FROM marketplace_reviews r WHERE r.provider_id=p.id AND r.status='PUBLISHED') average_rating,
            (SELECT COUNT(*) FROM marketplace_reviews r WHERE r.provider_id=p.id AND r.status='PUBLISHED') review_count
            FROM marketplace_quotes x JOIN marketplace_providers p ON p.id=x.provider_id
            WHERE (x.request_id IN (SELECT id FROM marketplace_quote_requests WHERE comparison_key=?) OR ( ? IS NULL AND x.request_id=?))
              AND (? = 1 OR p.user_id = ?)
            ORDER BY x.amount_cents ASC,x.id ASC""",(req['comparison_key'],req['comparison_key'],request_id,1 if is_requester else 0,g.user['id']))
        return render_template('marketplace_compare.html',req=req,quotes=quotes,is_requester=is_requester)

    @app.route('/marketplace/quote-requests/<int:request_id>/accept/<int:quote_id>', methods=['POST'])
    @login_required
    def marketplace_quote_accept(request_id,quote_id):
        req=db().one('SELECT * FROM marketplace_quote_requests WHERE id=?',(request_id,))
        quote=db().one('SELECT * FROM marketplace_quotes WHERE id=? AND request_id=?',(quote_id,request_id))
        if not req or not quote or int(req['user_id']) != int(g.user['id']): abort(404)
        if req['status'] not in ('OPEN','QUOTED'): abort(400)
        with db().tx():
            db().execute("UPDATE marketplace_quote_requests SET status='ACCEPTED',chosen_quote_id=?,updated_at=? WHERE id=? AND status IN ('OPEN','QUOTED')",(quote_id,stamp(),request_id))
            db().execute("UPDATE marketplace_quotes SET status='ACCEPTED' WHERE id=? AND request_id=?",(quote_id,request_id))
            selected_request=db().one('SELECT comparison_key FROM marketplace_quote_requests WHERE id=?',(request_id,))
            if selected_request and selected_request['comparison_key']:
                db().execute("UPDATE marketplace_quote_requests SET status='CANCELLED',updated_at=? WHERE comparison_key=? AND id<>? AND status IN ('OPEN','QUOTED')",(stamp(),selected_request['comparison_key'],request_id))
                db().execute("UPDATE marketplace_quotes SET status='NOT_SELECTED' WHERE request_id IN (SELECT id FROM marketplace_quote_requests WHERE comparison_key=? AND id<>?)",(selected_request['comparison_key'],request_id))
            else:
                db().execute("UPDATE marketplace_quotes SET status='NOT_SELECTED' WHERE request_id=? AND id<>?",(request_id,quote_id))
            audit(db(),g.user['id'],'MARKETPLACE_QUOTE_ACCEPTED','marketplace_quote',quote_id,req['chama_id'],{'request_id':request_id})
            provider_owner = db().val('SELECT user_id FROM marketplace_providers WHERE id=?', (quote['provider_id'],), None)
            if provider_owner:
                N.notify(db(), provider_owner, req['chama_id'], 'Your marketplace quote was selected.', f'/marketplace/quotes', g.user['id'])
            if req.get('comparison_key'):
                other_rows = db().all("""SELECT DISTINCT p.user_id FROM marketplace_quote_requests q JOIN marketplace_providers p ON p.id=q.provider_id
                    WHERE q.comparison_key=? AND q.id<>?""", (req['comparison_key'], request_id))
                for other in other_rows:
                    N.notify(db(), other['user_id'], req['chama_id'], 'A chama selected another offer in a quote comparison. This request is now closed.', '/marketplace/quotes', g.user['id'])
        flash('Quote selected. Confirm the scope, provider credentials, and contract directly before work begins.','success')
        return redirect(url_for('marketplace_quote_compare',request_id=request_id))

    @app.route('/marketplace/quote-requests/<int:request_id>/confirm-completion', methods=['POST'])
    @login_required
    def marketplace_quote_confirm_completion(request_id):
        req=db().one("""SELECT q.*,p.user_id provider_owner FROM marketplace_quote_requests q
            JOIN marketplace_providers p ON p.id=q.provider_id WHERE q.id=?""",(request_id,))
        if not req or req['status'] not in ('ACCEPTED','COMPLETION_PENDING','COMPLETED'): abort(404)
        requester=int(req['user_id'])==int(g.user['id'])
        provider=int(req['provider_owner'])==int(g.user['id'])
        if not requester and not provider: abort(404)
        with db().tx():
            if requester: db().execute('UPDATE marketplace_quote_requests SET requester_completed=1,updated_at=? WHERE id=?',(stamp(),request_id))
            if provider: db().execute('UPDATE marketplace_quote_requests SET provider_completed=1,updated_at=? WHERE id=?',(stamp(),request_id))
            latest=db().one('SELECT requester_completed,provider_completed FROM marketplace_quote_requests WHERE id=?',(request_id,))
            status='COMPLETED' if latest['requester_completed'] and latest['provider_completed'] else 'COMPLETION_PENDING'
            db().execute('UPDATE marketplace_quote_requests SET status=?,updated_at=? WHERE id=?',(status,stamp(),request_id))
            audit(db(),g.user['id'],'MARKETPLACE_COMPLETION_CONFIRMED','marketplace_quote_request',request_id,req['chama_id'],{'status':status})
            other_user = req['provider_owner'] if requester else req['user_id']
            N.notify(db(), other_user, req['chama_id'], 'The other party confirmed completion for your marketplace engagement.', '/marketplace/quotes', g.user['id'])
        if status=='COMPLETED': flash('Both sides confirmed completion. The requester can now leave one experience-based review.','success')
        else: flash('Your completion confirmation is recorded. The other party must also confirm before a review can be submitted.','info')
        return redirect(url_for('marketplace_quotes'))

    @app.route('/marketplace/quote-requests/<int:request_id>/review', methods=['POST'])
    @login_required
    def marketplace_review_submit(request_id):
        req=db().one("""SELECT q.*,p.user_id provider_owner FROM marketplace_quote_requests q
            JOIN marketplace_providers p ON p.id=q.provider_id WHERE q.id=?""",(request_id,))
        if not req or int(req['user_id']) != int(g.user['id']) or req['status']!='COMPLETED' or int(req['provider_owner'])==int(g.user['id']): abort(404)
        try:
            rating=request.form.get('rating',type=int)
            if rating not in (1,2,3,4,5): raise S.BusinessError('Choose a rating from 1 to 5 stars.')
            title=respectful_text(request.form.get('title'),'Review title',4,100)
            body=respectful_text(request.form.get('body'),'Review',10,1000)
            with db().tx():
                rid=db().insert('marketplace_reviews',provider_id=req['provider_id'],user_id=g.user['id'],request_id=request_id,
                    rating=rating,title=title,body=body,status='PUBLISHED',created_at=stamp())
                audit(db(),g.user['id'],'MARKETPLACE_REVIEW_PUBLISHED','marketplace_review',rid,req['chama_id'],{'provider_id':req['provider_id'],'rating':rating})
                N.notify(db(), req['provider_owner'], req['chama_id'], 'A chama member left a review for your completed marketplace engagement.', '/marketplace', g.user['id'])
            flash('Review published. Reviews are limited to completed quote engagements, one per request.','success')
        except S.BusinessError as e: flash(str(e),'warning')
        except Exception:
            flash('A review already exists for this completed request, or the submission could not be saved.','warning')
        return redirect(url_for('marketplace_quotes'))

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
            N.notify(db(), row['user_id'], None, 'Your marketplace listing was '+('approved for platform review and is now visible as reviewed.' if status=='VERIFIED' else 'not approved. You can update the listing and resubmit if appropriate.'), '/marketplace', g.user['id'])
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
                N.notify_roles(db(), chama_id, ADMIN_ROLES, 'A member submitted a welfare fund request for review.', f'/chamas/{chama_id}/welfare', g.user['id'])
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
            N.notify(db(), row['user_id'], chama_id, 'Your welfare request was '+status.lower()+'.', f'/chamas/{chama_id}/welfare', g.user['id'])
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
            all_replies=db().all("SELECT r.*,u.name author FROM collaboration_replies r JOIN users u ON u.id=r.user_id WHERE r.post_id=? AND r.status='ACTIVE' ORDER BY r.id ASC LIMIT 300",(p['id'],))
            by_parent={}
            for reply in all_replies:
                by_parent.setdefault(reply['parent_reply_id'], []).append(reply)
            p['replies']=by_parent.get(None, [])
            for reply in all_replies:
                reply['children']=by_parent.get(reply['id'], [])
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
            post=db().one("SELECT id,user_id,title,chama_id FROM collaboration_posts WHERE id=? AND status='ACTIVE' AND visibility='COMMUNITY'",(post_id,))
            if not post: abort(404)
            body=respectful_text(request.form.get('body'),'Reply',2,1200)
            parent_reply_id=request.form.get('parent_reply_id',type=int)
            if parent_reply_id and not db().one("SELECT id FROM collaboration_replies WHERE id=? AND post_id=? AND status='ACTIVE' AND parent_reply_id IS NULL",(parent_reply_id,post_id)):
                abort(400)
            with db().tx():
                rid=db().insert('collaboration_replies',post_id=post_id,user_id=g.user['id'],parent_reply_id=parent_reply_id,body=body,status='ACTIVE',created_at=stamp())
                audit(db(),g.user['id'],'COLLABORATION_REPLY_CREATED','collaboration_reply',rid,None,{'post_id':post_id,'parent_reply_id':parent_reply_id})
                N.notify(db(), post['user_id'], post['chama_id'], f'Someone replied to your discussion: {post["title"]}.', '/collaboration#post-'+str(post_id), g.user['id'])
                if parent_reply_id:
                    parent_author=db().val('SELECT user_id FROM collaboration_replies WHERE id=? AND post_id=?',(parent_reply_id,post_id),None)
                    if parent_author:
                        N.notify(db(), parent_author, post['chama_id'], f'Someone replied to your comment on: {post["title"]}.', '/collaboration#post-'+str(post_id), g.user['id'])
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
