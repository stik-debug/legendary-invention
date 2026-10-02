import os, sys, tempfile, unittest, uuid
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as dbm


class Upgrade(unittest.TestCase):
    """A database made by an older build must be healed when the new build starts, not crash on 'column does not exist'."""

    def setUp(self):
        self.schema = None
        base = os.environ.get('TEST_DATABASE_URL')
        if base:
            import psycopg
            self.schema = 'u' + uuid.uuid4().hex[:12]
            with psycopg.connect(base, autocommit=True) as c:
                c.execute(f'CREATE SCHEMA {self.schema}')
            url = base + ('&' if '?' in base else '?') + 'options=-csearch_path%3D' + self.schema
        else:
            url = 'sqlite:///' + tempfile.mkdtemp() + '/u.db'
        self.db = dbm.DB(url)

    def tearDown(self):
        self.db.close()
        if self.schema:
            import psycopg
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as c:
                c.execute(f'DROP SCHEMA {self.schema} CASCADE')

    def cols(self, t):
        return dbm._actual_columns(self.db, t)

    def test_missing_columns_are_added_with_defaults_and_data_survives(self):
        dbm.init_db(self.db)
        for t, c in (('users', 'is_test_data'), ('chamas', 'is_test_data'), ('payments', 'is_test_data'), ('contributions', 'reference'), ('fines', 'paid_cents')):
            self.assertIn(c, self.cols(t), (t, c))
        with self.db.tx():
            self.db.execute("INSERT INTO users(name,email,phone,password_hash,created_at) VALUES('Old User','o@example.test','254711000009','x','2026-01-01 00:00:00')")
            for t, c in (('users', 'is_test_data'), ('chamas', 'is_test_data'), ('payments', 'is_test_data')):
                self.db.execute(f'ALTER TABLE {t} DROP COLUMN {c}')
        self.assertNotIn('is_test_data', self.cols('users'))
        dbm.init_db(self.db)
        self.assertIn('is_test_data', self.cols('users')); self.assertIn('is_test_data', self.cols('chamas')); self.assertIn('is_test_data', self.cols('payments'))
        self.assertEqual(self.db.val("SELECT is_test_data FROM users WHERE email='o@example.test'"), 0, 'existing rows get the default')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM users'), 1, 'nothing was lost')

    def test_running_twice_changes_nothing(self):
        dbm.init_db(self.db); before = {t: self.cols(t) for t in ('users', 'chamas', 'loans', 'mgr_slots')}
        dbm.init_db(self.db)
        self.assertEqual(before, {t: self.cols(t) for t in before})

    def test_old_database_without_new_tables_gets_them(self):
        dbm.init_db(self.db)
        with self.db.tx():
            for t in ('mgr_payments', 'mgr_slots', 'mgr_rounds', 'attendance', 'meetings', 'notifications', 'announcements'):
                self.db.execute(f'DROP TABLE {t}')
        dbm.init_db(self.db)
        for t in ('mgr_payments', 'mgr_slots', 'mgr_rounds', 'attendance', 'meetings', 'notifications', 'announcements'):
            self.assertTrue(self.cols(t), t)

    def test_ledger_account_column_defaults_to_main(self):
        dbm.init_db(self.db)
        with self.db.tx():
            self.db.execute('ALTER TABLE ledger_transactions DROP COLUMN account')
        dbm.init_db(self.db)
        self.assertIn('account', self.cols('ledger_transactions'))


if __name__ == '__main__':
    unittest.main()
