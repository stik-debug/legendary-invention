import os, re, sys, unittest
from datetime import timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import recovery as RC
import services as S
from werkzeug.security import check_password_hash, generate_password_hash


class Reset(unittest.TestCase):
    _W = _tw.Web
    setUp, tearDown, client, tok, post, signup, make_chama, owner, phone_of = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.owner, _W.phone_of

    def setup_chama(self):
        self.admin, self.ea = self.signup('Grace Admin'); self.cid = self.make_chama(self.admin)
        self.m, self.em = self.signup('Mary Member')
        self.post(self.admin, f'/chamas/{self.cid}/members', {'phone': self.phone_of(self.em), 'role': 'MEMBER'})
        self.uid = self.db.val('SELECT id FROM users WHERE email=?', (self.em,))
        self.admin_id = self.db.val('SELECT id FROM users WHERE email=?', (self.ea,))
        return self

    def issue(self, c=None, uid=None):
        r = (c or self.admin).post(f'/chamas/{self.cid}/members/{uid or self.uid}/reset-code', data={'_csrf': self.tok(c or self.admin)}, follow_redirects=True)
        m = re.search(r'Reset code: (\d{8})', r.get_data(as_text=True))
        return m.group(1) if m else None

    def forgot(self, phone, code, pw='BrandNewPass1', confirm=None, c=None):
        c = c or self.client()
        return c, self.post(c, '/forgot', {'phone': phone, 'code': code, 'password': pw, 'confirm': confirm or pw}, follow=True)

    def can_login(self, email, pw):
        c = self.client()
        r = self.post(c, '/login', {'login': email, 'password': pw})
        return r.status_code == 302

    # ---------- the happy path ----------
    def test_chairperson_issues_code_member_sets_new_password(self):
        self.setup_chama()
        code = self.issue()
        self.assertRegex(code, r'^\d{8}$')
        h = self.db.val('SELECT reset_hash FROM users WHERE id=?', (self.uid,))
        self.assertNotIn(code, h, 'stored hashed')
        c, r = self.forgot(self.phone_of(self.em), code)
        self.assertIn('Your password was changed', r.get_data(as_text=True))
        self.assertTrue(self.can_login(self.em, 'BrandNewPass1'))
        self.assertFalse(self.can_login(self.em, 'Password123'))
        self.assertIsNone(self.db.val('SELECT reset_hash FROM users WHERE id=?', (self.uid,)))
        acts = [r['action'] for r in self.db.all('SELECT action FROM audit_logs ORDER BY id')]
        self.assertIn('PASSWORD_RESET_CODE_ISSUED', acts); self.assertIn('PASSWORD_RESET_DONE', acts)
        self.assertNotIn(code, str(self.db.all('SELECT metadata FROM audit_logs')), 'the code never reaches the audit log')

    def test_code_works_once_only(self):
        self.setup_chama(); code = self.issue()
        self.forgot(self.phone_of(self.em), code)
        c, r = self.forgot(self.phone_of(self.em), code, pw='SecondTry1234')
        self.assertIn('not right', r.get_data(as_text=True))
        self.assertFalse(self.can_login(self.em, 'SecondTry1234'))

    def test_new_code_replaces_old_one(self):
        self.setup_chama(); old = self.issue(); new = self.issue()
        c, r = self.forgot(self.phone_of(self.em), old)
        self.assertIn('not right', r.get_data(as_text=True))
        c, r = self.forgot(self.phone_of(self.em), new)
        self.assertIn('Your password was changed', r.get_data(as_text=True))

    # ---------- limits ----------
    def test_expired_code_is_refused(self):
        self.setup_chama(); code = self.issue()
        self.db.execute('UPDATE users SET reset_expires=? WHERE id=?', (S.iso(S.now_utc() - timedelta(minutes=1)), self.uid)); self.db.commit()
        c, r = self.forgot(self.phone_of(self.em), code)
        self.assertIn('not right', r.get_data(as_text=True))
        self.assertTrue(self.can_login(self.em, 'Password123'))

    def test_five_wrong_tries_lock_the_code(self):
        self.setup_chama(); code = self.issue()
        for i in range(5):
            with self.assertRaises(S.BusinessError):
                RC.reset_password(self.db, self.phone_of(self.em), '0000000' + str(i), 'BrandNewPass1', generate_password_hash, check_password_hash)
        with self.assertRaises(S.BusinessError):
            RC.reset_password(self.db, self.phone_of(self.em), code, 'BrandNewPass1', generate_password_hash, check_password_hash)
        self.assertTrue(self.can_login(self.em, 'Password123'))

    def test_web_guessing_is_rate_limited(self):
        self.setup_chama(); code = self.issue()
        c = self.client()
        for i in range(7):
            self.forgot(self.phone_of(self.em), '1111111' + str(i), c=c)
        c, r = self.forgot(self.phone_of(self.em), code, c=c)
        self.assertEqual(r.status_code, 429, 'even the right code is refused while locked')
        self.assertTrue(self.can_login(self.em, 'Password123'), 'locked out even with the right code')

    def test_same_message_for_unknown_phone_and_wrong_code(self):
        self.setup_chama(); code = self.issue()
        _, a = self.forgot('0799999999', '12345678')
        _, b = self.forgot(self.phone_of(self.em), '12345678')
        pick = lambda r: re.search(r'class="flash danger"[^>]*>([^<]+)<', r.get_data(as_text=True)).group(1)
        self.assertEqual(pick(a), pick(b))

    def test_password_rules(self):
        self.setup_chama(); code = self.issue()
        _, r = self.forgot(self.phone_of(self.em), code, pw='short')
        self.assertTrue(self.can_login(self.em, 'Password123'))
        _, r = self.forgot(self.phone_of(self.em), code, pw='BrandNewPass1', confirm='Different1234')
        self.assertIn('do not match', r.get_data(as_text=True))
        self.assertTrue(self.can_login(self.em, 'Password123'))
        _, r = self.forgot(self.phone_of(self.em), code)
        self.assertTrue(self.can_login(self.em, 'BrandNewPass1'), 'the code survived the two bad attempts')

    # ---------- who may issue ----------
    def test_member_cannot_issue_codes(self):
        self.setup_chama()
        r = self.m.post(f'/chamas/{self.cid}/members/{self.admin_id}/reset-code', data={'_csrf': self.tok(self.m)})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.db.val('SELECT reset_hash FROM users WHERE id=?', (self.admin_id,)))

    def test_treasurer_cannot_issue_codes(self):
        self.setup_chama()
        t, et = self.signup('Tom Treasurer')
        self.post(self.admin, f'/chamas/{self.cid}/members', {'phone': self.phone_of(et), 'role': 'TREASURER'})
        r = t.post(f'/chamas/{self.cid}/members/{self.uid}/reset-code', data={'_csrf': self.tok(t)})
        self.assertEqual(r.status_code, 403)

    def test_chairperson_cannot_reset_someone_in_two_chamas(self):
        self.setup_chama()
        other, eo = self.signup('Other Chair'); oc = self.make_chama(other, name='Other Group')
        self.post(other, f'/chamas/{oc}/members', {'phone': self.phone_of(self.em), 'role': 'MEMBER'})
        self.assertIsNone(self.issue())
        self.assertIsNone(self.db.val('SELECT reset_hash FROM users WHERE id=?', (self.uid,)))
        self.assertTrue(self.can_login(self.em, 'Password123'), 'account untouched')

    def test_chairperson_cannot_reset_another_administrator_or_themselves(self):
        self.setup_chama()
        a2, e2 = self.signup('Second Admin')
        self.post(self.admin, f'/chamas/{self.cid}/members', {'phone': self.phone_of(e2), 'role': 'CHAMA_ADMIN'})
        a2id = self.db.val('SELECT id FROM users WHERE email=?', (e2,))
        self.assertIsNone(self.issue(uid=a2id))
        self.assertIsNone(self.issue(uid=self.admin_id))

    def test_chairperson_cannot_reset_a_stranger_or_a_removed_member(self):
        self.setup_chama()
        s, es = self.signup('Stranger Person'); sid = self.db.val('SELECT id FROM users WHERE email=?', (es,))
        self.assertIsNone(self.issue(uid=sid))
        self.post(self.admin, f'/chamas/{self.cid}/members/{self.uid}/remove')
        self.assertIsNone(self.issue())

    def test_other_chamas_chairperson_has_no_power_here(self):
        self.setup_chama()
        evil, ee = self.signup('Evil Chair'); oc = self.make_chama(evil, name='Evil Group')
        r = evil.post(f'/chamas/{self.cid}/members/{self.uid}/reset-code', data={'_csrf': self.tok(evil)})
        self.assertEqual(r.status_code, 403)
        r = evil.post(f'/chamas/{oc}/members/{self.uid}/reset-code', data={'_csrf': self.tok(evil)}, follow_redirects=True)
        self.assertNotRegex(r.get_data(as_text=True), r'Reset code: \d{8}')

    def test_unregistered_member_gets_join_code_advice(self):
        self.setup_chama()
        self.post(self.admin, f'/chamas/{self.cid}/members', {'name': 'New Person', 'phone': '0722000111', 'role': 'MEMBER'})
        nid = self.db.val("SELECT id FROM users WHERE phone='254722000111'")
        self.assertIsNone(self.issue(uid=nid))
        self.assertIsNone(self.db.val('SELECT reset_hash FROM users WHERE id=?', (nid,)))

    # ---------- owner ----------
    def test_owner_can_reset_a_person_in_several_chamas_but_never_an_owner(self):
        self.setup_chama()
        other, eo = self.signup('Other Chair'); oc = self.make_chama(other, name='Other Group')
        self.post(other, f'/chamas/{oc}/members', {'phone': self.phone_of(self.em), 'role': 'MEMBER'})
        o = self.owner()
        r = o.get('/owner/users?q=Mary')
        self.assertEqual(r.status_code, 200); self.assertIn('Mary Member', r.get_data(as_text=True))
        r = o.post(f'/owner/users/{self.uid}/reset-code', data={'_csrf': self.tok(o, '/owner/users'), 'q': 'Mary'}, follow_redirects=True)
        code = re.search(r'Reset code: (\d{8})', r.get_data(as_text=True)).group(1)
        self.forgot(self.phone_of(self.em), code)
        self.assertTrue(self.can_login(self.em, 'BrandNewPass1'))
        oid = self.db.val('SELECT id FROM users WHERE is_super_admin=1')
        r = o.post(f'/owner/users/{oid}/reset-code', data={'_csrf': self.tok(o, '/owner/users')}, follow_redirects=True)
        self.assertNotRegex(r.get_data(as_text=True), r'Reset code: \d{8}')
        self.assertIsNone(self.db.val('SELECT reset_hash FROM users WHERE id=?', (oid,)))

    def test_owner_pages_are_owner_only(self):
        self.setup_chama()
        self.assertEqual(self.admin.get('/owner/users').status_code, 403)
        r = self.admin.post(f'/owner/users/{self.uid}/reset-code', data={'_csrf': self.tok(self.admin)})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client().get('/owner/users').status_code, 302)

    # ---------- sessions ----------
    def test_reset_logs_the_account_out_everywhere_else(self):
        self.setup_chama()
        self.assertEqual(self.m.get('/dashboard').status_code, 200)
        self.forgot(self.phone_of(self.em), self.issue())
        r = self.m.get('/dashboard')
        self.assertEqual(r.status_code, 302, 'the old (possibly stolen) session is dead')
        self.assertIn('/login', r.headers['Location'])
        c = self.client(); self.post(c, '/login', {'login': self.em, 'password': 'BrandNewPass1'})
        self.assertEqual(c.get('/dashboard').status_code, 200)

    def test_forgot_page_and_link(self):
        c = self.client()
        self.assertEqual(c.get('/forgot').status_code, 200)
        self.assertIn('/forgot', c.get('/login').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
