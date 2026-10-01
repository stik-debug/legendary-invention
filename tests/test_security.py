import base64, os, re, sys, time, unittest
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import twofactor as T
from app import create_app


class TotpUnits(unittest.TestCase):
    SECRET = base64.b32encode(b'12345678901234567890').decode().rstrip('=')

    def test_rfc6238_vector(self):
        self.assertEqual(T.code_at(self.SECRET, 59 // 30), '287082')
        self.assertEqual(T.code_at(self.SECRET, 1111111109 // 30), '081804')

    def test_window_replay_and_garbage(self):
        step = 1111111109 // 30
        code = T.code_at(self.SECRET, step)
        self.assertEqual(T.verify_code(self.SECRET, code, 0, now=1111111109), step)
        self.assertEqual(T.verify_code(self.SECRET, code, 0, now=1111111109 + 30), step, 'one step of clock drift is allowed')
        self.assertIsNone(T.verify_code(self.SECRET, code, 0, now=1111111109 + 95), 'old codes expire')
        self.assertIsNone(T.verify_code(self.SECRET, code, step, now=1111111109), 'a code cannot be used twice')
        self.assertEqual(T.verify_code(self.SECRET, '287 082', 0, now=59), 1, 'spaces are allowed')
        for junk in ('', None, '12345', '1234567', 'abcdef', '287082x', '٢٨٧٠٨٢'):
            self.assertIsNone(T.verify_code(self.SECRET, junk, 0, now=59))

    def test_uri_and_secret_shape(self):
        s = T.new_secret()
        self.assertRegex(s, r'^[A-Z2-7]{32}$')
        self.assertIn('secret=' + s, T.otpauth_uri(s, 'owner@example.test'))
        self.assertNotEqual(s, T.new_secret())


class Owner2FA(unittest.TestCase):
    _W = _tw.Web
    setUp, tearDown, client, tok, post, signup, make_chama, owner = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.owner

    def u(self):
        return self.db.one("SELECT * FROM users WHERE email='owner@example.test'")

    def enable(self, c):
        r = self.post(c, '/owner/security/start')
        secret = re.search(r'font:700 22px ui-monospace,monospace;word-break:break-all;margin:0 0 10px">([A-Z2-7]{32})<', r.get_data(as_text=True)).group(1)
        r = self.post(c, '/owner/security/confirm', {'code': T.code_at(secret, int(time.time() // 30))})
        return secret, self.codes_in(r)

    def codes_in(self, r):
        block = re.search(r'<pre[^>]*>(.*?)</pre>', r.get_data(as_text=True), re.S)
        return re.findall(r'[a-z2-9]{5}-[a-z2-9]{5}', block.group(1)) if block else []

    def login(self, password='OwnerPass123'):
        c = self.client()
        r = self.post(c, '/login', {'login': 'owner@example.test', 'password': password})
        return c, r

    def next_code(self, secret):
        return T.code_at(secret, self.u()['totp_last_step'] + 1)

    def test_setup_requires_a_working_code_and_gives_recovery_codes(self):
        c = self.owner()
        r = self.post(c, '/owner/security/start')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.u()['totp_enabled'], 0, 'not on until a code is proven')
        self.post(c, '/owner/security/confirm', {'code': '000000'})
        self.assertEqual(self.u()['totp_enabled'], 0)
        secret, codes = self.enable(c)
        self.assertEqual(self.u()['totp_enabled'], 1)
        self.assertEqual(len(codes), 8)
        self.assertNotIn(codes[0].replace('-', ''), self.u()['totp_recovery'], 'recovery codes are stored hashed')

    def test_login_needs_second_step(self):
        c = self.owner(); secret, codes = self.enable(c)
        c2, r = self.login()
        self.assertTrue(r.headers['Location'].endswith('/login/2fa'))
        self.assertEqual(c2.get('/owner').status_code, 302, 'password alone is not enough')
        self.assertIn('/login', c2.get('/owner').headers['Location'])
        self.post(c2, '/login/2fa', {'code': '123456'})
        self.assertEqual(c2.get('/owner').status_code, 302)
        r = self.post(c2, '/login/2fa', {'code': self.next_code(secret)})
        self.assertTrue(r.headers['Location'].endswith('/owner'))
        self.assertEqual(c2.get('/owner').status_code, 200)

    def test_code_cannot_be_replayed(self):
        c = self.owner(); secret, codes = self.enable(c)
        code = self.next_code(secret)
        c2, _ = self.login(); self.post(c2, '/login/2fa', {'code': code})
        self.assertEqual(c2.get('/owner').status_code, 200)
        c3, _ = self.login(); self.post(c3, '/login/2fa', {'code': code})
        self.assertEqual(c3.get('/owner').status_code, 302)

    def test_recovery_code_works_once(self):
        c = self.owner(); secret, codes = self.enable(c)
        c2, _ = self.login(); self.post(c2, '/login/2fa', {'code': codes[0]})
        self.assertEqual(c2.get('/owner').status_code, 200)
        c3, _ = self.login(); self.post(c3, '/login/2fa', {'code': codes[0]})
        self.assertEqual(c3.get('/owner').status_code, 302, 'a recovery code is single use')
        c4, _ = self.login(); self.post(c4, '/login/2fa', {'code': codes[1].upper()})
        self.assertEqual(c4.get('/owner').status_code, 200)

    def test_wrong_codes_are_rate_limited(self):
        c = self.owner(); secret, codes = self.enable(c)
        c2, _ = self.login()
        for _ in range(6):
            self.post(c2, '/login/2fa', {'code': '111111'})
        r = self.post(c2, '/login/2fa', {'code': self.next_code(secret)})
        self.assertEqual(r.status_code, 429, 'even the right code is refused while locked')

    def test_normal_users_and_wrong_password_skip_nothing(self):
        c = self.owner(); self.enable(c)
        c2, r = self.login('WrongPassword1')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('pre2fa', str(r.headers.get('Location')))
        user, _ = self.signup('Plain User')
        self.assertEqual(user.get('/owner/security').status_code, 403)
        self.assertEqual(self.client().get('/login/2fa').status_code, 302)

    def test_disable_needs_password_and_code(self):
        c = self.owner(); secret, codes = self.enable(c)
        self.post(c, '/owner/security/disable', {'password': 'WrongPassword1', 'code': codes[0]})
        self.assertEqual(self.u()['totp_enabled'], 1)
        self.post(c, '/owner/security/disable', {'password': 'OwnerPass123', 'code': '000000'})
        self.assertEqual(self.u()['totp_enabled'], 1)
        self.post(c, '/owner/security/disable', {'password': 'OwnerPass123', 'code': codes[2]})
        self.assertEqual(self.u()['totp_enabled'], 0)
        self.assertIsNone(self.u()['totp_secret'])
        c2, r = self.login()
        self.assertTrue(r.headers['Location'].endswith('/owner'))

    def test_new_recovery_codes_replace_old_ones(self):
        c = self.owner(); secret, codes = self.enable(c)
        self.post(c, '/owner/security/codes', {'code': '000000'})
        r = self.post(c, '/owner/security/codes', {'code': self.next_code(secret)})
        fresh = self.codes_in(r)
        self.assertEqual(len(fresh), 8)
        self.assertFalse(set(fresh) & set(codes))
        c2, _ = self.login(); self.post(c2, '/login/2fa', {'code': codes[0]})
        self.assertEqual(c2.get('/owner').status_code, 302, 'old recovery codes stop working')

    def test_server_side_reset(self):
        c = self.owner(); self.enable(c)
        self.assertFalse(T.force_disable(self.db, 'nobody@example.test'))
        self.assertEqual(self.u()['totp_enabled'], 1)
        self.assertTrue(T.force_disable(self.db, 'OWNER@example.test'))
        self.assertEqual(self.u()['totp_enabled'], 0)

    def test_require_flag_forces_setup_but_allows_the_setup_page(self):
        app = create_app({'DATABASE_URL': self.url, 'SECRET_KEY': 'k' * 48, 'TESTING': True, 'REQUIRE_OWNER_2FA': True})
        c = app.test_client()
        html = c.get('/login').get_data(as_text=True)
        tok = re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)
        c.post('/login', data={'_csrf': tok, 'login': 'owner@example.test', 'password': 'OwnerPass123'})
        r = c.get('/owner')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/owner/security'))
        self.assertEqual(c.get('/owner/security').status_code, 200)
        self.assertEqual(c.get('/owner/chamas').status_code, 302)


if __name__ == '__main__':
    unittest.main()
