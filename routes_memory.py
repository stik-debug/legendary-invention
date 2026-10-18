"""Searchable, permission-scoped institutional memory for a chama.

Memory is assembled from existing authoritative records; it does not create synthetic history.
"""
from flask import abort, render_template, request

STAFF = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY', 'AUDITOR')


def register_memory_routes(app, db, ctx, login_required):
    @app.route('/chamas/<int:chama_id>/memory')
    @login_required
    def chama_memory(chama_id):
        chama, me, sub = ctx(chama_id)
        q = (request.args.get('q') or '').strip()[:120]
        kind = (request.args.get('kind') or 'all').strip().lower()
        allowed = {'all', 'meetings', 'actions', 'decisions', 'rules', 'activity', 'scenarios', 'handovers'}
        if kind not in allowed:
            kind = 'all'
        rows = []
        needle = '%' + q.replace('%', '\\%').replace('_', '\\_') + '%'

        def add(section, title, detail, when, url, ref=''):
            rows.append({'section': section, 'title': title or '(Untitled record)',
                         'detail': detail or '', 'when': when or '', 'url': url, 'ref': ref})

        def want(section):
            return kind == 'all' or kind == section

        # Search existing group records only. The group ID is always part of the query.
        if want('meetings'):
            sql = "SELECT id,title,held_at,status,agenda,minutes FROM meetings WHERE chama_id=?"
            args = [chama_id]
            if q:
                sql += " AND (title LIKE ? ESCAPE '\\' OR COALESCE(agenda,'') LIKE ? ESCAPE '\\' OR COALESCE(minutes,'') LIKE ? ESCAPE '\\')"
                args += [needle, needle, needle]
            sql += ' ORDER BY held_at DESC LIMIT 100'
            for r in db().all(sql, args):
                add('meetings', r['title'], 'Status: %s · Agenda: %s · Minutes: %s' % (r['status'], r['agenda'] or 'not recorded', r['minutes'] or 'not recorded'), r['held_at'], '/chamas/%s/meetings' % chama_id, 'meeting #%s' % r['id'])

        if want('actions'):
            sql = "SELECT a.id,a.title,a.due_date,a.status,a.completed_at,u.name owner FROM meeting_actions a LEFT JOIN users u ON u.id=a.owner_user_id WHERE a.chama_id=?"
            args = [chama_id]
            if q:
                sql += " AND (a.title LIKE ? ESCAPE '\\' OR COALESCE(u.name,'') LIKE ? ESCAPE '\\')"
                args += [needle, needle]
            sql += " ORDER BY CASE WHEN a.status='OPEN' THEN 0 ELSE 1 END, a.due_date DESC LIMIT 100"
            for r in db().all(sql, args):
                add('actions', r['title'], 'Status: %s · Owner: %s · Due: %s' % (r['status'], r['owner'] or 'unassigned', r['due_date'] or 'no deadline'), r['completed_at'] or r['due_date'], '/chamas/%s/promises' % chama_id, 'action #%s' % r['id'])

        if want('decisions'):
            sql = "SELECT id,title,description,status,created_at,opens_at,closes_at FROM chama_votes WHERE chama_id=?"
            args = [chama_id]
            if q:
                sql += " AND (title LIKE ? ESCAPE '\\' OR COALESCE(description,'') LIKE ? ESCAPE '\\')"
                args += [needle, needle]
            sql += ' ORDER BY created_at DESC LIMIT 100'
            for r in db().all(sql, args):
                add('decisions', r['title'], 'Status: %s · %s' % (r['status'], r['description'] or 'No description recorded'), r['created_at'], '/chamas/%s/votes' % chama_id, 'vote #%s' % r['id'])

        if want('rules') and me['role'] in STAFF:
            sql = 'SELECT version_no,snapshot_json,changed_at,changed_by FROM chama_constitution_history WHERE chama_id=?'
            args = [chama_id]
            if q:
                sql += " AND snapshot_json LIKE ? ESCAPE '\\'"
                args += [needle]
            sql += ' ORDER BY version_no DESC LIMIT 100'
            for r in db().all(sql, args):
                add('rules', 'Constitution version %s' % r['version_no'], r['snapshot_json'], r['changed_at'], '/chamas/%s/constitution' % chama_id, 'constitution v%s' % r['version_no'])

        if want('activity'):
            sql = "SELECT a.id,a.action,a.entity_type,a.entity_id,a.metadata,a.created_at,u.name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id WHERE a.chama_id=?"
            args = [chama_id]
            if q:
                sql += " AND (a.action LIKE ? ESCAPE '\\' OR COALESCE(a.entity_type,'') LIKE ? ESCAPE '\\' OR COALESCE(a.metadata,'') LIKE ? ESCAPE '\\' OR COALESCE(u.name,'') LIKE ? ESCAPE '\\')"
                args += [needle, needle, needle, needle]
            sql += ' ORDER BY a.id DESC LIMIT 100'
            for r in db().all(sql, args):
                add('activity', r['action'], '%s %s · %s · %s' % (r['entity_type'] or 'record', r['entity_id'] or '', r['actor'] or 'System', r['metadata'] or ''), r['created_at'], '/chamas/%s/activity' % chama_id, 'audit #%s' % r['id'])

        if want('scenarios') and me['role'] in STAFF:
            sql = 'SELECT id,name,assumptions_json,results_json,created_at FROM chama_scenarios WHERE chama_id=?'
            args = [chama_id]
            if q:
                sql += " AND (name LIKE ? ESCAPE '\\' OR assumptions_json LIKE ? ESCAPE '\\')"
                args += [needle, needle]
            sql += ' ORDER BY id DESC LIMIT 100'
            for r in db().all(sql, args):
                add('scenarios', r['name'], 'Planning assumptions: %s · Results are estimates, not ledger entries.' % r['assumptions_json'], r['created_at'], '/chamas/%s/strategy' % chama_id, 'scenario #%s' % r['id'])

        if want('handovers') and me['role'] in STAFF:
            sql = "SELECT h.id,h.status,h.notes,h.created_at,h.accepted_at,u.name successor FROM chama_handovers h LEFT JOIN users u ON u.id=h.proposed_successor WHERE h.chama_id=?"
            args = [chama_id]
            if q:
                sql += " AND (COALESCE(h.notes,'') LIKE ? ESCAPE '\\' OR COALESCE(u.name,'') LIKE ? ESCAPE '\\' OR h.status LIKE ? ESCAPE '\\')"
                args += [needle, needle, needle]
            sql += ' ORDER BY h.id DESC LIMIT 100'
            for r in db().all(sql, args):
                add('handovers', 'Leadership handover · %s' % r['status'], 'Proposed successor: %s · Notes: %s' % (r['successor'] or 'not available', r['notes'] or 'none'), r['accepted_at'] or r['created_at'], '/chamas/%s/handover' % chama_id, 'handover #%s' % r['id'])

        # A broad search with a query should show a useful empty state, not fabricated matches.
        rows.sort(key=lambda x: x['when'], reverse=True)
        return render_template('chama_memory.html', chama=chama, me=me, sub=sub, q=q, kind=kind, rows=rows[:250], staff=me['role'] in STAFF)
