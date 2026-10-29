-- ChamaPay Production Schema
-- Designed for PostgreSQL; SQLite-compatible for local development.
-- Money stored as INTEGER (whole Kenyan Shillings) to avoid floating-point errors.

PRAGMA foreign_keys = ON;

-- Users
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  phone TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  is_test_data INTEGER NOT NULL DEFAULT 0,
  email_verified INTEGER NOT NULL DEFAULT 0,
  phone_verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_test ON users(is_test_data);

-- Chamas (tenants)
CREATE TABLE IF NOT EXISTS chamas (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  description TEXT,
  location TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chamas_slug ON chamas(slug);
CREATE INDEX IF NOT EXISTS idx_chamas_test ON chamas(is_test_data);

-- Chama membership + roles
CREATE TABLE IF NOT EXISTS chama_members (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('SUPER_ADMIN','CHAMA_ADMIN','TREASURER','SECRETARY','MEMBER')),
  is_active INTEGER NOT NULL DEFAULT 1,
  joined_at TEXT NOT NULL DEFAULT (datetime('now')),
  left_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(chama_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_chama_members_chama ON chama_members(chama_id);
CREATE INDEX IF NOT EXISTS idx_chama_members_user ON chama_members(user_id);
CREATE INDEX IF NOT EXISTS idx_chama_members_active ON chama_members(chama_id, is_active);

-- Subscription plans (configurable by SUPER_ADMIN)
CREATE TABLE IF NOT EXISTS subscription_plans (
  id TEXT PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  price_kes INTEGER NOT NULL, -- whole KES
  max_members INTEGER NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Subscriptions
CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL UNIQUE REFERENCES chamas(id),
  plan_id TEXT NOT NULL REFERENCES subscription_plans(id),
  status TEXT NOT NULL CHECK (status IN ('TRIAL','ACTIVE','PAST_DUE','GRACE_PERIOD','SUSPENDED','CANCELLED')),
  trial_ends_at TEXT,
  current_period_start TEXT NOT NULL,
  current_period_end TEXT NOT NULL,
  grace_ends_at TEXT,
  cancelled_at TEXT,
  suspended_at TEXT,
  suspension_reason TEXT,
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_period_end ON subscriptions(current_period_end);

-- Payments
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  subscription_id TEXT REFERENCES subscriptions(id),
  amount_kes INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'KES',
  status TEXT NOT NULL CHECK (status IN ('PENDING','SUCCESS','FAILED','CANCELLED','TIMEOUT')),
  provider TEXT NOT NULL,
  provider_ref TEXT,
  phone TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  metadata TEXT,
  verified_at TEXT,
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_provider_ref ON payments(provider, provider_ref) WHERE provider_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_chama ON payments(chama_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);

-- Webhook records for idempotency
CREATE TABLE IF NOT EXISTS payment_webhooks (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_ref TEXT NOT NULL,
  payload TEXT NOT NULL,
  processed INTEGER NOT NULL DEFAULT 0,
  processing_result TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(provider, provider_ref)
);

-- Contributions
CREATE TABLE IF NOT EXISTS contributions (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  member_id TEXT NOT NULL REFERENCES chama_members(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  amount_kes INTEGER NOT NULL CHECK (amount_kes > 0),
  contribution_date TEXT NOT NULL,
  payment_method TEXT,
  reference TEXT,
  status TEXT NOT NULL DEFAULT 'CONFIRMED',
  notes TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_contributions_chama ON contributions(chama_id);
CREATE INDEX IF NOT EXISTS idx_contributions_member ON contributions(member_id);
CREATE INDEX IF NOT EXISTS idx_contributions_date ON contributions(contribution_date);

-- Financial ledger (append-only transactional journal)
CREATE TABLE IF NOT EXISTS ledger_transactions (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  type TEXT NOT NULL,
  amount_kes INTEGER NOT NULL,
  balance_after INTEGER,
  reference_type TEXT,
  reference_id TEXT,
  description TEXT,
  created_by TEXT REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ledger_chama ON ledger_transactions(chama_id);
CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger_transactions(type);
CREATE INDEX IF NOT EXISTS idx_ledger_ref ON ledger_transactions(reference_type, reference_id);

-- Loans
CREATE TABLE IF NOT EXISTS loans (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  member_id TEXT NOT NULL REFERENCES chama_members(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  amount_kes INTEGER NOT NULL CHECK (amount_kes > 0),
  interest_rate INTEGER NOT NULL DEFAULT 0, -- percentage * 100 e.g. 500 = 5%
  outstanding_kes INTEGER NOT NULL CHECK (outstanding_kes >= 0),
  status TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED','DISBURSED','REPAYING','COMPLETED','DEFAULTED')),
  purpose TEXT,
  applied_at TEXT NOT NULL DEFAULT (datetime('now')),
  approved_at TEXT,
  approved_by TEXT REFERENCES users(id),
  disbursed_at TEXT,
  due_date TEXT,
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_loans_chama ON loans(chama_id);
CREATE INDEX IF NOT EXISTS idx_loans_member ON loans(member_id);
CREATE INDEX IF NOT EXISTS idx_loans_status ON loans(status);

-- Loan repayments
CREATE TABLE IF NOT EXISTS loan_repayments (
  id TEXT PRIMARY KEY,
  loan_id TEXT NOT NULL REFERENCES loans(id),
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  amount_kes INTEGER NOT NULL CHECK (amount_kes > 0),
  payment_method TEXT,
  reference TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_loan_repayments_loan ON loan_repayments(loan_id);

-- Fines
CREATE TABLE IF NOT EXISTS fines (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  member_id TEXT NOT NULL REFERENCES chama_members(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  amount_kes INTEGER NOT NULL CHECK (amount_kes > 0),
  outstanding_kes INTEGER NOT NULL CHECK (outstanding_kes >= 0),
  reason TEXT NOT NULL,
  due_date TEXT,
  status TEXT NOT NULL CHECK (status IN ('PENDING','PARTIAL','PAID','WAIVED')),
  created_by TEXT NOT NULL REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fines_chama ON fines(chama_id);
CREATE INDEX IF NOT EXISTS idx_fines_member ON fines(member_id);

-- Meetings
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  title TEXT NOT NULL,
  meeting_date TEXT NOT NULL,
  location TEXT,
  agenda TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_meetings_chama ON meetings(chama_id);

-- Meeting attendance
CREATE TABLE IF NOT EXISTS meeting_attendance (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id),
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  member_id TEXT NOT NULL REFERENCES chama_members(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  present INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  recorded_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(meeting_id, member_id)
);

-- Announcements
CREATE TABLE IF NOT EXISTS announcements (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  published INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL REFERENCES users(id),
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_announcements_chama ON announcements(chama_id);

-- Messages (same-Chama only)
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  sender_id TEXT NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  is_test_data INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_chama ON messages(chama_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(chama_id, created_at);

-- Message reads
CREATE TABLE IF NOT EXISTS message_reads (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES messages(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  read_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(message_id, user_id)
);

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  chama_id TEXT REFERENCES chamas(id),
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  read INTEGER NOT NULL DEFAULT 0,
  metadata TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read);

-- Invites
CREATE TABLE IF NOT EXISTS invites (
  id TEXT PRIMARY KEY,
  chama_id TEXT NOT NULL REFERENCES chamas(id),
  token TEXT NOT NULL UNIQUE,
  email TEXT,
  phone TEXT,
  intended_role TEXT NOT NULL CHECK (intended_role IN ('CHAMA_ADMIN','TREASURER','SECRETARY','MEMBER')),
  invited_by TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('PENDING','ACCEPTED','REJECTED','EXPIRED')),
  expires_at TEXT NOT NULL,
  accepted_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_invites_token ON invites(token);
CREATE INDEX IF NOT EXISTS idx_invites_chama ON invites(chama_id);

-- Audit logs (immutable)
CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  actor_id TEXT REFERENCES users(id),
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  chama_id TEXT,
  metadata TEXT,
  ip_address TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_chama ON audit_logs(chama_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

-- App settings (configurable trial/grace etc.)
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
