from datetime import date

from flask import Response, abort, flash, g, redirect, render_template, request, url_for

import community as C
import finance as F
import notify as N
import reports as R
import services as S

PAGE = 20
VIEW = F.VIEW_ROLES
COMMS = C.COMMS_ROLES


def register(app, db, ctx, login_required):
    def done(chama_id, endpoint, fn, ok, **kw):
        try:
            fn()
            flash(ok, 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for(endpoint, chama_id=chama_id, **kw))

    # ---------- announcements ----------
    @app.route('/chamas/<int:chama_id>/notices')
    @login_required
    def notices(chama_id):
        chama, me, sub = ctx(chama_id)
        page = max(1, request.args.get('page', 1, type=int))
        total = db().val('SELECT COUNT(*) FROM announcements WHERE chama_id=? AND deleted_at IS NULL', (chama_id,), 0)
        return render_template('notices.html', chama=chama, me=me, rows=C.list_notices(db(), chama_id, PAGE, (page - 1) * PAGE),
                               can=me['role'] in COMMS, page=page, pages=max(1, -(-total // PAGE)))

    @app.route('/chamas/<int:chama_id>/notices/add', methods=['POST'])
    @login_required
    def notice_add(chama_id):
        ctx(chama_id, roles=COMMS)
        return done(chama_id, 'notices', lambda: C.post_notice(db(), chama_id, request.form.get('title'), request.form.get('body'),
                                                              request.form.get('pinned') == '1', g.user['id']), 'Notice posted. Every member was alerted.')

    @app.route('/chamas/<int:chama_id>/notices/<int:nid>/<action>', methods=['POST'])
    @login_required
    def notice_action(chama_id, nid, action):
        ctx(chama_id, roles=COMMS)
        if action == 'delete':
            return done(chama_id, 'notices', lambda: C.delete_notice(db(), chama_id, nid, g.user['id']), 'Notice removed.')
        if action == 'pin':
            return done(chama_id, 'notices', lambda: C.toggle_pin(db(), chama_id, nid, g.user['id']), 'Done.')
        abort(404)

    # ---------- meetings ----------
    @app.route('/chamas/<int:chama_id>/meetings')
    @login_required
    def meetings(chama_id):
        chama, me, sub = ctx(chama_id)
        rows = db().all('SELECT * FROM meetings WHERE chama_id=? ORDER BY held_at DESC, id DESC LIMIT 60', (chama_id,))
        return render_template('meetings.html', chama=chama, me=me, rows=rows, mine=C.my_attendance(db(), chama_id, g.user['id']), can=me['role'] in COMMS)

    @app.route('/chamas/<int:chama_id>/meetings/add', methods=['POST'])
    @login_required
    def meeting_add(chama_id):
        ctx(chama_id, roles=COMMS)
        fine = (request.form.get('fine') or '').strip()
        try:
            cents = F.parse_kes(fine, 100) if fine and fine != '0' else 0
            mid = C.create_meeting(db(), chama_id, request.form.get('title'), request.form.get('when'), request.form.get('venue'),
                                   request.form.get('agenda'), cents, g.user['id'], online=request.form.get('online'), link=request.form.get('link'))
        except S.BusinessError as e:
            flash(str(e), 'warning')
            return redirect(url_for('meetings', chama_id=chama_id))
        flash('Meeting scheduled. Every member was alerted.', 'success')
        return redirect(url_for('meeting_view', chama_id=chama_id, mid=mid))

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>')
    @login_required
    def meeting_view(chama_id, mid):
        chama, me, sub = ctx(chama_id)
        try:
            m = C.get_meeting(db(), chama_id, mid)
        except S.BusinessError:
            abort(404)
        sheet = C.attendance_sheet(db(), chama_id, mid)
        actions = db().all("SELECT a.*,u.name owner FROM meeting_actions a LEFT JOIN users u ON u.id=a.owner_user_id WHERE a.meeting_id=? ORDER BY a.status,a.due_date,a.id", (mid,))
        members = db().all("SELECT u.id,u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name", (chama_id,))
        return render_template('meeting.html', chama=chama, me=me, m=m, sheet=sheet, actions=actions, members=members, join_open=C.join_open(m), joined=C.joined_ids(db(), mid), can=me['role'] in COMMS,
                               mine=next((r['status'] for r in sheet if r['id'] == g.user['id']), None))

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/join')
    @login_required
    def meeting_join(chama_id, mid):
        chama, me, sub = ctx(chama_id)  # membership in THIS chama is required
        try:
            target = C.join_target(db(), chama_id, mid, g.user)
            meeting = C.get_meeting(db(), chama_id, mid)
            if meeting.get('online_provider') == 'JITSI':
                # Keep the Jitsi room embedded in ChamaPay instead of sending members away.
                return render_template('meeting_room.html', chama=chama, me=me, m=meeting, room_url=target)
            return redirect(target)
        except S.BusinessError as e:
            flash(str(e), 'warning')
            return redirect(url_for('meeting_view', chama_id=chama_id, mid=mid))

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/attendance', methods=['POST'])
    @login_required
    def meeting_attendance(chama_id, mid):
        ctx(chama_id, roles=COMMS)
        statuses = {}
        for k, v in request.form.items():
            if k.startswith('st_'):
                try:
                    statuses[int(k[3:])] = v
                except ValueError:
                    abort(400)
        if not statuses:
            abort(400)
        try:
            marked, fines = C.mark_attendance(db(), chama_id, mid, statuses, g.user['id'])
            flash(f'Attendance saved for {marked} members.' + (f' {fines} absence fine(s) were created.' if fines else ''), 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('meeting_view', chama_id=chama_id, mid=mid))

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/minutes', methods=['POST'])
    @login_required
    def meeting_minutes(chama_id, mid):
        ctx(chama_id, roles=COMMS)
        return done(chama_id, 'meeting_view', lambda: C.save_minutes(db(), chama_id, mid, request.form.get('minutes'), g.user['id']), 'Minutes saved.', mid=mid)

    @app.route('/chamas/<int:chama_id>/meetings/<int:mid>/cancel', methods=['POST'])
    @login_required
    def meeting_cancel(chama_id, mid):
        ctx(chama_id, roles=COMMS)
        return done(chama_id, 'meeting_view', lambda: C.cancel_meeting(db(), chama_id, mid, g.user['id']), 'Meeting cancelled.', mid=mid)

    # ---------- reports ----------
    @app.route('/chamas/<int:chama_id>/reports')
    @login_required
    def reports(chama_id):
        chama, me, sub = ctx(chama_id, roles=VIEW)
        import re
        period = request.args.get('period', '')
        period = period if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) else date.today().isoformat()[:7]
        rep = R.chama_report(db(), chama_id, period)
        peak = max([h['total'] for h in rep['history']] or [1]) or 1
        return render_template('reports.html', chama=chama, me=me, r=rep, peak=peak, kinds=R.EXPORTS)

    @app.route('/chamas/<int:chama_id>/export/<kind>.csv')
    @login_required
    def export_csv(chama_id, kind):
        ctx(chama_id, roles=VIEW)
        try:
            header, rows = R.export(db(), chama_id, kind)
        except KeyError:
            abort(404)
        return Response(F.to_csv(header, rows), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={kind}-{chama_id}.csv'})

    # ---------- notifications (about the person, not one chama) ----------
    @app.route('/notifications')
    @login_required
    def notifications():
        return render_template('notifications.html', rows=N.recent(db(), g.user['id']))

    @app.route('/notifications/read', methods=['POST'])
    @login_required
    def notifications_read():
        N.mark_all_read(db(), g.user['id'])
        return redirect(url_for('notifications'))

    @app.route('/notifications/<int:nid>/open', methods=['POST'])
    @login_required
    def notification_open(nid):
        link = N.open_one(db(), g.user['id'], nid)
        if link is None and not db().one('SELECT id FROM notifications WHERE id=? AND user_id=?', (nid, g.user['id'])):
            abort(404)
        return redirect(link if link and link.startswith('/chamas/') else url_for('notifications'))
