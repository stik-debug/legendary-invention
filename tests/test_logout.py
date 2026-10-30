"""Log out must stay visible in the header on every signed-in page and must really sign the user out."""
import os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web

HEADER_FORM = re.compile(r'<form method="POST" action="/logout" class="header-logout">.*?name="_csrf".*?Log out', re.S)


class LogoutVisible(unittest.TestCase):
    setUp, tearDown = test_web.Web.setUp, test_web.Web.tearDown
    client, tok, post, signup, make_chama = (test_web.Web.client, test_web.Web.tok, test_web.Web.post,
                                             test_web.Web.signup, test_web.Web.make_chama)

    def test_header_logout_on_every_signed_in_page(self):
        c, _ = self.signup(); cid = self.make_chama(c)
        for path in ('/dashboard', f'/chamas/{cid}', f'/chamas/{cid}/subscription', '/notifications', '/marketplace'):
            html = c.get(path).get_data(as_text=True)
            self.assertRegex(html, HEADER_FORM, path)

    def test_logout_signs_out_and_needs_csrf(self):
        c, _ = self.signup()
        self.assertEqual(c.get('/dashboard').status_code, 200)
        c.post('/logout', data={})                                          # no CSRF token: must be refused
        self.assertEqual(c.get('/dashboard').status_code, 200)             # still signed in
        r = self.post(c, '/logout')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get('/dashboard').status_code, 302)             # signed out

    def test_no_logout_when_signed_out(self):
        html = self.client().get('/').get_data(as_text=True)
        self.assertNotIn('class="header-logout"', html)
        self.assertIn('Log in', html)


if __name__ == '__main__':
    unittest.main()
