"""Engagement features: RSVPs, notice receipts, payment promises, annual recap, referrals and profile photos."""
from datetime import date, datetime, timezone
import base64
from uuid import uuid4
from flask import Response, abort, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup, escape
import services as S
import finance as F
import notify as N
from db import audit

ROLES = ('CHAMA_ADMIN','TREASURER','SECRETARY')
ALLOWED_EXT = {'png','jpg','jpeg','webp'}
def stamp(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def register(app, db, ctx, login_required):
    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/rsvp', methods=['POST'])
    @login_required
    def meeting_rsvp(chama_id, mid):
        chama, me, sub = ctx(chama_id)
        meeting=db().one("SELECT * FROM meetings WHERE id=? AND chama_id=?",(mid,chama_id)) or abort(404)
        response=(request.form.get('response') or '').upper()
        if response not in ('COMING','NOT_COMING'): abort(400)
        if meeting['status'] != 'SCHEDULED':
            flash('RSVP is closed because this meeting is no longer scheduled.','warning')
        else:
            db().execute("INSERT INTO meeting_rsvps(meeting_id,chama_id,user_id,response,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(meeting_id,user_id) DO UPDATE SET response=excluded.response,updated_at=excluded.updated_at",(mid,chama_id,g.user['id'],response,stamp()))
            db().commit(); flash('Your meeting response was saved.','success')
        return redirect(url_for('meeting_view',chama_id=chama_id,mid=mid))

    @app.route('/chamas/<int:chama_id>/notices/<int:nid>/seen', methods=['POST'])
    @login_required
    def notice_seen(chama_id,nid):
        chama,me,sub=ctx(chama_id)
        row=db().one("SELECT id FROM announcements WHERE id=? AND chama_id=? AND deleted_at IS NULL",(nid,chama_id)) or abort(404)
        db().execute("INSERT INTO notice_reads(announcement_id,chama_id,user_id,seen_at) VALUES(?,?,?,?) ON CONFLICT(announcement_id,user_id) DO NOTHING",(nid,chama_id,g.user['id'],stamp())); db().commit()
        return redirect(request.form.get('next') or url_for('notices',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/notices/<int:nid>/readers')
    @login_required
    def notice_readers(chama_id,nid):
        chama,me,sub=ctx(chama_id,roles=ROLES)
        db().one("SELECT id FROM announcements WHERE id=? AND chama_id=? AND deleted_at IS NULL",(nid,chama_id)) or abort(404)
        rows=db().all("SELECT u.id,u.name,nr.seen_at FROM chama_members cm JOIN users u ON u.id=cm.user_id LEFT JOIN notice_reads nr ON nr.user_id=u.id AND nr.announcement_id=? WHERE cm.chama_id=? AND cm.status='ACTIVE' ORDER BY u.name",(nid,chama_id))
        return render_template('notice_readers.html',chama=chama,me=me,nid=nid,rows=rows,read_count=sum(1 for r in rows if r['seen_at']))

    @app.route('/chamas/<int:chama_id>/notices/<int:nid>/remind-unread',methods=['POST'])
    @login_required
    def notice_remind_unread(chama_id,nid):
        chama,me,sub=ctx(chama_id,roles=ROLES)
        notice=db().one("SELECT * FROM announcements WHERE id=? AND chama_id=? AND deleted_at IS NULL",(nid,chama_id)) or abort(404)
        unread=db().all("SELECT cm.user_id FROM chama_members cm LEFT JOIN notice_reads nr ON nr.user_id=cm.user_id AND nr.announcement_id=? WHERE cm.chama_id=? AND cm.status='ACTIVE' AND nr.id IS NULL AND cm.user_id<>?",(nid,chama_id,g.user['id']))
        for member in unread:
            N.notify(db(),member['user_id'],chama_id,'Reminder: please read the notice — '+notice['title'],url_for('notices',chama_id=chama_id),by=g.user['id'])
        db().commit(); flash(f'Reminder sent to {len(unread)} member(s) who have not marked this notice as read.','success')
        return redirect(url_for('notice_readers',chama_id=chama_id,nid=nid))

    @app.route('/chamas/<int:chama_id>/payment-promises',methods=['GET','POST'])
    @login_required
    def payment_promises(chama_id):
        chama,me,sub=ctx(chama_id)
        if request.method=='POST':
            try:
                amount=F.parse_kes(request.form.get('amount'),100)
                due=(request.form.get('due_date') or '').strip()
                date.fromisoformat(due)
                note=(request.form.get('note') or '').strip()[:240]
                db().insert('payment_promises',chama_id=chama_id,user_id=g.user['id'],amount_cents=amount,due_date=due,note=note,status='OPEN',created_at=stamp(),updated_at=stamp())
                db().commit(); flash('Payment promise recorded. This is an estimate, not a payment or proof of funds.','success')
            except (ValueError,S.BusinessError):
                db().rollback(); flash('Enter a valid positive amount and due date.','warning')
            return redirect(url_for('payment_promises',chama_id=chama_id))
        if me['role'] in ROLES:
            rows=db().all("SELECT p.*,u.name member FROM payment_promises p JOIN users u ON u.id=p.user_id WHERE p.chama_id=? ORDER BY CASE WHEN p.status='OPEN' THEN 0 ELSE 1 END,p.due_date",(chama_id,))
        else:
            rows=db().all("SELECT p.*,u.name member FROM payment_promises p JOIN users u ON u.id=p.user_id WHERE p.chama_id=? AND p.user_id=? ORDER BY p.due_date DESC",(chama_id,g.user['id']))
        return render_template('payment_promises.html',chama=chama,me=me,rows=rows)

    @app.route('/chamas/<int:chama_id>/payment-promises/<int:pid>/status',methods=['POST'])
    @login_required
    def payment_promise_status(chama_id,pid):
        chama,me,sub=ctx(chama_id,roles=ROLES)
        status=(request.form.get('status') or '').upper()
        if status not in ('OPEN','PAID','CANCELLED'): abort(400)
        changed=db().execute("UPDATE payment_promises SET status=?,updated_at=? WHERE id=? AND chama_id=?",(status,stamp(),pid,chama_id))
        if not changed: abort(404)
        db().commit(); flash('Promise status updated. Financial ledger entries are unchanged.','success')
        return redirect(url_for('payment_promises',chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/wrapped')
    @login_required
    def chama_wrapped(chama_id):
        chama,me,sub=ctx(chama_id)
        year=request.args.get('year',type=int) or date.today().year
        if year < 2000 or year > date.today().year: abort(400)
        start=f'{year}-01-01'; end=f'{year+1}-01-01'
        saved=db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID' AND paid_on>=? AND paid_on<?",(chama_id,start,end),0) or 0
        loans=db().val("SELECT COALESCE(SUM(principal_cents),0) FROM loans WHERE chama_id=? AND applied_at>=? AND applied_at<?",(chama_id,start,end),0) or 0
        payouts=db().val("SELECT COALESCE(SUM(i.dividend_cents),0) FROM chama_dividend_items i JOIN chama_dividend_runs r ON r.id=i.run_id WHERE r.chama_id=? AND r.year=? AND r.status='FINALIZED'",(chama_id,year),0) or 0
        meetings=db().val("SELECT COUNT(*) FROM meetings WHERE chama_id=? AND held_at>=? AND held_at<? AND status<>'CANCELLED'",(chama_id,start,end),0) or 0
        members=db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE'",(chama_id,),0) or 0
        return render_template('chama_wrapped.html',chama=chama,me=me,year=year,saved=saved,loans=loans,payouts=payouts,meetings=meetings,members=members)

    @app.route('/chamas/<int:chama_id>/referrals',methods=['GET','POST'])
    @login_required
    def chama_referrals(chama_id):
        chama,me,sub=ctx(chama_id,roles=ROLES)
        row=db().one("SELECT * FROM chama_referrals WHERE referrer_chama_id=? AND referred_chama_id IS NULL ORDER BY id DESC LIMIT 1",(chama_id,))
        if not row:
            code=uuid4().hex[:10].upper()
            db().insert('chama_referrals',referrer_chama_id=chama_id,code=code,status='PENDING',created_at=stamp()); db().commit()
            row=db().one("SELECT * FROM chama_referrals WHERE referrer_chama_id=? AND code=?",(chama_id,code))
        referred=db().all("SELECT r.*,c.name referred_name FROM chama_referrals r LEFT JOIN chamas c ON c.id=r.referred_chama_id WHERE r.referrer_chama_id=? ORDER BY r.id DESC",(chama_id,))
        return render_template('chama_referrals.html',chama=chama,me=me,row=row,referrals=referred)

    # ---------- profile photos (stored in the database so redeploys never delete them) ----------
    MAX_PHOTO_BYTES = 2 * 1024 * 1024

    def _image_mime(data):
        """Identify the image from its real bytes (never trust the filename or browser MIME)."""
        if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
        if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image/webp'
        return None

    def _back(default_endpoint, **kw):
        ref = request.referrer
        if ref and request.host_url.rstrip('/') in ref:
            return redirect(ref)
        return redirect(url_for(default_endpoint, **kw))

    def _store_photo(kind, owner_id):
        """Validate the upload and save it. Returns an error message, or None on success."""
        f = request.files.get('photo')
        if not f or not f.filename:
            return 'Choose a photo first.'
        data = f.read(MAX_PHOTO_BYTES + 1)
        if not data or len(data) > MAX_PHOTO_BYTES:
            return 'That photo is too large. Choose one under 2 MB.'
        mime = _image_mime(data)
        if not mime:
            return 'Use a JPG, PNG or WebP photo.'
        encoded = base64.b64encode(data).decode('ascii')
        now = stamp()
        db().execute("INSERT INTO profile_images(owner_type,owner_id,mime,data_b64,updated_at) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(owner_type,owner_id) DO UPDATE SET mime=excluded.mime,data_b64=excluded.data_b64,updated_at=excluded.updated_at",
                     (kind, owner_id, mime, encoded, now))
        url = f'/photo/{kind}/{owner_id}?v={int(datetime.now(timezone.utc).timestamp())}'
        table = 'users' if kind == 'user' else 'chamas'
        db().execute('UPDATE ' + table + ' SET profile_image=? WHERE id=?', (url, owner_id))
        db().commit()
        return None

    def _remove_photo(kind, owner_id):
        db().execute('DELETE FROM profile_images WHERE owner_type=? AND owner_id=?', (kind, owner_id))
        table = 'users' if kind == 'user' else 'chamas'
        db().execute('UPDATE ' + table + ' SET profile_image=NULL WHERE id=?', (owner_id,))
        db().commit()

    @app.route('/profile/photo', methods=['POST'])
    @login_required
    def profile_photo():
        err = _store_photo('user', g.user['id'])
        flash(err or 'Profile photo updated.', 'warning' if err else 'success')
        return _back('index')

    @app.route('/profile/photo/remove', methods=['POST'])
    @login_required
    def profile_photo_remove():
        _remove_photo('user', g.user['id'])
        flash('Profile photo removed.', 'info')
        return _back('index')

    @app.route('/chamas/<int:chama_id>/profile-photo', methods=['POST'])
    @login_required
    def chama_profile_photo(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN',))
        err = _store_photo('chama', chama_id)
        flash(err or 'Chama picture updated.', 'warning' if err else 'success')
        return _back('chama_home', chama_id=chama_id)

    @app.route('/chamas/<int:chama_id>/profile-photo/remove', methods=['POST'])
    @login_required
    def chama_profile_photo_remove(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN',))
        _remove_photo('chama', chama_id)
        flash('Chama picture removed.', 'info')
        return _back('chama_home', chama_id=chama_id)

    @app.route('/photo/<kind>/<int:owner_id>')
    @login_required
    def serve_photo(kind, owner_id):
        """Photos are visible only to the person themself or to people who share a chama with them."""
        if kind == 'user':
            allowed = owner_id == g.user['id'] or db().val(
                "SELECT COUNT(*) FROM chama_members a JOIN chama_members b ON a.chama_id=b.chama_id "
                "WHERE a.user_id=? AND b.user_id=? AND a.status='ACTIVE' AND b.status='ACTIVE'", (g.user['id'], owner_id), 0)
        elif kind == 'chama':
            allowed = db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (owner_id, g.user['id']), 0)
        else:
            abort(404)
        row = db().one('SELECT mime,data_b64 FROM profile_images WHERE owner_type=? AND owner_id=?', (kind, owner_id)) if allowed else None
        if not row:
            abort(404)
        resp = Response(base64.b64decode(row['data_b64']), mimetype=row['mime'])
        resp.headers['Cache-Control'] = 'private, max-age=604800'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        return resp

    def _photo_lookup(kind, owner_id):
        cache = g.__dict__.setdefault('_photo_cache', {})
        key = (kind, owner_id)
        if key not in cache:
            table = 'users' if kind == 'user' else 'chamas'
            cache[key] = db().val('SELECT profile_image FROM ' + table + ' WHERE id=?', (owner_id,))
        return cache[key]

    def avatar(kind, owner_id, name, extra=''):
        """Small round avatar: the photo when there is one, otherwise the initial."""
        url = _photo_lookup(kind, owner_id) if owner_id else None
        initial = escape((name or '?').strip()[:1].upper() or '?')
        css = ('av ' + extra).strip()
        if url and url.startswith('/'):
            return Markup(f'<span class="{css} has-img"><img src="{escape(url)}" alt="" loading="lazy" decoding="async"></span>')
        return Markup(f'<span class="{css}">{initial}</span>')
    app.jinja_env.globals['avatar'] = avatar
