"""Landing-page feature links, owner-only Command Center, and the merry-go-round Add participant button."""
import os, sys, re, unittest
from datetime import date
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from test_account_privacy import Base
import mgr as M


class LandingAndRounds(Base):
    def setUp(self):
        self._web_setup()
        self.team()
        self.ids = [r['user_id'] for r in self.db.all("SELECT user_id FROM chama_members WHERE chama_id=? AND status='ACTIVE' ORDER BY user_id", (self.cid,))]

    def make_round(self, ids, name='Test round'):
        self.post(self.treas, self.base() + '/merry-go-round/new', {'name': name, 'amount': '500', 'frequency': 'MONTHLY', 'start': date.today().isoformat(),
                                                                    'order': 'random', 'member': [str(i) for i in ids]})
        return self.db.val('SELECT id FROM mgr_rounds ORDER BY id DESC LIMIT 1')

    def page(self, client, rid):
        return client.get(f'{self.base()}/merry-go-round/{rid}').get_data(as_text=True)

    # ---- merry-go-round: Add participant ----
    def test_add_participant_works_when_chama_members_are_available(self):
        rid = self.make_round(self.ids[:3])
        h = self.page(self.admin, rid)
        self.assertIn('data-open="add-mgr-member"', h); self.assertIn('id="add-mgr-member"', h)
        self.assertIn('<select name="user_id">', h)
        left = [i for i in self.ids if i not in self.ids[:3]][0]
        r = self.post(self.admin, f'{self.base()}/merry-go-round/{rid}/members', {'user_id': str(left)}, follow=True)
        self.assertIn('added to this merry-go-round', r.get_data(as_text=True))
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_slots WHERE round_id=?', (rid,)), 4)

    def test_button_never_dead_when_everyone_is_already_in(self):
        rid = self.make_round(self.ids)
        h = self.page(self.admin, rid)
        self.assertIn('data-open="add-mgr-member"', h)
        self.assertIn('id="add-mgr-member"', h, 'the button must have a pop-up to open')
        self.assertIn('already in this round', h)

    def test_after_payments_start_the_dialog_explains_why(self):
        rid = self.make_round(self.ids)
        rnd = M.get_round(self.db, self.cid, rid)
        payer = M.turn_status(self.db, rnd)['unpaid'][0]['user_id']
        self.post(self.treas, f'{self.base()}/merry-go-round/{rid}/pay', {'user_id': str(payer), 'method': 'Cash', 'reference': '', 'paid_on': date.today().isoformat()})
        h = self.page(self.admin, rid)
        self.assertIn('data-open="add-mgr-member"', h)
        self.assertIn('Payments have already started', h)

    def test_every_button_on_the_round_page_has_its_popup(self):
        rid = self.make_round(self.ids)
        for c in (self.admin, self.treas, self.m1):
            h = self.page(c, rid)
            self.assertFalse(set(re.findall(r'data-open="([^"]+)"', h)) - set(re.findall(r'<dialog[^>]*id="([^"]+)"', h)))

    # ---- Command Center ----
    def test_command_center_opens_for_the_creator_only(self):
        r = self.admin.get(self.base() + '/command')
        self.assertEqual(r.status_code, 200); self.assertIn('Command Center', r.get_data(as_text=True))
        self.assertEqual(self.treas.get(self.base() + '/command').status_code, 403)
        self.assertEqual(self.m1.get(self.base() + '/command').status_code, 403)

    # ---- landing cards ----
    def test_cards_link_to_features_and_command_center_is_hidden_from_members(self):
        anon = self.client().get('/').get_data(as_text=True)
        member = self.m1.get('/').get_data(as_text=True)
        owner = self.admin.get('/').get_data(as_text=True)
        for h in (anon, member, owner):
            for k in ('loans', 'goals', 'votes', 'meetings', 'passport', 'ai'):
                self.assertIn(f'href="/go/{k}"', h)
            self.assertIn('href="/install"', h)
        self.assertNotIn('href="/go/command"', anon)
        self.assertNotIn('href="/go/command"', member)
        self.assertIn('href="/go/command"', owner)

    def test_go_sends_a_signed_out_visitor_to_log_in_then_on_to_the_feature(self):
        r = self.client().get('/go/loans')
        self.assertEqual(r.status_code, 302); self.assertIn('/login', r.headers['Location']); self.assertIn('next=', r.headers['Location'])

    def test_go_opens_the_feature_in_the_members_chama(self):
        for key, tail in (('loans', '/loans'), ('goals', '/goals'), ('votes', '/votes'), ('meetings', '/meetings'), ('ai', '/ai')):
            r = self.m1.get(f'/go/{key}')
            self.assertEqual(r.status_code, 302, key); self.assertTrue(r.headers['Location'].endswith(f'/chamas/{self.cid}{tail}'), (key, r.headers['Location']))
            self.assertEqual(self.m1.get(r.headers['Location']).status_code, 200, key)
        r = self.m1.get('/go/passport')
        self.assertTrue(r.headers['Location'].endswith(f'/chamas/{self.cid}/members/{self.uid["m1"]}/passport'))
        self.assertEqual(self.m1.get(r.headers['Location']).status_code, 200)

    def test_go_command_is_for_the_creator_only(self):
        self.assertTrue(self.admin.get('/go/command').headers['Location'].endswith(f'/chamas/{self.cid}/command'))
        r = self.m1.get('/go/command')
        self.assertTrue(r.headers['Location'].endswith('/dashboard'))
        self.assertIn('only available to the person who created', self.m1.get('/dashboard').get_data(as_text=True))

    def test_go_unknown_feature_is_404_and_install_page_is_public(self):
        self.assertEqual(self.m1.get('/go/nonsense').status_code, 404)
        self.assertEqual(self.client().get('/install').status_code, 200)

    def test_person_in_two_chamas_gets_to_choose(self):
        cid2 = self.make_chama(self.m1, name='Second Chama')
        h = self.m1.get('/go/loans').get_data(as_text=True)
        self.assertIn('Which chama', h)
        self.assertIn(f'/chamas/{cid2}/loans', h); self.assertIn(f'/chamas/{self.cid}/loans', h)

    def test_person_with_no_chama_is_sent_to_the_dashboard(self):
        c, _ = self.signup('Lonely Person')
        r = c.get('/go/loans')
        self.assertTrue(r.headers['Location'].endswith('/dashboard'))


    # ---- a safety net: no page may crash, and no button may open a pop-up that is not there ----
    def test_no_page_crashes_and_no_button_is_dead(self):
        rid = self.make_round(self.ids)
        self.post(self.treas, self.base() + '/meetings/add', {'title': 'Monthly meeting', 'held_at': '2026-12-01T10:00', 'venue': 'Hall', 'agenda': 'x'})
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        owner = self.owner()
        chama_rules = [r.rule.replace('<int:chama_id>', str(self.cid)) for r in self.app.url_map.iter_rules()
                       if 'GET' in r.methods and r.rule.startswith('/chamas/<int:chama_id>') and r.rule.count('<') == 1]
        plain_rules = [r.rule for r in self.app.url_map.iter_rules() if 'GET' in r.methods and '<' not in r.rule and not r.rule.startswith('/static')]
        problems = []
        for who, c in (('member', self.m1), ('treasurer', self.treas), ('admin', self.admin), ('owner', owner)):
            for url in chama_rules + plain_rules + [f'/chamas/{self.cid}/merry-go-round/{rid}']:
                resp = c.get(url)
                if resp.status_code >= 500:
                    problems.append(f'{who} {url} -> {resp.status_code}')
                elif resp.status_code == 200:
                    h = resp.get_data(as_text=True)
                    miss = set(re.findall(r'data-open="([^"]+)"', h)) - set(re.findall(r'<dialog[^>]*id="([^"]+)"', h))
                    if miss:
                        problems.append(f'{who} {url}: dead button for {sorted(miss)}')
        self.assertEqual(problems, [])

    def test_help_and_owner_analytics_open(self):
        self.assertEqual(self.m1.get('/help').status_code, 200)
        self.assertEqual(self.owner().get('/owner/analytics-v22').status_code, 200)


    # ---- the newer features: saving and submitting, not just opening the page ----
    def test_goals_investments_assets_votes_constitution_help_and_ai_all_work(self):
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        b, db = self.base(), self.db
        self.post(self.admin, b + '/goals', {'name': 'School fees fund', 'target': '50000', 'description': 'x', 'deadline': ''})
        gid = db.val('SELECT id FROM chama_goals WHERE chama_id=?', (self.cid,))
        self.assertTrue(gid)
        self.post(self.admin, f'{b}/goals/{gid}/fund', {'amount': '1000'})
        self.assertEqual(db.val('SELECT current_cents FROM chama_goals WHERE id=?', (gid,)), 100000)
        self.post(self.admin, b + '/investments', {'name': 'Money market fund', 'type': 'Other', 'purchase_price': '10000', 'current_value': '10500', 'income': '0'})
        self.assertEqual(db.val('SELECT COUNT(*) FROM chama_investments WHERE chama_id=?', (self.cid,)), 1)
        self.post(self.admin, b + '/assets', {'name': 'Plot of land', 'category': 'Land', 'purchase_price': '100000', 'current_value': '120000'})
        self.assertEqual(db.val('SELECT COUNT(*) FROM chama_assets WHERE chama_id=?', (self.cid,)), 1)
        self.post(self.admin, b + '/votes', {'title': 'Buy the plot?', 'description': 'Proposal', 'closes_at': ''})
        vid = db.val('SELECT id FROM chama_votes WHERE chama_id=?', (self.cid,))
        self.assertTrue(vid)
        self.post(self.m1, f'{b}/votes/{vid}', {'choice': 'YES'})
        self.assertEqual(db.val('SELECT choice FROM chama_vote_responses WHERE vote_id=? AND user_id=?', (vid, self.uid['m1'])), 'YES')
        self.post(self.m1, f'{b}/votes/{vid}', {'choice': 'NO'})  # changing your vote replaces it
        self.assertEqual(db.val('SELECT COUNT(*) FROM chama_vote_responses WHERE vote_id=?', (vid,)), 1)
        self.post(self.admin, b + '/constitution', {'monthly_contribution': '1500', 'joining_fee': '500', 'loan_interest': '10', 'loan_multiplier': '3',
                                                    'loan_months': '6', 'late_fine': '100', 'attendance_requirement': '75', 'voting_requirement': '50'})
        self.assertEqual(db.val('SELECT monthly_contribution_cents FROM chama_constitutions WHERE chama_id=?', (self.cid,)), 150000)
        self.post(self.m1, '/help', {'subject': 'Need a hand', 'body': 'Please help me', 'chama_id': str(self.cid)})
        tid = db.val('SELECT id FROM support_tickets WHERE user_id=?', (self.uid['m1'],))
        self.assertTrue(tid)
        self.assertIn('Need a hand', self.m1.get('/help').get_data(as_text=True))
        self.post(self.owner(), f'/owner/support/{tid}', {'status': 'RESOLVED', 'admin_note': 'Sorted'})
        self.assertEqual(db.val('SELECT status FROM support_tickets WHERE id=?', (tid,)), 'RESOLVED')
        r = self.post(self.m1, b + '/ai/ask', {'question': 'which members have not paid contributions?'}, follow=True)
        self.assertEqual(r.status_code, 200)
        for url in (b + '/goals', b + '/investments', b + '/assets', b + '/votes', b + '/constitution', b + '/command'):
            self.assertEqual(self.admin.get(url).status_code, 200, url)


if __name__ == '__main__':
    unittest.main()
