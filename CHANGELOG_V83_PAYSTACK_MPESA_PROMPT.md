# V83 — Paystack M-Pesa prompt for subscription payments

## What changed (minimal, additive)

1. **`providers.py`**
   - Added `PaystackPaymentProvider` (M-Pesa mobile_money `/charge` → phone PIN prompt).
   - Left `TestPaymentProvider` and `MpesaPaymentProvider` (Daraja STK) unchanged.
   - `get_provider()` respects `PAYMENT_PROVIDER=paystack|mpesa|test`.

2. **`app.py`**
   - Flash message uses Paystack `display_text` when present.
   - New webhook: `POST /webhooks/paystack` (signature verified).
   - CSRF exempt only for that webhook endpoint.
   - No changes to finance, members, loans, or other modules.

## Configure on Render

```
PAYMENT_PROVIDER=paystack
PAYSTACK_SECRET_KEY=sk_live_...   # or sk_test_...
PAYSTACK_PUBLIC_KEY=pk_live_...
```

Paystack Dashboard → Webhook URL:

```
https://YOUR-DOMAIN/webhooks/paystack
```

Events: `charge.success`, `charge.failed`

## Behaviour

Admin/Treasurer posts to existing `/chamas/<id>/subscription/pay` with phone →
Paystack sends M-Pesa prompt → customer enters PIN → webhook confirms →
existing `process_webhook` activates subscription (unchanged).

## Rollback

Set `PAYMENT_PROVIDER=mpesa` (with Daraja keys) or remove Paystack keys and use test in non-production.
