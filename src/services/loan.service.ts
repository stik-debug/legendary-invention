import { getDb, generateId, nowIso, withTransaction } from '../db/index.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../utils/errors.js';
import { toKesInteger, subtractMoney } from '../utils/money.js';

export function applyLoan(input: {
  chamaId: string;
  memberId: string;
  amountKes: number;
  purpose?: string;
  userId: string;
}) {
  const amount = toKesInteger(input.amountKes);
  if (amount <= 0) throw new ValidationError('Amount must be positive');
  const db = getDb();
  const member = db
    .prepare(`SELECT id, user_id FROM chama_members WHERE id = ? AND chama_id = ? AND is_active = 1`)
    .get(input.memberId, input.chamaId) as any;
  if (!member) throw new NotFoundError('Member not found');
  if (member.user_id !== input.userId) {
    // allow admin applying on behalf? for now member applies for self only via API role check
  }
  const id = generateId();
  const now = nowIso();
  db.prepare(
    `INSERT INTO loans (id, chama_id, member_id, user_id, amount_kes, interest_rate, outstanding_kes, status, purpose, applied_at, is_test_data, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, 0, ?, 'PENDING', ?, ?, 0, ?, ?)`
  ).run(id, input.chamaId, member.id, member.user_id, amount, amount, input.purpose || null, now, now, now);
  return { loanId: id };
}

export function approveLoan(input: { chamaId: string; loanId: string; approvedBy: string; approve: boolean }) {
  return withTransaction((tx) => {
    const loan = tx
      .prepare(`SELECT * FROM loans WHERE id = ? AND chama_id = ?`)
      .get(input.loanId, input.chamaId) as any;
    if (!loan) throw new NotFoundError('Loan not found');
    if (loan.status !== 'PENDING') throw new ValidationError('Loan is not pending');
    const now = nowIso();
    if (!input.approve) {
      tx.prepare(`UPDATE loans SET status = 'REJECTED', updated_at = ?, approved_by = ?, approved_at = ? WHERE id = ?`).run(
        now, input.approvedBy, now, input.loanId
      );
      return { status: 'REJECTED' };
    }
    tx.prepare(
      `UPDATE loans SET status = 'APPROVED', approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?`
    ).run(input.approvedBy, now, now, input.loanId);
    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, created_at)
       VALUES (?, ?, 'LOAN_APPROVE', 'loan', ?, ?, ?)`
    ).run(generateId(), input.approvedBy, input.loanId, input.chamaId, now);
    return { status: 'APPROVED' };
  });
}

export function disburseLoan(input: { chamaId: string; loanId: string; by: string }) {
  return withTransaction((tx) => {
    const loan = tx.prepare(`SELECT * FROM loans WHERE id = ? AND chama_id = ?`).get(input.loanId, input.chamaId) as any;
    if (!loan) throw new NotFoundError('Loan not found');
    if (loan.status !== 'APPROVED') throw new ValidationError('Loan must be approved before disbursement');
    const now = nowIso();
    tx.prepare(
      `UPDATE loans SET status = 'DISBURSED', disbursed_at = ?, outstanding_kes = ?, updated_at = ? WHERE id = ?`
    ).run(now, loan.amount_kes, now, input.loanId);
    tx.prepare(
      `INSERT INTO ledger_transactions (id, chama_id, type, amount_kes, reference_type, reference_id, description, created_by, is_test_data, created_at)
       VALUES (?, ?, 'LOAN_DISBURSEMENT', ?, 'loan', ?, ?, ?, 0, ?)`
    ).run(generateId(), input.chamaId, -Number(loan.amount_kes), input.loanId, `Loan disbursement`, input.by, now);
    return { status: 'DISBURSED' };
  });
}

export function repayLoan(input: {
  chamaId: string;
  loanId: string;
  amountKes: number;
  by: string;
  paymentMethod?: string;
  reference?: string;
}) {
  const amount = toKesInteger(input.amountKes);
  if (amount <= 0) throw new ValidationError('Amount must be positive');
  return withTransaction((tx) => {
    const loan = tx.prepare(`SELECT * FROM loans WHERE id = ? AND chama_id = ?`).get(input.loanId, input.chamaId) as any;
    if (!loan) throw new NotFoundError('Loan not found');
    if (!['DISBURSED', 'REPAYING'].includes(loan.status)) {
      throw new ValidationError('Loan is not in a repayable state');
    }
    const outstanding = Number(loan.outstanding_kes);
    if (amount > outstanding) throw new ValidationError(`Repayment exceeds outstanding (${outstanding})`);
    const newOut = subtractMoney(outstanding, amount);
    const now = nowIso();
    const repayId = generateId();
    tx.prepare(
      `INSERT INTO loan_repayments (id, loan_id, chama_id, amount_kes, payment_method, reference, created_by, is_test_data, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)`
    ).run(repayId, input.loanId, input.chamaId, amount, input.paymentMethod || null, input.reference || null, input.by, now);
    const status = newOut === 0 ? 'COMPLETED' : 'REPAYING';
    tx.prepare(`UPDATE loans SET outstanding_kes = ?, status = ?, updated_at = ? WHERE id = ?`).run(newOut, status, now, input.loanId);
    tx.prepare(
      `INSERT INTO ledger_transactions (id, chama_id, type, amount_kes, reference_type, reference_id, description, created_by, is_test_data, created_at)
       VALUES (?, ?, 'LOAN_REPAYMENT', ?, 'loan_repayment', ?, ?, ?, 0, ?)`
    ).run(generateId(), input.chamaId, amount, repayId, `Loan repayment`, input.by, now);
    return { repaymentId: repayId, outstandingKes: newOut, status };
  });
}

export function listLoans(chamaId: string) {
  const db = getDb();
  return db
    .prepare(
      `SELECT l.*, u.full_name as member_name FROM loans l
       JOIN users u ON u.id = l.user_id
       WHERE l.chama_id = ? ORDER BY l.created_at DESC`
    )
    .all(chamaId)
    .map((r: any) => ({
      id: String(r.id),
      amount_kes: Number(r.amount_kes),
      outstanding_kes: Number(r.outstanding_kes),
      status: String(r.status),
      purpose: r.purpose ? String(r.purpose) : null,
      member_name: String(r.member_name),
      member_id: String(r.member_id),
      applied_at: String(r.applied_at),
    }));
}

export function outstandingLoansTotal(chamaId: string): number {
  const db = getDb();
  const row = db
    .prepare(
      `SELECT COALESCE(SUM(outstanding_kes), 0) as t FROM loans
       WHERE chama_id = ? AND status IN ('DISBURSED','REPAYING')`
    )
    .get(chamaId) as { t: number };
  return Number(row.t) || 0;
}
