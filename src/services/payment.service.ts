/**
 * Subscription payments — server-side verification, idempotent webhooks.
 * Supports Test + Paystack (M-Pesa via Paystack mobile_money).
 */
import { getDb, generateId, nowIso, withTransaction } from '../db/index.js';
import { NotFoundError, PaymentError } from '../utils/errors.js';
import { getPaymentProvider } from '../providers/payment.provider.js';
import { toKesInteger } from '../utils/money.js';

function normalizeKenyaPhone(phone: string): string {
  let p = phone.replace(/\s+/g, '').replace(/^\+/, '');
  if (p.startsWith('0')) p = '254' + p.slice(1);
  if (p.startsWith('7') || p.startsWith('1')) p = '254' + p;
  return p;
}

export async function initiateSubscriptionPayment(input: {
  chamaId: string;
  phone: string;
  userId: string;
  email?: string;
}) {
  const db = getDb();
  const sub = db
    .prepare(
      `SELECT s.*, p.price_kes, p.code as plan_code FROM subscriptions s
       JOIN subscription_plans p ON p.id = s.plan_id WHERE s.chama_id = ?`
    )
    .get(input.chamaId) as any;
  if (!sub) throw new NotFoundError('Subscription not found');

  const user = db.prepare(`SELECT email, phone FROM users WHERE id = ?`).get(input.userId) as any;
  const phone = normalizeKenyaPhone(input.phone || user?.phone || '');
  if (!/^254[17]\d{8}$/.test(phone)) {
    throw new PaymentError('Valid Kenyan M-Pesa phone required (2547XXXXXXXX)', 'INVALID_PHONE');
  }

  const amount = toKesInteger(sub.price_kes);
  const provider = getPaymentProvider();
  const idempotencyKey = generateId();
  const paymentId = generateId();
  const now = nowIso();
  const appUrl = process.env.APP_URL || process.env.RENDER_EXTERNAL_URL || '';
  const callbackUrl =
    process.env.PAYSTACK_CALLBACK_URL ||
    (appUrl ? `${appUrl.replace(/\/$/, '')}/api/payments/paystack/return` : undefined);

  const init = await provider.initiate({
    amountKes: amount,
    phone,
    email: input.email || user?.email,
    accountRef: input.chamaId.replace(/-/g, '').slice(0, 12),
    description: `ChamaPay ${sub.plan_code} subscription`,
    callbackUrl,
    metadata: {
      chama_id: input.chamaId,
      subscription_id: sub.id,
      payment_id: paymentId,
      plan: sub.plan_code,
    },
  });

  const initialStatus = init.status === 'SUCCESS' ? 'SUCCESS' : 'PENDING';
  db.prepare(
    `INSERT INTO payments (id, chama_id, subscription_id, amount_kes, currency, status, provider, provider_ref, phone, idempotency_key, metadata, verified_at, is_test_data, created_at, updated_at)
     VALUES (?, ?, ?, ?, 'KES', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`
  ).run(
    paymentId,
    input.chamaId,
    sub.id,
    amount,
    initialStatus,
    provider.name,
    init.providerRef,
    phone,
    idempotencyKey,
    JSON.stringify({
      plan: sub.plan_code,
      authorizationUrl: init.authorizationUrl || null,
      accessCode: init.accessCode || null,
      promptSent: init.promptSent || false,
    }),
    initialStatus === 'SUCCESS' ? now : null,
    now,
    now
  );

  if (initialStatus === 'SUCCESS') {
    const periodEnd = new Date();
    periodEnd.setDate(periodEnd.getDate() + 30);
    const endStr = periodEnd.toISOString().replace('T', ' ').substring(0, 19);
    db.prepare(
      `UPDATE subscriptions SET status = 'ACTIVE', current_period_start = ?, current_period_end = ?,
       grace_ends_at = NULL, suspended_at = NULL, suspension_reason = NULL, updated_at = ? WHERE id = ?`
    ).run(now, endStr, now, sub.id);
  }

  return {
    paymentId,
    providerRef: init.providerRef,
    amountKes: amount,
    status: init.status === 'SUCCESS' ? ('SUCCESS' as const) : ('PENDING' as const),
    provider: provider.name,
    authorizationUrl: init.authorizationUrl,
    accessCode: init.accessCode,
    promptSent: Boolean(init.promptSent),
    displayText:
      init.displayText ||
      (provider.name === 'paystack'
        ? 'Check your phone and enter your M-Pesa PIN to complete payment.'
        : 'Payment pending verification.'),
    message:
      init.displayText ||
      (provider.name === 'paystack'
        ? 'M-Pesa prompt sent. Enter PIN on your phone.'
        : 'Test payment pending verification.'),
    testCallbackHint:
      provider.name === 'test'
        ? {
            path: '/api/payments/callback',
            body: { providerRef: init.providerRef, amountKes: amount, result: 'SUCCESS' },
          }
        : undefined,
  };
}

/**
 * Process verified callback/webhook. Idempotent on provider + provider_ref.
 */
export async function handleCallback(payload: Record<string, unknown>) {
  const provider = getPaymentProvider();
  const verified = await provider.verifyCallback(payload);
  if (!verified.valid || !verified.providerRef) {
    throw new PaymentError('Invalid payment callback', 'INVALID_CALLBACK');
  }

  return withTransaction((tx) => {
    const existingWh = tx
      .prepare(`SELECT id, processed FROM payment_webhooks WHERE provider = ? AND provider_ref = ?`)
      .get(provider.name, verified.providerRef) as any;

    if (existingWh?.processed) {
      return { duplicate: true, message: 'Already processed', status: verified.status };
    }

    const whId = existingWh?.id || generateId();
    if (!existingWh) {
      tx.prepare(
        `INSERT INTO payment_webhooks (id, provider, provider_ref, payload, processed, created_at)
         VALUES (?, ?, ?, ?, 0, ?)`
      ).run(whId, provider.name, verified.providerRef, JSON.stringify(payload), nowIso());
    }

    const payment = tx
      .prepare(`SELECT * FROM payments WHERE provider = ? AND provider_ref = ?`)
      .get(provider.name, verified.providerRef) as any;

    // Paystack may create ref before our row matches — also try by reference in metadata path
    let pay = payment;
    if (!pay) {
      pay = tx
        .prepare(`SELECT * FROM payments WHERE provider_ref = ?`)
        .get(verified.providerRef) as any;
    }

    if (!pay) {
      tx.prepare(`UPDATE payment_webhooks SET processed = 1, processing_result = ? WHERE id = ?`).run(
        'PAYMENT_NOT_FOUND',
        whId
      );
      throw new NotFoundError('Payment not found for provider reference');
    }

    if (pay.status === 'SUCCESS') {
      tx.prepare(`UPDATE payment_webhooks SET processed = 1, processing_result = ? WHERE id = ?`).run(
        'ALREADY_SUCCESS',
        whId
      );
      return { duplicate: true, paymentId: pay.id, status: 'SUCCESS' };
    }

    if (verified.status !== 'SUCCESS') {
      tx.prepare(`UPDATE payments SET status = ?, updated_at = ? WHERE id = ?`).run(
        verified.status,
        nowIso(),
        pay.id
      );
      tx.prepare(`UPDATE payment_webhooks SET processed = 1, processing_result = ? WHERE id = ?`).run(
        verified.status,
        whId
      );
      return { paymentId: pay.id, status: verified.status, duplicate: false };
    }

    // Amount must match (allow 0 only if verify failed to parse — still reject mismatch)
    if (verified.amountKes > 0 && Number(verified.amountKes) !== Number(pay.amount_kes)) {
      tx.prepare(`UPDATE payment_webhooks SET processed = 1, processing_result = ? WHERE id = ?`).run(
        'AMOUNT_MISMATCH',
        whId
      );
      throw new PaymentError(
        `Amount mismatch: expected ${pay.amount_kes}, got ${verified.amountKes}`,
        'AMOUNT_MISMATCH'
      );
    }

    const now = nowIso();
    tx.prepare(`UPDATE payments SET status = 'SUCCESS', verified_at = ?, updated_at = ? WHERE id = ?`).run(
      now,
      now,
      pay.id
    );

    const sub = tx.prepare(`SELECT * FROM subscriptions WHERE id = ?`).get(pay.subscription_id) as any;
    if (sub) {
      const periodEnd = new Date();
      periodEnd.setDate(periodEnd.getDate() + 30);
      const endStr = periodEnd.toISOString().replace('T', ' ').substring(0, 19);
      tx.prepare(
        `UPDATE subscriptions SET status = 'ACTIVE', current_period_start = ?, current_period_end = ?,
         grace_ends_at = NULL, suspended_at = NULL, suspension_reason = NULL, updated_at = ?
         WHERE id = ?`
      ).run(now, endStr, now, sub.id);
    }

    tx.prepare(
      `INSERT INTO ledger_transactions (id, chama_id, type, amount_kes, reference_type, reference_id, description, created_by, is_test_data, created_at)
       VALUES (?, ?, 'SUBSCRIPTION_PAYMENT', ?, 'payment', ?, ?, NULL, 0, ?)`
    ).run(generateId(), pay.chama_id, pay.amount_kes, pay.id, 'Subscription payment (Paystack/M-Pesa)', now);

    tx.prepare(
      `INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, chama_id, metadata, created_at)
       VALUES (?, NULL, 'PAYMENT_SUCCESS', 'payment', ?, ?, ?, ?)`
    ).run(generateId(), pay.id, pay.chama_id, JSON.stringify({ amount: pay.amount_kes, provider: provider.name }), now);

    tx.prepare(`UPDATE payment_webhooks SET processed = 1, processing_result = ? WHERE id = ?`).run('SUCCESS', whId);

    return { paymentId: pay.id, status: 'SUCCESS', duplicate: false };
  });
}

/** Poll Paystack (or test) for a pending payment by our payment id */
export async function verifyPaymentById(paymentId: string) {
  const db = getDb();
  const pay = db.prepare(`SELECT * FROM payments WHERE id = ?`).get(paymentId) as any;
  if (!pay) throw new NotFoundError('Payment not found');
  if (pay.status === 'SUCCESS') return { paymentId, status: 'SUCCESS', already: true };

  const result = await handleCallback({
    providerRef: pay.provider_ref,
    reference: pay.provider_ref,
    amountKes: pay.amount_kes,
  });
  return result;
}

export function revenueTotal(): number {
  const db = getDb();
  const row = db
    .prepare(`SELECT COALESCE(SUM(amount_kes), 0) as t FROM payments WHERE status = 'SUCCESS'`)
    .get() as { t: number };
  return Number(row.t) || 0;
}
