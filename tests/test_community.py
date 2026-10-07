import os, re, sys, unittest
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import test_web_finance as _tf
import services as S
import finance as F
import community as C
import reports as R
import notify as N

TODAY = date.today().isoformat()
NEXT = (date.today() + timedelta(days=7)).isoformat() + 'T15:00'


class CommWeb(unittest.TestCase):
    _W, _T = _tw.Web, _tf.FinWeb
    setUp, tearDown, client, tok, post, signup, make_chama, sub, phone_of, owner = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.sub, _W.phone_of, _W.owner
    team, base, add_contrib, bal = _T.team, _T.base, _T.add_contrib, _T.bal

    def notes(self, who):
        return [r['text'] for r in self.db.all('SELECT text FROM notifications WHERE user_id=? ORDER BY id', (self.uid[who] if who != 'admin' else self.db.val('SELECT created_by FROM chamas WHERE id=?', (self.cid,)),))]

    def meeting(self, fine='200', c=None):
        r = self.post(c or self.sec, self.base() + '/meetings/add', {'title': 'Monthly meeting', 'when': NEXT, 'venue': 'Chief camp', 'agenda': 'Loans', 'fine': fine})
        self.assertEqual(r.status_code, 302)
        return int(r.headers['Location'].rstrip('/').split('/')[-1])

    # ---------- pages and roles ----------
    def test_every_new_page_renders_and_reports_are_for_officials(self):
        self.team()
        mid = self.meeting()
        for c in (self.admin, self.treas, self.sec, self.m1):
            for p in ('/meetings', f'/meetings/{mid}', '/notices'):
                self.assertEqual(c.get(self.base() + p).status_code, 200, p)
            self.assertEqual(c.get('/notifications').status_code, 200)
        for c in (self.admin, self.treas, self.sec):
            self.assertEqual(c.get(self.base() + '/reports').status_code, 200)
        self.assertEqual(self.m1.get(self.base() + '/reports').status_code, 403)
        self.assertEqual(self.m1.get(self.base() + '/export/members.csv').status_code, 403)

    # ---------- announcements ----------
    def test_secretary_posts_notice_members_get_alerts_and_cannot_post(self):
        self.team()
        r = self.post(self.sec, self.base() + '/notices/add', {'title': 'Pay by the 5th', 'body': 'Please pay on time.', 'pinned': '1'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('Pay by the 5th', self.m1.get(self.base() + '/notices').get_data(as_text=True))
        self.assertTrue(any('Pay by the 5th' in t for t in self.notes('m1')))
        self.assertFalse(any('Pay by the 5th' in t for t in self.notes('sec')), 'the poster is not alerted about their own notice')
        self.post(self.m1, self.base() + '/notices/add', {'title': 'Hacked', 'body': 'nope nope'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM announcements'), 1)
        self.assertEqual(self.post(self.m1, self.base() + '/notices/add', {'title': 'Hacked', 'body': 'nope nope'}).status_code, 403)

    def test_notice_text_is_escaped_pin_order_and_soft_delete(self):
        self.team()
        self.post(self.sec, self.base() + '/notices/add', {'title': '<script>alert(1)</script>', 'body': 'x <b>bold</b> y'})
        self.post(self.sec, self.base() + '/notices/add', {'title': 'Second notice', 'body': 'Second body', 'pinned': '1'})
        html = self.m1.get(self.base() + '/notices').get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertLess(html.index('Second notice'), html.index('&lt;script&gt;'), 'pinned first')
        nid = self.db.val("SELECT id FROM announcements WHERE title='Second notice'")
        self.assertEqual(self.post(self.m1, self.base() + f'/notices/{nid}/delete').status_code, 403)
        self.post(self.sec, self.base() + f'/notices/{nid}/delete')
        self.assertNotIn('Second notice', self.m1.get(self.base() + '/notices').get_data(as_text=True))
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM announcements'), 2, 'removed notices are kept for the record')

    # ---------- meetings and attendance ----------
    def test_meeting_attendance_creates_one_absence_fine(self):
        self.team()
        mid = self.meeting('200')
        self.assertTrue(any('Monthly meeting' in t for t in self.notes('m1')))
        form = {f"st_{self.uid['treas']}": 'PRESENT', f"st_{self.uid['sec']}": 'PRESENT', f"st_{self.uid['m1']}": 'ABSENT', f"st_{self.uid['m2']}": 'APOLOGY'}
        form[f"st_{self.db.val('SELECT created_by FROM chamas WHERE id=?', (self.cid,))}"] = 'PRESENT'
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', form)
        fines = self.db.all('SELECT * FROM fines WHERE chama_id=?', (self.cid,))
        self.assertEqual([(f['user_id'], f['amount_cents'], f['status']) for f in fines], [(self.uid['m1'], 20000, 'UNPAID')])
        self.assertEqual(self.db.val('SELECT status FROM meetings WHERE id=?', (mid,)), 'HELD')
        self.assertTrue(any('fined KES 200' in t for t in self.notes('m1')))
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', form)  # save again
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM fines'), 1, 'saving twice must not fine twice')
        form[f"st_{self.uid['m1']}"] = 'PRESENT'
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', form)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM fines'), 1, 'the fine stays until an administrator waives it')
        self.assertIn('Apology', self.m2.get(self.base() + '/meetings').get_data(as_text=True))

    def test_members_cannot_mark_attendance_or_edit_minutes_or_cancel(self):
        self.team()
        mid = self.meeting()
        for path, data in (('attendance', {f"st_{self.uid['m1']}": 'PRESENT'}), ('minutes', {'minutes': 'x'}), ('cancel', {})):
            self.assertEqual(self.post(self.m1, self.base() + f'/meetings/{mid}/{path}', data).status_code, 403, path)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM attendance'), 0)

    def test_attendance_rejects_outsiders_and_bad_values(self):
        self.team()
        mid = self.meeting()
        other, _ = self.signup('Outsider Person')
        outsider = self.db.val("SELECT id FROM users WHERE name='Outsider Person'")
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', {f'st_{outsider}': 'PRESENT'})
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', {f"st_{self.uid['m1']}": 'MAYBE'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM attendance'), 0)
        self.assertEqual(self.sec.post(self.base() + f'/meetings/{mid}/attendance', data={'_csrf': self.tok(self.sec), 'st_abc': 'PRESENT'}).status_code, 400)

    def test_cancelled_meeting_cannot_be_marked_and_minutes_show_to_members(self):
        self.team()
        mid = self.meeting()
        self.post(self.sec, self.base() + f'/meetings/{mid}/minutes', {'minutes': 'We agreed to meet monthly.'})
        self.assertIn('We agreed to meet monthly.', self.m1.get(self.base() + f'/meetings/{mid}').get_data(as_text=True))
        self.post(self.sec, self.base() + f'/meetings/{mid}/cancel')
        self.assertEqual(self.db.val('SELECT status FROM meetings WHERE id=?', (mid,)), 'CANCELLED')
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', {f"st_{self.uid['m1']}": 'PRESENT'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM attendance'), 0)

    def test_bad_meeting_input_is_refused(self):
        self.team()
        self.post(self.sec, self.base() + '/meetings/add', {'title': 'ab', 'when': NEXT})
        self.post(self.sec, self.base() + '/meetings/add', {'title': 'Good title', 'when': 'not a date'})
        self.post(self.sec, self.base() + '/meetings/add', {'title': 'Good title', 'when': NEXT, 'fine': '-5'})
        self.post(self.sec, self.base() + '/meetings/add', {'title': 'Good title', 'when': '1990-01-01T10:00'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM meetings'), 0)

    # ---------- tenant isolation and suspension ----------
    def test_other_chama_cannot_see_or_touch_meetings_notices_reports(self):
        self.team()
        mid = self.meeting()
        self.post(self.sec, self.base() + '/notices/add', {'title': 'Private', 'body': 'Only for us'})
        evil, _ = self.signup('Other Admin'); other = self.make_chama(evil, name='Other Group')
        for p in ('/meetings', f'/meetings/{mid}', '/notices', '/reports', '/export/members.csv'):
            self.assertEqual(evil.get(self.base() + p).status_code, 403, p)
        self.assertEqual(self.post(evil, self.base() + f'/meetings/{mid}/minutes', {'minutes': 'x'}).status_code, 403)
        self.assertEqual(evil.get(f'/chamas/{other}/meetings/{mid}').status_code, 404, 'a meeting id from another chama must not load')
        self.assertNotIn('Only for us', evil.get('/notifications').get_data(as_text=True))

    def test_suspended_chama_is_locked_out_of_new_pages_too(self):
        self.team()
        self.db.execute("UPDATE subscriptions SET status='SUSPENDED' WHERE chama_id=?", (self.cid,)); self.db.commit()
        for p in ('/meetings', '/notices', '/reports'):
            r = self.admin.get(self.base() + p)
            self.assertEqual(r.status_code, 302, p)
            self.assertIn('/subscription', r.headers['Location'])

    # ---------- reports and exports ----------
    def test_report_numbers_and_csv_exports(self):
        self.team()
        self.add_contrib('m1', 1000); self.add_contrib('m2', 600)
        r = self.treas.get(self.base() + '/reports')
        html = r.get_data(as_text=True)
        self.assertIn('KES 1,600', html)
        self.assertIn('Part paid', html)
        rep = R.chama_report(self.db, self.cid, TODAY[:7])
        self.assertEqual((rep['collected'], rep['paid_count'], rep['cash']), (160000, 1, 160000))
        self.assertEqual(rep['members'], 5)
        csv = self.sec.get(self.base() + '/export/contributions.csv')
        self.assertEqual(csv.status_code, 200)
        self.assertEqual(csv.mimetype, 'text/csv')
        self.assertEqual(len(csv.get_data(as_text=True).strip().splitlines()), 3)
        for kind in R.EXPORTS:
            self.assertEqual(self.admin.get(self.base() + f'/export/{kind}.csv').status_code, 200, kind)
        self.assertEqual(self.admin.get(self.base() + '/export/passwords.csv').status_code, 404)

    def test_csv_cells_cannot_run_formulas(self):
        self.team()
        evil, e = self.signup('=HYPERLINK("http://x")')
        self.post(self.admin, self.base() + '/members', {'phone': self.phone_of(e), 'role': 'MEMBER'})
        body = self.admin.get(self.base() + '/export/members.csv').get_data(as_text=True)
        self.assertIn("'=HYPERLINK", body)
        self.assertNotRegex(body, r'(^|,)=HYPERLINK')

    def test_attendance_percentage_in_report(self):
        self.team()
        mid = self.meeting('')
        sheet = {f'st_{u}': 'PRESENT' for u in (self.uid['treas'], self.uid['sec'], self.uid['m1'])}
        sheet[f"st_{self.uid['m2']}"] = 'ABSENT'
        self.post(self.sec, self.base() + f'/meetings/{mid}/attendance', sheet)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM fines'), 0, 'no fine configured')
        rep = R.chama_report(self.db, self.cid, TODAY[:7])
        self.assertEqual((rep['meetings_held'], rep['attendance_pct']), (1, 75))

    # ---------- notifications ----------
    def test_money_events_alert_the_right_people_only(self):
        self.team()
        self.add_contrib('m1', 1000)
        self.assertTrue(any('contribution of KES 1,000' in t for t in self.notes('m1')))
        self.assertEqual(self.notes('treas'), [], 'the treasurer who recorded it is not alerted')
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '2000', 'purpose': 'Stock'})
        self.assertTrue(any('asked for a loan of KES 2,000' in t for t in self.notes('treas')))
        self.assertFalse(any('asked for a loan' in t for t in self.notes('m1')))
        self.assertFalse(any('asked for a loan' in t for t in self.notes('m2')), 'ordinary members are not told about loan requests')
        lid = self.db.val('SELECT id FROM loans')
        self.post(self.treas, self.base() + f'/loans/{lid}/approve')
        self.assertTrue(any('approved' in t for t in self.notes('m1')))

    def test_bell_count_open_and_read_all(self):
        self.team()
        self.add_contrib('m1', 1000)
        page = self.m1.get('/dashboard').get_data(as_text=True)
        self.assertRegex(page, r'Alerts \(1\)')
        nid = self.db.val('SELECT id FROM notifications WHERE user_id=?', (self.uid['m1'],))
        r = self.post(self.m1, f'/notifications/{nid}/open')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].startswith(f'/chamas/{self.cid}/'))
        self.assertEqual(N.unread_count(self.db, self.uid['m1']), 0)
        self.assertEqual(self.post(self.m2, f'/notifications/{nid}/open').status_code, 404, "nobody else can open someone's alert")
        self.assertEqual(N.unread_count(self.db, self.uid['m1']), 0)
        self.add_contrib('m1', 500, ref='XYZ1'); self.add_contrib('m1', 500, ref='XYZ2')
        self.assertEqual(N.unread_count(self.db, self.uid['m1']), 2)
        self.post(self.m1, '/notifications/read')
        self.assertEqual(N.unread_count(self.db, self.uid['m1']), 0)

    def test_notification_links_cannot_redirect_off_site(self):
        self.team()
        N.notify(self.db, self.uid['m1'], self.cid, 'Evil', 'https://evil.example/x'); self.db.commit()
        nid = self.db.val("SELECT id FROM notifications WHERE text='Evil'")
        r = self.post(self.m1, f'/notifications/{nid}/open')
        self.assertTrue(r.headers['Location'].endswith('/notifications'))


class CommRules(unittest.TestCase):
    def test_parse_when(self):
        self.assertEqual(C.parse_when('2026-10-04T15:00'), '2026-10-04 15:00')
        for bad in ('', 'tomorrow', '2026-13-40T10:00', '1999-01-01T10:00'):
            with self.assertRaises(S.BusinessError):
                C.parse_when(bad)


if __name__ == '__main__':
    unittest.main()
