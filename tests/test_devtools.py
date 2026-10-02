import os, sys, tempfile, unittest
from datetime import date
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import devtools as D
import finance as F
import services as S
from app import create_app
from db import DB
from werkzeug.security import generate_password_hash


class DevTools(unittest.TestCase):
    _W = _tw.Web
    setUp, tearDown, client, tok, post, signup, make_chama, owner = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.owner

    def problems(self, sev='ERROR'):
        return [m for s, m in D.validate(self.db) if s == sev]

    def test_seed_makes_realistic_valid_data(self):
        made = D.seed(self.db)
        self.assertEqual(len(made), 2)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chamas WHERE is_test_data=1'), 2)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM users WHERE is_test_data=1'), 13)
        for t in ('contributions', 'loans', 'fines', 'meetings', 'attendance', 'announcements', 'messages', 'notifications', 'ledger_transactions'):
            self.assertGreater(self.db.val(f'SELECT COUNT(*) FROM {t}'), 0, t)
        self.assertEqual(self.problems(), [], 'seeded data must satisfy every rule')
        self.assertEqual(self.problems('WARN'), [])
        self.assertTrue(all(F.cash_balance(self.db, c['id']) >= 0 for c in self.db.all('SELECT id FROM chamas')))

    def test_seed_twice_does_not_duplicate_and_demo_login_works(self):
        D.seed(self.db); self.assertEqual(D.seed(self.db), [])
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chamas'), 2)
        c = self.client()
        r = self.post(c, '/login', {'login': 'demo.001@chamapay.test', 'password': D.DEMO_PASSWORD})
        self.assertEqual(r.status_code, 302)
        cid = self.db.val("SELECT id FROM chamas WHERE name LIKE 'Umoja%'")
        for p in ('', '/savings', '/loans', '/meetings', '/notices', '/reports', '/chat'):
            self.assertEqual(c.get(f'/chamas/{cid}{p}').status_code, 200, p)
        other = self.db.val("SELECT id FROM chamas WHERE name LIKE 'Tujenge%'")
        self.assertEqual(c.get(f'/chamas/{other}/reports').status_code, 403, 'demo chamas stay separate')

    def test_reset_dry_run_changes_nothing(self):
        D.seed(self.db)
        before = {t: self.db.val(f'SELECT COUNT(*) FROM {t}') for t in ('users', 'chamas', 'contributions', 'audit_logs')}
        counts = D.reset_test_data(self.db, dry_run=True)
        self.assertEqual(counts['chamas'], 2)
        self.assertEqual(before, {t: self.db.val(f'SELECT COUNT(*) FROM {t}') for t in before})

    def test_reset_removes_only_test_data(self):
        c, e = self.signup('Real Person'); cid = self.make_chama(c, name='Real Chama')
        self.db.execute("INSERT INTO ledger_transactions(chama_id,kind,direction,amount_cents,occurred_on,created_at) VALUES(?,?,?,?,?,?)", (cid, 'OTHER_INCOME', 'IN', 5000, '2026-01-01', '2026-01-01 00:00:00'))
        D.seed(self.db)
        D.reset_test_data(self.db, dry_run=False)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chamas'), 1)
        self.assertEqual(self.db.val('SELECT name FROM chamas'), 'Real Chama')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM users WHERE is_test_data=1'), 0)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM users WHERE email=?', (e,)), 1)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM users WHERE is_super_admin=1'), 1, 'the owner is never deleted')
        self.assertEqual(F.cash_balance(self.db, cid), 5000)
        for t in ('contributions', 'loans', 'fines', 'meetings', 'attendance', 'announcements', 'messages', 'fine_payments', 'loan_repayments'):
            self.assertEqual(self.db.val(f'SELECT COUNT(*) FROM {t}'), 0, t)
        self.assertEqual(self.problems(), [])
        self.assertEqual(len(D.seed(self.db)), 2, 'can seed again after a reset')

    def test_reset_keeps_a_test_user_who_joined_a_real_chama(self):
        D.seed(self.db)
        c, _ = self.signup('Real Admin'); cid = self.make_chama(c, name='Real Chama')
        self.post(c, f'/chamas/{cid}/members', {'phone': '0790000004', 'role': 'MEMBER'})
        counts = D.reset_test_data(self.db, dry_run=False)
        self.assertEqual(counts['kept_users'], 1)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM users WHERE phone='254790000004'"), 1)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_members WHERE chama_id=?', (cid,)), 2)

    def test_validate_catches_broken_money_and_membership(self):
        D.seed(self.db)
        cid = self.db.val("SELECT id FROM chamas WHERE name LIKE 'Umoja%'")
        self.db.execute('UPDATE loans SET paid_cents=paid_cents+100 WHERE status=\'ACTIVE\' AND chama_id=?', (cid,))
        self.assertTrue(any('does not match its repayments' in m for m in self.problems()))
        self.db.execute('UPDATE loans SET paid_cents=paid_cents-100 WHERE status=\'ACTIVE\' AND chama_id=?', (cid,))
        self.assertEqual(self.problems(), [])
        self.db.execute("INSERT INTO ledger_transactions(chama_id,kind,direction,amount_cents,occurred_on,created_at) VALUES(?,?,?,?,?,?)", (cid, 'EXPENSE', 'OUT', 99999999, '2026-01-01', '2026-01-01 00:00:00'))
        self.assertTrue(any('negative cash balance' in m for m in self.problems()))
        self.db.execute("DELETE FROM ledger_transactions WHERE kind='EXPENSE' AND amount_cents=99999999")
        self.db.execute("UPDATE contributions SET amount_cents=amount_cents+1 WHERE id=(SELECT MIN(id) FROM contributions WHERE chama_id=?)", (cid,))
        self.assertTrue(any('ledger shows' in m for m in self.problems()))
        self.db.execute("UPDATE contributions SET amount_cents=amount_cents-1 WHERE id=(SELECT MIN(id) FROM contributions WHERE chama_id=?)", (cid,))
        self.db.execute("UPDATE chama_members SET role='MEMBER' WHERE chama_id=? AND role='CHAMA_ADMIN'", (cid,))
        self.assertTrue(any('no active administrator' in m for m in self.problems()))
        self.db.execute("UPDATE chama_members SET role='CHAMA_ADMIN' WHERE chama_id=? AND user_id=(SELECT MIN(user_id) FROM chama_members WHERE chama_id=?)", (cid, cid))
        self.db.execute("UPDATE subscription_plans SET max_members=2 WHERE code='growth'")
        self.assertTrue(any('plan allows 2' in m for m in self.problems()))

    def test_validate_flags_fines_and_payments(self):
        D.seed(self.db)
        self.db.execute("UPDATE fines SET status='PAID' WHERE status='PARTIAL'")
        self.assertTrue(any('marked PAID but is not fully paid' in m for m in self.problems()))
        self.db.execute("INSERT INTO payments(chama_id,plan_id,amount_cents,period_days,method,status,created_at,applied) VALUES((SELECT MIN(id) FROM chamas),1,50000,30,'MPESA','FAILED','2026-01-01 00:00:00',1)")
        self.assertTrue(any('applied to a subscription but is not marked SUCCESS' in m for m in self.problems()))

    def test_cli_commands(self):
        runner = self.app.test_cli_runner()
        r = runner.invoke(args=['seed'])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertIn('DemoPass123!', r.output)
        self.assertEqual(runner.invoke(args=['validate']).exit_code, 0)
        r = runner.invoke(args=['reset-test-data'])
        self.assertIn('Nothing was changed', r.output)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chamas'), 2)
        runner.invoke(args=['reset-test-data', '--yes'])
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chamas'), 0)

    def test_validate_cli_exit_code_on_errors(self):
        D.seed(self.db)
        self.db.execute("UPDATE loans SET paid_cents=paid_cents+1 WHERE status='ACTIVE'"); self.db.commit()
        r = self.app.test_cli_runner().invoke(args=['validate'])
        self.assertEqual(r.exit_code, 1)
        self.assertIn('ERROR:', r.output)

    def test_seed_refuses_production(self):
        tmp = tempfile.mkdtemp(); url = 'sqlite:///' + tmp + '/p.db'
        app = create_app({'DATABASE_URL': url, 'SECRET_KEY': 'k' * 48, 'IS_PRODUCTION': True, 'TESTING': True})
        r = app.test_cli_runner().invoke(args=['seed'])
        self.assertNotEqual(r.exit_code, 0)
        self.assertIn('Refusing', r.output)
        db = DB(url)
        self.assertEqual(db.val('SELECT COUNT(*) FROM chamas'), 0)
        db.close()


if __name__ == '__main__':
    unittest.main()
