import { Router, Request, Response, NextFunction } from 'express';
import * as payment from '../services/payment.service.js';
import { getPaymentProvider } from '../providers/payment.provider.js';
import { requireAuth, AuthedRequest } from '../middleware/auth.js';

const router = Router();

/**
 * Generic callback (test provider + manual verify body).
 * Paystack webhooks should use /api/payments/paystack/webhook
 */
router.post('/callback', async (req, res, next) => {
  try {
    const data = await payment.handleCallback(req.body || {});
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

/**
 * Paystack webhook — verify HMAC signature when secret is set.
 * Configure in Paystack Dashboard → Settings → API Keys & Webhooks:
 *   https://YOUR_DOMAIN/api/payments/paystack/webhook
 * Events: charge.success, charge.failed, paymentrequest.success
 */
router.post('/paystack/webhook', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const provider = getPaymentProvider();
    if (provider.name !== 'paystack') {
      // Still accept if switched later; try handle anyway
    }
    const signature = String(req.headers['x-paystack-signature'] || '');
    const rawBody =
      typeof (req as any).rawBody === 'string'
        ? (req as any).rawBody
        : JSON.stringify(req.body || {});

    if (provider.verifyWebhookSignature && signature) {
      const ok = provider.verifyWebhookSignature(rawBody, signature);
      if (!ok) {
        return res.status(401).json({ success: false, error: 'Invalid signature' });
      }
    }

    const event = String((req.body as any)?.event || '');
    // Only process success / failure events
    if (event && !/success|failed|abandon/i.test(event)) {
      return res.json({ success: true, ignored: event });
    }

    const data = await payment.handleCallback(req.body || {});
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

/** Browser return URL after Paystack hosted checkout */
router.get('/paystack/return', async (req, res) => {
  const reference = String(req.query.reference || req.query.trxref || '');
  try {
    if (reference) {
      await payment.handleCallback({ reference, providerRef: reference });
    }
    res.type('html').send(`<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Payment</title>
      <link rel="stylesheet" href="/css/app.css"/></head><body class="auth-page">
      <div class="auth-card"><h1>Payment received</h1>
      <p class="sub">If payment succeeded, your Chama subscription is now active. You can close this tab.</p>
      <a class="btn btn-primary btn-block" href="/app/dashboard.html">Back to dashboard</a>
      <p class="auth-footer">Ref: ${reference || '—'}</p></div></body></html>`);
  } catch {
    res.type('html').send(`<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Payment</title>
      <link rel="stylesheet" href="/css/app.css"/></head><body class="auth-page">
      <div class="auth-card"><h1>Payment pending</h1>
      <p class="sub">We could not confirm yet. If you paid via M-Pesa, wait a moment then check your dashboard.</p>
      <a class="btn btn-primary btn-block" href="/app/ops.html">Back to billing</a></div></body></html>`);
  }
});

/** Client polls after STK prompt */
router.post('/verify/:paymentId', requireAuth, async (req: AuthedRequest, res, next) => {
  try {
    const data = await payment.verifyPaymentById(String(req.params.paymentId));
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

export default router;
