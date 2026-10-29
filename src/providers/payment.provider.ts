/**
 * Payment providers for ChamaPay subscriptions.
 * Primary production path: Paystack → M-Pesa STK / USSD prompt on customer phone.
 */

import crypto from 'crypto';

export type PaymentStatus = 'PENDING' | 'SUCCESS' | 'FAILED' | 'CANCELLED' | 'TIMEOUT';

export type ProviderResult = {
  status: PaymentStatus;
  providerRef: string;
  raw?: unknown;
  authorizationUrl?: string;
  accessCode?: string;
  /** Human message e.g. "Check your phone for M-Pesa prompt" */
  displayText?: string;
  /** True when an STK/USSD prompt was sent to the phone */
  promptSent?: boolean;
};

export type CallbackVerification = {
  valid: boolean;
  providerRef: string;
  amountKes: number;
  status: PaymentStatus;
  raw?: unknown;
};

export type InitiateInput = {
  amountKes: number;
  phone: string;
  email?: string;
  accountRef: string;
  description: string;
  callbackUrl?: string;
  metadata?: Record<string, unknown>;
};

export interface PaymentProvider {
  name: string;
  initiate(input: InitiateInput): Promise<ProviderResult>;
  verifyCallback(payload: Record<string, unknown>): Promise<CallbackVerification>;
  verifyWebhookSignature?(rawBody: string, signature: string): boolean;
}

export function normalizeKenyaPhone(phone: string): string {
  let p = String(phone || '')
    .replace(/\s+/g, '')
    .replace(/^\+/, '')
    .replace(/-/g, '');
  if (p.startsWith('0') && p.length === 10) p = '254' + p.slice(1);
  if ((p.startsWith('7') || p.startsWith('1')) && p.length === 9) p = '254' + p;
  return p;
}

export class TestPaymentProvider implements PaymentProvider {
  name = 'test';

  async initiate(input: InitiateInput): Promise<ProviderResult> {
    const ref = `TEST-PAYMENT-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
    return {
      status: 'PENDING',
      providerRef: ref,
      promptSent: true,
      displayText: 'TEST MODE: No real M-Pesa prompt. Use “Simulate test success” or verify endpoint.',
      raw: { phone: input.phone, amount: input.amountKes },
    };
  }

  async verifyCallback(payload: Record<string, unknown>): Promise<CallbackVerification> {
    const providerRef = String(payload.providerRef || payload.reference || '');
    const amountKes = Number(payload.amountKes || payload.amount || 0);
    const force = String(payload.result || 'SUCCESS').toUpperCase();
    if (!providerRef) return { valid: false, providerRef: '', amountKes: 0, status: 'FAILED' };
    if (force === 'FAILED') return { valid: true, providerRef, amountKes, status: 'FAILED' };
    if (force === 'TIMEOUT') return { valid: true, providerRef, amountKes, status: 'TIMEOUT' };
    if (force === 'CANCELLED') return { valid: true, providerRef, amountKes, status: 'CANCELLED' };
    return { valid: true, providerRef, amountKes, status: 'SUCCESS' };
  }
}

/**
 * Paystack Kenya — M-Pesa prompt via /charge mobile_money.
 * Customer enters M-Pesa PIN on their phone; we confirm via verify/webhook.
 */
export class PaystackPaymentProvider implements PaymentProvider {
  name = 'paystack';
  private secretKey: string;
  private baseUrl = 'https://api.paystack.co';

  constructor() {
    this.secretKey = process.env.PAYSTACK_SECRET_KEY || '';
    if (!this.secretKey) {
      throw new Error('PAYSTACK_SECRET_KEY is not configured. Set it on Render to accept M-Pesa payments.');
    }
  }

  private async paystackFetch(path: string, body?: Record<string, unknown>): Promise<any> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: body ? 'POST' : 'GET',
      headers: {
        Authorization: `Bearer ${this.secretKey}`,
        'Content-Type': 'application/json',
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status === false) {
      throw new Error(data.message || data.data?.message || `Paystack error ${res.status}`);
    }
    return data;
  }

  async initiate(input: InitiateInput): Promise<ProviderResult> {
    const amountSubunit = Math.round(Number(input.amountKes) * 100);
    const phone = normalizeKenyaPhone(input.phone);
    if (!/^254[17]\d{8}$/.test(phone)) {
      throw new Error('Use a valid Safaricom M-Pesa number: 2547XXXXXXXX');
    }
    const email = (input.email || `subscriber-${input.accountRef}@chamapay.app`).toLowerCase();
    const reference = `CP${input.accountRef}${Date.now()}`.replace(/[^a-zA-Z0-9]/g, '').slice(0, 50);

    // --- Primary path: M-Pesa STK / USSD prompt on handset ---
    const charge = await this.paystackFetch('/charge', {
      email,
      amount: amountSubunit,
      currency: 'KES',
      reference,
      mobile_money: {
        phone,
        provider: 'mpesa',
      },
      metadata: {
        custom_fields: [
          { display_name: 'Purpose', variable_name: 'purpose', value: input.description },
          { display_name: 'Chama', variable_name: 'account_ref', value: input.accountRef },
        ],
        ...(input.metadata || {}),
      },
    });

    const d = charge.data || {};
    const ref = String(d.reference || reference);
    const statusRaw = String(d.status || '').toLowerCase();
    const displayText =
      d.display_text ||
      d.gateway_response ||
      'A payment prompt has been sent to your phone. Enter your M-Pesa PIN to complete.';

    // Paystack sometimes returns pay_offline / send_otp / pending
    if (statusRaw === 'success') {
      return {
        status: 'SUCCESS',
        providerRef: ref,
        promptSent: true,
        displayText: 'Payment completed.',
        raw: d,
      };
    }

    return {
      status: 'PENDING',
      providerRef: ref,
      promptSent: true,
      displayText: String(displayText),
      accessCode: d.access_code ? String(d.access_code) : undefined,
      // Only include hosted URL if Paystack returned one (rare on pure mobile_money charge)
      authorizationUrl: d.authorization_url ? String(d.authorization_url) : undefined,
      raw: d,
    };
  }

  async verifyCallback(payload: Record<string, unknown>): Promise<CallbackVerification> {
    const data = (payload.data as Record<string, unknown>) || payload;
    const providerRef = String(data.reference || payload.providerRef || payload.reference || '');
    if (!providerRef) {
      return { valid: false, providerRef: '', amountKes: 0, status: 'FAILED' };
    }

    try {
      const verified = await this.paystackFetch(`/transaction/verify/${encodeURIComponent(providerRef)}`);
      const d = verified.data || {};
      const amountKes = Math.round(Number(d.amount || 0) / 100);
      const ps = String(d.status || '').toLowerCase();

      let status: PaymentStatus = 'PENDING';
      if (ps === 'success') status = 'SUCCESS';
      else if (ps === 'failed') status = 'FAILED';
      else if (ps === 'abandoned') status = 'CANCELLED';
      else if (ps === 'reversed') status = 'FAILED';

      return {
        valid: true,
        providerRef: String(d.reference || providerRef),
        amountKes,
        status,
        raw: d,
      };
    } catch {
      return { valid: false, providerRef, amountKes: 0, status: 'FAILED' };
    }
  }

  verifyWebhookSignature(rawBody: string, signature: string): boolean {
    if (!signature || !this.secretKey) return false;
    const hash = crypto.createHmac('sha512', this.secretKey).update(rawBody).digest('hex');
    try {
      return crypto.timingSafeEqual(Buffer.from(hash), Buffer.from(String(signature)));
    } catch {
      return hash === signature;
    }
  }
}

export class MpesaPaymentProvider implements PaymentProvider {
  name = 'mpesa';
  async initiate(): Promise<ProviderResult> {
    throw new Error('Use PAYMENT_PROVIDER=paystack for M-Pesa prompts via Paystack');
  }
  async verifyCallback(): Promise<CallbackVerification> {
    return { valid: false, providerRef: '', amountKes: 0, status: 'FAILED' };
  }
}

export function getPaymentProvider(): PaymentProvider {
  const mode = (process.env.PAYMENT_PROVIDER || 'test').toLowerCase();
  if (mode === 'paystack') return new PaystackPaymentProvider();
  if (mode === 'mpesa') return new MpesaPaymentProvider();
  return new TestPaymentProvider();
}
