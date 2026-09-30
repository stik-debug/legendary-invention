import os, re, sys, tempfile, unittest
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['MPESA_CALLBACK_SECRET'] = 'hooksecret123'
from werkzeug.security import generate_password_hash
import services as S
from db import DB
from app import create_app


class Web(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.url = 'sqlite:///' + self.tmp + '/w.db'
        self.app = create_app({'DATABASE_URL': self.url, 'SECRET_KEY': 'k' * 48, 'TESTING': True})
        self.db = DB(self.url)
        S.create_user(self.db, 'Owner Person', 'owner@example.test', '0700000001', generate_password_hash('OwnerPass123'), super_admin=True)
        self.n = 10

    def tearDown(self):
        self.db.close()

    def client(self):
        return self.app.test_client()

    def tok(self, c, path='/login'):
        html = c.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)

    def post(self, c, path, data=None, follow=False):
        d = dict(data or {}); d['_csrf'] = self.tok(c)
        return c.post(path, data=d, follow_redirects=follow)

    def signup(self, name='Amina Wanjiru'):
        self.n += 1
        c = self.client()
        r = self.post(c, '/register', {'name': name, 'email': f'u{self.n}@example.test', 'phone': f'07{20000000 + self.n}',
                                       'password': 'Password123', 'confirm': 'Password123'})
        self.assertEqual(r.status_code, 302)
        return c, f'u{self.n}@example.test'

    def make_chama(self, c, plan='starter', name='Umoja Group'):
        r = self.post(c, '/chamas/new', {'name': name, 'plan': plan})
        self.assertEqual(r.status_code, 302)
        return int(r.headers['Location'].rstrip('/').split('/')[-1])

    def sub(self, cid):
        return self.db.one('SELECT * FROM subscriptions WHERE chama_id=?', (cid,))

    def owner(self):
        c = self.client()
        self.post(c, '/login', {'login': 'owner@example.test', 'password': 'OwnerPass123'})
        return c

    # ---------- auth ----------
    def test_public_pages_and_landing_content(self):
        c = self.client()
        html = c.get('/').get_data(as_text=True)
        for s in ('Manage Your Chama', 'Save Together. Grow Together.', 'KES 500', 'KES 1,500', 'KES 2,000', 'Up to 15 members', 'Up to 70 members', 'Up to 100 members', 'not per member'):
            self.assertIn(s, html)
        self.assertEqual(c.get('/healthz').json, {'status': 'ok'})
        self.assertEqual(c.get('/login').status_code, 200); self.assertEqual(c.get('/register').status_code, 200)

    def test_register_login_logout(self):
        c, email = self.signup()
        self.assertEqual(c.get('/dashboard').status_code, 200)
        self.post(c, '/logout'); self.assertEqual(c.get('/dashboard').status_code, 302)
        r = self.post(c, '/login', {'login': email, 'password': 'Password123'}); self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get('/dashboard').status_code, 200)

    def test_wrong_password_and_rate_limit(self):
        c, email = self.signup(); self.post(c, '/logout')
        for _ in range(6): self.assertEqual(self.post(c, '/login', {'login': email, 'password': 'nope'}).status_code, 200)
        r = self.post(c, '/login', {'login': email, 'password': 'Password123'})
        self.assertEqual(r.status_code, 429)

    def test_password_hashed_and_duplicate_rejected_and_no_self_promotion(self):
        c = self.client()
        r = self.post(c, '/register', {'name': 'Evil User', 'email': 'evil@example.test', 'phone': '0722000000', 'password': 'Password123',
                                        'confirm': 'Password123', 'is_super_admin': '1'})
        u = self.db.one("SELECT * FROM users WHERE email='evil@example.test'")
        self.assertEqual(u['is_super_admin'], 0); self.assertNotIn('Password123', u['password_hash'])
        c2 = self.client()
        r = self.post(c2, '/register', {'name': 'Evil Two', 'email': 'evil@example.test', 'phone': '0722000001', 'password': 'Password123', 'confirm': 'Password123'})
        self.assertEqual(r.status_code, 200); self.assertEqual(self.db.val("SELECT COUNT(*) FROM users WHERE email='evil@example.test'"), 1)

    def test_csrf_required(self):
        c = self.client(); c.get('/login')
        r = c.post('/login', data={'login': 'owner@example.test', 'password': 'OwnerPass123'})
        self.assertEqual(r.status_code, 302); self.assertEqual(c.get('/owner').status_code, 302)

    def test_open_redirect_blocked(self):
        c, email = self.signup(); self.post(c, '/logout')
        c.get('/login')
        d = {'login': email, 'password': 'Password123', '_csrf': self.tok(c)}
        r = c.post('/login?next=https://evil.example', data=d)
        self.assertEqual(r.headers['Location'], '/dashboard')

    # ---------- authorization ----------
    def test_member_cannot_open_owner_area(self):
        c, _ = self.signup()
        for p in ('/owner', '/owner/chamas', '/owner/plans', '/owner/audit'): self.assertEqual(c.get(p).status_code, 403, p)
        self.assertEqual(self.client().get('/owner').status_code, 302)

    def test_cross_chama_isolation(self):
        a, _ = self.signup('Alice A'); b, _ = self.signup('Bob B')
        ca, cb = self.make_chama(a, name='Chama A'), self.make_chama(b, name='Chama B')
        for path in (f'/chamas/{cb}', f'/chamas/{cb}/subscription'):
            self.assertEqual(a.get(path).status_code, 403, path)
        for path in (f'/chamas/{cb}/members', f'/chamas/{cb}/subscription/pay', f'/chamas/{cb}/subscription/plan'):
            self.assertEqual(self.post(a, path, {'phone': '0700000001', 'plan_id': '1'}).status_code, 403, path)
        self.assertNotIn('Chama B', a.get('/dashboard').get_data(as_text=True))
        self.assertEqual(a.get(f'/chamas/{ca}').status_code, 200)

    def test_plain_member_cannot_manage_members_or_pay(self):
        admin, _ = self.signup('Admin One'); cid = self.make_chama(admin)
        m, email = self.signup('Plain Member')
        mid = self.db.val('SELECT id FROM users WHERE email=?', (email,))
        S.add_member(self.db, cid, mid, 'MEMBER', None)
        self.assertEqual(m.get(f'/chamas/{cid}').status_code, 200)
        self.assertEqual(self.post(m, f'/chamas/{cid}/members', {'phone': email}).status_code, 403)
        self.assertEqual(self.post(m, f'/chamas/{cid}/subscription/pay', {'plan_id': '1'}).status_code, 403)
        self.assertEqual(self.post(m, f'/chamas/{cid}/members/{mid}/remove').status_code, 403)

    def test_error_pages_do_not_leak(self):
        c = self.client(); r = c.get('/nope'); self.assertEqual(r.status_code, 404)
        self.assertNotIn('Traceback', r.get_data(as_text=True))

    def test_security_headers(self):
        h = self.client().get('/').headers
        self.assertEqual(h['X-Frame-Options'], 'DENY'); self.assertIn("script-src 'self'", h['Content-Security-Policy'])

    # ---------- plan limits over HTTP ----------
    def test_member_16_rejected_through_the_app(self):
        c, _ = self.signup(); cid = self.make_chama(c)
        for i in range(14):
            _, email = self.signup(f'Member {i}')
            self.post(c, f'/chamas/{cid}/members', {'phone': email, 'role': 'MEMBER'})
        self.assertEqual(S.active_member_count(self.db, cid), 15)
        _, extra = self.signup('Member Sixteen')
        r = self.post(c, f'/chamas/{cid}/members', {'phone': extra, 'role': 'MEMBER'}, follow=True)
        self.assertIn('allows 15 members', r.get_data(as_text=True))
        self.assertEqual(S.active_member_count(self.db, cid), 15)

    # ---------- subscription lifecycle over HTTP ----------
    def test_suspended_chama_blocked_data_kept_then_payment_restores(self):
        c, _ = self.signup(); cid = self.make_chama(c)
        _, e2 = self.signup('Second Person'); self.post(c, f'/chamas/{cid}/members', {'phone': e2, 'role': 'TREASURER'})
        due = S.parse(self.sub(cid)['due_at'])
        S.sweep_all(self.db, due + timedelta(days=10))
        self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')
        r = c.get(f'/chamas/{cid}'); self.assertEqual(r.status_code, 302); self.assertIn('/subscription', r.headers['Location'])
        self.assertEqual(self.post(c, f'/chamas/{cid}/members', {'phone': e2}).status_code, 302)
        page = c.get(f'/chamas/{cid}/subscription').get_data(as_text=True)
        self.assertIn('Access is suspended', page); self.assertIn('safe', page)
        self.assertEqual(S.active_member_count(self.db, cid), 2)
        plan_id = S.get_plan(self.db, code='starter')['id']
        self.post(c, f'/chamas/{cid}/subscription/pay', {'plan_id': plan_id, 'months': '1', 'phone': '0712345678'})
        p = self.db.one('SELECT * FROM payments WHERE chama_id=?', (cid,))
        self.assertEqual((p['status'], p['amount_cents']), ('PENDING', 50000))
        self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')  # not active until confirmed
        self.post(c, f'/dev/pay/{p["provider_txn_id"]}/success')
        self.assertEqual(self.sub(cid)['status'], 'ACTIVE')
        self.assertEqual(c.get(f'/chamas/{cid}').status_code, 200)
        self.post(c, f'/dev/pay/{p["provider_txn_id"]}/success')
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM payments WHERE status='SUCCESS'"), 1)

    def test_failed_test_payment_does_not_unlock(self):
        c, _ = self.signup(); cid = self.make_chama(c)
        S.sweep_all(self.db, S.parse(self.sub(cid)['due_at']) + timedelta(days=10))
        self.post(c, f'/chamas/{cid}/subscription/pay', {'plan_id': '1', 'months': '1', 'phone': '0712345678'})
        p = self.db.one('SELECT * FROM payments WHERE chama_id=?', (cid,))
        self.post(c, f'/dev/pay/{p["provider_txn_id"]}/failed')
        self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')

    def test_webhook_needs_secret_and_is_idempotent(self):
        c, _ = self.signup(); cid = self.make_chama(c)
        self.post(c, f'/chamas/{cid}/subscription/pay', {'plan_id': '1', 'months': '1', 'phone': '0712345678'})
        p = self.db.one('SELECT * FROM payments WHERE chama_id=?', (cid,))
        self.db.execute("UPDATE payments SET provider='MPESA', provider_txn_id='ws_CO_123' WHERE id=?", (p['id'],)); self.db.commit()
        body = {'Body': {'stkCallback': {'CheckoutRequestID': 'ws_CO_123', 'ResultCode': 0, 'CallbackMetadata': {'Item': [
            {'Name': 'Amount', 'Value': 500.0}, {'Name': 'MpesaReceiptNumber', 'Value': 'SJK2L9XYZA'}]}}}}
        wc = self.client()
        self.assertEqual(wc.post('/webhooks/mpesa/wrong', json=body).status_code, 403)
        self.assertEqual(self.sub(cid)['status'], 'TRIAL')
        due = self.sub(cid)['due_at']
        self.assertEqual(wc.post('/webhooks/mpesa/hooksecret123', json=body).json['ResultCode'], 0)
        wc.post('/webhooks/mpesa/hooksecret123', json=body)
        self.assertEqual(self.sub(cid)['status'], 'ACTIVE')
        self.assertEqual(S.parse(self.sub(cid)['due_at']) - S.parse(due), timedelta(days=30))
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM payments WHERE status='SUCCESS'"), 1)

    def test_forged_and_garbage_webhooks_do_nothing(self):
        c, _ = self.signup(); cid = self.make_chama(c); wc = self.client()
        for body in ({}, {'Body': {}}, {'Body': {'stkCallback': {'CheckoutRequestID': 'fake', 'ResultCode': 0,
                     'CallbackMetadata': {'Item': [{'Name': 'Amount', 'Value': 500}, {'Name': 'MpesaReceiptNumber', 'Value': 'FAKE1'}]}}}}):
            self.assertEqual(wc.post('/webhooks/mpesa/hooksecret123', json=body).status_code, 200)
        self.assertEqual(self.sub(cid)['status'], 'TRIAL'); self.assertEqual(self.db.val("SELECT COUNT(*) FROM payments WHERE status='SUCCESS'"), 0)

    # ---------- owner control center ----------
    def test_owner_pages_render_with_real_numbers(self):
        c, _ = self.signup(); cid = self.make_chama(c); o = self.owner()
        html = o.get('/owner').get_data(as_text=True)
        self.assertIn('Control Center', html); self.assertIn('CONFIGURATION REQUIRED', html)
        for p in ('/owner/chamas', '/owner/plans', '/owner/audit', f'/owner/chamas/{cid}', '/owner/chamas?q=umoja&status=TRIAL'):
            self.assertEqual(o.get(p).status_code, 200, p)
        self.assertIn('Umoja Group', o.get('/owner/chamas?q=umoja').get_data(as_text=True))
        self.assertNotIn('Umoja Group', o.get('/owner/chamas?q=zzzz').get_data(as_text=True))

    def test_owner_suspend_reactivate_extend_manual_payment(self):
        c, _ = self.signup(); cid = self.make_chama(c); o = self.owner()
        self.post(o, f'/owner/chamas/{cid}/suspend', {'reason': ''}); self.assertEqual(self.sub(cid)['status'], 'TRIAL')
        self.post(o, f'/owner/chamas/{cid}/suspend', {'reason': 'Misuse'}); self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')
        self.assertEqual(c.get(f'/chamas/{cid}').status_code, 302)
        self.post(o, f'/owner/chamas/{cid}/reactivate', {'days': '30'}); self.assertEqual(self.sub(cid)['status'], 'ACTIVE')
        self.assertEqual(c.get(f'/chamas/{cid}').status_code, 200)
        due = self.sub(cid)['due_at']
        self.post(o, f'/owner/chamas/{cid}/extend', {'days': '60'})
        self.assertEqual(S.parse(self.sub(cid)['due_at']) - S.parse(due), timedelta(days=60))
        self.post(o, f'/owner/chamas/{cid}/manual-payment', {'plan_id': '1', 'months': '1', 'method': 'Cash', 'reference': 'R-1', 'paid_on': '2026-09-30'})
        self.assertEqual(self.db.val("SELECT method FROM payments WHERE reference='R-1'"), 'MANUAL')
        acts = {r['action'] for r in self.db.all('SELECT action FROM audit_logs')}
        self.assertTrue({'SUBSCRIPTION_SUSPENDED', 'SUBSCRIPTION_REACTIVATED', 'MANUAL_PAYMENT'} <= acts)
        self.assertIn('Manual', o.get('/owner/audit').get_data(as_text=True).title())

    def test_owner_can_change_pricing_limits_and_grace(self):
        o = self.owner()
        self.post(o, '/owner/plans', {'plan_id': '1', 'name': 'Starter', 'price': '600', 'max_members': '20', 'is_active': '1'})
        self.post(o, '/owner/plans', {'kind': 'settings', 'trial_days': '10', 'grace_days': '5', 'billing_period_days': '30'})
        self.assertEqual(self.db.one('SELECT price_cents,max_members FROM subscription_plans WHERE id=1'), {'price_cents': 60000, 'max_members': 20})
        self.assertEqual(S.setting(self.db, 'grace_days'), 5)
        self.assertIn('KES 600', self.client().get('/').get_data(as_text=True))

    def test_production_refuses_weak_secret_and_test_provider(self):
        os.environ['APP_ENV'] = 'production'
        try:
            with self.assertRaises(RuntimeError): create_app({'DATABASE_URL': self.url, 'SECRET_KEY': 'short'})
            app = create_app({'DATABASE_URL': self.url, 'SECRET_KEY': 'k' * 48})
            self.assertIsNone(app.config['PROVIDER'])
            c = app.test_client(); c.get('/login')
            self.assertEqual(c.post('/dev/pay/x/success').status_code, 302)
            page = self.tok(c)
        finally:
            del os.environ['APP_ENV']


if __name__ == '__main__':
    unittest.main(verbosity=1)
