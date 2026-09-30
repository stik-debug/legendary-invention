import os, sys, tempfile, unittest
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as dbm
import services as S


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = dbm.DB('sqlite:///' + self.dir + '/t.db')
        dbm.init_db(self.db)
        self.n = 0
        self.t0 = S.now_utc()

    def user(self):
        self.n += 1
        return S.create_user(self.db, f'User {self.n}', f'u{self.n}@example.test', f'07{10000000 + self.n}', 'x', now=self.t0)

    def chama(self, plan='starter'):
        owner = self.user()
        return owner, S.create_chama(self.db, owner, 'Test Chama', '', plan, self.t0)

    def fill(self, cid, upto):
        while S.active_member_count(self.db, cid) < upto:
            S.add_member(self.db, cid, self.user(), 'MEMBER', None, self.t0)

    def pay(self, cid, plan_code='starter', months=1, now=None, prov='TEST'):
        plan = S.get_plan(self.db, code=plan_code)
        pid = S.create_payment(self.db, cid, plan['id'], months, 'MPESA', prov, '254712345678', None, now or self.t0)
        chk = f'CHK-{pid}'
        self.db.execute('UPDATE payments SET provider_txn_id=? WHERE id=?', (chk, pid)); self.db.commit()
        return pid, chk, plan['price_cents'] * months

    def ev(self, chk, amount, code=0, eid=None, receipt=None):
        return {'event_id': eid or chk, 'checkout_id': chk, 'result_code': code, 'amount_cents': amount, 'receipt': receipt or 'RCP' + chk}

    def sub(self, cid):
        return self.db.one('SELECT * FROM subscriptions WHERE chama_id=?', (cid,))


class PlanLimits(Base):
    def check(self, plan, limit):
        _, cid = self.chama(plan)
        self.fill(cid, limit)
        self.assertEqual(S.active_member_count(self.db, cid), limit)
        with self.assertRaises(S.PlanLimitError):
            S.add_member(self.db, cid, self.user(), 'MEMBER', None, self.t0)
        self.assertEqual(S.active_member_count(self.db, cid), limit)

    def test_starter_15_ok_16_rejected(self): self.check('starter', 15)
    def test_growth_70_ok_71_rejected(self): self.check('growth', 70)
    def test_business_100_ok_101_rejected(self): self.check('business', 100)

    def test_limit_comes_from_database(self):
        _, cid = self.chama('starter')
        S.update_plan(self.db, S.get_plan(self.db, code='starter')['id'], 'Starter', 500, 3, True, None)
        self.fill(cid, 3)
        with self.assertRaises(S.PlanLimitError):
            S.add_member(self.db, cid, self.user(), 'MEMBER', None, self.t0)

    def test_removed_member_frees_a_slot_and_history_kept(self):
        owner, cid = self.chama(); self.fill(cid, 15)
        u = self.db.val("SELECT user_id FROM chama_members WHERE chama_id=? AND role='MEMBER' LIMIT 1", (cid,))
        S.remove_member(self.db, cid, u, owner, self.t0)
        self.assertEqual(self.db.val('SELECT status FROM chama_members WHERE chama_id=? AND user_id=?', (cid, u)), 'REMOVED')
        S.add_member(self.db, cid, self.user(), 'MEMBER', None, self.t0)
        with self.assertRaises(S.PlanLimitError):
            S.add_member(self.db, cid, u, 'MEMBER', None, self.t0)

    def test_last_admin_cannot_be_removed(self):
        owner, cid = self.chama()
        with self.assertRaises(S.BusinessError): S.remove_member(self.db, cid, owner, owner, self.t0)

    def test_downgrade_blocked_when_too_many_members_and_nobody_removed(self):
        owner, cid = self.chama('growth'); self.fill(cid, 20)
        with self.assertRaises(S.PlanLimitError):
            S.downgrade_plan(self.db, cid, S.get_plan(self.db, code='starter')['id'], owner, self.t0)
        self.assertEqual(S.active_member_count(self.db, cid), 20)

    def test_downgrade_ok_when_fits_and_upgrade_needs_payment(self):
        owner, cid = self.chama('growth'); self.fill(cid, 10)
        S.downgrade_plan(self.db, cid, S.get_plan(self.db, code='starter')['id'], owner, self.t0)
        with self.assertRaises(S.BusinessError):
            S.downgrade_plan(self.db, cid, S.get_plan(self.db, code='business')['id'], owner, self.t0)

    def test_duplicate_and_bad_role_and_duplicate_account(self):
        owner, cid = self.chama()
        with self.assertRaises(S.BusinessError): S.add_member(self.db, cid, owner, 'MEMBER', None, self.t0)
        with self.assertRaises(S.BusinessError): S.add_member(self.db, cid, self.user(), 'SUPER_ADMIN', None, self.t0)
        with self.assertRaises(S.BusinessError): S.create_user(self.db, 'Dup', 'u1@example.test', '0799999999', 'x')


class Lifecycle(Base):
    def test_new_chama_is_trial_for_7_days(self):
        _, cid = self.chama()
        self.assertEqual(self.sub(cid)['status'], 'TRIAL')
        self.assertEqual(S.parse(self.sub(cid)['due_at']) - self.t0, timedelta(days=7))

    def test_timeline_trial_pastdue_grace_suspended(self):
        _, cid = self.chama()
        due = S.parse(self.sub(cid)['due_at'])
        for offset, expect in [(timedelta(days=-1), 'TRIAL'), (timedelta(hours=1), 'PAST_DUE'), (timedelta(days=2), 'GRACE_PERIOD'),
                               (timedelta(days=3, minutes=1), 'SUSPENDED')]:
            self.assertEqual(S.sync_subscription(self.db, cid, due + offset)['status'], expect, offset)

    def test_grace_is_configurable(self):
        _, cid = self.chama(); S.update_settings(self.db, 7, 10, 30, None)
        due = S.parse(self.sub(cid)['due_at'])
        self.assertEqual(S.sync_subscription(self.db, cid, due + timedelta(days=9))['status'], 'GRACE_PERIOD')
        self.assertEqual(S.sync_subscription(self.db, cid, due + timedelta(days=11))['status'], 'SUSPENDED')

    def test_suspension_keeps_all_data_and_blocks_adding(self):
        owner, cid = self.chama(); self.fill(cid, 5)
        due = S.parse(self.sub(cid)['due_at'])
        S.sweep_all(self.db, due + timedelta(days=5))
        self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')
        self.assertEqual(S.active_member_count(self.db, cid), 5)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chamas'), 1)
        with self.assertRaises(S.SubscriptionInactive):
            S.add_member(self.db, cid, self.user(), 'MEMBER', None, due + timedelta(days=5))

    def test_payment_after_suspension_reactivates_automatically(self):
        owner, cid = self.chama(); due = S.parse(self.sub(cid)['due_at']); late = due + timedelta(days=6)
        S.sweep_all(self.db, late)
        pid, chk, amt = self.pay(cid, now=late)
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(chk, amt)), 'SUCCESS')
        s = self.sub(cid)
        self.assertEqual(s['status'], 'ACTIVE'); self.assertIsNone(s['suspended_at'])
        self.assertGreater(S.parse(s['due_at']), late)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM audit_logs WHERE action='SUBSCRIPTION_REACTIVATED'"), 1)

    def test_early_payment_does_not_lose_days(self):
        _, cid = self.chama(); due = S.parse(self.sub(cid)['due_at'])
        pid, chk, amt = self.pay(cid)
        S.process_webhook(self.db, 'TEST', self.ev(chk, amt))
        self.assertEqual(S.parse(self.sub(cid)['due_at']), due + timedelta(days=30))

    def test_sweep_does_not_reactivate_or_touch_cancelled(self):
        owner, cid = self.chama(); S.cancel_subscription(self.db, cid, owner, self.t0)
        S.sweep_all(self.db, self.t0 + timedelta(days=1))
        self.assertEqual(self.sub(cid)['status'], 'CANCELLED')
        self.assertEqual(S.active_member_count(self.db, cid), 1)


class Payments(Base):
    def test_verified_payments_for_each_plan(self):
        for plan, cents in [('starter', 50000), ('growth', 150000), ('business', 200000)]:
            _, cid = self.chama(plan)
            pid, chk, amt = self.pay(cid, plan)
            self.assertEqual(amt, cents)
            self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(chk, amt)), 'SUCCESS')
            self.assertEqual(self.sub(cid)['status'], 'ACTIVE')

    def test_duplicate_webhook_counts_once(self):
        _, cid = self.chama(); pid, chk, amt = self.pay(cid); due = S.parse(self.sub(cid)['due_at'])
        r = [S.process_webhook(self.db, 'TEST', self.ev(chk, amt)) for _ in range(3)]
        self.assertEqual(r, ['SUCCESS', 'DUPLICATE', 'DUPLICATE'])
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM payments WHERE status='SUCCESS'"), 1)
        self.assertEqual(S.parse(self.sub(cid)['due_at']), due + timedelta(days=30))
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM audit_logs WHERE action='SUBSCRIPTION_CHANGED'"), 1)

    def test_same_payment_different_event_id_still_applied_once(self):
        _, cid = self.chama(); pid, chk, amt = self.pay(cid); due = S.parse(self.sub(cid)['due_at'])
        S.process_webhook(self.db, 'TEST', self.ev(chk, amt, eid='A'))
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(chk, amt, eid='B')), 'DUPLICATE')
        self.assertEqual(S.parse(self.sub(cid)['due_at']), due + timedelta(days=30))

    def test_unknown_or_invalid_callback_activates_nothing(self):
        _, cid = self.chama(); due = self.sub(cid)['due_at']
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev('NOPE', 50000)), 'UNKNOWN')
        self.assertEqual(S.process_webhook(self.db, 'TEST', {'event_id': None, 'checkout_id': None}), 'INVALID')
        self.assertEqual(self.sub(cid)['due_at'], due); self.assertEqual(self.sub(cid)['status'], 'TRIAL')

    def test_wrong_amount_is_rejected(self):
        _, cid = self.chama(); pid, chk, amt = self.pay(cid)
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(chk, amt - 100)), 'MISMATCH')
        self.assertEqual(self.sub(cid)['status'], 'TRIAL')
        self.assertEqual(self.db.val('SELECT status FROM payments WHERE id=?', (pid,)), 'FAILED')

    def test_failed_and_cancelled_do_not_activate_or_count_as_revenue(self):
        _, cid = self.chama()
        p1, c1, a1 = self.pay(cid); p2, c2, a2 = self.pay(cid); p3, c3, a3 = self.pay(cid)
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(c1, a1, code=1)), 'FAILED')
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(c2, a2, code=1032)), 'CANCELLED')
        self.assertEqual(self.sub(cid)['status'], 'TRIAL')
        self.assertEqual(S.owner_stats(self.db)['revenue_total'], 0)

    def test_pending_times_out(self):
        _, cid = self.chama(); pid, chk, amt = self.pay(cid)
        S.expire_pending_payments(self.db, self.t0 + timedelta(minutes=30))
        self.assertEqual(self.db.val('SELECT status FROM payments WHERE id=?', (pid,)), 'TIMEOUT')
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(chk, amt)), 'DUPLICATE')
        self.assertEqual(self.sub(cid)['status'], 'TRIAL')

    def test_same_receipt_cannot_pay_two_chamas(self):
        _, c1 = self.chama(); _, c2 = self.chama()
        p1, k1, a1 = self.pay(c1); p2, k2, a2 = self.pay(c2)
        self.assertEqual(S.process_webhook(self.db, 'TEST', self.ev(k1, a1, receipt='SAME')), 'SUCCESS')
        with self.assertRaises(S.BusinessError): S.process_webhook(self.db, 'TEST', self.ev(k2, a2, receipt='SAME'))
        self.assertEqual(self.sub(c2)['status'], 'TRIAL')

    def test_cannot_pay_for_plan_smaller_than_membership(self):
        _, cid = self.chama('growth'); self.fill(cid, 20)
        with self.assertRaises(S.PlanLimitError): self.pay(cid, 'starter')

    def test_upgrade_by_payment_raises_limit_and_keeps_members(self):
        _, cid = self.chama('starter'); self.fill(cid, 15)
        pid, chk, amt = self.pay(cid, 'growth')
        S.process_webhook(self.db, 'TEST', self.ev(chk, amt))
        self.assertEqual(S.chama_limit(self.db, cid), 70); self.assertEqual(S.active_member_count(self.db, cid), 15)
        S.add_member(self.db, cid, self.user(), 'MEMBER', None, self.t0)

    def test_manual_payment_owner_flow_and_audit(self):
        owner, cid = self.chama(); plan = S.get_plan(self.db, code='starter')
        with self.assertRaises(S.BusinessError): S.record_manual_payment(self.db, cid, plan['id'], 1, 'Cash', '', '2026-09-29', '', owner, self.t0)
        pid = S.record_manual_payment(self.db, cid, plan['id'], 1, 'Cash', 'RCPT-77', '2026-09-29', 'paid at office', owner, self.t0)
        self.assertEqual(self.sub(cid)['status'], 'ACTIVE')
        self.assertEqual(self.db.val('SELECT method FROM payments WHERE id=?', (pid,)), 'MANUAL')
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM audit_logs WHERE action='MANUAL_PAYMENT'"), 1)
        self.assertEqual(S.owner_stats(self.db)['revenue_total'], 50000)

    def test_revenue_ten_chamas_per_plan(self):
        for plan, total in [('starter', 500000), ('growth', 1500000), ('business', 2000000)]:
            db2 = dbm.DB('sqlite:///' + tempfile.mkdtemp() + '/r.db'); dbm.init_db(db2); self.db = db2
            for _ in range(10):
                _, cid = self.chama(plan); pid, chk, amt = self.pay(cid, plan)
                S.process_webhook(db2, 'TEST', self.ev(chk, amt))
            self.assertEqual(S.owner_stats(db2)['revenue_total'], total * 1)


class OwnerActions(Base):
    def test_suspend_requires_reason_and_reactivate_and_extend(self):
        owner, cid = self.chama()
        with self.assertRaises(S.BusinessError): S.suspend_chama(self.db, cid, '  ', owner, self.t0)
        S.suspend_chama(self.db, cid, 'Fraud check', owner, self.t0)
        self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')
        S.sweep_all(self.db, self.t0 + timedelta(hours=1))
        self.assertEqual(self.sub(cid)['status'], 'SUSPENDED')  # sweep must not undo an owner suspension
        S.grant_access(self.db, cid, 30, owner, reactivate=True, now=self.t0)
        self.assertEqual(self.sub(cid)['status'], 'ACTIVE')
        acts = [r['action'] for r in self.db.all('SELECT action FROM audit_logs')]
        self.assertIn('SUBSCRIPTION_SUSPENDED', acts); self.assertIn('SUBSCRIPTION_REACTIVATED', acts)

    def test_stats_come_from_records(self):
        self.chama(); self.chama()
        st = S.owner_stats(self.db)
        self.assertEqual((st['chamas'], st['users'], st['by_status'].get('TRIAL')), (2, 2, 2))

    def test_validation(self):
        self.assertEqual(S.normalize_phone('0712345678'), '254712345678')
        self.assertEqual(S.normalize_phone('+254 112 345 678'), '254112345678')
        self.assertIsNone(S.normalize_phone('12345'))
        self.assertEqual(S.kes(150000), '1,500')


if __name__ == '__main__':
    unittest.main(verbosity=1)
