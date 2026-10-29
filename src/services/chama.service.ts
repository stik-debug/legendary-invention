/**
 * Chama management + member limit enforcement (server-side).
 * Pricing is per Chama / month — never per member.
 */
import { getDb, generateId, nowIso, withTransaction } from '../db/index.js';
import { ValidationError, ForbiddenError, NotFoundError, ConflictError } from '../utils/errors.js';
import type { Role, SubscriptionStatus } from '../types/index.js';

const TRIAL_DAYS = parseInt(process.env.TRIAL_DAYS || '7', 10);

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 48);
}

export function createChama(input: {
  name: string;
  description?: string;
  location?: string;
  createdBy: string;
  planCode?: string; // default STARTER
}): { chamaId: string; subscriptionId: string } {
  const name = input.name.trim();
  if (!name || name.length < 2) throw new ValidationError('Chama name required');

  const db = getDb();
  const planCode = (input.planCode || 'STARTER').toUpperCase();
  const plan = db
    .prepare('SELECT * FROM subscription_plans WHERE code = ? AND is_active = 1')
    .get(planCode) as any;
  if (!plan) throw new ValidationError(`Plan ${planCode} not found`);

  let slug = slugify(name);
  const existingSlug = db.prepare('SELECT id FROM chamas WHERE slug = ?').get(slug);
  if (existingSlug) slug = `${slug}-${Date.now().toString(36)}`;

  const chamaId = generateId();
  const subId = generateId();
  const memberId = generateId();
  const now = nowIso();
  const trialEnd = new Date();
  trialEnd.setDate(trialEnd.getDate() + TRIAL_DAYS);
  const trialEndsAt = trialEnd.toISOString().replace('T', ' ').substring(0, 19);

  withTransaction((tx) => {
    tx.prepare(
      `INSERT INTO chamas (id, name, slug, description, location, is_active, is_test_data, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?)`
    ).run(chamaId, name, slug, input.description || null, input.location || null, input.createdBy, now, now);

    // Creator becomes CHAMA_ADMIN
    tx.prepare(
      `INSERT INTO chama_members (id, chama_id, user_id, role, is_active, joined_at, created_at)
       VALUES (?, ?, ?, 'CHAMA_ADMIN', 1, ?, ?)`
    ).run(memberId, chamaId, input.createdBy, now, now);

    // Trial subscription
    tx.prepare(
      `INSERT INTO subscriptions (id, chama_id, plan_id, status, trial_ends_at, current_period_start, current_period_end, is_test_data, created_at, updated_at)
       VALUES (?, ?, ?, 'TRIAL', ?, ?, ?, 0, ?, ?)`
    ).run(subId, chamaId, plan.id, trialEndsAt, now, trialEndsAt, now, now);

    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'CHAMA_CREATE', 'chama', ?, ?, ?, ?)`
    ).run(generateId(), input.createdBy, chamaId, chamaId, JSON.stringify({ name, plan: planCode }), now);
  });

  return { chamaId, subscriptionId: subId };
}

export function getChama(chamaId: string, requesterId: string, isSuperAdmin: boolean) {
  const db = getDb();
  const chama = db
    .prepare('SELECT * FROM chamas WHERE id = ? AND is_active = 1')
    .get(chamaId) as any;
  if (!chama) throw new NotFoundError('Chama not found');

  if (!isSuperAdmin) {
    const member = db
      .prepare('SELECT id FROM chama_members WHERE chama_id = ? AND user_id = ? AND is_active = 1')
      .get(chamaId, requesterId);
    if (!member) throw new ForbiddenError('Not a member of this Chama');
  }

  const sub = db
    .prepare(
      `SELECT s.*, p.code as plan_code, p.name as plan_name, p.price_kes, p.max_members
       FROM subscriptions s
       JOIN subscription_plans p ON p.id = s.plan_id
       WHERE s.chama_id = ?`
    )
    .get(chamaId) as any;

  const memberCount = (
    db.prepare('SELECT COUNT(*) as c FROM chama_members WHERE chama_id = ? AND is_active = 1').get(chamaId) as any
  ).c;

  return { ...chama, subscription: sub, memberCount };
}

/**
 * Add member with HARD server-side plan limit enforcement.
 * Starter 15 → allowed, 16 → rejected. Same for Growth 70/71, Business 100/101.
 */
export function addMember(input: {
  chamaId: string;
  userId: string; // the user being added
  role: Role;
  addedBy: string;
}): { memberId: string } {
  if (input.role === 'SUPER_ADMIN') {
    throw new ValidationError('Cannot assign SUPER_ADMIN via member add');
  }

  const db = getDb();

  return withTransaction((tx) => {
    // Verify chama + subscription + plan limits
    const sub = tx
      .prepare(
        `SELECT s.status, s.id as sub_id, p.max_members, p.code as plan_code
         FROM subscriptions s
         JOIN subscription_plans p ON p.id = s.plan_id
         WHERE s.chama_id = ?`
      )
      .get(input.chamaId) as any;

    if (!sub) throw new NotFoundError('Subscription not found for Chama');

    // Suspended / cancelled chamas cannot add members
    if (sub.status === 'SUSPENDED' || sub.status === 'CANCELLED') {
      throw new ForbiddenError(`Chama is ${sub.status} — cannot add members`);
    }

    const currentCount = (
      tx
        .prepare('SELECT COUNT(*) as c FROM chama_members WHERE chama_id = ? AND is_active = 1')
        .get(input.chamaId) as any
    ).c;

    if (currentCount >= sub.max_members) {
      throw new ForbiddenError(
        `Member limit reached for plan ${sub.plan_code} (max ${sub.max_members}). Upgrade to add more members.`
      );
    }

    // Already a member?
    const existing = tx
      .prepare('SELECT id, is_active FROM chama_members WHERE chama_id = ? AND user_id = ?')
      .get(input.chamaId, input.userId) as any;

    if (existing?.is_active) {
      throw new ConflictError('User is already an active member of this Chama');
    }

    const now = nowIso();
    let memberId: string;

    if (existing) {
      // Re-activate soft-removed member
      memberId = existing.id;
      tx.prepare(
        `UPDATE chama_members SET is_active = 1, role = ?, left_at = NULL, joined_at = ? WHERE id = ?`
      ).run(input.role, now, memberId);
    } else {
      memberId = generateId();
      tx.prepare(
        `INSERT INTO chama_members (id, chama_id, user_id, role, is_active, joined_at, created_at)
         VALUES (?, ?, ?, ?, 1, ?, ?)`
      ).run(memberId, input.chamaId, input.userId, input.role, now, now);
    }

    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'MEMBER_ADD', 'chama_member', ?, ?, ?, ?)`
    ).run(
      generateId(),
      input.addedBy,
      memberId,
      input.chamaId,
      JSON.stringify({ userId: input.userId, role: input.role, countAfter: currentCount + 1 }),
      now
    );

    return { memberId };
  });
}

/**
 * Soft-remove member. NEVER deletes financial history.
 */
export function removeMember(input: {
  chamaId: string;
  memberId: string;
  removedBy: string;
}): void {
  const db = getDb();
  withTransaction((tx) => {
    const member = tx
      .prepare('SELECT * FROM chama_members WHERE id = ? AND chama_id = ? AND is_active = 1')
      .get(input.memberId, input.chamaId) as any;
    if (!member) throw new NotFoundError('Active member not found');

    // Prevent removing the last CHAMA_ADMIN
    if (member.role === 'CHAMA_ADMIN') {
      const adminCount = (
        tx
          .prepare(
            `SELECT COUNT(*) as c FROM chama_members
             WHERE chama_id = ? AND role = 'CHAMA_ADMIN' AND is_active = 1`
          )
          .get(input.chamaId) as any
      ).c;
      if (adminCount <= 1) {
        throw new ForbiddenError('Cannot remove the last Chama Admin');
      }
    }

    const now = nowIso();
    tx.prepare(
      `UPDATE chama_members SET is_active = 0, left_at = ? WHERE id = ?`
    ).run(now, input.memberId);

    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'MEMBER_REMOVE', 'chama_member', ?, ?, ?, ?)`
    ).run(
      generateId(),
      input.removedBy,
      input.memberId,
      input.chamaId,
      JSON.stringify({ userId: member.user_id, role: member.role }),
      now
    );
  });
}

export function listMembers(chamaId: string): Array<{
  member_id: string;
  role: string;
  joined_at: string;
  is_active: number;
  user_id: string;
  full_name: string;
  email: string;
  phone: string;
}> {
  const db = getDb();
  const rows = db
    .prepare(
      `SELECT cm.id as member_id, cm.role, cm.joined_at, cm.is_active,
              u.id as user_id, u.full_name, u.email, u.phone
       FROM chama_members cm
       JOIN users u ON u.id = cm.user_id
       WHERE cm.chama_id = ? AND cm.is_active = 1
       ORDER BY cm.joined_at`
    )
    .all(chamaId) as Array<Record<string, unknown>>;
  return rows.map((r) => ({
    member_id: String(r.member_id),
    role: String(r.role),
    joined_at: String(r.joined_at),
    is_active: Number(r.is_active),
    user_id: String(r.user_id),
    full_name: String(r.full_name),
    email: String(r.email),
    phone: String(r.phone),
  }));
}

export function getMemberCount(chamaId: string): number {
  const db = getDb();
  return (
    db.prepare('SELECT COUNT(*) as c FROM chama_members WHERE chama_id = ? AND is_active = 1').get(chamaId) as any
  ).c;
}
