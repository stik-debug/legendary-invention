# ChamaPay Kenya (stage 3)

A multi-tenant SaaS for Kenyan chamas. **Stage 1 (billing, security, Control Center, 3D interface), stage 2 (contributions, ledger, loans, fines, statements, chat) and stage 3 (meetings, attendance, announcements, notifications, reports, owner two-factor login, test-data commands) are built.** See "What is and is not built" below. Please read it.

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
| Contributions | Built, tested | Treasurer/chairperson records them. Duplicate M-Pesa codes refused. Mistakes are cancelled, never deleted |
| Financial ledger | Built, tested | One row per cash movement, written in the same transaction. Cash balance can never go negative. CSV export |
| Loans and repayments | Built, tested | Limit = savings x multiple. Flat interest. Approver cannot be the borrower. Cannot lend more cash than the chama has |
| Fines | Built, tested | Unpaid / part paid / paid / waived. Payments go to the ledger |
| Member statements | Built, tested | Members see their own. Officials see anyone's. CSV download |
| In-app chat | Built, tested | One room per chama, unread badge, auto-refresh every 10 seconds, XSS-safe |
| Meetings and attendance | Built, tested | Officers (chairperson, secretary) schedule meetings and mark present / absent / apology. Optional absence fine is created once per absent member. Minutes, cancel. Every member can read |
| Announcements (notice board) | Built, tested | Chairperson/secretary post and pin. Members read. Removed notices are kept in the database |
| In-app notifications (the Alerts bell) | Built, tested | Contributions, loans, fines, notices and meetings alert the right people. Nobody is alerted about their own action. Stays inside the app |
| Reports and CSV export | Built, tested | Officials only: cash, savings, loans out, fines owed, who has not paid, collections by month, attendance. CSV for members, contributions, loans, fines, attendance (plus the ledger). Cells are spreadsheet-formula safe |
| Email and SMS | NOT built | Shown as NOT BUILT YET, never faked |
| Test-data commands (seed/reset/validate) | Built, tested | `flask --app app seed`, `reset-test-data`, `validate`. See "Test-data commands" below |
| Two-factor login for owner | Built, tested | Authenticator app (TOTP) plus 8 one-time recovery codes. Optional `REQUIRE_OWNER_2FA=1`. See "Owner two-factor login" below |

## Tests (144 automated, all passing when this was packaged)

    python -m unittest discover -s tests -v

`test_services.py` covers plan limits, the subscription timeline, payments and idempotency. `test_finance.py` covers the money rules, including a KES 10,000 loan with KES 1,000 interest repaid in two parts. `test_web_finance.py` covers roles, privacy, tenant isolation and the full loan, fine and chat journeys over HTTP. `test_web.py` covers login, CSRF, authorization, tenant isolation, suspension and the owner screens over real HTTP. `test_community.py` covers meetings, attendance fines, notices, reports, CSV safety, notifications and tenant isolation of every new page. `test_security.py` covers TOTP (including the RFC 6238 test vector), replay, recovery codes, rate limiting and the require-2FA switch. `test_devtools.py` covers seed, reset and validate, including a deliberately corrupted database.
Not covered here: a real PostgreSQL run, a real M-Pesa payment, real phones. You must test those.

## Test-data commands

Run these on your own computer or a staging database, never on real customers' data.

    flask --app app seed                 # 2 demo chamas (13 people) with contributions, a loan, fines, meetings, notices and chat
    flask --app app validate             # checks the whole database; exit code 1 if any money or membership rule is broken
    flask --app app reset-test-data      # dry run: shows what would be deleted
    flask --app app reset-test-data --yes

- Everything seeded is flagged `is_test_data=1` and named "(TEST DATA)". Every demo account uses the password `DemoPass123!` (emails like `demo.001@chamapay.test`; the command prints them all).
- `seed` refuses to run when the app is in production unless you add `--yes-production`.
- `reset-test-data` deletes only flagged chamas and flagged users who belong to no real chama. It never touches real chamas, real users or the owner. A flagged user who also joined a real chama is kept.
- `validate` checks: every chama has an administrator and exactly one subscription; members within the plan limit; cash never negative; the ledger agrees with contributions, loan repayments, fine payments and loans paid out; loan and fine balances agree with their payment rows; payment flags are consistent. Run it before and after anything risky, and on your real database from time to time.

## Owner two-factor login

1. Log in as owner, open **Control Center > Security**, choose *Set up two-factor login*.
2. In Google Authenticator, Microsoft Authenticator or Authy add the setup key (time-based, 6 digits). Enter the current code to turn it on.
3. Save the 8 recovery codes shown once. Each works one time.
4. From then on, the password alone does not log the owner in. Codes cannot be reused, and wrong codes are rate limited.
- `REQUIRE_OWNER_2FA=1` forces the owner to set it up before using any Control Center page, and stops it being switched off from the screen.
- Lost phone and recovery codes: set `OWNER_RESET_2FA=1` on the server and restart once. It switches 2FA off for `OWNER_EMAIL`. Remove the variable straight after.
- Honest limit: the authenticator secret is stored in the database as-is (it has to be readable to check codes). Protect database access and backups.

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
- Turn on `REQUIRE_OWNER_2FA=1`, set up two-factor login, and keep the recovery codes offline.
- Run `flask --app app validate` on the real database after your click-through. It also warns if test data is still present.
- Notifications live only inside the app. Members who never open ChamaPay will not see them until email or SMS exists.

## How it works (for developers)

- `db.py` schema + tiny database layer (SQLite locally, PostgreSQL in production). Money is stored as integer cents.
- `finance.py` money rules, `community.py` meetings, attendance and announcements, `reports.py` officials' reports and CSV, `notify.py` in-app alerts, `twofactor.py` owner 2FA, `devtools.py` seed/reset/validate commands, `routes_finance.py`, `routes_community.py` and `routes_security.py` their screens.
- `services.py` all rules (limits, states, payments). `app.py` routes and authorization. `providers.py` Test and M-Pesa providers. The Test provider is disabled in production.
- Payments are only applied by `process_webhook`, guarded by unique constraints on webhook event and receipt, and an `applied` flag.
