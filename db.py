"""Thin database layer. Same SQL runs on PostgreSQL (production) and SQLite (tests/local).
Money is stored as integer cents (KES x 100): never floats."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # only needed when DATABASE_URL points at PostgreSQL
    psycopg = None


class IntegrityError(Exception):
    pass


class DB:
    def __init__(self, url):
        self.pg = url.startswith(('postgres://', 'postgresql://'))
        if self.pg:
            if psycopg is None:
                raise RuntimeError('psycopg is not installed')
            self.conn = psycopg.connect(url.replace('postgres://', 'postgresql://', 1), row_factory=dict_row)
        else:
            path = url.replace('sqlite:///', '', 1)
            self.conn = sqlite3.connect(path, isolation_level=None, timeout=15)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA foreign_keys=ON')

    def _sql(self, sql):
        # psycopg treats every % as a placeholder, so a literal % (for example LIKE 'abc%') must be doubled
        return sql.replace('%', '%%').replace('?', '%s') if self.pg else sql

    def execute(self, sql, params=()):
        try:
            cur = self.conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            return cur
        except sqlite3.IntegrityError as e:
            raise IntegrityError(str(e))
        except Exception as e:
            if psycopg is not None and isinstance(e, psycopg.errors.IntegrityError):
                raise IntegrityError(str(e))
            raise

    def one(self, sql, params=()):
        r = self.execute(sql, params).fetchone()
        return dict(r) if r else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def val(self, sql, params=(), default=None):
        r = self.execute(sql, params).fetchone()
        if not r:
            return default
        v = list(dict(r).values())[0]
        return default if v is None else v

    def insert(self, table, **cols):
        names = ','.join(cols)
        marks = ','.join('?' for _ in cols)
        cur = self.execute(f'INSERT INTO {table} ({names}) VALUES ({marks}) RETURNING id', list(cols.values()))
        return dict(cur.fetchone())['id']

    def begin(self):
        if not self.pg and not self.conn.in_transaction:
            self.conn.execute('BEGIN IMMEDIATE')

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    @contextmanager
    def tx(self):
        """Transactions nest: only the outermost one begins and commits, so a rule that calls another rule stays atomic."""
        depth = getattr(self, '_depth', 0)
        if depth == 0:
            self.begin()
        self._depth = depth + 1
        try:
            yield self
        except BaseException:
            self._depth = depth
            if depth == 0:
                self.rollback()
            raise
        else:
            self._depth = depth
            if depth == 0:
                self.commit()

    def lock(self, table, id_):
        """Row lock so two people cannot race past a plan limit (PostgreSQL). SQLite already serialises writers."""
        if self.pg:
            self.execute(f'SELECT id FROM {table} WHERE id=? FOR UPDATE', (id_,))

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id {PK}, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL, is_super_admin INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
  is_test_data INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  claimed INTEGER NOT NULL DEFAULT 1, claim_code_hash TEXT, claim_fails INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS subscription_plans(
  id {PK}, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, price_cents INTEGER NOT NULL CHECK(price_cents>=0),
  max_members INTEGER NOT NULL CHECK(max_members>0), is_active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chamas(
  id {PK}, name TEXT NOT NULL, description TEXT, created_by INTEGER NOT NULL REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chama_members(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', joined_at TEXT NOT NULL, removed_at TEXT,
  UNIQUE(chama_id, user_id));
CREATE TABLE IF NOT EXISTS subscriptions(
  id {PK}, chama_id INTEGER NOT NULL UNIQUE REFERENCES chamas(id), plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
  status TEXT NOT NULL, trial_ends_at TEXT, due_at TEXT NOT NULL, suspended_at TEXT, suspend_reason TEXT,
  cancelled_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
  amount_cents INTEGER NOT NULL CHECK(amount_cents>0), period_days INTEGER NOT NULL, method TEXT NOT NULL, provider TEXT,
  provider_txn_id TEXT, provider_receipt TEXT, status TEXT NOT NULL, phone TEXT, reference TEXT, notes TEXT,
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, completed_at TEXT,
  applied INTEGER NOT NULL DEFAULT 0, is_test_data INTEGER NOT NULL DEFAULT 0,
  UNIQUE(provider, provider_txn_id), UNIQUE(provider, provider_receipt));
CREATE TABLE IF NOT EXISTS payment_webhooks(
  id {PK}, provider TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT, payment_id INTEGER,
  result TEXT, processed INTEGER NOT NULL DEFAULT 0, received_at TEXT NOT NULL, UNIQUE(provider, event_id));
CREATE TABLE IF NOT EXISTS audit_logs(
  id {PK}, actor_id INTEGER, action TEXT NOT NULL, entity_type TEXT, entity_id TEXT, chama_id INTEGER,
  metadata TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(id {PK}, key TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_transactions(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER REFERENCES users(id), kind TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('IN','OUT','MEMO')), amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
  ref_type TEXT, ref_id INTEGER, description TEXT, occurred_on TEXT NOT NULL, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contributions(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL REFERENCES users(id),
  amount_cents BIGINT NOT NULL CHECK(amount_cents>0), paid_on TEXT NOT NULL, period TEXT NOT NULL, method TEXT NOT NULL,
  reference TEXT, notes TEXT, status TEXT NOT NULL DEFAULT 'PAID', created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
  voided_by INTEGER, voided_at TEXT, void_reason TEXT);
CREATE TABLE IF NOT EXISTS loans(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL REFERENCES users(id),
  principal_cents BIGINT NOT NULL CHECK(principal_cents>0), interest_cents BIGINT NOT NULL CHECK(interest_cents>=0),
  total_due_cents BIGINT NOT NULL, paid_cents BIGINT NOT NULL DEFAULT 0, rate_bps INTEGER NOT NULL, purpose TEXT,
  status TEXT NOT NULL, applied_at TEXT NOT NULL, decided_by INTEGER, decided_at TEXT, disbursed_by INTEGER, disbursed_at TEXT);
CREATE TABLE IF NOT EXISTS loan_repayments(
  id {PK}, loan_id INTEGER NOT NULL REFERENCES loans(id), chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL,
  amount_cents BIGINT NOT NULL CHECK(amount_cents>0), paid_on TEXT NOT NULL, method TEXT NOT NULL, reference TEXT,
  status TEXT NOT NULL DEFAULT 'PAID', created_by INTEGER, created_at TEXT NOT NULL, voided_at TEXT, void_reason TEXT);
CREATE TABLE IF NOT EXISTS fines(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL REFERENCES users(id),
  amount_cents BIGINT NOT NULL CHECK(amount_cents>0), paid_cents BIGINT NOT NULL DEFAULT 0, reason TEXT NOT NULL, due_on TEXT,
  status TEXT NOT NULL DEFAULT 'UNPAID', created_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fine_payments(
  id {PK}, fine_id INTEGER NOT NULL REFERENCES fines(id), chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL,
  amount_cents BIGINT NOT NULL CHECK(amount_cents>0), paid_on TEXT NOT NULL, method TEXT NOT NULL, reference TEXT,
  status TEXT NOT NULL DEFAULT 'PAID', created_by INTEGER, created_at TEXT NOT NULL, voided_at TEXT, void_reason TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), sender_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS message_reads(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), user_id INTEGER NOT NULL REFERENCES users(id),
  last_read_id BIGINT NOT NULL DEFAULT 0, UNIQUE(chama_id, user_id));
CREATE TABLE IF NOT EXISTS notifications(
  id {PK}, user_id INTEGER NOT NULL REFERENCES users(id), chama_id INTEGER, text TEXT NOT NULL, link TEXT, created_at TEXT NOT NULL, read_at TEXT);
CREATE TABLE IF NOT EXISTS meetings(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), title TEXT NOT NULL, venue TEXT, held_at TEXT NOT NULL, agenda TEXT, minutes TEXT,
  status TEXT NOT NULL DEFAULT 'SCHEDULED', absent_fine_cents BIGINT NOT NULL DEFAULT 0, created_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attendance(
  id {PK}, meeting_id INTEGER NOT NULL REFERENCES meetings(id), chama_id INTEGER NOT NULL REFERENCES chamas(id),
  user_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL, fine_id INTEGER, marked_by INTEGER, marked_at TEXT, UNIQUE(meeting_id, user_id));
CREATE TABLE IF NOT EXISTS announcements(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), title TEXT NOT NULL, body TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER, created_at TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, read_at, id);
CREATE INDEX IF NOT EXISTS ix_meetings_chama ON meetings(chama_id, held_at);
CREATE INDEX IF NOT EXISTS ix_announce_chama ON announcements(chama_id, id);
CREATE TABLE IF NOT EXISTS mgr_rounds(
  id {PK}, chama_id INTEGER NOT NULL REFERENCES chamas(id), name TEXT NOT NULL, amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
  frequency TEXT NOT NULL, start_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', created_by INTEGER, created_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE IF NOT EXISTS mgr_slots(
  id {PK}, round_id INTEGER NOT NULL REFERENCES mgr_rounds(id), chama_id INTEGER NOT NULL REFERENCES chamas(id), position INTEGER NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id), due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', payout_cents BIGINT NOT NULL DEFAULT 0,
  paid_out_at TEXT, paid_out_by INTEGER, method TEXT, reference TEXT, reminded_at TEXT, UNIQUE(round_id, position), UNIQUE(round_id, user_id));
CREATE TABLE IF NOT EXISTS mgr_payments(
  id {PK}, round_id INTEGER NOT NULL REFERENCES mgr_rounds(id), slot_id INTEGER NOT NULL REFERENCES mgr_slots(id), chama_id INTEGER NOT NULL REFERENCES chamas(id),
  user_id INTEGER NOT NULL REFERENCES users(id), amount_cents BIGINT NOT NULL CHECK(amount_cents>0), method TEXT NOT NULL, reference TEXT, paid_on TEXT NOT NULL,
  recorded_by INTEGER, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PAID', voided_by INTEGER, voided_at TEXT, void_reason TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mgr_payment ON mgr_payments(slot_id, user_id) WHERE status='PAID';
CREATE INDEX IF NOT EXISTS ix_mgr_rounds ON mgr_rounds(chama_id, status);
CREATE INDEX IF NOT EXISTS ix_ledger_chama ON ledger_transactions(chama_id, id);
CREATE INDEX IF NOT EXISTS ix_contrib_chama ON contributions(chama_id, period, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contrib_ref ON contributions(chama_id, reference) WHERE reference IS NOT NULL AND status='PAID';
CREATE INDEX IF NOT EXISTS ix_loans_chama ON loans(chama_id, user_id, status);
CREATE INDEX IF NOT EXISTS ix_fines_chama ON fines(chama_id, user_id, status);
CREATE INDEX IF NOT EXISTS ix_messages_chama ON messages(chama_id, id);
CREATE INDEX IF NOT EXISTS ix_members_chama ON chama_members(chama_id, status);
CREATE INDEX IF NOT EXISTS ix_members_user ON chama_members(user_id);
CREATE INDEX IF NOT EXISTS ix_pay_chama ON payments(chama_id, created_at);
CREATE INDEX IF NOT EXISTS ix_pay_status ON payments(status, completed_at);
CREATE INDEX IF NOT EXISTS ix_audit_chama ON audit_logs(chama_id, created_at);
CREATE INDEX IF NOT EXISTS ix_sub_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS ix_attempts ON login_attempts(key, at);
"""

DEFAULT_PLANS = [('starter', 'Starter', 50000, 15, 1), ('growth', 'Growth', 150000, 70, 2), ('business', 'Business', 200000, 100, 3)]
DEFAULT_SETTINGS = {'trial_days': '7', 'grace_days': '3', 'billing_period_days': '30'}


def ensure_column(db, table, col, ddl):
    if db.pg:
        db.execute('ALTER TABLE ' + table + ' ADD COLUMN IF NOT EXISTS ' + col + ' ' + ddl)
    elif col not in [r['name'] for r in db.all('PRAGMA table_info(' + table + ')')]:
        db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + col + ' ' + ddl)


def _actual_columns(db, table):
    if db.pg:
        return {r['column_name'] for r in db.all('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?', (table,))}
    return {r['name'] for r in db.all('PRAGMA table_info(' + table + ')')}


def repair_columns(db, pk):
    """Upgrading an old database: CREATE TABLE IF NOT EXISTS never adds columns to a table that already exists, so a database created by an
    older build can lack columns the new code reads (and crash with 'column does not exist'). This compares every table with the current
    definition and adds whatever is missing, keeping the defaults. Only additive and safe: it never drops, renames or rewrites anything."""
    ref = sqlite3.connect(':memory:')
    try:
        for stmt in SCHEMA.replace('{PK}', 'INTEGER PRIMARY KEY AUTOINCREMENT').split(';'):
            if stmt.strip().upper().startswith('CREATE TABLE'):
                ref.execute(stmt)
        added = []
        for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            have = _actual_columns(db, table)
            if not have:
                continue
            for _cid, name, typ, notnull, dflt, is_pk in ref.execute('PRAGMA table_info(' + table + ')').fetchall():
                if name in have or is_pk:
                    continue
                ddl = typ or 'TEXT'
                if dflt is not None:
                    ddl += ' DEFAULT ' + str(dflt) + (' NOT NULL' if notnull else '')
                db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + ddl)
                added.append(table + '.' + name)
        return added
    finally:
        ref.close()


def init_db(db):
    pk = 'INTEGER PRIMARY KEY AUTOINCREMENT' if not db.pg else 'BIGSERIAL PRIMARY KEY'
    with db.tx():
        if db.pg:
            # Several server workers start at once on Render. Without this lock they all run CREATE TABLE together and most crash on the first deploy.
            db.execute('SELECT pg_advisory_xact_lock(7283640)')
        stmts = [x for x in SCHEMA.replace('{PK}', pk).split(';') if x.strip()]
        for stmt in stmts:  # tables first, then repair old tables, then indexes (an index may mention a column an old table lacks)
            if stmt.strip().upper().startswith('CREATE TABLE'):
                db.execute(stmt)
        repair_columns(db, pk)
        for stmt in stmts:
            if not stmt.strip().upper().startswith('CREATE TABLE'):
                db.execute(stmt)
        ensure_column(db, 'users', 'claimed', 'INTEGER NOT NULL DEFAULT 1')
        ensure_column(db, 'users', 'claim_code_hash', 'TEXT')
        ensure_column(db, 'users', 'claim_fails', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(db, 'ledger_transactions', 'account', "TEXT NOT NULL DEFAULT 'MAIN'")
        ensure_column(db, 'users', 'reset_hash', 'TEXT')
        ensure_column(db, 'users', 'reset_expires', 'TEXT')
        ensure_column(db, 'users', 'reset_fails', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(db, 'users', 'session_epoch', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(db, 'users', 'totp_secret', 'TEXT')
        ensure_column(db, 'users', 'totp_enabled', 'INTEGER NOT NULL DEFAULT 0')
        ensure_column(db, 'users', 'totp_last_step', 'BIGINT NOT NULL DEFAULT 0')
        ensure_column(db, 'users', 'totp_recovery', 'TEXT')
        ensure_column(db, 'chamas', 'contribution_cents', 'BIGINT NOT NULL DEFAULT 100000')
        ensure_column(db, 'chamas', 'loan_rate_bps', 'INTEGER NOT NULL DEFAULT 1000')
        ensure_column(db, 'chamas', 'loan_multiplier', 'INTEGER NOT NULL DEFAULT 3')
        if not db.val('SELECT COUNT(*) FROM subscription_plans'):
            for code, name, price, mx, order in DEFAULT_PLANS:
                db.insert('subscription_plans', code=code, name=name, price_cents=price, max_members=mx, sort_order=order)
        for k, v in DEFAULT_SETTINGS.items():
            if db.val('SELECT COUNT(*) FROM settings WHERE key=?', (k,)) == 0:
                db.execute('INSERT INTO settings(key,value) VALUES(?,?)', (k, v))


def audit(db, actor_id, action, entity_type=None, entity_id=None, chama_id=None, meta=None):
    db.insert('audit_logs', actor_id=actor_id, action=action, entity_type=entity_type,
              entity_id=str(entity_id) if entity_id is not None else None, chama_id=chama_id,
              metadata=json.dumps(meta or {}, default=str), created_at=datetime.utcnow().replace(microsecond=0).isoformat(sep=' '))
