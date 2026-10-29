/**
 * Contributions + atomic ledger. Money = integer KES only.
 */
import { getDb, generateId, nowIso, withTransaction } from '../db/index.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../utils/errors.js';
import { toKesInteger } from '../utils/money.js';

export function recordContribution(input: {
  chamaId: string;
  memberId: string;
  amountKes: number;
  contributionDate?: string;
  paymentMethod?: string;
  reference?: string;
  notes?: string;
  createdBy: string;
}) {
  const amount = toKesInteger(input.amountKes);
  if (amount <= 0) throw new ValidationError('Amount must be positive');

  return withTransaction((tx) => {
    const member = tx
      .prepare(
        `SELECT id, user_id FROM chama_members WHERE id = ? AND chama_id = ? AND is_active = 1`
      )
      .get(input.memberId, input.chamaId) as { id: string; user_id: string } | undefined;
    if (!member) throw new NotFoundError('Member not found in this Chama');

    const id = generateId();
    const ledgerId = generateId();
    const now = nowIso();
    const date = input.contributionDate || now.slice(0, 10);

    tx.prepare(
      `INSERT INTO contributions (id, chama_id, member_id, user_id, amount_kes, contribution_date, payment_method, reference, status, notes, created_by, is_test_data, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?, ?, 0, ?)`
    ).run(
      id,
      input.chamaId,
      member.id,
      member.user_id,
      amount,
      date,
      input.paymentMethod || null,
      input.reference || null,
      input.notes || null,
      input.createdBy,
      now
    );

    tx.prepare(
      `INSERT INTO ledger_transactions (id, chama_id, type, amount_kes, reference_type, reference_id, description, created_by, is_test_data, created_at)
       VALUES (?, ?, 'CONTRIBUTION', ?, 'contribution', ?, ?, ?, 0, ?)`
    ).run(ledgerId, input.chamaId, amount, id, `Contribution ${amount} KES`, input.createdBy, now);

    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, ?, 'CONTRIBUTION_CREATE', 'contribution', ?, ?, ?, ?)`
    ).run(generateId(), input.createdBy, id, input.chamaId, JSON.stringify({ amount }), now);

    return { contributionId: id, ledgerId, amountKes: amount };
  });
}

export function listContributions(chamaId: string, limit = 50) {
  const db = getDb();
  return db
    .prepare(
      `SELECT c.*, u.full_name as member_name
       FROM contributions c
       JOIN users u ON u.id = c.user_id
       WHERE c.chama_id = ?
       ORDER BY c.contribution_date DESC, c.created_at DESC
       LIMIT ?`
    )
    .all(chamaId, limit)
    .map((r: any) => ({
      id: String(r.id),
      amount_kes: Number(r.amount_kes),
      contribution_date: String(r.contribution_date),
      member_name: String(r.member_name),
      payment_method: r.payment_method ? String(r.payment_method) : null,
      reference: r.reference ? String(r.reference) : null,
      status: String(r.status),
    }));
}

export function getSavingsTotal(chamaId: string): number {
  const db = getDb();
  const row = db
    .prepare(
      `SELECT COALESCE(SUM(amount_kes), 0) as total FROM ledger_transactions
       WHERE chama_id = ? AND type IN ('CONTRIBUTION','OTHER_INCOME','FINE_PAYMENT','LOAN_REPAYMENT')`
    )
    .get(chamaId) as { total: number };
  const out = db
    .prepare(
      `SELECT COALESCE(SUM(ABS(amount_kes)), 0) as total FROM ledger_transactions
       WHERE chama_id = ? AND type IN ('LOAN_DISBURSEMENT','EXPENSE')`
    )
    .get(chamaId) as { total: number };
  // Net: contributions etc minus disbursements/expenses — simple chama pool view
  const credits = Number(row.total) || 0;
  const debits = Number(out.total) || 0;
  return credits - debits;
}

export function getContributionTotal(chamaId: string): number {
  const db = getDb();
  const row = db
    .prepare(`SELECT COALESCE(SUM(amount_kes), 0) as t FROM contributions WHERE chama_id = ?`)
    .get(chamaId) as { t: number };
  return Number(row.t) || 0;
}

export function listLedger(chamaId: string, limit = 50) {
  const db = getDb();
  return db
    .prepare(
      `SELECT * FROM ledger_transactions WHERE chama_id = ? ORDER BY created_at DESC LIMIT ?`
    )
    .all(chamaId, limit)
    .map((r: any) => ({
      id: String(r.id),
      type: String(r.type),
      amount_kes: Number(r.amount_kes),
      description: r.description ? String(r.description) : null,
      created_at: String(r.created_at),
    }));
}
