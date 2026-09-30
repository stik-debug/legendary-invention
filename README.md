# ChamaPay Kenya (fresh build, stage 1)

A multi-tenant SaaS for Kenyan chamas. **Stage 1 is the money-and-security foundation plus the full Control Center and a 3D interface.** See "What is and is not built" below. Please read it.

## What is and is not built

| Feature | Status | Notes |
|---|---|---|
| Registration, login, logout | Built, tested | Hashed passwords, CSRF, rate limiting, secure cookies |
| Password reset | NOT built | Needs email/SMS. Owner can be recovered by changing env vars |
| Roles (SUPER_ADMIN, CHAMA_ADMIN, TREASURER, SECRETARY, MEMBER) | Built, tested | Enforced on the server for every route |
| Multi-tenant isolation | Built, tested | Chama A user gets 403 on Chama B pages and actions |
| Chama create, members add/remove | Built, tested | Add by name + phone number. New people get an 8-digit join code to claim their account. Removal keeps history |
| Invitations by link | NOT built | Stage 2 |
| Plans Starter 500/15, Growth 1,500/70, Business 2,000/100 | Built, tested | In the database, editable by the owner. Per chama, never per member |
| Member limit (16th, 71st, 101st rejected) | Built, tested | Enforced in the backend, race-safe on PostgreSQL |
| Upgrade / downgrade | Built, tested | Upgrade = pay. Downgrade blocked if members do not fit. Nobody is auto-removed |
| Subscription states TRIAL, ACTIVE, PAST_DUE, GRACE_PERIOD, SUSPENDED, CANCELLED | Built, tested | 7-day trial and 3-day grace, both editable |
| Suspension keeps all data, payment auto-reactivates | Built, tested | |
| Payments: pending/success/failed/cancelled/timeout | Built, tested | Verified server-side only |
| Duplicate webhook protection | Built, tested | Payment, subscription extension and audit each happen once |
| Forged / wrong-amount callbacks | Built, tested | Ignored or rejected |
| M-Pesa STK push | Built, NOT tested live | Needs your Daraja credentials. Shows CONFIGURATION REQUIRED until set |
| Control Center (stats, search, suspend, reactivate, extend, manual payment, plans, audit) | Built, tested | All numbers come from the database |
| Audit log | Built, tested | Append-only, no edit/delete screens |
| Contributions, ledger, loans, repayments, fines | NOT built | Stage 2 |
| Meetings, attendance, announcements, messaging, notifications | NOT built | Stage 3 |
| Reports and CSV export | NOT built | Stage 3 |
| Email and SMS | NOT built | Shown as NOT BUILT YET, never faked |
| Test-data commands (seed/reset/validate) | NOT built | Stage 4. Automated tests use their own throwaway database |
| Two-factor login for owner | NOT built | Use a strong password meanwhile |

## Tests (59 automated, all passing when this was packaged)

    python -m unittest discover -s tests -v

`test_services.py` covers plan limits, the subscription timeline, payments and idempotency. `test_web.py` covers login, CSRF, authorization, tenant isolation, suspension and the owner screens over real HTTP.
Not covered here: a real PostgreSQL run, a real M-Pesa payment, real phones. You must test those.

## Deploy on Render

1. Create a **PostgreSQL** database. Copy its **Internal Database URL**.
2. Create a **Web Service** from this folder. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app --workers 2 --threads 4 --timeout 60`.
3. Set environment variables from `.env.example`. At minimum: `AUTH_SECRET`, `DATABASE_URL`, `OWNER_EMAIL`, `OWNER_PHONE`, `OWNER_PASSWORD`, `PAY_INSTRUCTIONS`.
4. Health check path: `/healthz`.
5. Log in with the owner email and password. The Control Center opens. The OWNER_* settings are checked on every start: if that email already has an account it is promoted to owner, and changing OWNER_PASSWORD resets the owner password (this is your recovery method). Check the deploy logs for a line starting with `OWNER SETUP:` to see what happened.
6. Optional but recommended: add a Render **Cron Job** running `flask --app app sweep` every hour. (Statuses also update whenever anyone opens a chama.)

The app refuses to start in production without a 32+ character `AUTH_SECRET`. On the free PostgreSQL plan, Render may expire the database; use a paid plan and backups before real customers.

## Before taking real customers

- PostgreSQL on a paid plan with backups. I could not run PostgreSQL where this was built; the SQL is written to work on both, but do a full click-through on your real database first.
- Your Daraja credentials and callback URL, tested in sandbox first.
- Register with the Office of the Data Protection Commissioner if required, and publish a privacy policy.
- Have a few real chamas try it with small amounts.

## How it works (for developers)

- `db.py` schema + tiny database layer (SQLite locally, PostgreSQL in production). Money is stored as integer cents.
- `services.py` all rules (limits, states, payments). `app.py` routes and authorization. `providers.py` Test and M-Pesa providers. The Test provider is disabled in production.
- Payments are only applied by `process_webhook`, guarded by unique constraints on webhook event and receipt, and an `applied` flag.
