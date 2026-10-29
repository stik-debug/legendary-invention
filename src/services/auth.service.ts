/**
 * Authentication service — registration, login, password reset foundations.
 * Passwords never stored in plaintext. Server-side validation only.
 */
import { getDb, generateId, nowIso } from '../db/index.js';
import { hashPassword, verifyPassword } from '../utils/crypto.js';
import { ValidationError, UnauthorizedError, ConflictError } from '../utils/errors.js';
import { signToken } from '../middleware/auth.js';
import type { AuthPayload, Role } from '../types/index.js';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_RE = /^254[17]\d{8}$/; // Kenyan format 2547XXXXXXXX or 2541XXXXXXXX

function validatePassword(password: string): void {
  if (!password || password.length < 8) {
    throw new ValidationError('Password must be at least 8 characters');
  }
  if (password.length > 128) {
    throw new ValidationError('Password too long');
  }
}

function normalizePhone(phone: string): string {
  let p = phone.replace(/\s+/g, '').replace(/^\+/, '');
  if (p.startsWith('0')) p = '254' + p.slice(1);
  if (p.startsWith('7') || p.startsWith('1')) p = '254' + p;
  return p;
}

export async function register(input: {
  email: string;
  phone: string;
  password: string;
  fullName: string;
}): Promise<{ userId: string; token: string }> {
  const email = input.email.trim().toLowerCase();
  const phone = normalizePhone(input.phone);
  const fullName = input.fullName.trim();

  if (!EMAIL_RE.test(email)) throw new ValidationError('Invalid email');
  if (!PHONE_RE.test(phone)) throw new ValidationError('Invalid Kenyan phone (use 2547XXXXXXXX)');
  if (!fullName || fullName.length < 2) throw new ValidationError('Full name required');
  validatePassword(input.password);

  const db = getDb();
  const existing = db
    .prepare('SELECT id FROM users WHERE email = ? OR phone = ?')
    .get(email, phone);
  if (existing) throw new ConflictError('Email or phone already registered');

  const id = generateId();
  const password_hash = await hashPassword(input.password);
  const now = nowIso();

  db.prepare(
    `INSERT INTO users (id, email, phone, password_hash, full_name, is_active, is_test_data, email_verified, phone_verified, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, 1, 0, 0, 0, ?, ?)`
  ).run(id, email, phone, password_hash, fullName, now, now);

  // Audit
  db.prepare(
    `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, metadata, created_at)
     VALUES (?, ?, 'USER_REGISTER', 'user', ?, ?, ?)`
  ).run(generateId(), id, id, JSON.stringify({ email, phone }), now);

  const payload: AuthPayload = { userId: id, email, roles: [], chamaIds: [] };
  const token = signToken(payload);
  return { userId: id, token };
}

export async function login(input: {
  emailOrPhone: string;
  password: string;
}): Promise<{ userId: string; token: string; roles: Role[]; chamaIds: string[] }> {
  const raw = input.emailOrPhone.trim().toLowerCase();
  const db = getDb();

  let user = db
    .prepare('SELECT * FROM users WHERE email = ? AND is_active = 1')
    .get(raw) as any;

  if (!user) {
    const phone = normalizePhone(input.emailOrPhone);
    user = db
      .prepare('SELECT * FROM users WHERE phone = ? AND is_active = 1')
      .get(phone) as any;
  }

  if (!user) throw new UnauthorizedError('Invalid credentials');

  const ok = await verifyPassword(input.password, user.password_hash);
  if (!ok) throw new UnauthorizedError('Invalid credentials');

  // Load roles & chama memberships
  const memberships = db
    .prepare(
      `SELECT chama_id, role FROM chama_members WHERE user_id = ? AND is_active = 1`
    )
    .all(user.id) as { chama_id: string; role: Role }[];

  const roles = [...new Set(memberships.map((m) => m.role))] as Role[];
  const isSuperMembership = memberships.some((m) => m.role === 'SUPER_ADMIN');
  const superEmails = (process.env.SUPER_ADMIN_EMAILS || process.env.SUPER_ADMIN_EMAIL || 'owner@example.test')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  if (isSuperMembership || superEmails.includes(String(user.email).toLowerCase())) {
    if (!roles.includes('SUPER_ADMIN')) roles.push('SUPER_ADMIN');
  }

  const chamaIds = memberships
    .filter((m) => m.role !== 'SUPER_ADMIN')
    .map((m) => m.chama_id);

  const now = nowIso();
  db.prepare('UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?').run(now, now, user.id);

  db.prepare(
    `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, created_at)
     VALUES (?, ?, 'USER_LOGIN', 'user', ?, ?)`
  ).run(generateId(), user.id, user.id, now);

  const payload: AuthPayload = {
    userId: user.id,
    email: user.email,
    roles,
    chamaIds,
  };
  const token = signToken(payload);
  return { userId: user.id, token, roles, chamaIds };
}

export function getMe(userId: string) {
  const db = getDb();
  const user = db
    .prepare(
      `SELECT id, email, phone, full_name, is_active, email_verified, phone_verified, created_at, last_login_at
       FROM users WHERE id = ? AND is_active = 1`
    )
    .get(userId) as any;
  if (!user) throw new UnauthorizedError();

  const memberships = db
    .prepare(
      `SELECT cm.chama_id, cm.role, c.name as chama_name, c.slug
       FROM chama_members cm
       JOIN chamas c ON c.id = cm.chama_id
       WHERE cm.user_id = ? AND cm.is_active = 1`
    )
    .all(userId);

  return { ...user, memberships };
}
