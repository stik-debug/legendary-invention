import hmac
import os
import secrets
import time
from datetime import date, timedelta
from functools import wraps

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

import devtools
import finance as F
import notify as N
import routes_community
import routes_finance
import routes_pay
import universal_pay as UP
import routes_mgr
import routes_recovery
import routes_security
import routes_upgrade
import routes_v22
import routes_plus
import services as S
import twofactor as TF
from db import DB, audit, init_db
from providers import MpesaPaymentProvider, get_provider

PAGE = 25


def create_app(overrides=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    production = bool(os.environ.get('RENDER')) or os.environ.get('APP_ENV') == 'production'
    app.config.update(
        IS_PRODUCTION=production,
        SECRET_KEY=os.environ.get('AUTH_SECRET') or os.environ.get('SECRET_KEY') or '',
        DATABASE_URL=os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(app.instance_path, 'chamapay.db'),
        PAY_INSTRUCTIONS=os.environ.get('PAY_INSTRUCTIONS', 'Contact ChamaPay support to pay. Your chama is reactivated as soon as we record your payment.'),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(days=14), MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        REQUIRE_OWNER_2FA=os.environ.get('REQUIRE_OWNER_2FA') == '1')
    app.config.update(overrides or {})
    if len(app.config['SECRET_KEY']) < 32:
        if production:
            raise RuntimeError('Set AUTH_SECRET (or SECRET_KEY) to a random string of at least 32 characters.')
        app.config['SECRET_KEY'] = 'dev-only-' + secrets.token_hex(16)
    if app.config['DATABASE_URL'].startswith('postgres') is False and production:
        print('WARNING: production is running on SQLite. Data will be lost on redeploy. Set DATABASE_URL to PostgreSQL.')
    provider = get_provider(app.config['IS_PRODUCTION'] and not app.config.get('ALLOW_TEST_PROVIDER'))
    app.config['PROVIDER'] = provider

    boot = DB(app.config['DATABASE_URL'])
    init_db(boot)
    e, p, pw = os.environ.get('OWNER_EMAIL', ''), os.environ.get('OWNER_PHONE', ''), os.environ.get('OWNER_PASSWORD', '')
    if e or p or pw:
        if len(pw) < 10:
            print('OWNER SETUP: OWNER_PASSWORD must be at least 10 characters. Owner account NOT created.')
        else:
            try:
                print('OWNER SETUP:', S.ensure_owner(boot, e, p, generate_password_hash(pw)))
            except Exception as ex:
                print('OWNER SETUP failed:', type(ex).__name__)
    elif not boot.val('SELECT COUNT(*) FROM users WHERE is_super_admin=1'):
        print('OWNER SETUP: no owner exists. Set OWNER_EMAIL, OWNER_PHONE and OWNER_PASSWORD in the environment.')
    if os.environ.get('OWNER_RESET_2FA') == '1':
        print('OWNER 2FA RESET:', 'two-factor login switched off for ' + e if TF.force_disable(boot, e) else 'no owner with OWNER_EMAIL found, nothing changed', '(remove OWNER_RESET_2FA now)')
    boot.close()

    # ---------- plumbing ----------
    def db():
        if 'db' not in g:
            g.db = DB(app.config['DATABASE_URL'])
        return g.db

    @app.teardown_appcontext
    def close(_):
        d = g.pop('db', None)
        if d:
            try:
                d.rollback()
            except Exception:
                pass
            d.close()

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get('uid')
        if uid:
            u = db().one('SELECT * FROM users WHERE id=? AND is_active=1', (uid,))
            if u and session.get('ep', 0) == u['session_epoch']:
                g.user = u
            else:
                session.clear()

    @app.before_request
    def csrf():
        if request.method == 'POST' and request.endpoint not in ('mpesa_webhook', 'chama_mpesa_webhook'):
            sent, want = request.form.get('_csrf', ''), session.get('_csrf', '')
            if not want or not hmac.compare_digest(sent, want):
                flash('Your session expired. Please try again.', 'danger')
                return redirect(request.referrer or url_for('index'))

    def token():
        if '_csrf' not in session:
            session['_csrf'] = secrets.token_hex(16)
        return session['_csrf']

    app.jinja_env.globals['csrf_input'] = lambda: Markup(f'<input type="hidden" name="_csrf" value="{token()}">')
    app.jinja_env.filters['kes'] = S.kes
    app.jinja_env.filters['date'] = lambda s: S.parse(s).strftime('%d %b %Y') if s else '-'
    app.jinja_env.filters['datetime'] = lambda s: S.parse(s).strftime('%d %b %Y %H:%M') if s else '-'

    @app.context_processor
    def inject():
        unread, notif, cid = 0, 0, (request.view_args or {}).get('chama_id')
        if g.get('user'):
            try:
                notif = N.unread_count(db(), g.user['id'])
                if cid:
                    unread = F.unread_count(db(), cid, g.user['id'])
            except Exception:
                pass
        return {'user': g.get('user'), 'S': S, 'unread': unread, 'notif_unread': notif}

    @app.after_request
    def headers(r):
        r.headers['X-Content-Type-Options'] = 'nosniff'
        r.headers['X-Frame-Options'] = 'DENY'
        r.headers['Referrer-Policy'] = 'same-origin'
        r.headers['Content-Security-Policy'] = ("default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                                "font-src https://fonts.gstatic.com; img-src 'self' data:; script-src 'self'; frame-ancestors 'none'; form-action 'self'")
        if app.config['IS_PRODUCTION']:
            r.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        if request.endpoint == 'static':
            # Static assets are versioned by the service-worker cache. Let browsers
            # reuse them instead of asking Flask for them on every navigation.
            r.headers['Cache-Control'] = 'public, max-age=604800, stale-while-revalidate=86400'
        elif request.endpoint == 'index' and request.method == 'GET':
            # The public homepage contains aggregate, non-user-specific numbers.
            # A short cache avoids recalculating the same landing-page aggregates
            # on every visit while keeping the figures reasonably fresh.
            r.headers['Cache-Control'] = 'public, max-age=30, stale-while-revalidate=60'
        else:
            r.headers['Cache-Control'] = 'no-store'
        return r

    def login_required(f):
        @wraps(f)
        def w(*a, **k):
            if not g.user:
                return redirect(url_for('login', next=request.path))
            return f(*a, **k)
        return w

    def owner_required(f):
        @wraps(f)
        @login_required
        def w(*a, **k):
            if not g.user['is_super_admin']:
                abort(403)
            if app.config['REQUIRE_OWNER_2FA'] and not g.user['totp_enabled'] and request.endpoint not in ('owner_security', 'owner_2fa_start', 'owner_2fa_confirm'):
                flash('This server requires two-factor login for the owner. Set it up now.', 'warning')
                return redirect(url_for('owner_security'))
            return f(*a, **k)
        return w

    def ctx(chama_id, roles=None, allow_inactive=False):
        """Server-side gate for every chama route: must be an ACTIVE member of THIS chama, with the right role, and (unless allowed) a working subscription."""
        m = db().one("SELECT * FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, g.user['id']))
        if not m or (roles and m['role'] not in roles):
            abort(403)
        sub = S.sync_subscription(db(), chama_id)
        if not allow_inactive and sub['status'] not in S.ACCESS_OK:
            abort(redirect(url_for('subscription', chama_id=chama_id)))
        return db().one('SELECT * FROM chamas WHERE id=?', (chama_id,)), m, sub

    def safe_next(t):
        return t if t and t.startswith('/') and not t.startswith('//') and '\\' not in t else None

    def attempts_blocked(key):
        cut = S.iso(S.now_utc() - timedelta(minutes=10))
        db().execute('DELETE FROM login_attempts WHERE at<?', (cut,)); db().commit()
        return db().val('SELECT COUNT(*) FROM login_attempts WHERE key=?', (key,), 0) >= 6

    # ---------- public ----------
    @app.route('/')
    def index():
        # The public landing page must never display invented/demo financial figures.
        # These are aggregated from real, non-test Chama records only.
        period = date.today().strftime('%Y-%m')
        real_chama = 'c.is_test_data=0'
        month_saved = db().val(
            f"SELECT COALESCE(SUM(x.amount_cents),0) FROM contributions x"
            f" JOIN chamas c ON c.id=x.chama_id WHERE {real_chama}"
            " AND x.period=? AND x.status='PAID'",
            (period,), 0) or 0
        loans_approved = db().val(
            f"SELECT COALESCE(SUM(l.principal_cents),0) FROM loans l"
            f" JOIN chamas c ON c.id=l.chama_id WHERE {real_chama}"
            " AND l.decided_at LIKE ? AND l.status IN ('APPROVED','ACTIVE','PAID')",
            (period + '%',), 0) or 0
        total_members = db().val(
            f"SELECT COUNT(*) FROM chama_members m JOIN chamas c ON c.id=m.chama_id"
            f" WHERE {real_chama} AND m.status='ACTIVE'", (), 0) or 0
        paid_members = db().val(
            f"SELECT COUNT(*) FROM chama_members m JOIN chamas c ON c.id=m.chama_id"
            f" WHERE {real_chama} AND m.status='ACTIVE'"
            " AND EXISTS (SELECT 1 FROM contributions x WHERE x.chama_id=m.chama_id"
            " AND x.user_id=m.user_id AND x.period=? AND x.status='PAID')",
            (period,), 0) or 0
        plans = db().all('SELECT * FROM subscription_plans WHERE is_active=1 ORDER BY sort_order')
        return render_template(
            'index.html', plans=plans, trial=S.setting(db(), 'trial_days', 7),
            month_saved=month_saved, loans_approved=loans_approved,
            paid_members=paid_members, total_members=total_members)

    @app.route('/healthz')
    def healthz():
        db().val('SELECT 1')
        return jsonify(status='ok')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            f = request.form
            if len(f.get('password', '')) < 8:
                flash('Password must be at least 8 characters.', 'danger')
            elif f.get('password') != f.get('confirm'):
                flash('Passwords do not match.', 'danger')
            else:
                try:
                    uid = S.claim_account(db(), f.get('phone'), f.get('join_code'), f.get('name'), f.get('email'),
                                          generate_password_hash(f['password']), check_password_hash)
                    if uid is None:
                        uid = S.create_user(db(), f.get('name'), f.get('email'), f.get('phone'), generate_password_hash(f['password']))
                    session.clear(); session['uid'] = uid; session.permanent = True
                    audit(db(), uid, 'USER_REGISTERED', 'user', uid); db().commit()
                    return redirect(url_for('dashboard'))
                except S.BusinessError as e:
                    flash(str(e), 'danger')
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            ident = (request.form.get('login') or '').strip().lower()
            key = f'{ident}|{request.remote_addr}'
            if attempts_blocked(key):
                flash('Too many attempts. Wait 10 minutes and try again.', 'danger')
                return render_template('login.html'), 429
            phone = S.normalize_phone(ident)
            u = db().one('SELECT * FROM users WHERE is_active=1 AND (email=? OR phone=?)', (ident, phone or '-'))
            if u and u['claimed'] and u['password_hash'] and check_password_hash(u['password_hash'], request.form.get('password', '')):
                nxt = safe_next(request.args.get('next'))
                if u['is_super_admin'] and u['totp_enabled']:
                    session.clear(); session['pre2fa'] = u['id']; session['pre2fa_at'] = time.time(); session['pre2fa_next'] = nxt
                    return redirect(url_for('login_2fa'))
                session.clear(); session['uid'] = u['id']; session['ep'] = u['session_epoch']; session.permanent = True
                audit(db(), u['id'], 'LOGIN', 'user', u['id']); db().commit()
                return redirect(nxt or url_for('owner_home' if u['is_super_admin'] else 'dashboard'))
            db().execute('INSERT INTO login_attempts(key,at) VALUES(?,?)', (key, S.iso(S.now_utc()))); db().commit()
            flash('Wrong email/phone or password.', 'danger')
        return render_template('login.html')

    @app.route('/logout', methods=['POST'])
    def logout():
        session.clear()
        return redirect(url_for('index'))

    # ---------- chama ----------
    @app.route('/dashboard')
    @login_required
    def dashboard():
        # Keep a user's Chama visible even if a legacy/migrated database is missing
        # its subscription or plan row. Those records should never make the Chama
        # disappear from the user's dashboard.
        rows = db().all("""SELECT c.id, c.name, c.description, m.role,
            COALESCE(s.status, 'ACTIVE') status, s.due_at,
            COALESCE(p.name, 'Starter') plan_name, COALESCE(p.max_members, 20) max_members,
            (SELECT COUNT(*) FROM chama_members x WHERE x.chama_id=c.id AND x.status='ACTIVE') members
            FROM chama_members m
            JOIN chamas c ON c.id=m.chama_id
            LEFT JOIN subscriptions s ON s.chama_id=c.id
            LEFT JOIN subscription_plans p ON p.id=s.plan_id
            WHERE m.user_id=? AND m.status='ACTIVE'
            ORDER BY c.name""", (g.user['id'],))
        return render_template('dashboard.html', chamas=rows)

    @app.route('/chamas/new', methods=['GET', 'POST'])
    @login_required
    def chama_new():
        plans = db().all('SELECT * FROM subscription_plans WHERE is_active=1 ORDER BY sort_order')
        if request.method == 'POST':
            try:
                cid = S.create_chama(db(), g.user['id'], request.form.get('name'), request.form.get('description'), request.form.get('plan'))
                flash('Your chama is ready. Your free trial has started.', 'success')
                return redirect(url_for('chama_home', chama_id=cid))
            except S.BusinessError as e:
                flash(str(e), 'danger')
        return render_template('chama_new.html', plans=plans, trial=S.setting(db(), 'trial_days', 7))

    @app.route('/chamas/<int:chama_id>')
    @login_required
    def chama_home(chama_id):
        chama, me, sub = ctx(chama_id)
        members = db().all("""SELECT u.id, u.name, u.phone, u.claimed, m.role, m.joined_at FROM chama_members m JOIN users u ON u.id=m.user_id
            WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY m.joined_at LIMIT 200""", (chama_id,))
        plan = S.get_plan(db(), sub['plan_id'])
        period = date.today().isoformat()[:7]
        month_paid = sum(1 for r in F.month_status(db(), chama_id, period) if r['status'] == 'PAID')
        return render_template('chama_home.html', chama=chama, me=me, sub=sub, plan=plan, members=members, roles=S.ROLES,
                               is_admin=me['role'] == 'CHAMA_ADMIN', balance=F.cash_balance(db(), chama_id), my_savings=F.savings(db(), chama_id, g.user['id']),
                               st=F.member_statement(db(), chama_id, g.user['id']), month_paid=month_paid, can_view_statements=me['role'] in F.VIEW_ROLES)

    @app.route('/chamas/<int:chama_id>/members', methods=['POST'])
    @login_required
    def member_add(chama_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        try:
            uid, code = S.add_member_by_phone(db(), chama_id, request.form.get('phone'), request.form.get('name'),
                                              request.form.get('role', 'MEMBER'), g.user['id'], generate_password_hash)
            if code:
                flash('Member added. Give them this join code to register: ' + code + ' (shown only once).', 'success')
            else:
                flash('Member added. They can already log in.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chama_home', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/members/<int:user_id>/code', methods=['POST'])
    @login_required
    def member_code(chama_id, user_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        try:
            code = S.reset_join_code(db(), chama_id, user_id, generate_password_hash)
            flash('New join code: ' + code + '. The old code no longer works.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chama_home', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/members/<int:user_id>/remove', methods=['POST'])
    @login_required
    def member_remove(chama_id, user_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        try:
            S.remove_member(db(), chama_id, user_id, g.user['id'])
            flash('Member removed. Their history is kept.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chama_home', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/subscription')
    @login_required
    def subscription(chama_id):
        chama, me, sub = ctx(chama_id, allow_inactive=True)
        plans = db().all('SELECT * FROM subscription_plans WHERE is_active=1 ORDER BY sort_order')
        pays = db().all('SELECT * FROM payments WHERE chama_id=? ORDER BY id DESC LIMIT 20', (chama_id,))
        return render_template('subscription.html', chama=chama, me=me, sub=sub, plan=S.get_plan(db(), sub['plan_id']), plans=plans, pays=pays,
                               members=S.active_member_count(db(), chama_id), provider=app.config['PROVIDER'], dev=(app.config['PROVIDER'] and app.config['PROVIDER'].name == 'TEST'),
                               can_pay=me['role'] in ('CHAMA_ADMIN', 'TREASURER'), active=sub['status'] in S.ACCESS_OK)

    @app.route('/chamas/<int:chama_id>/subscription/pay', methods=['POST'])
    @login_required
    def subscription_pay(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN', 'TREASURER'), allow_inactive=True)
        prov = app.config['PROVIDER']
        if not prov:
            flash('M-Pesa is not configured yet. ' + app.config['PAY_INSTRUCTIONS'], 'warning')
            return redirect(url_for('subscription', chama_id=chama_id))
        phone = S.normalize_phone(request.form.get('phone'))
        try:
            plan_id, months = int(request.form.get('plan_id')), int(request.form.get('months', 1))
        except (TypeError, ValueError):
            abort(400)
        if not phone:
            flash('Enter a valid M-Pesa phone number.', 'danger')
            return redirect(url_for('subscription', chama_id=chama_id))
        try:
            pid = S.create_payment(db(), chama_id, plan_id, months, prov.name, prov.name, phone, g.user['id'], is_test=prov.name == 'TEST')
        except S.BusinessError as e:
            flash(str(e), 'danger')
            return redirect(url_for('subscription', chama_id=chama_id))
        p = db().one('SELECT * FROM payments WHERE id=?', (pid,))
        res = prov.initiate(p, phone)
        if res['ok']:
            db().execute('UPDATE payments SET provider_txn_id=? WHERE id=?', (res['checkout_id'], pid)); db().commit()
            flash('Payment request sent. Approve it on your phone. Your chama unlocks when M-Pesa confirms.', 'success')
        else:
            db().execute("UPDATE payments SET status='FAILED', notes=?, completed_at=? WHERE id=?", (res['error'][:200], S.iso(S.now_utc()), pid)); db().commit()
            flash(res['error'], 'danger')
        return redirect(url_for('subscription', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/subscription/plan', methods=['POST'])
    @login_required
    def subscription_downgrade(chama_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',), allow_inactive=True)
        try:
            S.downgrade_plan(db(), chama_id, int(request.form.get('plan_id')), g.user['id'])
            flash('Plan changed.', 'success')
        except (S.BusinessError, TypeError, ValueError) as e:
            flash(str(e) if isinstance(e, S.BusinessError) else 'Invalid plan.', 'warning')
        return redirect(url_for('subscription', chama_id=chama_id))

    # ---------- payment callbacks ----------
    @app.route('/webhooks/mpesa/<secret>', methods=['POST'])
    def mpesa_webhook(secret):
        want = os.environ.get('MPESA_CALLBACK_SECRET', '')
        if not want or not hmac.compare_digest(secret, want):
            abort(403)
        ev = MpesaPaymentProvider.parse_callback(request.get_json(silent=True) or {})
        try:
            S.process_webhook(db(), 'MPESA', ev)
        except Exception:
            app.logger.exception('webhook processing failed')
        return jsonify(ResultCode=0, ResultDesc='Accepted')

    @app.route('/dev/pay/<checkout_id>/<outcome>', methods=['POST'])
    @login_required
    def dev_pay(checkout_id, outcome):
        prov = app.config['PROVIDER']
        if not prov or prov.name != 'TEST' or app.config['IS_PRODUCTION']:
            abort(404)
        p = db().one("SELECT * FROM payments WHERE provider='TEST' AND provider_txn_id=?", (checkout_id,))
        if not p:
            abort(404)
        ctx(p['chama_id'], roles=('CHAMA_ADMIN', 'TREASURER'), allow_inactive=True)
        code = {'success': 0, 'failed': 1, 'cancelled': 1032}.get(outcome)
        if code is None:
            abort(400)
        S.process_webhook(db(), 'TEST', {'event_id': 'EVT-' + checkout_id + '-' + outcome, 'checkout_id': checkout_id, 'result_code': code,
                                         'amount_cents': p['amount_cents'], 'receipt': 'TESTRCP' + checkout_id[-8:]})
        flash(f'Test payment marked {outcome}.', 'info')
        return redirect(url_for('subscription', chama_id=p['chama_id']))

    routes_finance.register(app, db, ctx, login_required)
    routes_community.register(app, db, ctx, login_required)
    routes_mgr.register(app, db, ctx, login_required)
    routes_pay.register(app, db, ctx, login_required)
    UP.register(app, db, ctx, login_required)
    routes_security.register(app, db, owner_required, safe_next)
    routes_recovery.register(app, db, ctx, login_required, owner_required)
    devtools.register(app)

    # ---------- owner control center ----------
    @app.route('/owner')
    @owner_required
    def owner_home():
        S.sweep_all(db())
        st = S.owner_stats(db())
        recent = db().all("""SELECT p.*, c.name chama FROM payments p JOIN chamas c ON c.id=p.chama_id ORDER BY p.id DESC LIMIT 8""")
        integrations = [('M-Pesa', 'READY' if MpesaPaymentProvider().configured() else 'CONFIGURATION REQUIRED'),
                        ('Email', 'NOT BUILT YET'), ('SMS', 'NOT BUILT YET'),
                        ('Database', 'PostgreSQL' if db().pg else 'SQLite (not safe for production)')]
        return render_template('owner_home.html', st=st, recent=recent, integrations=integrations)

    @app.route('/owner/chamas')
    @owner_required
    def owner_chamas():
        q, status = (request.args.get('q') or '').strip(), request.args.get('status', '')
        page = max(1, request.args.get('page', 1, type=int))
        where, args = ['1=1'], []
        if q:
            where.append('LOWER(c.name) LIKE ?'); args.append('%' + q.lower() + '%')
        if status:
            where.append('s.status=?'); args.append(status)
        sql = ' AND '.join(where)
        total = db().val(f'SELECT COUNT(*) FROM chamas c JOIN subscriptions s ON s.chama_id=c.id WHERE {sql}', args, 0)
        rows = db().all(f"""SELECT c.id, c.name, c.created_at, s.status, s.due_at, p.name plan_name,
            (SELECT COUNT(*) FROM chama_members x WHERE x.chama_id=c.id AND x.status='ACTIVE') members
            FROM chamas c JOIN subscriptions s ON s.chama_id=c.id JOIN subscription_plans p ON p.id=s.plan_id
            WHERE {sql} ORDER BY c.id DESC LIMIT {PAGE} OFFSET {(page - 1) * PAGE}""", args)
        return render_template('owner_chamas.html', rows=rows, q=q, status=status, page=page, pages=max(1, -(-total // PAGE)), total=total)

    @app.route('/owner/chamas/<int:chama_id>')
    @owner_required
    def owner_chama(chama_id):
        chama = db().one('SELECT * FROM chamas WHERE id=?', (chama_id,)) or abort(404)
        sub = S.sync_subscription(db(), chama_id)
        return render_template('owner_chama.html', chama=chama, sub=sub, plan=S.get_plan(db(), sub['plan_id']),
            plans=db().all('SELECT * FROM subscription_plans ORDER BY sort_order'),
            members=db().all("""SELECT u.name, u.phone, u.email, m.role, m.status FROM chama_members m JOIN users u ON u.id=m.user_id
                WHERE m.chama_id=? ORDER BY m.status, m.joined_at LIMIT 300""", (chama_id,)),
            pays=db().all('SELECT * FROM payments WHERE chama_id=? ORDER BY id DESC LIMIT 20', (chama_id,)),
            logs=db().all('SELECT a.*, u.name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id WHERE a.chama_id=? ORDER BY a.id DESC LIMIT 15', (chama_id,)),
            count=S.active_member_count(db(), chama_id))

    @app.route('/owner/chamas/<int:chama_id>/<action>', methods=['POST'])
    @owner_required
    def owner_action(chama_id, action):
        db().one('SELECT id FROM chamas WHERE id=?', (chama_id,)) or abort(404)
        f, me = request.form, g.user['id']
        try:
            if action == 'suspend':
                S.suspend_chama(db(), chama_id, f.get('reason'), me); flash('Chama suspended. All data is kept.', 'success')
            elif action in ('reactivate', 'extend'):
                S.grant_access(db(), chama_id, int(f.get('days') or 30), me, reactivate=(action == 'reactivate')); flash('Done.', 'success')
            elif action == 'manual-payment':
                S.record_manual_payment(db(), chama_id, int(f.get('plan_id')), int(f.get('months', 1)), f.get('method', 'Cash')[:30],
                                        f.get('reference', ''), f.get('paid_on', '')[:10], f.get('notes', ''), me); flash('Manual payment recorded and subscription updated.', 'success')
            else:
                abort(404)
        except (S.BusinessError, ValueError, TypeError) as e:
            flash(str(e) if isinstance(e, S.BusinessError) else 'Please check the values you entered.', 'danger')
        return redirect(url_for('owner_chama', chama_id=chama_id))

    @app.route('/owner/plans', methods=['GET', 'POST'])
    @owner_required
    def owner_plans():
        if request.method == 'POST':
            f = request.form
            try:
                if f.get('kind') == 'settings':
                    S.update_settings(db(), f.get('trial_days'), f.get('grace_days'), f.get('billing_period_days'), g.user['id'])
                else:
                    S.update_plan(db(), int(f.get('plan_id')), f.get('name'), f.get('price'), f.get('max_members'), f.get('is_active') == '1', g.user['id'])
                flash('Saved.', 'success')
            except (S.BusinessError, ValueError, TypeError) as e:
                flash(str(e) if isinstance(e, S.BusinessError) else 'Invalid values.', 'danger')
            return redirect(url_for('owner_plans'))
        return render_template('owner_plans.html', plans=db().all('SELECT * FROM subscription_plans ORDER BY sort_order'),
                               cfg={k: S.setting(db(), k) for k in ('trial_days', 'grace_days', 'billing_period_days')})

    @app.route('/owner/audit')
    @owner_required
    def owner_audit():
        page = max(1, request.args.get('page', 1, type=int))
        total = db().val('SELECT COUNT(*) FROM audit_logs', (), 0)
        rows = db().all(f"""SELECT a.*, u.name actor, c.name chama FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id
            LEFT JOIN chamas c ON c.id=a.chama_id ORDER BY a.id DESC LIMIT {PAGE} OFFSET {(page - 1) * PAGE}""")
        return render_template('owner_audit.html', rows=rows, page=page, pages=max(1, -(-total // PAGE)))

    # ---------- ChamaPay 2.0 upgrade routes ----------
    routes_upgrade.register_upgrade_routes(app, db, login_required, ctx)
    routes_plus.register_plus_routes(app, db, login_required, ctx, owner_required)
    routes_v22.register_v22_routes(app, db, login_required, ctx, owner_required)

    @app.cli.command('sweep')
    def sweep_cmd():
        d = DB(app.config['DATABASE_URL']); print('changed:', S.sweep_all(d)); d.close()

    for code, msg in ((400, 'That request was not valid.'), (403, 'You do not have access to this page.'), (404, 'We could not find that page.'), (429, 'Too many requests.')):
        app.register_error_handler(code, lambda e, code=code, msg=msg: (render_template('error.html', code=code, msg=msg), code))

    @app.errorhandler(500)
    def server_error(e):
        return render_template('error.html', code=500, msg='Something went wrong on our side. Your data is safe. Please try again.'), 500

    return app


# Always create the WSGI application when Gunicorn imports this module.
# This avoids a None app when the hosting platform does not expose RENDER.
app = create_app()

if __name__ == '__main__':
    create_app().run(debug=False, port=int(os.environ.get('PORT', 5000)))
