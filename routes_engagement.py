"""Engagement features: RSVPs, notice receipts, payment promises, annual recap, referrals and profile photos."""
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename
from flask import abort, flash, g, redirect, render_template, request, url_for
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

    @app.route('/profile/photo',methods=['POST'])
    @login_required
    def profile_photo():
        f=request.files.get('photo')
        if not f or not f.filename: flash('Choose a profile image first.','warning'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('index'))
        ext=f.filename.rsplit('.',1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_EXT or f.mimetype not in ('image/png','image/jpeg','image/webp'):
            flash('Use a PNG, JPG or WebP image.','warning'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('index'))
        data=f.read(3*1024*1024+1)
        if not data or len(data)>3*1024*1024 or not data.startswith((b'\x89PNG',b'\xff\xd8\xff',b'RIFF')):
            flash('Image must be valid and no larger than 3 MB.','warning'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('index'))
        name=secure_filename(f"user-{g.user['id']}-{uuid4().hex}.{ext}")
        folder=Path(app.static_folder)/'uploads'/'profiles'; folder.mkdir(parents=True,exist_ok=True)
        (folder/name).write_bytes(data)
        db().execute('UPDATE users SET profile_image=? WHERE id=?',('/static/uploads/profiles/'+name,g.user['id'])); db().commit()
        flash('Profile photo updated.','success'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('index'))

    @app.route('/chamas/<int:chama_id>/profile-photo',methods=['POST'])
    @login_required
    def chama_profile_photo(chama_id):
        chama,me,sub=ctx(chama_id,roles=('CHAMA_ADMIN',))
        f=request.files.get('photo')
        if not f or not f.filename: flash('Choose a chama image first.','warning'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('chama_home',chama_id=chama_id))
        ext=f.filename.rsplit('.',1)[-1].lower() if '.' in f.filename else ''
        data=f.read(3*1024*1024+1)
        if ext not in ALLOWED_EXT or f.mimetype not in ('image/png','image/jpeg','image/webp') or not data or len(data)>3*1024*1024 or not data.startswith((b'\x89PNG',b'\xff\xd8\xff',b'RIFF')):
            flash('Use a valid PNG, JPG or WebP image no larger than 3 MB.','warning'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('chama_home',chama_id=chama_id))
        name=secure_filename(f'chama-{chama_id}-{uuid4().hex}.{ext}')
        folder=Path(app.static_folder)/'uploads'/'profiles'; folder.mkdir(parents=True,exist_ok=True); (folder/name).write_bytes(data)
        db().execute('UPDATE chamas SET profile_image=? WHERE id=?',('/static/uploads/profiles/'+name,chama_id)); db().commit()
        flash('Chama profile photo updated.','success'); return redirect(request.referrer if request.referrer and request.host_url.rstrip('/') in request.referrer else url_for('chama_home',chama_id=chama_id))
