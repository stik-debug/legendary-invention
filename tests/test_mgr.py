import os, re, sys, unittest
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import test_web_finance as _tf
import devtools as D
import finance as F
import mgr as M
import notify as N
import reports as R
import services as S

TODAY = date.today().isoformat()


class MgrWeb(unittest.TestCase):
    _W, _T = _tw.Web, _tf.FinWeb
    setUp, tearDown, client, tok, post, signup, make_chama, sub, phone_of, owner = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.sub, _W.phone_of, _W.owner
    team, base, add_contrib, bal = _T.team, _T.base, _T.add_contrib, _T.bal

    def everyone(self):
        self.team()
        self.aid = self.db.val('SELECT created_by FROM chamas WHERE id=?', (self.cid,))
        self.ids = {'admin': self.aid, **self.uid}
        self.clients = {'admin': self.admin, 'treas': self.treas, 'sec': self.sec, 'm1': self.m1, 'm2': self.m2}
        return self

    def notes(self, who):
        return [r['text'] for r in self.db.all('SELECT text FROM notifications WHERE user_id=? ORDER BY id', (self.ids[who],))]

    def start(self, order=('m1', 'm2', 'sec', 'treas', 'admin'), amount='1000', freq='MONTHLY', c=None):
        form = {'name': 'Test round', 'amount': amount, 'frequency': freq, 'start': TODAY, 'order': 'listed', 'member': [str(self.ids[k]) for k in order]}
        for i, k in enumerate(order):
            form[f'pos_{self.ids[k]}'] = str(i + 1)
        r = self.post(c or self.treas, self.base() + '/merry-go-round/new', form)
        self.assertEqual(r.status_code, 302)
        m = re.search(r'/merry-go-round/(\d+)$', r.headers['Location'])
        self.assertTrue(m, r.headers['Location'])
        self.rid = int(m.group(1))
        return self.rid

    def pay(self, who, c=None, rid=None, ref=None):
        return self.post(c or self.treas, self.base() + f'/merry-go-round/{rid or self.rid}/pay', {'user_id': self.ids[who], 'method': 'Cash', 'reference': ref or '', 'paid_on': TODAY})

    def payout(self, c=None, rid=None):
        return self.post(c or self.treas, self.base() + f'/merry-go-round/{rid or self.rid}/payout', {'method': 'M-Pesa', 'reference': 'QWE123'})

    def pot(self): return M.pot_balance(self.db, self.cid)
    def slot(self, pos): return self.db.one('SELECT * FROM mgr_slots WHERE round_id=? AND position=?', (self.rid, pos))

    # ---------- starting ----------
    def test_start_round_order_slots_and_alerts(self):
        self.everyone(); self.start()
        slots = [(s['position'], s['user_id']) for s in self.db.all('SELECT * FROM mgr_slots WHERE round_id=? ORDER BY position', (self.rid,))]
        self.assertEqual(slots, [(i + 1, self.ids[k]) for i, k in enumerate(('m1', 'm2', 'sec', 'treas', 'admin'))])
        self.assertTrue(any('number 2 of 5' in t for t in self.notes('m2')))
        self.assertTrue(any('Your turn to pay: KES 1,000 to M1 Person' in t for t in self.notes('m2')), 'a payer is told it is their turn to pay')
        self.assertTrue(any('Your turn to pay' in t for t in self.notes('sec')))
        self.assertFalse(any('Your turn to pay' in t for t in self.notes('m1')), 'the recipient is not asked to pay')
        self.assertTrue(any('your turn to receive' in t.lower() for t in self.notes('m1')))
        self.assertEqual(self.pot(), 0)

    def test_random_order_is_a_permutation(self):
        self.everyone()
        r = self.post(self.treas, self.base() + '/merry-go-round/new', {'name': 'Random one', 'amount': '500', 'frequency': 'WEEKLY', 'start': TODAY, 'order': 'random', 'member': [str(v) for v in self.ids.values()]})
        self.assertEqual(r.status_code, 302)
        rid = int(r.headers['Location'].rsplit('/', 1)[1])
        got = [s['user_id'] for s in self.db.all('SELECT user_id FROM mgr_slots WHERE round_id=? ORDER BY position', (rid,))]
        self.assertEqual(sorted(got), sorted(self.ids.values()))

    def test_due_dates_weekly_and_monthly_with_short_months(self):
        self.assertEqual(M.due_date(date(2026, 1, 31), 'MONTHLY', 1), date(2026, 2, 28))
        self.assertEqual(M.due_date(date(2026, 1, 31), 'MONTHLY', 2), date(2026, 3, 31))
        self.assertEqual(M.due_date(date(2026, 11, 15), 'MONTHLY', 3), date(2027, 2, 15))
        self.assertEqual(M.due_date(date(2026, 1, 1), 'WEEKLY', 3), date(2026, 1, 22))

    def test_bad_rounds_are_refused(self):
        self.everyone()
        base = {'name': 'Round', 'amount': '1000', 'frequency': 'MONTHLY', 'start': TODAY, 'order': 'random'}
        two = [str(self.ids['m1']), str(self.ids['m2'])]
        stranger, es = self.signup('Stranger Person'); sid = self.db.val('SELECT id FROM users WHERE email=?', (es,))
        for bad in ({**base, 'member': two}, {**base, 'member': two + [str(sid)]}, {**base, 'amount': '0', 'member': two + [str(self.ids['sec'])]},
                    {**base, 'amount': 'abc', 'member': two + [str(self.ids['sec'])]}, {**base, 'frequency': 'DAILY', 'member': two + [str(self.ids['sec'])]},
                    {**base, 'name': 'ab', 'member': two + [str(self.ids['sec'])]}, {**base, 'start': '2020-01-01', 'member': two + [str(self.ids['sec'])]}):
            self.post(self.treas, self.base() + '/merry-go-round/new', bad)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_rounds'), 0)

    # ---------- the pot is separate ----------
    def test_payments_go_to_the_pot_not_to_cash(self):
        self.everyone(); self.add_contrib('m1', 1000); self.start()
        cash = self.bal()
        self.pay('m2'); self.pay('sec')
        self.assertEqual(self.pot(), 200000)
        self.assertEqual(self.bal(), cash, 'cash in hand is untouched by the pot')
        self.assertEqual(F.cash_totals(self.db, self.cid), (100000, 0))

    def test_pot_can_never_be_lent_or_spent(self):
        self.everyone(); self.add_contrib('m1', 1000); self.start()
        for k in ('m2', 'sec', 'treas', 'admin'):
            self.pay(k)
        self.assertEqual((self.pot(), self.bal()), (400000, 100000))
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '2000', 'purpose': 'Stock'})
        lid = self.db.val('SELECT id FROM loans')
        self.assertTrue(lid, 'a loan request exists')
        self.post(self.admin, self.base() + f'/loans/{lid}/approve')
        self.post(self.treas, self.base() + f'/loans/{lid}/disburse')
        self.assertEqual(self.db.val('SELECT status FROM loans WHERE id=?', (lid,)), 'APPROVED', 'KES 1,000 cash cannot fund KES 2,000 even with KES 4,000 in the pot')
        self.assertEqual(self.pot(), 400000)
        self.post(self.treas, self.base() + '/ledger/add', {'kind': 'EXPENSE', 'amount': '3000', 'description': 'Party', 'occurred_on': TODAY})
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM ledger_transactions WHERE kind='EXPENSE'"), 0)
        self.assertEqual(self.pot(), 400000)

    def test_ledger_page_and_csv_show_only_main_account(self):
        self.everyone(); self.add_contrib('m1', 1000); self.start(); self.pay('m2')
        html = self.treas.get(self.base() + '/ledger').get_data(as_text=True)
        self.assertNotIn('MGR_CONTRIBUTION', html); self.assertNotIn('Test round', html)
        csv = self.treas.get(self.base() + '/ledger.csv').get_data(as_text=True)
        self.assertNotIn('MGR_', csv)

    # ---------- paying in ----------
    def test_only_current_turn_payers_once(self):
        self.everyone(); self.start()
        self.pay('m1')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_payments'), 0, 'the recipient does not pay')
        self.pay('m2'); self.pay('m2')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_payments'), 1, 'no double payment')
        stranger, es = self.signup('Out Sider'); sid = self.db.val('SELECT id FROM users WHERE email=?', (es,))
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/pay', {'user_id': sid, 'method': 'Cash', 'reference': '', 'paid_on': TODAY})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_payments'), 1)
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/pay', {'user_id': self.ids['sec'], 'method': 'Cheque', 'reference': '', 'paid_on': TODAY})
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/pay', {'user_id': self.ids['sec'], 'method': 'Cash', 'reference': '', 'paid_on': '2099-01-01'})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_payments'), 1)
        self.assertTrue(any('was recorded' in t for t in self.notes('m2')))

    def test_roles_members_cannot_run_the_money_side(self):
        self.everyone(); self.start()
        for path, data in (('/pay', {'user_id': self.ids['m2'], 'method': 'Cash', 'paid_on': TODAY}), ('/payout', {'method': 'Cash'}), ('/remind', {}), ('/cancel', {'reason': 'x y z'})):
            self.assertEqual(self.post(self.m2, self.base() + f'/merry-go-round/{self.rid}{path}', data).status_code, 403, path)
        self.assertEqual(self.post(self.m2, self.base() + '/merry-go-round/new', {'name': 'Mine now', 'amount': '100', 'frequency': 'WEEKLY', 'start': TODAY, 'member': [str(self.ids['m1'])]}).status_code, 403)
        self.assertEqual(self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/cancel', {'reason': 'nope'}).status_code, 403, 'only the chairperson cancels')

    def test_void_payment_reverses_the_pot_until_paid_out(self):
        self.everyone(); self.start(); self.pay('m2')
        pid = self.db.val('SELECT id FROM mgr_payments')
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/payments/{pid}/void', {'reason': 'x'})
        self.assertEqual(self.pot(), 100000, 'a reason of 1 character is refused')
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/payments/{pid}/void', {'reason': 'Wrong person'})
        self.assertEqual(self.pot(), 0)
        self.assertEqual(self.db.val('SELECT status FROM mgr_payments WHERE id=?', (pid,)), 'CANCELLED')
        self.pay('m2')
        self.assertEqual(self.pot(), 100000, 'can pay again after a cancellation')

    # ---------- payout and rotation ----------
    def test_payout_waits_for_everyone_and_names_who_is_missing(self):
        self.everyone(); self.start()
        for k in ('m2', 'sec', 'treas'):
            self.pay(k)
        r = self.payout(rid=self.rid)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mgr_slots WHERE status=?', ('PAID_OUT',)), 0)
        html = self.treas.get(self.base() + f'/merry-go-round/{self.rid}').get_data(as_text=True)
        self.assertIn('Still to pay', html); self.assertIn('Grace Admin', html)
        with self.assertRaises(S.BusinessError) as cm:
            M.payout(self.db, self.cid, self.rid, 'Cash', None, self.ids['treas'])
        self.assertIn('Grace Admin', str(cm.exception))

    def test_recipient_cannot_pay_out_their_own_turn(self):
        self.everyone(); self.start(order=('treas', 'm1', 'm2', 'sec', 'admin'))
        for k in ('m1', 'm2', 'sec', 'admin'):
            self.pay(k)
        self.payout(c=self.treas)
        self.assertEqual(self.slot(1)['status'], 'PENDING', 'the treasurer cannot pay herself')
        self.assertEqual(self.pot(), 400000)
        self.payout(c=self.admin)
        self.assertEqual(self.slot(1)['status'], 'PAID_OUT')

    def test_full_rotation_to_completion(self):
        self.everyone(); self.start()
        order = ['m1', 'm2', 'sec', 'treas', 'admin']
        for turn, recipient in enumerate(order):
            payers = [k for k in order if k != recipient]
            for k in payers:
                self.pay(k)
            self.assertEqual(self.pot(), 400000)
            actor = self.admin if recipient == 'treas' else self.treas
            self.payout(c=actor)
            self.assertEqual(self.pot(), 0, f'pot empty after turn {turn + 1}')
            self.assertEqual(self.slot(turn + 1)['status'], 'PAID_OUT')
            self.assertEqual(self.slot(turn + 1)['payout_cents'], 400000)
            self.assertTrue(any('You received KES 4,000' in t for t in self.notes(recipient)))
            if turn < 4:
                self.assertTrue(any(f'to {M.current_slot(self.db, self.rid)["name"]}' in t and 'Your turn to pay' in t for t in self.notes(order[(turn + 2) % 5])), 'the next turn alerts the next payers')
        self.assertEqual(self.db.val('SELECT status FROM mgr_rounds WHERE id=?', (self.rid,)), 'COMPLETED')
        self.assertTrue(any('is complete' in t for t in self.notes('m2')))
        self.assertEqual(self.bal(), 0, 'cash was never touched')
        self.pay('m1')
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM mgr_payments"), 20, 'nothing more can be paid once the round is over')
        self.assertEqual([m for s, m in D.validate(self.db) if s == 'ERROR'], [])

    # ---------- reminders ----------
    def test_reminder_goes_only_to_unpaid_payers_and_not_twice(self):
        self.everyone(); self.start(); self.pay('m2')
        before = {k: len(self.notes(k)) for k in self.ids}
        self.post(self.sec, self.base() + f'/merry-go-round/{self.rid}/remind')
        after = {k: len(self.notes(k)) for k in self.ids}
        self.assertEqual(set(k for k in self.ids if after[k] > before[k]), {'sec', 'treas', 'admin'}, 'everyone who still owes, nobody who already paid and not the recipient')
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/remind')
        self.assertEqual(len(self.notes('admin')), after['admin'], 'at most one reminder per 12 hours')
        self.assertEqual(self.post(self.m1, self.base() + f'/merry-go-round/{self.rid}/remind').status_code, 403)

    # ---------- cancel ----------
    def test_cancel_only_when_pot_is_empty(self):
        self.everyone(); self.start(); self.pay('m2')
        self.post(self.admin, self.base() + f'/merry-go-round/{self.rid}/cancel', {'reason': 'Group decided'})
        self.assertEqual(self.db.val('SELECT status FROM mgr_rounds WHERE id=?', (self.rid,)), 'ACTIVE')
        pid = self.db.val('SELECT id FROM mgr_payments')
        self.post(self.treas, self.base() + f'/merry-go-round/{self.rid}/payments/{pid}/void', {'reason': 'Returned the money'})
        self.post(self.admin, self.base() + f'/merry-go-round/{self.rid}/cancel', {'reason': 'Group decided'})
        self.assertEqual(self.db.val('SELECT status FROM mgr_rounds WHERE id=?', (self.rid,)), 'CANCELLED')
        self.assertTrue(any('was cancelled' in t for t in self.notes('m2')))

    # ---------- pages, isolation, reports ----------
    def test_pages_render_for_every_role_and_chamas_stay_separate(self):
        self.everyone(); self.start(); self.pay('m2')
        for c in self.clients.values():
            self.assertEqual(c.get(self.base() + '/merry-go-round').status_code, 200)
            self.assertEqual(c.get(self.base() + f'/merry-go-round/{self.rid}').status_code, 200)
        self.assertIn('Your turn to pay', self.clients['sec'].get(self.base() + f'/merry-go-round/{self.rid}').get_data(as_text=True))
        self.assertNotIn('Your turn to pay', self.clients['m2'].get(self.base() + f'/merry-go-round/{self.rid}').get_data(as_text=True))
        evil, _ = self.signup('Other Chair'); oc = self.make_chama(evil, name='Other Group')
        self.assertEqual(evil.get(self.base() + '/merry-go-round').status_code, 403)
        self.assertEqual(evil.get(f'/chamas/{oc}/merry-go-round/{self.rid}').status_code, 404, 'a round id from another chama must not load')
        self.assertEqual(self.post(evil, f'/chamas/{oc}/merry-go-round/{self.rid}/pay', {'user_id': self.ids['m2'], 'method': 'Cash', 'paid_on': TODAY}).status_code, 404)
        self.assertEqual(self.post(evil, self.base() + f'/merry-go-round/{self.rid}/payout', {'method': 'Cash'}).status_code, 403)

    def test_report_shows_pot_and_csv_exports(self):
        self.everyone(); self.add_contrib('m1', 1000); self.start(); self.pay('m2'); self.pay('sec')
        rep = R.chama_report(self.db, self.cid, TODAY[:7])
        self.assertEqual((rep['pot'], rep['cash'], rep['mgr_active']), (200000, 100000, 1))
        self.assertIn('Merry-go-round pot', self.treas.get(self.base() + '/reports').get_data(as_text=True))
        csv = self.treas.get(self.base() + '/export/merrygoround.csv')
        self.assertEqual(csv.status_code, 200)
        self.assertEqual(len(csv.get_data(as_text=True).strip().splitlines()), 3)

    def test_suspended_chama_cannot_use_it(self):
        self.everyone(); self.start()
        self.db.execute("UPDATE subscriptions SET status='SUSPENDED' WHERE chama_id=?", (self.cid,)); self.db.commit()
        r = self.treas.get(self.base() + '/merry-go-round')
        self.assertEqual(r.status_code, 302); self.assertIn('/subscription', r.headers['Location'])

    # ---------- validate ----------
    def test_validate_catches_tampering_with_the_pot(self):
        self.everyone(); self.start(); self.pay('m2'); self.pay('sec')
        errs = lambda: [m for s, m in D.validate(self.db) if s == 'ERROR']
        self.assertEqual(errs(), [])
        self.db.execute("UPDATE ledger_transactions SET amount_cents=amount_cents+500 WHERE kind='MGR_CONTRIBUTION' AND id=(SELECT MIN(id) FROM ledger_transactions WHERE kind='MGR_CONTRIBUTION')"); self.db.commit()
        self.assertTrue(any('pot holds' in m for m in errs()))
        self.db.execute("UPDATE ledger_transactions SET amount_cents=amount_cents-500 WHERE kind='MGR_CONTRIBUTION' AND id=(SELECT MIN(id) FROM ledger_transactions WHERE kind='MGR_CONTRIBUTION')")
        self.db.execute("UPDATE ledger_transactions SET account='MAIN' WHERE kind='MGR_CONTRIBUTION' AND id=(SELECT MIN(id) FROM ledger_transactions WHERE kind='MGR_CONTRIBUTION')"); self.db.commit()
        self.assertTrue(any('main account' in m for m in errs()))


if __name__ == '__main__':
    unittest.main()
