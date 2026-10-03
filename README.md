# ChamaPay Kenya (stage 5: online meetings + in-app chama payments)

A multi-tenant SaaS for Kenyan chamas. **Stage 1 (billing, security, Control Center, 3D interface), stage 2 (contributions, ledger, loans, fines, statements, chat) and stage 3 (meetings, attendance, announcements, notifications, reports, owner two-factor login, test-data commands) are built.** See "What is and is not built" below. Please read it.

## What is and is not built

| Feature | Status | Notes |
|---|---|---|
| Registration, login, logout | Built, tested | Hashed passwords, CSRF, rate limiting, secure cookies |
| Password reset (no SMS or email) | Built, tested | A trusted person issues a one-time 8-digit code (like the join code); the member enters it on **Forgot password**. Chairperson can do it for ordinary members who belong to one chama only; the ChamaPay owner can do it for anyone (Control Center > Users). Code works once, expires in 60 minutes, locks after 5 wrong tries, is stored hashed, and logs the account out everywhere else. The owner account itself is recovered with env vars |
| Merry-go-round (rotating savings) | Built, tested | Each round has an order (random draw or typed), an amount and weekly/monthly turns. Everyone except that turn's recipient pays in; when all have paid, an official pays the pot to the recipient (never to themselves). **The pot is a separate money account** (`MGR` in the ledger): loans, expenses and "cash in hand" only look at the main account, so the pot can never be lent or spent. "Your turn to pay" alerts, reminders (max one per 12 hours), cancel, reports and CSV. See the section below |
| Online meetings | Built, tested | When scheduling, choose *In person*, *Online with a free video room* (Jitsi, an unguessable room name is made for you) or *Online with my own link* (Zoom, Google Meet, Teams: https only). Members of that chama only see a **Join** button from 30 minutes before the start until 4 hours after. Opening it marks the member **Joined online** on the attendance sheet as a hint: nobody is marked present automatically, an official still confirms (so absence fines stay fair) |
| Paying the chama inside the app | Built, tested (simulated payments) | **Automatic:** the chama brings its **own** Daraja paybill; a member taps Pay, gets the M-Pesa PIN prompt, and the contribution / fine / loan repayment is recorded when Safaricom confirms (verified, once only, wrong amounts rejected). **Manual:** the member pays the chama's paybill/till/phone, enters the M-Pesa code, and another official approves it. Same finance rules and ledger as a treasurer entry. ChamaPay never holds chama money |
| Real M-Pesa for chama payments | Built, NOT tested live | Needs the chama's own Daraja credentials and `CHAMA_SECRETS_KEY` on the server. Until then it runs in simulation (dev) or shows as not set up |
| Roles (SUPER_ADMIN, CHAMA_ADMIN, TREASURER, SECRETARY, MEMBER) | Built, tested | Enforced on the server for every route |
| Multi-tenant isolation | Built, tested | Chama A user gets 403 on Chama B pages and actions |
| Chama create, members add/remove | Built, tested | Add by name + phone number. New people get an 8-digit join code to claim their account. Removal keeps history |
| Invitations by link | NOT built | Join codes work today (admin adds a phone number, gives the person an 8-digit code) |
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

## Online meetings

Officials schedule a meeting and pick how it is held. Jitsi needs no keys: the app creates `https://meet.jit.si/ChamaPay-<random>` (set `JITSI_BASE` to use your own Jitsi server). The room opens in a new tab, because public Jitsi limits embedding it inside other sites. On meet.jit.si the first person to start the room may need to sign in as moderator, so an official should open it first. For chamas that already use Zoom or Google Meet, paste that link instead. The link is only shown to active members of that chama, and only near the meeting time.

## Paying inside the app

1. **Chairperson: Pay > Payment settings.** Choose *Members pay the chama, then send me the M-Pesa code* (works for everyone, no Safaricom account) or *Automatic* (needs the chama's own Daraja paybill: consumer key, secret, passkey). Keys are stored encrypted and never shown again.
2. **Member: Pay.** Sees this month's contribution, own fines and own loans. Automatic: enter the amount, get the PIN prompt, the page updates itself. Manual: enter the M-Pesa code from the SMS.
3. **Officials: Pay > Waiting for you.** Approve (after checking the M-Pesa statement) or reject with a reason. You cannot approve your own payment. If money arrives by M-Pesa but cannot be recorded automatically (for example the fine was already settled), it lands here as *Review*.
- A payment is only recorded after M-Pesa confirms it, with the amount checked. Repeated callbacks do nothing. "Ask M-Pesa now" covers a lost callback and never invents a result.
- Server settings: `CHAMA_SECRETS_KEY` (32+ random characters; protects the stored keys and the per-chama callback URL), optional `PUBLIC_URL` (the https address Safaricom calls back; defaults to the request's address). Callback URL per chama: `/webhooks/chama-mpesa/<chama id>/<secret>` (built automatically).
- Limits: paybill only for automatic payments (tills work in manual mode); M-Pesa's KES 250,000 per transaction.

## Tests (201 automated, all passing on SQLite; the web-level ones (including the new payment and online-meeting tests) also pass on PostgreSQL)

    python -m unittest discover -s tests -v

`test_services.py` covers plan limits, the subscription timeline, payments and idempotency. `test_finance.py` covers the money rules, including a KES 10,000 loan with KES 1,000 interest repaid in two parts. `test_web_finance.py` covers roles, privacy, tenant isolation and the full loan, fine and chat journeys over HTTP. `test_web.py` covers login, CSRF, authorization, tenant isolation, suspension and the owner screens over real HTTP. `test_community.py` covers meetings, attendance fines, notices, reports, CSV safety, notifications and tenant isolation of every new page. `test_security.py` covers TOTP (including the RFC 6238 test vector), replay, recovery codes, rate limiting and the require-2FA switch. `test_devtools.py` covers seed, reset and validate, including a deliberately corrupted database.
`test_recovery.py` covers password reset (who may issue codes, expiry, lockout, single use, other sessions logged out). `test_mgr.py` covers the merry-go-round: a full rotation to completion, the pot never being lent or spent, payout rules, reminders, cancellation, isolation between chamas, and `validate` catching a tampered pot. `test_upgrade.py` covers healing an old database.

Run the same web tests on PostgreSQL (each test gets its own schema, nothing is left behind):

    TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/testdb python -m unittest tests.test_web tests.test_web_finance tests.test_community tests.test_security tests.test_devtools tests.test_recovery tests.test_mgr tests.test_upgrade

Not covered here: a real M-Pesa payment and real phones. You must test those.

## Merry-go-round

1. Chairperson or treasurer: **Merry-go-round > Start**. Choose the members (at least 3), the amount each pays per turn, weekly or monthly, the date of the first turn, and the order (fair random draw by the system, or typed turn numbers).
2. Each turn: everyone except that turn's recipient pays the amount. The treasurer records each payment with its M-Pesa code. Members see who has paid and "Your turn to pay" alerts. Officials can send a reminder (one per 12 hours).
3. When everyone has paid, an official pays the whole pot to the recipient and records it. A recipient cannot pay out their own turn: another official must. The next turn then starts and the next payers are alerted.
4. After the last turn the round is complete. Mistakes are cancelled with a reason, never deleted, and only before that turn is paid out.
- The pot is its own account. It does not appear in the Ledger page, cannot be lent, and cannot be spent. `validate` checks that the pot always equals the payments waiting for payout.
- Honest limit: ChamaPay records the money, it does not move it. Members still send the money by M-Pesa or cash as your chama agreed.
- If someone has not paid, the payout waits. The officials decide what to do (remind, fine, or cancel the round).

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

## If the site shows an error after a deploy

- Open **Render > your service > Logs** and look at the first red line. That line is the real cause; send it to whoever is helping you.
- A fresh deploy used to be able to crash when both server workers created the tables at the same moment (PostgreSQL then reports `pg_type_typname_nsp_index`). That is fixed: setup now takes a lock.
- Upgrading is automatic: on every start the app adds any column an older database lacks. It never deletes or rewrites data.
- Render's free web service sleeps when idle; the first visit after a pause can take up to a minute. That is a wait, not an error.
- Check the environment variables in step 3 above. A missing or short `AUTH_SECRET` stops the app on purpose.

## Before taking real customers

- PostgreSQL on a paid plan with backups. The web-level tests pass on PostgreSQL 16 and two gunicorn workers start cleanly on an empty database, but still do a full click-through on your real database first.
- Your Daraja credentials and callback URL, tested in sandbox first.
- Register with the Office of the Data Protection Commissioner if required, and publish a privacy policy.
- Have a few real chamas try it with small amounts.
- Turn on `REQUIRE_OWNER_2FA=1`, set up two-factor login, and keep the recovery codes offline.
- Run `flask --app app validate` on the real database after your click-through. It also warns if test data is still present.
- Notifications live only inside the app. Members who never open ChamaPay will not see them until email or SMS exists.

## How it works (for developers)

- `db.py` schema + tiny database layer (SQLite locally, PostgreSQL in production). Money is stored as integer cents.
- `mgr.py` merry-go-round rules, `recovery.py` password reset, `routes_mgr.py` and `routes_recovery.py` their screens, `finance.py` money rules, `community.py` meetings, attendance and announcements, `reports.py` officials' reports and CSV, `notify.py` in-app alerts, `twofactor.py` owner 2FA, `devtools.py` seed/reset/validate commands, `routes_finance.py`, `routes_community.py` and `routes_security.py` their screens.
- `services.py` all rules (limits, states, payments). `app.py` routes and authorization. `providers.py` Test and M-Pesa providers. The Test provider is disabled in production.
- Payments are only applied by `process_webhook`, guarded by unique constraints on webhook event and receipt, and an `applied` flag.


## PWA / Phone Installation

This build includes PWA support for ChamaPay:
- Web App Manifest: `/static/manifest.webmanifest`
- Service Worker: `/static/sw.js`
- App icons: `/static/icons/`
- Install prompt on supported Android/Chrome browsers
- iPhone/iPad guidance for Safari → Share → Add to Home Screen

The service worker intentionally caches only static assets. Authenticated pages, sessions, forms, financial records, and other dynamic data remain network-only.
