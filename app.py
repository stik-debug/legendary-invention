import hmac
import io
import zipfile
import os
import secrets
import time
import threading
from datetime import date, timedelta
from functools import wraps

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for, send_file
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
import routes_otp
import routes_features
import routes_strategy
import routes_growth
import services as S
import auth_otp as OTP
import twofactor as TF
from db import DB, audit, init_db
from providers import MpesaPaymentProvider, get_provider
from push_config import ensure_vapid_keys, get_vapid_keys

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
        REQUIRE_OWNER_2FA=os.environ.get('REQUIRE_OWNER_2FA') == '1',
        # SMS OTP is OFF unless SMS_OTP_ENABLED=1 is set explicitly (it is not forced on in production).
        SMS_OTP_ENABLED=False,
        # Email OTP is intentionally disabled in this release. A stale Render
        # variable must never strand members on a Gmail code screen.
        EMAIL_OTP_ENABLED=False,
        SUPPORT_CONTACT=os.environ.get('SUPPORT_CONTACT', ''))
    app.config.update(overrides or {})
    # Optional Sentry monitoring; app startup remains functional if monitoring is not configured.
    if os.environ.get('SENTRY_DSN'):
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=os.environ['SENTRY_DSN'], traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.05')),
                            send_default_pii=False, environment=os.environ.get('APP_ENV', 'production' if production else 'development'))
        except ImportError:
            app.logger.warning('SENTRY_DSN is set but sentry-sdk is not installed; error monitoring is inactive.')
        except Exception:
            app.logger.exception('Sentry initialization failed; continuing without remote error monitoring.')
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
    if not ensure_vapid_keys(boot):
        print('WEB PUSH: VAPID keypair unavailable; install cryptography or configure VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY.')
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
            sent, want = (request.form.get('_csrf', '') or request.headers.get('X-CSRF-Token', '')), session.get('_csrf', '')
            if not want or not hmac.compare_digest(sent, want):
                flash('Your session expired. Please try again.', 'danger')
                return redirect(request.referrer or url_for('index'))

    def token():
        if '_csrf' not in session:
            session['_csrf'] = secrets.token_hex(16)
        return session['_csrf']

    app.jinja_env.globals['csrf_input'] = lambda: Markup(f'<input type="hidden" name="_csrf" value="{token()}">')
    app.jinja_env.globals['csrf_token'] = token
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
        return {'user': g.get('user'), 'S': S, 'unread': unread, 'notif_unread': notif, 'push_public_key': get_vapid_keys(db())[0], 'notif_latest_id': (db().val('SELECT COALESCE(MAX(id),0) FROM notifications WHERE user_id=?', (g.user['id'],), 0) if g.get('user') else 0)}

    @app.route('/sw.js')
    def service_worker():
        # A service worker served from /static/ is scoped to /static/ only and
        # navigator.serviceWorker.ready can hang on app pages. Serve it at root
        # so it can control all ChamaPay pages and receive push events reliably.
        response = send_file(os.path.join(app.static_folder, 'sw.js'), mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    @app.after_request
    def deliver_queued_sms(r):
        # SMS is deliberately processed after the response is prepared and in a separate
        # daemon thread so Africa's Talking latency never makes a financial action feel slow.
        if os.environ.get('AT_USERNAME') and os.environ.get('AT_API_KEY'):
            try:
                t = threading.Thread(target=N.process_sms_outbox, args=(app.config['DATABASE_URL'], 8), daemon=True)
                t.start()
            except Exception:
                pass
        return r

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
        # Expired groups retain safe, read-only access to essential historical
        # records. Mutating routes and all POST requests still require an active plan.
        read_only_endpoints = {'chama_home', 'savings', 'statement', 'member_statement_alias',
            'statement_pdf', 'statement_csv', 'reports', 'export_csv', 'chama_audit_log',
            'dividends', 'activity', 'chama_ledger', 'ledger'}
        read_only = request.method == 'GET' and request.endpoint in read_only_endpoints
        if not allow_inactive and sub['status'] not in S.ACCESS_OK and not read_only:
            abort(redirect(url_for('subscription', chama_id=chama_id)))
        return db().one('SELECT * FROM chamas WHERE id=?', (chama_id,)), m, sub

    # App-wide notification polling: alerts appear as toasts wherever the user is working.
    @app.route('/api/notifications/poll')
    @login_required
    def notification_poll():
        after = max(0, request.args.get('after', 0, type=int))
        rows = db().all("""SELECT n.id,n.text,n.link,n.created_at,n.chama_id,c.name AS chama
            FROM notifications n LEFT JOIN chamas c ON c.id=n.chama_id
            WHERE n.user_id=? AND n.id>? ORDER BY n.id ASC LIMIT 30""", (g.user['id'], after))
        return jsonify(notifications=[{'id': r['id'], 'text': r['text'], 'link': r['link'], 'created_at': r['created_at'], 'chama': r['chama']} for r in rows])

    @app.route('/api/push/subscribe', methods=['POST'])
    @login_required
    def push_subscribe():
        vapid_public, vapid_private = get_vapid_keys(db())
        if not vapid_public or not vapid_private:
            return jsonify(ok=False, error='Push setup failed on the server. Please contact ChamaPay support.'), 503
        payload = request.get_json(silent=True) or {}
        subscription = payload.get('subscription')
        if not isinstance(subscription, dict) or not subscription.get('endpoint') or not isinstance(subscription.get('keys'), dict):
            return jsonify(ok=False, error='Invalid browser subscription.'), 400
        import json as _json
        from urllib.parse import urlparse
        endpoint = str(subscription['endpoint'])[:2000]
        parsed_endpoint = urlparse(endpoint)
        host = (parsed_endpoint.hostname or '').lower()
        allowed_push_hosts = ('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com', 'notify.windows.com')
        if parsed_endpoint.scheme != 'https' or not host or not any(host == allowed or host.endswith('.' + allowed) for allowed in allowed_push_hosts):
            return jsonify(ok=False, error='Unsupported browser push endpoint.'), 400
        existing = db().one('SELECT id FROM push_subscriptions WHERE endpoint=?', (endpoint,))
        now = S.iso(S.now_utc())
        if existing:
            db().execute('UPDATE push_subscriptions SET user_id=?,subscription_json=?,last_used_at=? WHERE id=?', (g.user['id'], _json.dumps(subscription), now, existing['id']))
        else:
            db().insert('push_subscriptions', user_id=g.user['id'], endpoint=endpoint, subscription_json=_json.dumps(subscription), created_at=now, last_used_at=now)
        db().commit()
        # A member who explicitly enables device notifications has opted in to push.
        stamp = S.iso(S.now_utc())
        db().execute("INSERT INTO notification_preferences(user_id,in_app,email,sms,push,updated_at) VALUES(?,1,1,0,1,?) ON CONFLICT(user_id) DO UPDATE SET push=1,updated_at=excluded.updated_at", (g.user['id'], stamp))
        db().commit()
        return jsonify(ok=True)

    @app.route('/api/push/status')
    @login_required
    def push_status():
        public, private = get_vapid_keys(db())
        count = int(db().val('SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?', (g.user['id'],), 0))
        try:
            import importlib.util as _import_util
            delivery_library = _import_util.find_spec('pywebpush') is not None
        except Exception:
            delivery_library = False
        return jsonify(server_configured=bool(public and private), delivery_library=delivery_library, device_subscriptions=count,
                       ready=bool(public and private and delivery_library and count > 0))

    @app.route('/api/push/test', methods=['POST'])
    @login_required
    def push_test():
        public, private = get_vapid_keys(db())
        if not public or not private:
            return jsonify(ok=False, error='Push service is not configured. Please contact ChamaPay support.'), 503
        try:
            import pywebpush  # noqa: F401
        except ImportError:
            return jsonify(ok=False, error='Push delivery package is missing on the server. Please contact ChamaPay support.'), 503
        pref = db().one('SELECT push FROM notification_preferences WHERE user_id=?', (g.user['id'],))
        if pref is not None and not int(pref.get('push', 1)):
            return jsonify(ok=False, error='Push notifications are turned off in your preferences. Enable them and try again.'), 400
        result = N.send_push_test(db(), g.user['id'])
        return jsonify(result), (200 if result.get('ok') else 502)

    @app.route('/api/push/unsubscribe', methods=['POST'])
    @login_required
    def push_unsubscribe():
        payload = request.get_json(silent=True) or {}
        endpoint = str(payload.get('endpoint') or '')[:2000]
        if endpoint:
            db().execute('DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?', (endpoint, g.user['id']))
            db().commit()
        return jsonify(ok=True)

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
        # The Command Center is the owner's private console: only the platform owner or the person who created a chama sees its card.
        show_command = bool(g.user and (g.user['is_super_admin'] or db().val('SELECT COUNT(*) FROM chamas WHERE created_by=?', (g.user['id'],), 0)))
        return render_template(
            'index.html', plans=plans, trial=S.setting(db(), 'trial_days', 7), show_command=show_command,
            month_saved=month_saved, loans_approved=loans_approved,
            paid_members=paid_members, total_members=total_members)

    # Landing-page feature cards: take the signed-in person straight to that feature in their chama.
    GO_FEATURES = {'loans': ('loans', 'Loans'), 'goals': ('chama_goals', 'Goals & Wealth'), 'votes': ('chama_votes', 'Voting & Constitution'),
                   'meetings': ('meetings', 'Meetings'), 'passport': ('member_passport', 'Member Passport'),
                   'ai': ('chama_ai', 'ChamaPay AI'), 'strategy': ('chama_strategy', 'Strategy & Continuity'), 'command': ('command_center', 'Command Center')}

    @app.route('/go/<feature>')
    @login_required
    def go_feature(feature):
        if feature not in GO_FEATURES:
            abort(404)
        endpoint, label = GO_FEATURES[feature]
        rows = db().all("SELECT c.id, c.name, c.created_by FROM chama_members m JOIN chamas c ON c.id=m.chama_id"
                        " WHERE m.user_id=? AND m.status='ACTIVE' ORDER BY c.name", (g.user['id'],))
        if feature == 'command':  # owner-only: only chamas this person created
            rows = [r for r in rows if int(r['created_by']) == int(g.user['id'])]
            if not rows:
                flash('The Command Center is only available to the person who created the chama.', 'warning')
                return redirect(url_for('dashboard'))
        if not rows:
            flash('Join or create a chama first, then you can open ' + label + '.', 'info')
            return redirect(url_for('dashboard'))

        def target(cid):
            kw = {'user_id': g.user['id']} if feature == 'passport' else {}
            return url_for(endpoint, chama_id=cid, **kw)
        if len(rows) == 1:
            return redirect(target(rows[0]['id']))
        return render_template('go_choose.html', label=label, chamas=[{'name': r['name'], 'url': target(r['id'])} for r in rows])

    @app.route('/install')
    def install_app():
        return render_template('install.html')

    @app.route('/privacy-policy')
    def privacy_policy():
        return render_template('privacy_policy.html', support_contact=app.config.get('SUPPORT_CONTACT'))

    @app.route('/terms')
    def terms():
        return render_template('terms.html', support_contact=app.config.get('SUPPORT_CONTACT'))

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
                    normalized_phone = S.normalize_phone(f.get('phone'))
                    normalized_email = (f.get('email') or '').strip().lower()
                    # If OTP delivery failed after account creation, let the same person
                    # safely retry signup with the same phone, email and password. This
                    # must not bypass invitation codes for unclaimed placeholders.
                    existing = db().one('SELECT * FROM users WHERE phone=? OR email=?',
                                        (normalized_phone or '-', normalized_email))
                    if (existing and existing.get('claimed') and not existing.get('phone_verified_at')
                            and existing.get('phone') == normalized_phone
                            and existing.get('email') == normalized_email
                            and existing.get('password_hash')
                            and check_password_hash(existing['password_hash'], f['password'])):
                        uid = existing['id']
                    else:
                        uid = S.claim_account(db(), f.get('phone'), f.get('join_code'), f.get('name'), f.get('email'),
                                              generate_password_hash(f['password']), check_password_hash)
                        if uid is None:
                            uid = S.create_user(db(), f.get('name'), f.get('email'), f.get('phone'), generate_password_hash(f['password']))
                    db().commit()
                    if not app.config['EMAIL_OTP_ENABLED']:
                        # Email verification is switched off only for local/test deployments: create the account and log the person in.
                        with db().tx():
                            audit(db(), uid, 'USER_REGISTERED', 'user', uid)
                        row = db().one('SELECT session_epoch FROM users WHERE id=?', (uid,))
                        session.clear(); session['uid'] = uid; session['ep'] = row['session_epoch']; session.permanent = True
                        return redirect(url_for('dashboard'))
                    # Signup is a sensitive action: verify ownership of the phone before creating a logged-in session.
                    ok, msg, oid = __import__('email_otp').request_otp(app, db(), uid, normalized_email, 'signup', request.remote_addr)
                    if not ok:
                        flash(msg, 'warning')
                        return render_template('register.html')
                    session.clear(); session['otp_flow'] = {'purpose':'signup', 'user_id':uid,
                                                            'email':normalized_email, 'otp_id':oid}
                    return redirect(url_for('otp_verify'))
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
                # New/unverified accounts must complete phone verification once before
                # they can establish a normal password session. After verification,
                # ordinary logins never require SMS OTP again.
                if app.config['EMAIL_OTP_ENABLED'] and not u.get('email_verified_at'):
                    ok, msg, oid = __import__('email_otp').request_otp(app, db(), u['id'], u['email'], 'login_email_verify', request.remote_addr)
                    if not ok:
                        flash(msg, 'warning')
                        return render_template('login.html')
                    session.clear(); session['otp_flow'] = {'purpose':'login_email_verify', 'user_id':u['id'],
                                                            'email':u['email'], 'otp_id':oid, 'next':nxt}
                    return redirect(url_for('otp_verify'))
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

    def render_chama_home(chama_id, invite_code=None, invite_name=None):
        chama, me, sub = ctx(chama_id)
        members = db().all("""SELECT u.id, u.name, u.phone, u.claimed, m.role, m.joined_at FROM chama_members m JOIN users u ON u.id=m.user_id
            WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY m.joined_at LIMIT 200""", (chama_id,))
        plan = S.get_plan(db(), sub['plan_id'])
        period = date.today().isoformat()[:7]
        month_paid = sum(1 for r in F.month_status(db(), chama_id, period) if r['status'] == 'PAID')
        my_status = next((r for r in F.month_status(db(), chama_id, period) if int(r['id']) == int(g.user['id'])), None)
        my_actions = db().all("SELECT title,due_date,status FROM meeting_actions WHERE chama_id=? AND owner_user_id=? AND status='OPEN' ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,due_date LIMIT 5", (chama_id, g.user['id']))
        my_open_loans = db().all("SELECT id,total_due_cents,paid_cents FROM loans WHERE chama_id=? AND user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 5", (chama_id,g.user['id']))
        return render_template('chama_home.html', chama=chama, me=me, sub=sub, plan=plan, members=members, roles=S.ROLES,
                               is_admin=me['role'] == 'CHAMA_ADMIN', balance=F.cash_balance(db(), chama_id), my_savings=F.savings(db(), chama_id, g.user['id']),
                               st=F.member_statement(db(), chama_id, g.user['id']), month_paid=month_paid, can_view_statements=me['role'] in F.VIEW_ROLES,
                               my_status=my_status, my_actions=my_actions, my_open_loans=my_open_loans, invite_code=invite_code, invite_name=invite_name)

    @app.route('/chamas/<int:chama_id>')
    @login_required
    def chama_home(chama_id):
        return render_chama_home(chama_id)

    @app.route('/chamas/<int:chama_id>/leave', methods=['POST'])
    @login_required
    def chama_leave(chama_id):
        # A member can leave voluntarily, but unresolved financial obligations or an
        # untransferred administrator role must be settled first. Historical books stay intact.
        chama, me, sub = ctx(chama_id, allow_inactive=True)
        uid = g.user['id']
        marks = "'PENDING','APPROVED','ACTIVE'"
        blockers = []
        if db().val(f"SELECT COUNT(*) FROM loans WHERE chama_id=? AND user_id=? AND status IN ({marks})", (chama_id, uid), 0):
            blockers.append('Close your outstanding loans first.')
        if db().val("SELECT COUNT(*) FROM fines WHERE chama_id=? AND user_id=? AND status NOT IN ('PAID','WAIVED','CANCELLED')", (chama_id, uid), 0):
            blockers.append('Settle or waive your outstanding fines first.')
        if db().val(f"SELECT COUNT(*) FROM loan_guarantors g JOIN loans l ON l.id=g.loan_id WHERE l.chama_id=? AND g.guarantor_user_id=? AND l.status IN ({marks})", (chama_id, uid), 0):
            blockers.append('Arrange release or replacement for your active loan guarantees first.')
        if me['role'] == 'CHAMA_ADMIN' and db().val("SELECT COUNT(*) FROM chama_members WHERE chama_id=? AND status='ACTIVE' AND role='CHAMA_ADMIN'", (chama_id,), 0) <= 1:
            blockers.append('Transfer the Chama Admin role to another active member before leaving.')
        if blockers:
            flash(' '.join(blockers), 'warning')
            return redirect(url_for('chama_home', chama_id=chama_id))
        with db().tx():
            db().execute("UPDATE chama_members SET status='REMOVED',removed_at=? WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (S.iso(S.now_utc()), chama_id, uid))
            audit(db(), uid, 'MEMBER_LEFT_CHAMA', 'chama_member', me['id'], chama_id, {'user_id': uid})
            N.notify_roles(db(), chama_id, ('CHAMA_ADMIN','TREASURER','SECRETARY'), f'{g.user["name"]} left {chama["name"]}. Review the member list and any outstanding records.', f'/chamas/{chama_id}/members', by=uid)
        flash('You have left this chama. Your financial and audit history remains preserved.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/chamas/<int:chama_id>/delete', methods=['POST'])
    @login_required
    def chama_delete(chama_id):
        # "Delete" is a controlled archive: never hard-delete a chama's financial books.
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN',), allow_inactive=True)
        if int(chama['created_by']) != int(g.user['id']):
            abort(403)
        if (request.form.get('confirm_name') or '').strip() != chama['name']:
            flash('Type the exact chama name to confirm closure.', 'warning')
            return redirect(url_for('chama_home', chama_id=chama_id))
        now = S.iso(S.now_utc())
        with db().tx():
            db().execute("DELETE FROM public_chama_listings WHERE chama_id=?", (chama_id,))
            db().execute("UPDATE subscriptions SET status='CANCELLED',cancelled_at=?,updated_at=? WHERE chama_id=?", (now, now, chama_id))
            db().execute("UPDATE chama_members SET status='REMOVED',removed_at=? WHERE chama_id=? AND status='ACTIVE'", (now, chama_id))
            audit(db(), g.user['id'], 'CHAMA_CLOSED_BY_ADMIN', 'chama', chama_id, chama_id, {'name': chama['name'], 'records_preserved': True})
        flash('Chama closed. Members can no longer access it, and financial/audit records have been preserved for accountability.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/account/delete', methods=['POST'])
    @login_required
    def account_delete_self():
        import accounts as AC
        from services import BusinessError
        uid = g.user['id']
        if (request.form.get('confirm') or '').strip().upper() != 'DELETE':
            flash('Type DELETE to confirm account deletion.', 'warning')
            return redirect(url_for('security_privacy'))
        try:
            AC.anonymise_user(db(), uid, uid)
        except BusinessError as e:
            flash(str(e), 'warning')
            return redirect(url_for('security_privacy'))
        session.clear()
        flash('Your account has been deleted and personal details anonymised. Financial records required for group accountability are retained without your personal identity.', 'success')
        return redirect(url_for('index'))

    @app.route('/chamas/<int:chama_id>/members', methods=['POST'])
    @login_required
    def member_add(chama_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        try:
            uid, code = S.add_member_by_phone(db(), chama_id, request.form.get('phone'), request.form.get('name'),
                                              request.form.get('role', 'MEMBER'), g.user['id'], generate_password_hash)
            if code:
                # Render the code directly in the authenticated admin's response.
                # It is never stored in plaintext and never placed in a URL/session cookie.
                return render_chama_home(chama_id, invite_code=code, invite_name=request.form.get('name') or 'New member')
            N.notify(db(), uid, chama_id, f'You were added to {db().one("SELECT name FROM chamas WHERE id=?", (chama_id,))["name"]}. Open ChamaPay to view your group and its updates.', f'/chamas/{chama_id}', by=g.user['id'])
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
            member = db().one('SELECT name FROM users WHERE id=?', (user_id,))
            return render_chama_home(chama_id, invite_code=code, invite_name=member['name'] if member else 'Member')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chama_home', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/members/<int:user_id>/remove', methods=['POST'])
    @login_required
    def member_remove(chama_id, user_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',))
        try:
            S.remove_member(db(), chama_id, user_id, g.user['id'])
            N.notify(db(), user_id, chama_id, f'Your membership in {db().one("SELECT name FROM chamas WHERE id=?", (chama_id,))["name"]} was removed by a group administrator. Your historical financial records remain preserved.', '/notifications', by=g.user['id'])
            flash('Member removed. Their history is kept.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('chama_home', chama_id=chama_id))

    @app.route('/discover')
    def discover_chamas():
        # Only groups whose officials explicitly opted in are shown publicly.
        rows = db().all("""SELECT c.id,c.name,c.description,l.tagline,l.updated_at,
            (SELECT COUNT(*) FROM chama_members m WHERE m.chama_id=c.id AND m.status='ACTIVE') members
            FROM public_chama_listings l JOIN chamas c ON c.id=l.chama_id ORDER BY l.updated_at DESC LIMIT 100""")
        return render_template('discover.html', rows=rows)

    @app.route('/chamas/<int:chama_id>/discoverability', methods=['GET','POST'])
    @login_required
    def chama_discoverability(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN',), allow_inactive=True)
        listing = db().one('SELECT * FROM public_chama_listings WHERE chama_id=?', (chama_id,))
        if request.method == 'POST':
            action = request.form.get('action')
            now = S.iso(S.now_utc())
            if action == 'publish':
                tagline = (request.form.get('tagline') or '').strip()[:180]
                db().execute("INSERT INTO public_chama_listings(chama_id,tagline,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(chama_id) DO UPDATE SET tagline=excluded.tagline,updated_at=excluded.updated_at", (chama_id,tagline,now,now))
                audit(db(), g.user['id'], 'CHAMA_PUBLIC_LISTING_ENABLED', 'chama', chama_id, chama_id, {'tagline':tagline})
                db().commit(); flash('Your chama is now listed publicly. Only its name, description, tagline and active-member count are shown.', 'success')
            elif action == 'unpublish':
                db().execute('DELETE FROM public_chama_listings WHERE chama_id=?', (chama_id,))
                audit(db(), g.user['id'], 'CHAMA_PUBLIC_LISTING_DISABLED', 'chama', chama_id, chama_id)
                db().commit(); flash('Your chama was removed from public discovery.', 'success')
            else: abort(400)
            return redirect(url_for('chama_discoverability', chama_id=chama_id))
        requests = db().all("SELECT r.*,u.name user_name,u.email user_email FROM chama_discovery_requests r JOIN users u ON u.id=r.user_id WHERE r.chama_id=? ORDER BY r.id DESC LIMIT 100", (chama_id,))
        return render_template('discoverability.html', chama=chama, me=me, listing=listing, requests=requests)

    @app.route('/discover/<int:chama_id>/request', methods=['POST'])
    @login_required
    def request_to_join_discovered_chama(chama_id):
        if not db().one('SELECT chama_id FROM public_chama_listings WHERE chama_id=?', (chama_id,)): abort(404)
        if db().one("SELECT id FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id,g.user['id'])):
            flash('You are already a member of this chama.', 'info'); return redirect(url_for('discover_chamas'))
        msg = (request.form.get('message') or '').strip()[:500]
        now = S.iso(S.now_utc())
        db().execute("INSERT INTO chama_discovery_requests(chama_id,user_id,message,status,created_at,updated_at) VALUES(?,?,?,'PENDING',?,?) ON CONFLICT(chama_id,user_id) DO UPDATE SET message=excluded.message,status='PENDING',updated_at=excluded.updated_at", (chama_id,g.user['id'],msg,now,now))
        db().commit(); flash('Your request was sent to the chama admin for review. You have not been added as a member yet.', 'success')
        return redirect(url_for('discover_chamas'))

    @app.route('/chamas/<int:chama_id>/discovery-requests/<int:request_id>', methods=['POST'])
    @login_required
    def review_discovery_request(chama_id, request_id):
        ctx(chama_id, roles=('CHAMA_ADMIN',), allow_inactive=True)
        action = request.form.get('action')
        status = {'approve':'APPROVED','decline':'DECLINED'}.get(action)
        if not status: abort(400)
        req = db().one('SELECT * FROM chama_discovery_requests WHERE id=? AND chama_id=?', (request_id,chama_id))
        if not req: abort(404)
        db().execute('UPDATE chama_discovery_requests SET status=?,updated_at=? WHERE id=?', (status,S.iso(S.now_utc()),request_id))
        audit(db(), g.user['id'], 'DISCOVERY_REQUEST_'+status, 'chama_discovery_request', request_id, chama_id)
        db().commit()
        flash('Request reviewed. Approving records interest only; add the person through normal membership management to grant access.', 'success')
        return redirect(url_for('chama_discoverability', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/audit-pack.zip')
    @login_required
    def audit_pack(chama_id):
        # A chama-scoped, read-only evidence pack for annual review. Uses existing records only.
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN', 'TREASURER', 'AUDITOR'))
        import reports as AuditReports
        from datetime import datetime, timezone
        from flask import Response
        buffer = io.BytesIO()
        manifest = [f'ChamaPay financial audit pack', f'Chama: {chama["name"]}', f'Chama ID: {chama_id}',
                    f'Generated UTC: {datetime.now(timezone.utc).isoformat()}',
                    '', 'This pack is an export of application records, not an independent bank audit. Verify balances against external statements.']
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('README.txt', '\n'.join(manifest) + '\n')
            for kind in AuditReports.EXPORTS:
                try:
                    header, rows = AuditReports.export(db(), chama_id, kind)
                    archive.writestr(f'{kind}.csv', F.to_csv(header, rows))
                except (KeyError, ValueError):
                    continue
            ledger = db().all("SELECT occurred_on,kind,direction,amount_cents,account,description,ref_type,ref_id,created_by,created_at FROM ledger_transactions WHERE chama_id=? ORDER BY id", (chama_id,))
            archive.writestr('ledger.csv', F.to_csv(['Date','Kind','Direction','Amount cents','Account','Description','Reference type','Reference ID','Created by user ID','Created at'],
                [[r.get('occurred_on'),r.get('kind'),r.get('direction'),r.get('amount_cents'),r.get('account'),r.get('description'),r.get('ref_type'),r.get('ref_id'),r.get('created_by'),r.get('created_at')] for r in ledger]))
            logs = db().all("SELECT actor_id,action,entity_type,entity_id,metadata,created_at FROM audit_logs WHERE chama_id=? ORDER BY id", (chama_id,))
            archive.writestr('audit-log.csv', F.to_csv(['Actor user ID','Action','Entity type','Entity ID','Metadata','Created at'],
                [[r.get('actor_id'),r.get('action'),r.get('entity_type'),r.get('entity_id'),r.get('metadata'),r.get('created_at')] for r in logs]))
        buffer.seek(0)
        return Response(buffer.getvalue(), mimetype='application/zip', headers={'Content-Disposition': f'attachment; filename=chamapay-audit-pack-{chama_id}.zip', 'Cache-Control':'no-store'})

    @app.route('/chamas/<int:chama_id>/subscription')
    @login_required
    def subscription(chama_id):
        chama, me, sub = ctx(chama_id, allow_inactive=True)
        plans = db().all('SELECT * FROM subscription_plans WHERE is_active=1 ORDER BY sort_order')
        pays = db().all('SELECT * FROM payments WHERE chama_id=? ORDER BY id DESC LIMIT 20', (chama_id,))
        members = S.active_member_count(db(), chama_id)
        value_report = S.subscription_value_report(db(), chama_id)
        plan = S.get_plan(db(), sub['plan_id'])
        return render_template('subscription.html', chama=chama, me=me, sub=sub, plan=plan, plans=plans, pays=pays,
                               members=members, member_price=S.setting(db(), 'price_per_member_cents', S.DEFAULT_MEMBER_PRICE_CENTS),
                               monthly_cost=int(plan['price_cents']),
                               value_report=value_report, provider=app.config['PROVIDER'], dev=(app.config['PROVIDER'] and app.config['PROVIDER'].name == 'TEST'),
                               can_pay=me['role'] in ('CHAMA_ADMIN', 'TREASURER'), active=sub['status'] in S.ACCESS_OK, trial_days=S.setting(db(), 'trial_days', 30))

    @app.route('/chamas/<int:chama_id>/trust-and-planning', methods=['GET', 'POST'])
    @login_required
    def trust_and_planning(chama_id):
        chama, me, sub = ctx(chama_id, allow_inactive=True)
        if request.method == 'POST':
            subject = (request.form.get('subject') or '').strip()[:120]
            body = (request.form.get('body') or '').strip()[:4000]
            if not subject or len(body) < 10:
                flash('Enter a subject and at least 10 characters describing the issue.', 'warning')
            else:
                now = S.iso(S.now_utc())
                db().insert('support_tickets', user_id=g.user['id'], chama_id=chama_id, subject=subject, body=body,
                            status='OPEN', created_at=now, updated_at=now)
                audit(db(), g.user['id'], 'SUPPORT_TICKET_CREATED', 'support_ticket', None, chama_id, meta={'subject': subject})
                db().commit()
                flash('Support request submitted.', 'success')
            return redirect(url_for('trust_and_planning', chama_id=chama_id))
        # Heuristic flags are prompts for human review, never proof of fraud.
        recent = db().all("SELECT id,kind,direction,amount_cents,description,occurred_on,created_by FROM ledger_transactions WHERE chama_id=? AND occurred_on>=? ORDER BY id DESC LIMIT 100", (chama_id, (date.today()-timedelta(days=30)).isoformat()))
        flags = []
        for row in recent:
            if int(row.get('amount_cents') or 0) >= 10000000:
                flags.append({'when': row.get('occurred_on'), 'title': 'Large transaction for review', 'detail': f"{row.get('kind')} · {row.get('direction')} · KES {int(row.get('amount_cents'))/100:,.2f}", 'id': row.get('id')})
        bursts = db().all("SELECT occurred_on,COUNT(*) n,SUM(amount_cents) total FROM ledger_transactions WHERE chama_id=? AND occurred_on>=? GROUP BY occurred_on HAVING COUNT(*)>=5 ORDER BY occurred_on DESC", (chama_id, (date.today()-timedelta(days=30)).isoformat()))
        for row in bursts:
            flags.append({'when': row.get('occurred_on'), 'title': 'High transaction volume', 'detail': f"{row.get('n')} ledger entries on one date; review if expected.", 'id': ''})
        # Forecast uses observed recent average contribution inflow and known outstanding loan balances.
        inflow = int(db().val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND status='PAID' AND paid_on>=?", (chama_id, (date.today()-timedelta(days=90)).isoformat()), 0) or 0)
        outstanding = int(db().val("SELECT COALESCE(SUM(total_due_cents-paid_cents),0) FROM loans WHERE chama_id=? AND status IN ('DISBURSED','ACTIVE','APPROVED')", (chama_id,), 0) or 0)
        cash_in = int(db().val("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount_cents WHEN direction='OUT' THEN -amount_cents ELSE 0 END),0) FROM ledger_transactions WHERE chama_id=?", (chama_id,), 0) or 0)
        forecast_month = round(inflow / 3)
        tickets = db().all("SELECT id,subject,status,created_at,updated_at FROM support_tickets WHERE chama_id=? AND user_id=? ORDER BY id DESC LIMIT 10", (chama_id, g.user['id']))
        return render_template('trust_planning.html', chama=chama, me=me, flags=flags[:30], cash_in=cash_in,
                               forecast_month=forecast_month, forecast_3m=forecast_month*3, outstanding=outstanding, tickets=tickets)

    @app.route('/chamas/<int:chama_id>/subscription/feedback', methods=['POST'])
    @login_required
    def subscription_feedback(chama_id):
        chama, me, sub = ctx(chama_id, roles=('CHAMA_ADMIN', 'TREASURER'), allow_inactive=True)
        reason = (request.form.get('reason') or '').strip()[:80]
        detail = (request.form.get('detail') or '').strip()[:500]
        allowed = {'PRICE', 'NOT_ENOUGH_VALUE', 'TOO_COMPLEX', 'PAYMENT_DIFFICULTY', 'GROUP_INACTIVE', 'OTHER'}
        if reason not in allowed:
            flash('Choose a reason so we can improve ChamaPay.', 'warning')
        else:
            audit(db(), g.user['id'], 'SUBSCRIPTION_FEEDBACK', 'subscription', sub['id'], chama_id,
                  {'reason': reason, 'detail': detail})
            db().commit()
            flash('Thank you. Your feedback has been recorded.', 'success')
        return redirect(url_for('subscription', chama_id=chama_id))

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
    routes_growth.register(app, db, ctx, login_required, owner_required)
    routes_mgr.register(app, db, ctx, login_required)
    routes_pay.register(app, db, ctx, login_required)
    UP.register(app, db, ctx, login_required)
    routes_security.register(app, db, owner_required, safe_next)
    routes_recovery.register(app, db, ctx, login_required, owner_required)
    routes_otp.register(app, db, login_required)
    routes_features.register(app, db, ctx, login_required, owner_required)
    routes_strategy.register_strategy_routes(app, db, ctx, login_required)
    import routes_trust
    routes_trust.register(app, db, ctx, login_required)
    devtools.register(app)

    # ---------- owner control center ----------
    @app.route('/owner')
    @owner_required
    def owner_home():
        S.sweep_all(db())
        st = S.owner_stats(db())
        recent = db().all("""SELECT p.*, c.name chama FROM payments p JOIN chamas c ON c.id=p.chama_id ORDER BY p.id DESC LIMIT 8""")
        integrations = [('M-Pesa', 'READY' if MpesaPaymentProvider().configured() else 'CONFIGURATION REQUIRED'),
                        ('Email', 'NOT CONFIGURED'), ('SMS', 'READY' if os.environ.get('AT_USERNAME') and os.environ.get('AT_API_KEY') else 'CONFIGURATION REQUIRED'),
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
                    current_plan = S.get_plan(db(), int(f.get('plan_id')))
                    if not current_plan:
                        raise S.BusinessError('Plan not found.')
                    # Each tier has one fixed monthly price paid by the chama as a group.
                    S.update_plan(db(), int(f.get('plan_id')), f.get('name'), current_plan['price_cents'] / 100, f.get('max_members'), f.get('is_active') == '1', g.user['id'])
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
