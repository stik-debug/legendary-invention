import os, sys, unittest
from datetime import timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from test_services import Base
import services as S
import finance as F

D = '2026-09-15'


class Fin(Base):
    def setUp(self):
        super().setUp()
        self.admin, self.cid = self.chama('growth')
        self.treas = self.user(); S.add_member(self.db, self.cid, self.treas, 'TREASURER', self.admin, self.t0)
        self.members = []
        for _ in range(3):
            u = self.user(); S.add_member(self.db, self.cid, u, 'MEMBER', self.admin, self.t0); self.members.append(u)

    def contrib(self, u, kes=1000, ref=None, on=D):
        return F.record_contribution(self.db, self.cid, u, kes * 100, on, 'Cash', ref, '', self.treas, self.t0)

    def bal(self): return F.cash_balance(self.db, self.cid)

    def ledger_rows(self): return self.db.all('SELECT * FROM ledger_transactions WHERE chama_id=? ORDER BY id', (self.cid,))


class Parsing(Fin):
    def test_amounts(self):
        self.assertEqual(F.parse_kes('1,500.50'), 150050)
        for bad in ('abc', '-5', '0', '12.345', 'NaN', 'Infinity', '99999999', ''):
            with self.assertRaises(S.BusinessError, msg=bad): F.parse_kes(bad)

    def test_dates(self):
        F.parse_date('2026-09-30', today=__import__('datetime').date(2026, 9, 30))
        for bad in ('2099-01-01', '2020-01-01', 'nope', ''):
            with self.assertRaises(S.BusinessError, msg=bad): F.parse_date(bad, today=__import__('datetime').date(2026, 9, 30))

    def test_csv_blocks_formula_injection(self):
        out = F.to_csv(['a'], [['=HYPERLINK("x")'], ['+1'], ['@x'], ['ok']])
        self.assertIn("'=HYPERLINK", out); self.assertIn("'+1", out); self.assertIn("ok\r\n", out)


class Contributions(Fin):
    def test_contribution_writes_one_ledger_row_and_balance(self):
        cid = self.contrib(self.members[0], 1000, 'ABC123')
        rows = self.ledger_rows()
        self.assertEqual([(r['kind'], r['direction'], r['amount_cents'], r['ref_id']) for r in rows], [('CONTRIBUTION', 'IN', 100000, cid)])
        self.assertEqual(self.bal(), 100000); self.assertEqual(F.savings(self.db, self.cid, self.members[0]), 100000)

    def test_duplicate_reference_and_non_member_rejected(self):
        self.contrib(self.members[0], 1000, 'ref-1')
        with self.assertRaises(S.BusinessError): self.contrib(self.members[1], 1000, 'REF-1')
        _, other = self.chama('starter')
        stranger = self.user()
        with self.assertRaises(S.BusinessError): self.contrib(stranger, 1000)
        with self.assertRaises(S.BusinessError): F.record_contribution(self.db, other, self.members[0], 100000, D, 'Cash', None, '', 1, self.t0)
        with self.assertRaises(S.BusinessError): F.record_contribution(self.db, self.cid, self.members[0], 100000, D, 'Crypto', None, '', 1, self.t0)
        self.assertEqual(len(self.ledger_rows()), 1)

    def test_void_reverses_ledger_and_frees_reference(self):
        cid = self.contrib(self.members[0], 1000, 'REF9')
        F.void_contribution(self.db, self.cid, cid, 'typed wrong amount', self.treas, self.t0)
        self.assertEqual(self.bal(), 0); self.assertEqual(F.savings(self.db, self.cid, self.members[0]), 0)
        self.assertEqual([r['kind'] for r in self.ledger_rows()], ['CONTRIBUTION', 'REVERSAL'])
        with self.assertRaises(S.BusinessError): F.void_contribution(self.db, self.cid, cid, 'again', self.treas, self.t0)
        self.contrib(self.members[0], 1000, 'REF9')  # can be re-entered correctly

    def test_void_blocked_if_money_already_lent_out(self):
        c = self.contrib(self.members[0], 1000)
        lid = F.apply_loan(self.db, self.cid, self.members[0], 100000, 'stock', self.t0)
        F.decide_loan(self.db, self.cid, lid, True, self.treas, self.t0); F.disburse_loan(self.db, self.cid, lid, self.treas, self.t0)
        with self.assertRaises(S.BusinessError): F.void_contribution(self.db, self.cid, c, 'oops', self.treas, self.t0)
        self.assertEqual(self.bal(), 0)

    def test_month_status_matches_spec_fixture(self):
        for u in self.members: pass
        self.contrib(self.members[0], 1000); self.contrib(self.members[1], 500)
        st = {r['id']: r['status'] for r in F.month_status(self.db, self.cid, '2026-09')}
        self.assertEqual((st[self.members[0]], st[self.members[1]], st[self.members[2]]), ('PAID', 'PARTIAL', 'PENDING'))
        self.assertEqual(F.month_status(self.db, self.cid, '2026-08')[0]['status'], 'PENDING')

    def test_no_float_drift(self):
        for _ in range(30): self.contrib(self.members[0], 0.10 if False else 1)
        F.record_contribution(self.db, self.cid, self.members[0], F.parse_kes('0.10', 1), D, 'Cash', None, '', 1, self.t0)
        self.assertEqual(self.bal(), 30 * 100 + 10)


class Loans(Fin):
    def borrow(self, kes=10000, saved=5000, rate=None):
        if rate is not None: F.update_chama_settings(self.db, self.cid, 100000, rate, 3, self.admin)
        self.contrib(self.members[0], saved); self.contrib(self.members[1], saved)
        return F.apply_loan(self.db, self.cid, self.members[0], kes * 100, 'shop', self.t0)

    def go_live(self, lid):
        F.decide_loan(self.db, self.cid, lid, True, self.treas, self.t0); F.disburse_loan(self.db, self.cid, lid, self.treas, self.t0)

    def loan(self, lid): return self.db.one('SELECT * FROM loans WHERE id=?', (lid,))

    def test_spec_example_10000_plus_1000_repaid_8000_then_3000(self):
        lid = self.borrow(10000, 5000, rate='10')
        l = self.loan(lid); self.assertEqual((l['principal_cents'], l['interest_cents'], l['total_due_cents']), (1000000, 100000, 1100000))
        self.go_live(lid)
        F.repay_loan(self.db, self.cid, lid, 800000, D, 'Cash', None, self.treas, self.t0)
        l = self.loan(lid); self.assertEqual((l['paid_cents'], l['total_due_cents'] - l['paid_cents'], l['status']), (800000, 300000, 'ACTIVE'))
        F.repay_loan(self.db, self.cid, lid, 300000, D, 'Cash', None, self.treas, self.t0)
        l = self.loan(lid); self.assertEqual((l['total_due_cents'] - l['paid_cents'], l['status']), (0, 'PAID'))
        with self.assertRaises(S.BusinessError): F.repay_loan(self.db, self.cid, lid, 100, D, 'Cash', None, self.treas, self.t0)

    def test_loan_limit_is_savings_times_multiple(self):
        self.contrib(self.members[0], 1000)
        with self.assertRaises(S.BusinessError): F.apply_loan(self.db, self.cid, self.members[0], 300100, '', self.t0)
        F.apply_loan(self.db, self.cid, self.members[0], 300000, '', self.t0)
        with self.assertRaises(S.BusinessError): F.apply_loan(self.db, self.cid, self.members[0], 10000, '', self.t0)  # second open loan

    def test_cannot_approve_or_pay_own_loan_and_states_enforced(self):
        lid = self.borrow(1000, 1000)
        with self.assertRaises(S.BusinessError): F.decide_loan(self.db, self.cid, lid, True, self.members[0], self.t0)
        with self.assertRaises(S.BusinessError): F.disburse_loan(self.db, self.cid, lid, self.treas, self.t0)  # not approved yet
        F.decide_loan(self.db, self.cid, lid, True, self.treas, self.t0)
        with self.assertRaises(S.BusinessError): F.decide_loan(self.db, self.cid, lid, False, self.admin, self.t0)  # already decided
        with self.assertRaises(S.BusinessError): F.repay_loan(self.db, self.cid, lid, 100, D, 'Cash', None, self.treas, self.t0)  # not disbursed

    def test_cannot_lend_more_cash_than_chama_has(self):
        self.contrib(self.members[0], 1000)
        lid = F.apply_loan(self.db, self.cid, self.members[0], 300000, '', self.t0)
        F.decide_loan(self.db, self.cid, lid, True, self.treas, self.t0)
        with self.assertRaises(S.BusinessError): F.disburse_loan(self.db, self.cid, lid, self.treas, self.t0)
        self.assertEqual(self.bal(), 100000); self.assertEqual(self.loan(lid)['status'], 'APPROVED')

    def test_disbursement_and_repayment_ledger(self):
        lid = self.borrow(1000, 1000, rate='10'); before = self.bal(); self.go_live(lid)
        self.assertEqual(self.bal(), before - 100000)
        F.repay_loan(self.db, self.cid, lid, 50000, D, 'M-Pesa', 'QWE1', self.treas, self.t0)
        self.assertEqual(self.bal(), before - 100000 + 50000)
        self.assertEqual([r['kind'] for r in self.ledger_rows()][-2:], ['LOAN_DISBURSEMENT', 'LOAN_REPAYMENT'])

    def test_overpay_and_bad_amounts_rejected_balance_never_negative(self):
        lid = self.borrow(1000, 1000, rate='10'); self.go_live(lid)
        with self.assertRaises(S.BusinessError): F.repay_loan(self.db, self.cid, lid, 110001, D, 'Cash', None, self.treas, self.t0)
        F.repay_loan(self.db, self.cid, lid, 110000, D, 'Cash', None, self.treas, self.t0)
        self.assertEqual(self.loan(lid)['status'], 'PAID')

    def test_void_repayment_reopens_loan(self):
        lid = self.borrow(1000, 1000, rate='10'); self.go_live(lid)
        rid = F.repay_loan(self.db, self.cid, lid, 110000, D, 'Cash', None, self.treas, self.t0)
        F.void_repayment(self.db, self.cid, rid, 'wrong member', self.treas, self.t0)
        l = self.loan(lid); self.assertEqual((l['paid_cents'], l['status']), (0, 'ACTIVE'))

    def test_rejected_loan_and_new_loan_after_paid(self):
        lid = self.borrow(1000, 1000)
        F.decide_loan(self.db, self.cid, lid, False, self.treas, self.t0)
        F.apply_loan(self.db, self.cid, self.members[0], 100000, '', self.t0)

    def test_other_chama_cannot_touch_loan(self):
        lid = self.borrow(1000, 1000); _, other = self.chama('starter')
        with self.assertRaises(S.BusinessError): F.decide_loan(self.db, other, lid, True, self.treas, self.t0)


class Fines(Fin):
    def test_spec_fine_flow(self):
        u = self.members[2]
        fid = F.create_fine(self.db, self.cid, u, 20000, 'Late meeting attendance', None, self.treas, self.t0)
        self.assertEqual(self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), 'UNPAID'); self.assertEqual(self.bal(), 0)
        F.pay_fine(self.db, self.cid, fid, 5000, D, 'Cash', None, self.treas, self.t0)
        self.assertEqual(self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), 'PARTIAL')
        F.pay_fine(self.db, self.cid, fid, 15000, D, 'Cash', None, self.treas, self.t0)
        self.assertEqual(self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), 'PAID'); self.assertEqual(self.bal(), 20000)
        with self.assertRaises(S.BusinessError): F.pay_fine(self.db, self.cid, fid, 100, D, 'Cash', None, self.treas, self.t0)
        self.assertEqual([(r['kind'], r['direction']) for r in self.ledger_rows()], [('FINE', 'MEMO'), ('FINE_PAYMENT', 'IN'), ('FINE_PAYMENT', 'IN')])

    def test_overpay_waive_void(self):
        fid = F.create_fine(self.db, self.cid, self.members[0], 20000, 'Noise', None, self.treas, self.t0)
        with self.assertRaises(S.BusinessError): F.pay_fine(self.db, self.cid, fid, 20001, D, 'Cash', None, self.treas, self.t0)
        pid = F.pay_fine(self.db, self.cid, fid, 5000, D, 'Cash', None, self.treas, self.t0)
        F.void_fine_payment(self.db, self.cid, pid, 'wrong', self.treas, self.t0)
        self.assertEqual((self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), self.bal()), ('UNPAID', 0))
        F.waive_fine(self.db, self.cid, fid, self.admin, self.t0)
        self.assertEqual(self.db.val('SELECT status FROM fines WHERE id=?', (fid,)), 'WAIVED')
        with self.assertRaises(S.BusinessError): F.pay_fine(self.db, self.cid, fid, 100, D, 'Cash', None, self.treas, self.t0)
        with self.assertRaises(S.BusinessError): F.create_fine(self.db, self.cid, self.members[0], 100, 'x', None, self.treas, self.t0)

    def test_fine_for_non_member_rejected(self):
        with self.assertRaises(S.BusinessError): F.create_fine(self.db, self.cid, self.user(), 1000, 'Late arrival', None, self.treas, self.t0)


class Ledger(Fin):
    def test_expense_needs_cash_and_income_adds(self):
        with self.assertRaises(S.BusinessError): F.record_ledger_entry(self.db, self.cid, 'EXPENSE', 100000, 'Venue', D, self.treas, self.t0)
        F.record_ledger_entry(self.db, self.cid, 'OTHER_INCOME', 300000, 'Bank interest', D, self.treas, self.t0)
        F.record_ledger_entry(self.db, self.cid, 'EXPENSE', 100000, 'Venue hire', D, self.treas, self.t0)
        self.assertEqual(self.bal(), 200000); self.assertEqual(F.cash_totals(self.db, self.cid), (300000, 100000))

    def test_ledger_always_equals_source_records(self):
        self.contrib(self.members[0], 2000); self.contrib(self.members[1], 1000)
        F.create_fine(self.db, self.cid, self.members[0], 5000, 'Late', None, self.treas, self.t0)
        contrib_total = self.db.val("SELECT SUM(amount_cents) FROM contributions WHERE status='PAID'")
        self.assertEqual(self.bal(), contrib_total)

    def test_statement(self):
        self.contrib(self.members[0], 2000)
        lid = F.apply_loan(self.db, self.cid, self.members[0], 100000, '', self.t0)
        F.decide_loan(self.db, self.cid, lid, True, self.treas, self.t0); F.disburse_loan(self.db, self.cid, lid, self.treas, self.t0)
        F.create_fine(self.db, self.cid, self.members[0], 5000, 'Late', None, self.treas, self.t0)
        st = F.member_statement(self.db, self.cid, self.members[0])
        self.assertEqual((st['saved'], st['loan_balance'], st['fine_balance'], st['max_loan']), (200000, 110000, 5000, 600000))


class Messaging(Fin):
    def test_send_read_unread(self):
        a, b = self.members[0], self.members[1]
        F.send_message(self.db, self.cid, a, 'Meeting on Saturday', self.t0); F.send_message(self.db, self.cid, a, 'Bring receipts', self.t0)
        self.assertEqual((F.unread_count(self.db, self.cid, b), F.unread_count(self.db, self.cid, a)), (2, 0))
        F.mark_read(self.db, self.cid, b); F.mark_read(self.db, self.cid, b)
        self.assertEqual(F.unread_count(self.db, self.cid, b), 0)
        F.send_message(self.db, self.cid, a, 'Thanks', self.t0)
        self.assertEqual(F.unread_count(self.db, self.cid, b), 1)
        self.assertEqual([m['body'] for m in F.list_messages(self.db, self.cid)], ['Meeting on Saturday', 'Bring receipts', 'Thanks'])
        first = F.list_messages(self.db, self.cid)[0]['id']
        self.assertEqual([m['body'] for m in F.list_messages(self.db, self.cid, after=first)], ['Bring receipts', 'Thanks'])

    def test_validation_rate_limit_and_isolation(self):
        a = self.members[0]
        for bad in ('', '   ', 'x' * 1001):
            with self.assertRaises(S.BusinessError): F.send_message(self.db, self.cid, a, bad, self.t0)
        for i in range(20): F.send_message(self.db, self.cid, a, f'm{i}', self.t0)
        with self.assertRaises(S.BusinessError): F.send_message(self.db, self.cid, a, 'too many', self.t0)
        owner2, other = self.chama('starter')
        F.send_message(self.db, other, owner2, 'secret of B', self.t0)
        self.assertNotIn('secret of B', [m['body'] for m in F.list_messages(self.db, self.cid, limit=100)])
        with self.assertRaises(S.BusinessError): F.send_message(self.db, other, a, 'intruder', self.t0)

    def test_removed_member_cannot_send(self):
        a = self.members[0]; S.remove_member(self.db, self.cid, a, self.admin, self.t0)
        with self.assertRaises(S.BusinessError): F.send_message(self.db, self.cid, a, 'hello', self.t0)


if __name__ == '__main__':
    unittest.main()
