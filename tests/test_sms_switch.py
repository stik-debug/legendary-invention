"""SMS_OTP_ENABLED switch: off (default) = no SMS step anywhere; on = the SMS flows are still in place."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
from app import create_app
from werkzeug.security import check_password_hash


class SmsOff(_tw.Web):
    """Inherits the harness only; the inherited tests are not re-run here (see load_tests below)."""

    def login_client(self, ident, pw='Password123'):
        c = self.client()
        r = self.post(c, '/login', {'login': ident, 'password': pw})
        return c, r

    def test_default_is_off(self):
        self.assertFalse(self.app.config['SMS_OTP_ENABLED'])

    def test_signup_logs_in_without_any_code(self):
        c, email = self.signup('No Sms Person')
        self.assertEqual(c.get('/dashboard').status_code, 200)
        u = self.db.one('SELECT * FROM users WHERE email=?', (email,))
        self.assertIsNone(u['phone_verified_at'])
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM auth_otps"), 0)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM audit_logs WHERE action='USER_REGISTERED' AND actor_id=?", (u['id'],)), 1)

    def test_unverified_user_can_log_in_later(self):
        _, email = self.signup()
        c, r = self.login_client(email)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/dashboard'))
        self.assertEqual(c.get('/dashboard').status_code, 200)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM auth_otps"), 0)

    def test_wrong_password_still_refused(self):
        _, email = self.signup()
        c, r = self.login_client(email, 'WrongPassword1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get('/dashboard').status_code, 302)

    def test_change_password_needs_current_password(self):
        c, email = self.signup()
        self.assertEqual(c.get('/security/password').status_code, 200)
        self.post(c, '/security/password', {'current_password': 'Nope12345', 'password': 'NewPassword99', 'confirm': 'NewPassword99'})
        self.assertTrue(check_password_hash(self.db.val('SELECT password_hash FROM users WHERE email=?', (email,)), 'Password123'))
        r = self.post(c, '/security/password', {'current_password': 'Password123', 'password': 'NewPassword99', 'confirm': 'NewPassword99'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(check_password_hash(self.db.val('SELECT password_hash FROM users WHERE email=?', (email,)), 'NewPassword99'))
        self.assertEqual(self.login_client(email, 'NewPassword99')[1].status_code, 302)

    def test_change_password_rejects_short_and_mismatch(self):
        c, email = self.signup()
        self.post(c, '/security/password', {'current_password': 'Password123', 'password': 'short', 'confirm': 'short'})
        self.post(c, '/security/password', {'current_password': 'Password123', 'password': 'NewPassword99', 'confirm': 'Different999'})
        self.assertTrue(check_password_hash(self.db.val('SELECT password_hash FROM users WHERE email=?', (email,)), 'Password123'))

    def test_change_phone_needs_current_password_and_a_free_valid_number(self):
        c, email = self.signup()
        _, other = self.signup('Other Person')
        other_phone = self.phone_of(other)
        old = self.phone_of(email)
        self.post(c, '/security/phone', {'current_password': 'Wrong12345', 'new_phone': '0733111222'})
        self.assertEqual(self.phone_of(email), old)
        self.post(c, '/security/phone', {'current_password': 'Password123', 'new_phone': 'abc'})
        self.assertEqual(self.phone_of(email), old)
        self.post(c, '/security/phone', {'current_password': 'Password123', 'new_phone': other_phone})
        self.assertEqual(self.phone_of(email), old)
        r = self.post(c, '/security/phone', {'current_password': 'Password123', 'new_phone': '0733111222'})
        self.assertEqual(r.status_code, 302)
        self.assertNotEqual(self.phone_of(email), old)

    def test_forgot_page_asks_for_a_reset_code_not_sms(self):
        html = self.client().get('/forgot').get_data(as_text=True)
        self.assertIn('Reset code', html)
        self.assertNotIn('by SMS', html)

    def test_forgot_wrong_code_is_refused_and_rate_limited(self):
        _, email = self.signup()
        c = self.client()
        for _ in range(6):
            self.post(c, '/forgot', {'phone': self.phone_of(email), 'code': '11111111', 'password': 'BrandNew12345', 'confirm': 'BrandNew12345'})
        r = self.post(c, '/forgot', {'phone': self.phone_of(email), 'code': '11111111', 'password': 'BrandNew12345', 'confirm': 'BrandNew12345'})
        self.assertEqual(r.status_code, 429)


class SmsOn(_tw.Web):
    def setUp(self):
        super().setUp()
        self.app = create_app({'DATABASE_URL': self.url, 'SECRET_KEY': 'k' * 48, 'TESTING': True, 'SMS_OTP_ENABLED': True})

    def test_signup_does_not_log_in_directly_when_on(self):
        c = self.client()
        self.post(c, '/register', {'name': 'On Person', 'email': 'on@example.test', 'phone': '0722000999',
                                   'password': 'Password123', 'confirm': 'Password123'})
        self.assertEqual(c.get('/dashboard').status_code, 302)  # still logged out: the code step comes first

    def test_login_of_unverified_user_is_held_back_when_on(self):
        S = _tw.S
        S.create_user(self.db, 'Held Back', 'held@example.test', '0722000888', _tw.generate_password_hash('Password123'))
        c = self.client()
        self.post(c, '/login', {'login': 'held@example.test', 'password': 'Password123'})
        self.assertEqual(c.get('/dashboard').status_code, 302)


def load_tests(loader, tests, pattern):
    """Run only this file's own tests, not the ones inherited from the web harness."""
    suite = unittest.TestSuite()
    for case in (SmsOff, SmsOn):
        names = [n for n in loader.getTestCaseNames(case) if n in case.__dict__ or n.startswith('test_') and n in vars(case)]
        for n in names:
            suite.addTest(case(n))
    return suite


if __name__ == '__main__':
    unittest.main()
