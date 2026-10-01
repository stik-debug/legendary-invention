import json, os, re, sys, unittest
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import services as S
import finance as F

TODAY = date.today().isoformat()


class FinWeb(unittest.TestCase):
    _W = _tw.Web
    setUp, tearDown, client, tok, post, signup, make_chama, sub, phone_of = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.sub, _W.phone_of

    def team(self, plan='starter'):
        """admin + treasurer + secretary + two members, all logged in."""
        self.admin, ea = self.signup('Grace Admin'); self.cid = self.make_chama(self.admin, plan)
        out = {}
        for key, role in (('treas', 'TREASURER'), ('sec', 'SECRETARY'), ('m1', 'MEMBER'), ('m2', 'MEMBER')):
            c, e = self.signup(key.upper() + ' Person')
            self.post(self.admin, f'/chamas/{self.cid}/members', {'phone': self.phone_of(e), 'role': role})
            out[key] = (c, self.db.val('SELECT id FROM users WHERE email=?', (e,)))
        self.treas, self.sec, self.m1, self.m2 = out['treas'][0], out['sec'][0], out['m1'][0], out['m2'][0]
        self.uid = {k: v[1] for k, v in out.items()}
        return self

    def base(self, cid=None): return f'/chamas/{cid or self.cid}'

    def add_contrib(self, who, kes=1000, ref=None, c=None):
        return self.post(c or self.treas, self.base() + '/savings/add', {'user_id': self.uid[who], 'amount': str(kes), 'paid_on': TODAY, 'method': 'Cash', 'reference': ref or ''})

    def bal(self): return F.cash_balance(self.db, self.cid)

    # ---------- every page renders for every role ----------
    def test_all_pages_render_for_all_roles(self):
        self.team()
        for c in (self.admin, self.treas, self.sec, self.m1):
            for p in ('', '/savings', '/loans', '/fines', '/ledger', '/chat', '/statement'):
                self.assertEqual(c.get(self.base() + p).status_code, 200, p)

    # ---------- contributions ----------
    def test_treasurer_records_contribution_ledger_and_statement(self):
        self.team()
        self.assertEqual(self.add_contrib('m1', 1000, 'abc1').status_code, 302)
        self.assertEqual(self.bal(), 100000)
        self.assertIn('KES 1,000', self.m1.get(self.base() + '/ledger').get_data(as_text=True))
        self.assertIn('KES 1,000', self.m1.get(self.base() + '/statement').get_data(as_text=True))
        self.add_contrib('m2', 1000, 'ABC1')  # duplicate code
        self.assertEqual(self.bal(), 100000)

    def test_bad_amounts_record_nothing(self):
        self.team()
        for bad in ('-50', 'abc', '0', '12.345', '999999999'):
            self.add_contrib('m1', bad)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)
        self.assertEqual(self.bal(), 0)

    def test_members_and_secretary_cannot_record_money(self):
        self.team()
        for c in (self.m1, self.sec):
            self.assertEqual(self.add_contrib('m1', 500, c=c).status_code, 403)
            self.assertEqual(self.post(c, self.base() + '/ledger/add', {'kind': 'OTHER_INCOME', 'amount': '5', 'on': TODAY, 'description': 'cheat'}).status_code, 403)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM ledger_transactions'), 0)

    def test_cancel_contribution_corrects_ledger(self):
        self.team(); self.add_contrib('m1', 1000)
        cid = self.db.val('SELECT id FROM contributions')
        self.post(self.treas, self.base() + f'/savings/{cid}/void', {'reason': 'wrong member'})
        self.assertEqual(self.bal(), 0); self.assertEqual(self.db.val('SELECT status FROM contributions WHERE id=?', (cid,)), 'CANCELLED')
        self.assertEqual(self.post(self.m1, self.base() + f'/savings/{cid}/void', {'reason': 'x'}).status_code, 403)

    # ---------- full loan journey ----------
    def test_loan_journey_over_http(self):
        self.team(); self.add_contrib('m1', 5000); self.add_contrib('m2', 5000)
        F.update_chama_settings(self.db, self.cid, 100000, '10', 3, 1)
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '10000', 'purpose': 'stock'})
        lid = self.db.val('SELECT id FROM loans')
        self.assertEqual(self.post(self.m1, self.base() + f'/loans/{lid}/approve').status_code, 403)  # member cannot approve
        self.post(self.treas, self.base() + f'/loans/{lid}/approve')
        before = self.bal(); self.post(self.admin, self.base() + f'/loans/{lid}/disburse')
        self.assertEqual(self.bal(), before - 1000000)
        self.post(self.treas, self.base() + f'/loans/{lid}/repay', {'amount': '8000', 'paid_on': TODAY, 'method': 'Cash'})
        row = self.db.one('SELECT * FROM loans WHERE id=?', (lid,))
        self.assertEqual((row['total_due_cents'], row['interest_cents'], row['paid_cents'], row['status']), (1100000, 100000, 800000, 'ACTIVE'))
        self.post(self.treas, self.base() + f'/loans/{lid}/repay', {'amount': '3000', 'paid_on': TODAY, 'method': 'Cash'})
        self.assertEqual(self.db.val('SELECT status FROM loans WHERE id=?', (lid,)), 'PAID')
        self.post(self.treas, self.base() + f'/loans/{lid}/repay', {'amount': '1', 'paid_on': TODAY, 'method': 'Cash'})
        self.assertEqual(self.db.val('SELECT paid_cents FROM loans WHERE id=?', (lid,)), 1100000)

    def test_loan_over_limit_and_second_loan_refused(self):
        self.team(); self.add_contrib('m1', 1000)
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '3001'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM loans'), 0)
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '3000'}); self.post(self.m1, self.base() + '/loans/apply', {'amount': '100'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM loans'), 1)

    def test_borrower_who_is_also_treasurer_cannot_approve_own_loan(self):
        self.team(); self.add_contrib('treas', 1000, c=self.admin)
        self.post(self.treas, self.base() + '/loans/apply', {'amount': '1000'}); lid = self.db.val('SELECT id FROM loans')
        self.post(self.treas, self.base() + f'/loans/{lid}/approve')
        self.assertEqual(self.db.val('SELECT status FROM loans WHERE id=?', (lid,)), 'PENDING')

    # ---------- fines ----------
    def test_fine_journey(self):
        self.team()
        self.assertEqual(self.post(self.m1, self.base() + '/fines/add', {'user_id': self.uid['m2'], 'amount': '200', 'reason': 'x' * 5}).status_code, 403)
        self.post(self.sec, self.base() + '/fines/add', {'user_id': self.uid['m1'], 'amount': '200', 'reason': 'Late meeting attendance'})
        fid = self.db.val('SELECT id FROM fines')
        self.assertIn('Late meeting', self.m1.get(self.base() + '/fines').get_data(as_text=True))
        self.assertNotIn('Late meeting', self.m2.get(self.base() + '/fines').get_data(as_text=True))  # privacy
        self.assertEqual(self.post(self.sec, self.base() + f'/fines/{fid}/pay', {'amount': '200', 'paid_on': TODAY, 'method': 'Cash'}).status_code, 403)
        self.post(self.treas, self.base() + f'/fines/{fid}/pay', {'amount': '100', 'paid_on': TODAY, 'method': 'Cash'})
        self.assertEqual(self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), 'PARTIAL')
        self.post(self.treas, self.base() + f'/fines/{fid}/pay', {'amount': '100', 'paid_on': TODAY, 'method': 'Cash'})
        self.assertEqual((self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), self.bal()), ('PAID', 20000))

    # ---------- statements privacy ----------
    def test_statement_privacy(self):
        self.team(); self.add_contrib('m2', 777)
        self.assertEqual(self.m1.get(self.base() + f'/statement/{self.uid["m2"]}').status_code, 403)
        self.assertEqual(self.m1.get(self.base() + f'/statement/{self.uid["m2"]}.csv').status_code, 403)
        self.assertIn('777', self.treas.get(self.base() + f'/statement/{self.uid["m2"]}').get_data(as_text=True))
        self.assertIn('777', self.m2.get(self.base() + '/statement').get_data(as_text=True))

    # ---------- cross-chama ----------
    def test_cross_chama_isolation_for_new_features(self):
        self.team(); self.add_contrib('m1', 1000)
        other, _ = self.signup('Other Admin'); ocid = self.make_chama(other, name='Other Chama')
        for p in ('/savings', '/loans', '/fines', '/ledger', '/chat', '/statement', '/ledger.csv', '/chat/poll'):
            self.assertEqual(other.get(self.base() + p).status_code, 403, p)
            self.assertEqual(self.treas.get(self.base(ocid) + p).status_code, 403, p)
        for p, d in (('/savings/add', {'user_id': self.uid['m1'], 'amount': '5', 'paid_on': TODAY, 'method': 'Cash'}), ('/chat/send', {'body': 'hi'}), ('/loans/apply', {'amount': '500'})):
            self.assertEqual(self.post(other, self.base() + p, d).status_code, 403, p)
        # IDOR: a real official of chama B using their own chama URL with chama A's record ids
        ouid = self.db.val('SELECT created_by FROM chamas WHERE id=?', (ocid,))
        F.record_contribution(self.db, ocid, ouid, 500000, TODAY, 'Cash', None, '', ouid)
        lid = F.apply_loan(self.db, self.cid, self.uid['m1'], 100000, '', S.now_utc())
        self.post(other, self.base(ocid) + f'/loans/{lid}/approve')
        self.assertEqual(self.db.val('SELECT status FROM loans WHERE id=?', (lid,)), 'PENDING')
        cid_a = self.db.val('SELECT id FROM contributions WHERE chama_id=?', (self.cid,))
        self.post(other, self.base(ocid) + f'/savings/{cid_a}/void', {'reason': 'attack'})
        self.assertEqual(self.db.val('SELECT status FROM contributions WHERE id=?', (cid_a,)), 'PAID')

    # ---------- suspension ----------
    def test_suspended_chama_blocks_new_pages_but_keeps_data(self):
        self.team(); self.add_contrib('m1', 1000)
        S.suspend_chama(self.db, self.cid, 'test', 1)
        for p in ('/savings', '/loans', '/fines', '/ledger', '/chat', '/statement'):
            r = self.treas.get(self.base() + p); self.assertEqual(r.status_code, 302, p); self.assertIn('/subscription', r.headers['Location'])
        self.assertEqual(self.add_contrib('m1', 500).status_code, 302); self.assertEqual(self.bal(), 100000)
        S.grant_access(self.db, self.cid, 30, 1, reactivate=True)
        self.assertEqual(self.treas.get(self.base() + '/ledger').status_code, 200)

    # ---------- exports ----------
    def test_csv_exports_roles_and_formula_safety(self):
        self.team()
        self.post(self.treas, self.base() + '/ledger/add', {'kind': 'OTHER_INCOME', 'amount': '500', 'on': TODAY, 'description': '=cmd|calc'})
        r = self.treas.get(self.base() + '/ledger.csv')
        self.assertEqual(r.status_code, 200); self.assertIn('text/csv', r.content_type)
        self.assertIn("'=cmd|calc", r.get_data(as_text=True)); self.assertNotIn(',=cmd', r.get_data(as_text=True))
        self.assertEqual(self.m1.get(self.base() + '/ledger.csv').status_code, 403)
        self.assertEqual(self.m1.get(self.base() + f'/statement/{self.uid["m1"]}.csv').status_code, 200)

    def test_expense_cannot_exceed_cash(self):
        self.team(); self.add_contrib('m1', 100)
        self.post(self.treas, self.base() + '/ledger/add', {'kind': 'EXPENSE', 'amount': '500', 'on': TODAY, 'description': 'Venue'})
        self.assertEqual(self.bal(), 10000)

    # ---------- chat ----------
    def test_chat_send_poll_unread_xss_and_privacy(self):
        self.team()
        self.post(self.m1, self.base() + '/chat/send', {'body': '<script>alert(1)</script> hello'})
        self.post(self.m1, self.base() + '/chat/send', {'body': 'Meeting Saturday'})
        self.assertIn('class="dot">2<', self.m2.get(self.base()).get_data(as_text=True))        # unread badge
        html = self.m2.get(self.base() + '/chat').get_data(as_text=True)
        self.assertNotIn('<script>alert', html); self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('class="dot"', self.m2.get(self.base()).get_data(as_text=True))        # read now
        last = F.list_messages(self.db, self.cid)[-1]['id']
        self.post(self.m1, self.base() + '/chat/send', {'body': 'one more'})
        j = self.m2.get(self.base() + f'/chat/poll?after={last}').json
        self.assertEqual([m['body'] for m in j['messages']], ['one more']); self.assertFalse(j['messages'][0]['mine'])
        self.post(self.m1, self.base() + '/chat/send', {'body': ''}); self.post(self.m1, self.base() + '/chat/send', {'body': 'x' * 1001})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM messages'), 3)

    def test_removed_member_loses_chat_and_money_pages(self):
        self.team(); self.post(self.admin, self.base() + f'/members/{self.uid["m2"]}/remove')
        for p in ('/chat', '/savings', '/ledger', '/statement'):
            self.assertEqual(self.m2.get(self.base() + p).status_code, 403, p)
        self.assertEqual(self.post(self.m2, self.base() + '/chat/send', {'body': 'hi'}).status_code, 403)

    def test_settings_admin_only_and_validated(self):
        self.team()
        self.assertEqual(self.post(self.treas, self.base() + '/settings', {'contribution': '500', 'rate': '5', 'multiplier': '2'}).status_code, 403)
        self.post(self.admin, self.base() + '/settings', {'contribution': '500', 'rate': '5', 'multiplier': '2'})
        c = self.db.one('SELECT * FROM chamas WHERE id=?', (self.cid,)); self.assertEqual((c['contribution_cents'], c['loan_rate_bps'], c['loan_multiplier']), (50000, 500, 2))
        self.post(self.admin, self.base() + '/settings', {'contribution': '500', 'rate': '500', 'multiplier': '99'})
        self.assertEqual(self.db.val('SELECT loan_rate_bps FROM chamas WHERE id=?', (self.cid,)), 500)

    def test_audit_trail_for_money_actions(self):
        self.team(); self.add_contrib('m1', 1000)
        self.post(self.treas, self.base() + '/fines/add', {'user_id': self.uid['m1'], 'amount': '100', 'reason': 'Late again'})
        acts = {r['action'] for r in self.db.all('SELECT action FROM audit_logs')}
        self.assertTrue({'CONTRIBUTION_CREATED', 'FINE_CREATED'} <= acts)


if __name__ == '__main__':
    unittest.main()
