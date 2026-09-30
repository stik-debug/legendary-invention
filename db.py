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
        return sql.replace('?', '%s') if self.pg else sql

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


def init_db(db):
    pk = 'INTEGER PRIMARY KEY AUTOINCREMENT' if not db.pg else 'BIGSERIAL PRIMARY KEY'
    with db.tx():
        for stmt in SCHEMA.replace('{PK}', pk).split(';'):
            if stmt.strip():
                db.execute(stmt)
        ensure_column(db, 'users', 'claimed', 'INTEGER NOT NULL DEFAULT 1')
        ensure_column(db, 'users', 'claim_code_hash', 'TEXT')
        ensure_column(db, 'users', 'claim_fails', 'INTEGER NOT NULL DEFAULT 0')
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
