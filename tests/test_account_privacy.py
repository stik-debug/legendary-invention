"""Account deletion (anonymise), legal pages, and the restored chat badge."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import test_web_finance as _tf  # keep this file's name sorting before test_pay.py, which removes _tf.FinWeb after loading
import accounts as AC
import services as S
import finance as F


class Base(unittest.TestCase):
    _W, _T = _tw.Web, _tf.FinWeb
    _web_setup = _W.setUp
    tearDown, client, tok, post, signup, make_chama, sub, phone_of, owner = _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.sub, _W.phone_of, _W.owner
    team, base, add_contrib, bal = _T.team, _T.base, _T.add_contrib, _T.bal


class Deletion(Base):
    def request_deletion(self, who):
        self.post(self.c[who], '/privacy/request', {'kind': 'DELETION', 'reason': 'please delete'})
        return self.db.val("SELECT id FROM privacy_requests WHERE user_id=? ORDER BY id DESC LIMIT 1", (self.uid[who],))

    def setUp(self):
        self._web_setup()
        self.team()
        self.c = {'m1': self.m1, 'm2': self.m2, 'treas': self.treas, 'admin': self.admin}
        self.owner_c = self.owner()

    def anonymise(self, rid):
        return self.post(self.owner_c, f'/owner/privacy/{rid}/anonymise', follow=True)

    def test_member_with_nothing_owing_is_anonymised_and_books_stay_balanced(self):
        self.add_contrib('m1', 1500)
        before = self.bal()
        rid = self.request_deletion('m1')
        r = self.anonymise(rid)
        self.assertIn('anonymised', r.get_data(as_text=True))
        u = self.db.one('SELECT * FROM users WHERE id=?', (self.uid['m1'],))
        self.assertEqual((u['name'], u['is_active'], u['password_hash']), ('Deleted member', 0, ''))
        self.assertTrue(u['email'].endswith('@deleted.invalid'))
        self.assertTrue(u['phone'].startswith('deleted-'))
        self.assertEqual(self.bal(), before, 'the group\'s cash balance is untouched')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions WHERE user_id=?', (self.uid['m1'],)), 1, 'the entry is kept')
        self.assertEqual(self.db.val("SELECT status FROM chama_members WHERE user_id=?", (self.uid['m1'],)), 'REMOVED')
        self.assertEqual(self.db.val('SELECT status FROM privacy_requests WHERE id=?', (rid,)), 'RESOLVED')

    def test_they_are_signed_out_and_cannot_log_in_again(self):
        rid = self.request_deletion('m1'); self.anonymise(rid)
        self.assertEqual(self.m1.get(self.base()).status_code, 302)  # old session no longer works
        c = self.client()
        self.post(c, '/login', {'login': self.phone_of(self.db.val('SELECT email FROM users WHERE id=?', (self.uid['m1'],))), 'password': 'Password123'})
        self.assertEqual(c.get('/dashboard').status_code, 302)

    def test_open_loan_blocks_deletion(self):
        self.add_contrib('m1', 5000); self.add_contrib('m2', 5000)
        F.update_chama_settings(self.db, self.cid, 100000, '10', 3, 1)
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '1000', 'purpose': 'stock'})
        rid = self.request_deletion('m1')
        r = self.anonymise(rid)
        self.assertIn('open loan', r.get_data(as_text=True))
        self.assertEqual(self.db.val('SELECT is_active FROM users WHERE id=?', (self.uid['m1'],)), 1)
        self.assertNotEqual(self.db.val('SELECT status FROM privacy_requests WHERE id=?', (rid,)), 'RESOLVED')

    def test_unpaid_fine_blocks_deletion(self):
        self.db.insert('fines', chama_id=self.cid, user_id=self.uid['m1'], amount_cents=10000, paid_cents=0, reason='Late', status='UNPAID', created_by=self.uid['treas'], created_at='2026-01-01 00:00:00')
        self.assertTrue(any('unpaid fine' in b for b in AC.deletion_blockers(self.db, self.uid['m1'])))

    def test_chairperson_must_hand_over_first(self):
        admin_id = self.db.val("SELECT user_id FROM chama_members WHERE chama_id=? AND role='CHAMA_ADMIN'", (self.cid,))
        self.assertTrue(any('chairperson' in b for b in AC.deletion_blockers(self.db, admin_id)))

    def test_owner_account_and_repeat_deletion_refused(self):
        oid = self.db.val('SELECT id FROM users WHERE is_super_admin=1')
        self.assertTrue(AC.deletion_blockers(self.db, oid))
        rid = self.request_deletion('m2'); self.anonymise(rid)
        self.assertTrue(any('already' in b for b in AC.deletion_blockers(self.db, self.uid['m2'])))

    def test_only_the_owner_can_anonymise(self):
        rid = self.request_deletion('m1')
        r = self.post(self.m2, f'/owner/privacy/{rid}/anonymise')
        self.assertIn(r.status_code, (302, 403, 404))
        self.assertEqual(self.db.val('SELECT is_active FROM users WHERE id=?', (self.uid['m1'],)), 1)

    def test_only_deletion_requests_can_be_anonymised(self):
        self.post(self.m1, '/privacy/request', {'kind': 'ACCESS', 'reason': ''})
        rid = self.db.val("SELECT id FROM privacy_requests WHERE kind='ACCESS'")
        self.assertEqual(self.post(self.owner_c, f'/owner/privacy/{rid}/anonymise').status_code, 404)
        self.assertEqual(self.db.val('SELECT is_active FROM users WHERE id=?', (self.uid['m1'],)), 1)

    def test_audit_trail_records_the_deletion(self):
        rid = self.request_deletion('m1'); self.anonymise(rid)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM audit_logs WHERE action='ACCOUNT_ANONYMISED'"), 1)


class LegalPagesAndBadge(Base):
    def setUp(self):
        self._web_setup()

    def test_public_pages_and_links(self):
        c = self.client()
        self.assertIn('Privacy Policy', c.get('/privacy-policy').get_data(as_text=True))
        self.assertIn('Terms of Use', c.get('/terms').get_data(as_text=True))
        self.assertIn('/privacy-policy', c.get('/').get_data(as_text=True))
        self.assertIn('/terms', c.get('/register').get_data(as_text=True))

    def test_chat_badge_shows_unread_count(self):
        self.team()
        self.post(self.m1, self.base() + '/chat/send', {'body': 'one'})
        self.assertIn('class="dot">1<', self.m2.get(self.base()).get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
