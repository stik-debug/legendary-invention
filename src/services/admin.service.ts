import { getDb, generateId, nowIso, withTransaction } from '../db/index.js';
import { NotFoundError, ValidationError } from '../utils/errors.js';
import { revenueTotal } from './payment.service.js';

export function platformStats() {
  const db = getDb();
  const one = (sql: string) => Number((db.prepare(sql).get() as any)?.c || 0);
  return {
    totalChamas: one(`SELECT COUNT(*) as c FROM chamas WHERE is_active = 1`),
    totalUsers: one(`SELECT COUNT(*) as c FROM users WHERE is_active = 1`),
    trial: one(`SELECT COUNT(*) as c FROM subscriptions WHERE status = 'TRIAL'`),
    active: one(`SELECT COUNT(*) as c FROM subscriptions WHERE status = 'ACTIVE'`),
    pastDue: one(`SELECT COUNT(*) as c FROM subscriptions WHERE status = 'PAST_DUE'`),
    grace: one(`SELECT COUNT(*) as c FROM subscriptions WHERE status = 'GRACE_PERIOD'`),
    suspended: one(`SELECT COUNT(*) as c FROM subscriptions WHERE status = 'SUSPENDED'`),
    cancelled: one(`SELECT COUNT(*) as c FROM subscriptions WHERE status = 'CANCELLED'`),
    successfulPayments: one(`SELECT COUNT(*) as c FROM payments WHERE status = 'SUCCESS'`),
    revenueKes: revenueTotal(),
  };
}

export function listChamas(q?: string) {
  const db = getDb();
  let rows;
  if (q?.trim()) {
    rows = db
      .prepare(
        `SELECT c.*, s.status as sub_status, p.code as plan_code,
          (SELECT COUNT(*) FROM chama_members cm WHERE cm.chama_id = c.id AND cm.is_active = 1) as member_count
         FROM chamas c
         LEFT JOIN subscriptions s ON s.chama_id = c.id
         LEFT JOIN subscription_plans p ON p.id = s.plan_id
         WHERE c.name LIKE ? OR c.slug LIKE ?
         ORDER BY c.created_at DESC LIMIT 100`
      )
      .all(`%${q}%`, `%${q}%`);
  } else {
    rows = db
      .prepare(
        `SELECT c.*, s.status as sub_status, p.code as plan_code,
          (SELECT COUNT(*) FROM chama_members cm WHERE cm.chama_id = c.id AND cm.is_active = 1) as member_count
         FROM chamas c
         LEFT JOIN subscriptions s ON s.chama_id = c.id
         LEFT JOIN subscription_plans p ON p.id = s.plan_id
         ORDER BY c.created_at DESC LIMIT 100`
      )
      .all();
  }
  return rows.map((r: any) => ({
    id: String(r.id),
    name: String(r.name),
    slug: String(r.slug),
    sub_status: r.sub_status ? String(r.sub_status) : null,
    plan_code: r.plan_code ? String(r.plan_code) : null,
    member_count: Number(r.member_count) || 0,
    created_at: String(r.created_at),
  }));
}

export function suspendChama(input: { chamaId: string; reason: string; adminId: string }) {
  if (!input.reason?.trim()) throw new ValidationError('Reason required');
  return withTransaction((tx) => {
    const sub = tx.prepare(`SELECT id FROM subscriptions WHERE chama_id = ?`).get(input.chamaId) as any;
    if (!sub) throw new NotFoundError('Subscription not found');
    const now = nowIso();
    tx.prepare(
      `UPDATE subscriptions SET status = 'SUSPENDED', suspended_at = ?, suspension_reason = ?, updated_at = ? WHERE id = ?`
    ).run(now, input.reason.trim(), now, sub.id);
    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'CHAMA_SUSPEND', 'subscription', ?, ?, ?, ?)`
    ).run(generateId(), input.adminId, sub.id, input.chamaId, JSON.stringify({ reason: input.reason }), now);
    return { status: 'SUSPENDED' };
  });
}

export function reactivateChama(input: { chamaId: string; adminId: string; reason?: string }) {
  return withTransaction((tx) => {
    const sub = tx.prepare(`SELECT id FROM subscriptions WHERE chama_id = ?`).get(input.chamaId) as any;
    if (!sub) throw new NotFoundError('Subscription not found');
    const now = nowIso();
    const end = new Date();
    end.setDate(end.getDate() + 30);
    const endStr = end.toISOString().replace('T', ' ').substring(0, 19);
    tx.prepare(
      `UPDATE subscriptions SET status = 'ACTIVE', suspended_at = NULL, suspension_reason = NULL,
       current_period_start = ?, current_period_end = ?, updated_at = ? WHERE id = ?`
    ).run(now, endStr, now, sub.id);
    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'CHAMA_REACTIVATE', 'subscription', ?, ?, ?, ?)`
    ).run(generateId(), input.adminId, sub.id, input.chamaId, JSON.stringify({ reason: input.reason || null }), now);
    return { status: 'ACTIVE' };
  });
}

export function extendSubscription(input: {
  chamaId: string;
  days: number;
  adminId: string;
}) {
  if (![7, 30, 60, 90].includes(input.days) && input.days < 1) {
    throw new ValidationError('Invalid extension');
  }
  return withTransaction((tx) => {
    const sub = tx.prepare(`SELECT * FROM subscriptions WHERE chama_id = ?`).get(input.chamaId) as any;
    if (!sub) throw new NotFoundError('Subscription not found');
    const base = new Date(sub.current_period_end || Date.now());
    if (base < new Date()) base.setTime(Date.now());
    base.setDate(base.getDate() + input.days);
    const endStr = base.toISOString().replace('T', ' ').substring(0, 19);
    const now = nowIso();
    tx.prepare(
      `UPDATE subscriptions SET current_period_end = ?, status = CASE WHEN status IN ('SUSPENDED','CANCELLED') THEN status ELSE 'ACTIVE' END, updated_at = ? WHERE id = ?`
    ).run(endStr, now, sub.id);
    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'SUBSCRIPTION_EXTEND', 'subscription', ?, ?, ?, ?)`
    ).run(generateId(), input.adminId, sub.id, input.chamaId, JSON.stringify({ days: input.days }), now);
    return { current_period_end: endStr };
  });
}

export function recordManualPayment(input: {
  chamaId: string;
  amountKes: number;
  method: string;
  reference?: string;
  notes?: string;
  adminId: string;
}) {
  return withTransaction((tx) => {
    const sub = tx.prepare(`SELECT * FROM subscriptions WHERE chama_id = ?`).get(input.chamaId) as any;
    if (!sub) throw new NotFoundError('Subscription not found');
    const now = nowIso();
    const paymentId = generateId();
    tx.prepare(
      `INSERT INTO payments (id, chama_id, subscription_id, amount_kes, currency, status, provider, provider_ref, phone, idempotency_key, metadata, verified_at, is_test_data, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'KES', 'SUCCESS', 'manual', ?, NULL, ?, ?, ?, 0, ?, ?)`
    ).run(
      paymentId,
      input.chamaId,
      sub.id,
      input.amountKes,
      input.reference || `MANUAL-${Date.now()}`,
      generateId(),
      JSON.stringify({ method: input.method, notes: input.notes || null, admin: input.adminId }),
      now,
      now,
      now
    );
    const end = new Date();
    end.setDate(end.getDate() + 30);
    const endStr = end.toISOString().replace('T', ' ').substring(0, 19);
    tx.prepare(
      `UPDATE subscriptions SET status = 'ACTIVE', current_period_start = ?, current_period_end = ?,
       suspended_at = NULL, suspension_reason = NULL, updated_at = ? WHERE id = ?`
    ).run(now, endStr, now, sub.id);
    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'MANUAL_PAYMENT', 'payment', ?, ?, ?, ?)`
    ).run(generateId(), input.adminId, paymentId, input.chamaId, JSON.stringify({ amount: input.amountKes }), now);
    return { paymentId, status: 'SUCCESS' };
  });
}
