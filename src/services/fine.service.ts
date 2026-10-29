import { getDb, generateId, nowIso, withTransaction } from '../db/index.js';
import { ValidationError, NotFoundError } from '../utils/errors.js';
import { toKesInteger, subtractMoney } from '../utils/money.js';

export function createFine(input: {
  chamaId: string;
  memberId: string;
  amountKes: number;
  reason: string;
  dueDate?: string;
  createdBy: string;
}) {
  const amount = toKesInteger(input.amountKes);
  if (amount <= 0) throw new ValidationError('Amount must be positive');
  if (!input.reason?.trim()) throw new ValidationError('Reason required');
  const db = getDb();
  const member = db
    .prepare(`SELECT id, user_id FROM chama_members WHERE id = ? AND chama_id = ? AND is_active = 1`)
    .get(input.memberId, input.chamaId) as any;
  if (!member) throw new NotFoundError('Member not found');
  const id = generateId();
  const now = nowIso();
  db.prepare(
    `INSERT INTO fines (id, chama_id, member_id, user_id, amount_kes, outstanding_kes, reason, due_date, status, created_by, is_test_data, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, 0, ?, ?)`
  ).run(id, input.chamaId, member.id, member.user_id, amount, amount, input.reason.trim(), input.dueDate || null, input.createdBy, now, now);
  db.prepare(
    `INSERT INTO ledger_transactions (id, chama_id, type, amount_kes, reference_type, reference_id, description, created_by, is_test_data, created_at)
     VALUES (?, ?, 'FINE', ?, 'fine', ?, ?, ?, 0, ?)`
  ).run(generateId(), input.chamaId, amount, id, `Fine: ${input.reason.trim()}`, input.createdBy, now);
  return { fineId: id };
}

export function payFine(input: { chamaId: string; fineId: string; amountKes: number; by: string }) {
  const amount = toKesInteger(input.amountKes);
  if (amount <= 0) throw new ValidationError('Amount must be positive');
  return withTransaction((tx) => {
    const fine = tx.prepare(`SELECT * FROM fines WHERE id = ? AND chama_id = ?`).get(input.fineId, input.chamaId) as any;
    if (!fine) throw new NotFoundError('Fine not found');
    if (fine.status === 'PAID' || fine.status === 'WAIVED') throw new ValidationError('Fine already closed');
    const outstanding = Number(fine.outstanding_kes);
    if (amount > outstanding) throw new ValidationError(`Exceeds outstanding (${outstanding})`);
    const newOut = subtractMoney(outstanding, amount);
    const status = newOut === 0 ? 'PAID' : 'PARTIAL';
    const now = nowIso();
    tx.prepare(`UPDATE fines SET outstanding_kes = ?, status = ?, updated_at = ? WHERE id = ?`).run(newOut, status, now, input.fineId);
    tx.prepare(
      `INSERT INTO ledger_transactions (id, chama_id, type, amount_kes, reference_type, reference_id, description, created_by, is_test_data, created_at)
       VALUES (?, ?, 'FINE_PAYMENT', ?, 'fine', ?, ?, ?, 0, ?)`
    ).run(generateId(), input.chamaId, amount, input.fineId, 'Fine payment', input.by, now);
    return { outstandingKes: newOut, status };
  });
}

export function listFines(chamaId: string) {
  const db = getDb();
  return db
    .prepare(
      `SELECT f.*, u.full_name as member_name FROM fines f
       JOIN users u ON u.id = f.user_id WHERE f.chama_id = ? ORDER BY f.created_at DESC`
    )
    .all(chamaId)
    .map((r: any) => ({
      id: String(r.id),
      amount_kes: Number(r.amount_kes),
      outstanding_kes: Number(r.outstanding_kes),
      reason: String(r.reason),
      status: String(r.status),
      member_name: String(r.member_name),
      member_id: String(r.member_id),
    }));
}

export function outstandingFinesTotal(chamaId: string): number {
  const db = getDb();
  const row = db
    .prepare(`SELECT COALESCE(SUM(outstanding_kes), 0) as t FROM fines WHERE chama_id = ? AND status IN ('PENDING','PARTIAL')`)
    .get(chamaId) as { t: number };
  return Number(row.t) || 0;
}
