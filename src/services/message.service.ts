import { getDb, generateId, nowIso } from '../db/index.js';
import { ValidationError, ForbiddenError } from '../utils/errors.js';

export function sendMessage(input: { chamaId: string; senderId: string; body: string }) {
  if (!input.body?.trim()) throw new ValidationError('Message required');
  // Verify sender is member
  const db = getDb();
  const member = db
    .prepare(`SELECT id FROM chama_members WHERE chama_id = ? AND user_id = ? AND is_active = 1`)
    .get(input.chamaId, input.senderId);
  if (!member) throw new ForbiddenError('Not a member of this Chama');
  const id = generateId();
  const now = nowIso();
  db.prepare(
    `INSERT INTO messages (id, chama_id, sender_id, body, is_test_data, created_at) VALUES (?, ?, ?, ?, 0, ?)`
  ).run(id, input.chamaId, input.senderId, input.body.trim(), now);
  return { messageId: id };
}

export function listMessages(chamaId: string, userId: string, limit = 50) {
  const db = getDb();
  const member = db
    .prepare(`SELECT id FROM chama_members WHERE chama_id = ? AND user_id = ? AND is_active = 1`)
    .get(chamaId, userId);
  if (!member) throw new ForbiddenError('Not a member of this Chama');
  return db
    .prepare(
      `SELECT m.*, u.full_name as sender_name FROM messages m
       JOIN users u ON u.id = m.sender_id
       WHERE m.chama_id = ?
       ORDER BY m.created_at DESC LIMIT ?`
    )
    .all(chamaId, limit)
    .map((r: any) => ({
      id: String(r.id),
      body: String(r.body),
      sender_name: String(r.sender_name),
      sender_id: String(r.sender_id),
      created_at: String(r.created_at),
    }));
}

export function unreadCount(chamaId: string, userId: string): number {
  const db = getDb();
  const row = db
    .prepare(
      `SELECT COUNT(*) as c FROM messages m
       WHERE m.chama_id = ? AND m.sender_id != ?
       AND NOT EXISTS (
         SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.user_id = ?
       )`
    )
    .get(chamaId, userId, userId) as { c: number };
  return Number(row.c) || 0;
}

export function markRead(chamaId: string, messageId: string, userId: string) {
  const db = getDb();
  const msg = db.prepare(`SELECT id FROM messages WHERE id = ? AND chama_id = ?`).get(messageId, chamaId);
  if (!msg) return;
  const existing = db.prepare(`SELECT id FROM message_reads WHERE message_id = ? AND user_id = ?`).get(messageId, userId);
  if (existing) return;
  db.prepare(`INSERT INTO message_reads (id, message_id, user_id, read_at) VALUES (?, ?, ?, ?)`).run(
    generateId(),
    messageId,
    userId,
    nowIso()
  );
}
