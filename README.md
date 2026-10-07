# ChamaPay Kenya (V23)

A multi-tenant SaaS for Kenyan chamas. Billing, security, the Control Center, contributions, the ledger, loans, fines, statements, chat, meetings, announcements, notifications, reports, merry-go-round, owner two-factor login and the test-data commands are built (V22 added the trust, privacy and polish features below; V23 added payment setup, member joining codes and the new logo). See "What is and is not built" below. Please read it.


## V22 additions

V22 focuses on making the existing product easier, safer and more trustworthy rather than adding another large feature module. It adds:

- Command Center 2.0 polish with owner-only access and clearer explain-this-number links.
- Member Needs Your Attention panel for contribution, loan and vote actions.
- Money Timeline and proof/receipt pages for contributions, loan repayments and fine payments.
- Chama Health Score and Trust Center.
- Setup completion wizard for Chama owners.
- Security & Privacy Center, personal data export and privacy request workflow.
- Owner Privacy Requests and Owner Analytics 2.0.
- Expanded deterministic Chama AI for savings, unpaid members, loan balances, expenses, investments, assets, goals and meetings.
- Data Saver mode and a more deliberate offline snapshot.
- Friendlier recovery/error pages and financial-action warnings.

Financial actions remain server-authorized and M-Pesa integration is not expanded by V22.

## What is and is not built

| Feature | Status | Notes |
|---|---|---|
| Registration, login, logout | Built, tested | Hashed passwords, CSRF, rate limiting, secure cookies |
| SMS security OTP | Built, **switched OFF** (set `SMS_OTP_ENABLED=1` to turn on) | Ordinary login does **not** require SMS OTP. SMS OTP is used for signup phone verification, Forgot Password, changing the phone number (current phone + new phone verification), and changing a password from Security & Privacy. Codes are 6 digits, expire after 5 minutes, are single-use, hashed, attempt-limited and resend/rate limited. |
| Merry-go-round (rotating savings) | Built, tested | Each round has an order (random draw or typed), an amount and weekly/monthly turns. Everyone except that turn's recipient pays in; when all have paid, an official pays the pot to the recipient (never to themselves). **The pot is a separate money account** (`MGR` in the ledger): loans, expenses and "cash in hand" only look at the main account, so the pot can never be lent or spent. "Your turn to pay" alerts, reminders (max one per 12 hours), cancel, reports and CSV. See the section below |
| Online meetings | Built, tested | When scheduling, choose *In person*, *Online with a free video room* (Jitsi, an unguessable room name is made for you) or *Online with my own link* (Zoom, Google Meet, Teams: https only). Members of that chama only see a **Join** button from 30 minutes before the start until 4 hours after. Opening it marks the member **Joined online** on the attendance sheet as a hint: nobody is marked present automatically, an official still confirms (so absence fines stay fair) |
| Paying the chama (contributions, fines, loan repayments) | Built, tested | Members pay the chama's **own** paybill, till or phone as they always do, then paste the M-Pesa message in the app. The code and amount are read from it and the payment is recorded with the same finance rules and ledger as a treasurer entry. ChamaPay never holds chama money and sends no payment prompts. See "Paying the chama" below |
| Roles (SUPER_ADMIN, CHAMA_ADMIN, TREASURER, SECRETARY, MEMBER) | Built, tested | Enforced on the server for every route |
| Multi-tenant isolation | Built, tested | Chama A user gets 403 on Chama B pages and actions |
| Chama create, members add/remove | Built, tested | Add by name + phone number. New people get an 8-digit join code to claim their account. Removal keeps history |
| Invitations by link | NOT built | Join codes work today (admin adds a phone number, gives the person an 8-digit code) |
| Plans Starter 500/20, Growth 1,500/70, Pro 2,000/100 (KES per month) | Built, tested | In the database, editable by the owner. Per chama, never per member |
| Member limit (21st, 71st, 101st rejected) | Built, tested | Enforced in the backend, race-safe on PostgreSQL |
| Upgrade / downgrade | Built, tested | Upgrade = pay. Downgrade blocked if members do not fit. Nobody is auto-removed |
| Subscription states TRIAL, ACTIVE, PAST_DUE, GRACE_PERIOD, SUSPENDED, CANCELLED | Built, tested | 7-day trial and 3-day grace, both editable |
| Suspension keeps all data, payment auto-reactivates | Built, tested | |
| Payments: pending/success/failed/cancelled/timeout | Built, tested | Verified server-side only |
| Duplicate webhook protection | Built, tested | Payment, subscription extension and audit each happen once |
| Forged / wrong-amount callbacks | Built, tested | Ignored or rejected |
| M-Pesa STK push for ChamaPay subscription fees | Built, NOT tested live | Only for the chama paying ChamaPay its monthly plan, not for member payments to the chama. Needs the ChamaPay owner's Daraja credentials. Shows CONFIGURATION REQUIRED until set |
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
| Email and SMS | SMS built, but SMS sign-in codes are OFF | Africa's Talking SMS is used for account-security OTPs and existing in-app notification SMS. Email is not required for security flows. |
| Test-data commands (seed/reset/validate) | Built, tested | `flask --app app seed`, `reset-test-data`, `validate`. See "Test-data commands" below |
| Two-factor login for owner | Built, tested | Authenticator app (TOTP) plus 8 one-time recovery codes. Optional `REQUIRE_OWNER_2FA=1`. See "Owner two-factor login" below |
| Privacy Policy and Terms of Use | Built (starting text) | Public pages at `/privacy-policy` and `/terms`, linked from the home and sign-up pages. Plain-language starting text: **have a Kenyan lawyer review it before launch**. Set `SUPPORT_CONTACT` on Render to show a contact line |
| Account deletion | Built, tested | A member asks under Security & Privacy; the owner reviews it at Owner > Privacy requests and presses *Delete account (anonymise)*. Name, phone, email, password, 2FA and sessions are erased and the person leaves their chamas, but the group's entries stay (shown as "Deleted member") so the books still balance. Refused while the person has an open loan, an unpaid fine, a loan guarantee, or is a chairperson |
| Old "paste an M-Pesa message" page | Not shown | `/pay` now goes to the Payments page. The paste-a-message routes and the officials' warning page still exist in the code but nothing links to them |


### SMS security OTP

**Currently switched off.** Until SMS delivery works, `SMS_OTP_ENABLED` is unset/`0` (the default, also set in `render.yaml`). With it off: signup logs the person straight in; login never asks for a phone code; Change password asks for the current password; Change phone asks for the current password (the new number is saved as unverified); and Forgot password uses the one-time code a chairperson or the owner issues. All the SMS code stays in place. When SMS is ready, set `SMS_OTP_ENABLED=1` on Render and redeploy: accounts created while it was off will be asked to verify their phone once at their next login.


ChamaPay deliberately does **not** ask for an SMS code on every login. Normal login remains email/phone + password (with the existing owner authenticator-app 2FA when enabled). SMS OTP is reserved for sensitive actions:

- New-account phone verification during signup
- Forgot password / password recovery
- Changing the current phone number (verify the current number, then the new number)
- Changing a password from Security & Privacy

Each OTP is 6 digits, expires in 5 minutes, is stored only as a hash, becomes invalid after successful use, locks after 5 failed attempts, and has resend/request rate limits. OTP messages bypass ordinary SMS notification preferences because they are security messages. Keep `AT_USERNAME`, `AT_API_KEY` and optional `AT_SENDER_ID` in Render environment variables; never commit them to source control.

## Online meetings

Officials schedule a meeting and pick how it is held. Jitsi needs no keys: the app creates `https://meet.jit.si/ChamaPay-<random>` (set `JITSI_BASE` to use your own Jitsi server). The room opens in a new tab, because public Jitsi limits embedding it inside other sites. On meet.jit.si the first person to start the room may need to sign in as moderator, so an official should open it first. For chamas that already use Zoom or Google Meet, paste that link instead. The link is only shown to active members of that chama, and only near the meeting time.

## Paying the chama

1. **Chairperson: Pay > Payment settings.** Write where members pay (for example `Paybill 123456, account: your name`) and choose how a pasted message is checked (below).
2. **Member: Pay.** Step 1: pay the chama's paybill, till or phone. Step 2: paste the whole M-Pesa SMS and choose what it was for: this month's contribution, one of your fines, or a loan repayment. The code and amount are read from the message. If you type a code or amount too, they must match it. If the message cannot be read you can type the code and amount instead, and an official approves it.
3. **Officials: Pay > Waiting for you.** Approve or reject (with a reason) anything that was not recorded automatically. You cannot approve your own payment. Also shows payments recorded from a message that the chama's records have not confirmed yet.

**Two ways to check a pasted message (the chairperson chooses):**

| Setting | What happens | Best for |
|---|---|---|
| After it matches the chama's M-Pesa records (default) | Officials paste the messages the *chama received* under **Pay > M-Pesa records** (or lines like `QGH7XY12AB 1000`). When a member's code **and** amount match, it is recorded automatically, whichever was added first. A made-up or edited message never matches, so it is never recorded. | Chamas with real money at stake |
| Straight away | A readable message from the last 7 days is recorded at once. Officials are told and can cancel a mistake (Savings, Loans or Fines page). When the chama's records are added later, each such payment is compared and any difference is flagged. | Small, close-knit groups that trust each other |

**Safeguards in both:** a code can be used once; members can only pay their own fines and loans, never more than is owed; nobody can confirm their own payment with a record they added themselves (another official must approve); an edited amount is flagged; every automatic recording and every record added is in the audit log; balances and full phone numbers from messages are not kept.

**What this can and cannot prove (please read).** A pasted message is only text, and anyone can type one. ChamaPay cannot look inside M-Pesa, so it cannot tell a real message from a fake one just by looking. In the default setting the protection is the comparison with the chama's own records, which are only as honest as the officials who add them (hence the self-confirmation block and the audit log). In the *Straight away* setting a member could paste a made-up message, and only the officials' review and later record matching would catch it. There is no payment prompt and no automatic Safaricom confirmation in this app.

Message layouts differ between send money, paybill and till, and Safaricom can change the wording. The reader looks for the 10-character code, the first amount, the date and the other party. It was tested on the common layouts, **not on live Safaricom messages**. Try a few real ones first.

Upgrading: chamas that used the old automatic (Daraja) setting keep their payment instructions and move to the default check. Stored Daraja keys are no longer used. `CHAMA_SECRETS_KEY` is no longer needed.

## Tests (229 automated, all passing on SQLite; the web-level ones up to V22 also passed on PostgreSQL, but the newest tests (SMS switch, account deletion, legal pages) have only been run on SQLite)

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
2. Create a **Web Service** from the project root. Build command: `python -m pip install --upgrade pip && pip install -r requirements.txt`. Start command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -`. A `render.yaml` is included with a PostgreSQL database, generated `AUTH_SECRET`, and the required environment-variable prompts.
3. Set environment variables using `.env.example` as the template. At minimum: `AUTH_SECRET`, `DATABASE_URL`, `OWNER_EMAIL`, `OWNER_PHONE`, `OWNER_PASSWORD`, `PAY_INSTRUCTIONS`.
4. Health check path: `/healthz`. The included deployment config pins Render to Python 3.13.5 and binds Gunicorn to `0.0.0.0:$PORT`, which is required for reliable Render port detection.
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
- If you charge chamas a subscription through M-Pesa: your Daraja credentials and callback URL for that, tested in sandbox first. Member payments to chamas do not use Daraja.
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


## ChamaPay 2.0 upgrade

This build preserves the existing ChamaPay architecture and adds a new upgrade layer.

### Added
- ChamaPay Command Center
- Chama Health score
- Goals and goal progress
- Investment portfolio
- Group assets
- Digital voting
- Digital Chama Constitution
- Member Passport
- Financial activity timeline
- ChamaPay AI data-grounded assistant
- Payment receipt view
- Global member search
- Help/support center
- Mobile bottom navigation
- Expanded landing-page positioning
- Starter plan capacity: 20 members
- Growth plan capacity: 70 members
- Pro plan capacity: 100 members
- Loan due-date support
- Constitution defaults for new and existing chamas

### Safety
The upgrade is additive: existing tables/routes/components are retained. New database tables are created automatically and existing databases are migrated additively without dropping existing data.

### AI note
The included ChamaPay AI interface is intentionally data-grounded and permission-aware. It provides deterministic answers from recorded group data without inventing transactions. A production LLM provider can be connected later through a server-side integration.

## ChamaPay 2.1 upgrades

This build adds a broader production-oriented layer while intentionally leaving M-Pesa integration unchanged:

- Private member dashboard with savings, loan, fines, attendance, meetings and votes
- Loan repayment schedules and guarantor requests/responses
- Secure permission-controlled document storage (up to 8 MB per document)
- Meeting action items with owners, due dates and completion tracking
- Notification preferences (in-app remains enabled; external channels are readiness settings only)
- Financial reconciliation checks against the ledger
- Chama JSON backup export and platform-owner data backup export
- Organization workspace for grouping Chamas owned by the same user
- Printable financial report / browser Save-as-PDF flow
- Expanded deterministic ChamaPay AI questions for overdue loans, expenses, attendance, meetings and financial position
- Platform owner analytics and support-ticket management

M-Pesa/STK/callback integration is deliberately not expanded in this release.
