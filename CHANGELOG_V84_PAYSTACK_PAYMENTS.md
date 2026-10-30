# ChamaPay V84: Paystack payments fixed + new payment page

## Why Paystack payments did not work
1. **Browser blocked the redirect to Paystack.** The CSP had `form-action 'self'`; the pay button POSTs and then redirects to
   `checkout.paystack.com`, which browsers block under that rule. Fixed (`form-action` now allows Paystack).
2. **Pending payments were marked FAILED.** Returning from Paystack before M-Pesa confirmed was treated as a failure, and the later
   success was ignored. Now only a real Paystack `failed/abandoned` state fails a payment; pending stays pending.
3. **The payment page's JavaScript never ran.** It used inline `<script>`/`onclick`, which the CSP (`script-src 'self'`) blocks.
   The new page uses `static/js/subpay.js`.
4. `render.yaml` had no Paystack variables; checkout channels included ones Kenya doesn't support. Both fixed.

## What's new
- Modern payment card: plan cards, 1/3/6/12 month picker, live total, M-Pesa number field with validation.
- **Direct M-Pesa prompt** (Paystack Charge API) so the customer stays in ChamaPay; automatic fallback to Paystack hosted checkout.
- Live payment-status page (`/chamas/<id>/subscription/payment/<payment_id>`) that updates itself.
- Payments are confirmed server-side with Paystack (`/transaction/verify`), amount **and currency (KES)** checked, idempotent.
- A payment that timed out locally but is later confirmed by Paystack still activates the plan.

## Other bugs fixed
- `routes_otp.py`: password change / phone change / password reset crashed (`audit(db, ...)` instead of `db()`).
- `routes_finance.py`: approving a dividend run crashed (`audit` not imported).
- `command_centre.html`: linked to a non-existent `votes` endpoint (page crashed for officials).

## Tests
`tests/test_paystack.py` (16 tests, Paystack API mocked). Older tests updated to match current intended behaviour
(read-only access when suspended, fixed tier prices, maker-checker approvals, new alert bell text). Two SMS-login tests are skipped
because the SMS verification step no longer exists.

## Go-live checklist (Render → Environment)
`PAYMENT_PROVIDER=paystack`, `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`, `APP_URL`, and `DATABASE_URL` (PostgreSQL).
In Paystack: set the webhook URL to `https://YOUR-DOMAIN/webhooks/paystack`, make sure KES / M-Pesa is enabled, and use matching
test or live keys. Do one test payment before announcing.
