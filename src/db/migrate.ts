import 'dotenv/config';
import { runMigration, closeDb, getDb, generateId, nowIso } from './index.js';

runMigration();

// Seed default subscription plans (idempotent)
const db = getDb();
const plans = [
  { code: 'STARTER', name: 'Starter', price_kes: 500, max_members: 15, description: 'Up to 15 members. Per Chama / month.' },
  { code: 'GROWTH', name: 'Growth', price_kes: 1500, max_members: 70, description: 'Up to 70 members. Per Chama / month.' },
  { code: 'BUSINESS', name: 'Business', price_kes: 2000, max_members: 100, description: 'Up to 100 members. Per Chama / month.' },
];

const now = nowIso();
for (const p of plans) {
  const existing = db.prepare('SELECT id FROM subscription_plans WHERE code = ?').get(p.code);
  if (!existing) {
    db.prepare(
      `INSERT INTO subscription_plans (id, code, name, price_kes, max_members, is_active, description, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`
    ).run(generateId(), p.code, p.name, p.price_kes, p.max_members, p.description, now, now);
    console.log(`[seed] Plan ${p.code} created`);
  } else {
    console.log(`[seed] Plan ${p.code} already exists`);
  }
}

// Default settings
const settings = [
  ['trial_days', process.env.TRIAL_DAYS || '7'],
  ['grace_period_days', process.env.GRACE_PERIOD_DAYS || '3'],
];
for (const [k, v] of settings) {
  db.prepare(
    `INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`
  ).run(k, v, now);
}

console.log('[migrate] Done');
closeDb();
