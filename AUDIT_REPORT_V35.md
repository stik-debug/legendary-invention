# ChamaPay v35 — Source Audit and Reliability Fixes

## Scope

Reviewed the uploaded `chamapay-v34-discovery-audit-pack.zip`. The source contains 34 top-level Python modules, 15 pre-existing test modules, Flask templates, and additive schema migration logic. This update is deliberately conservative: it keeps the existing financial flows and records, adds no new mobile-money integration, and does not claim that a production deployment has been repaired from source alone.

## Changes in v35

1. **Email OTP configuration is now usable and explicit.** `EMAIL_OTP_ENABLED=auto` (the Render default) enables email OTP when `RESEND_API_KEY`, `BREVO_API_KEY`, or `SMTP_HOST` is configured. `EMAIL_OTP_ENABLED=0` explicitly disables it; `EMAIL_OTP_ENABLED=1` explicitly requires it. SMS OTP remains off. The mail sender/provider still needs to be verified and tested in the deployment environment.
2. **Render backup durability now fails loudly.** Render cron containers are ephemeral. If `RENDER` is set but `BACKUP_S3_BUCKET` is missing, `backup.save_backup()` now raises a clear configuration error instead of writing a misleading “daily backup” into ephemeral `/tmp`. Local development can still use `BACKUP_DIR`.
3. **Added focused backup tests** for the production missing-bucket guard and local-development output.
4. **Updated deployment notes** to describe OTP configuration and the durable-backup requirement.

## Existing feature coverage found in source

The codebase already contains implementations or routes for:

- Member money dashboard and money timeline
- Contributions, loans, repayment schedules, guarantor consent, fines and ledger-based corrections
- Dividend calculation and member exit quotes/payout records
- PDF statements and receipts; CSV exports and downloadable audit pack
- Chama-scoped audit log and audit events for many governance actions
- Meetings, attendance, minutes, action items, WhatsApp sharing, voting and announcements
- Reconciliation checks, cash/loan forecasting, Chama Health score and action centre
- Document vault, support/dispute tickets, investments/assets, multi-chama organization workspace
- Subscription plans, member limits, upgrades/downgrades and subscription payment routes
- Daily job for reminders, queued email/WhatsApp work and logical database backup
- Optional public discovery with opt-in publication and join-interest requests

This is a source-presence inventory, not proof every path works correctly in production. Some capabilities are configuration-dependent, and full integration/E2E coverage remains necessary.

## Validation performed

- `python -m compileall -q .`: **passed**.
- `python -m pytest -q tests/test_backup.py`: **2 passed**.
- Full test suite collection: **blocked** because this audit runtime does not have the `Werkzeug` dependency installed (`ModuleNotFoundError: No module named 'werkzeug'`). No dependency download was possible in this environment, so Flask route and financial E2E tests were not run here.
- The live app's prior `503 Service Unavailable` has **not been root-caused** by this source-only review. A definitive diagnosis needs the Render deploy/runtime logs and current environment status. The application runs DB initialization during startup, and production startup requires a valid `AUTH_SECRET` of at least 32 characters and a reachable configured database.

## Important remaining gaps / release gates

- **Voting quorum/approval thresholds:** the current vote workflow records YES/NO/ABSTAIN and closing time, but this review did not find configurable quorum and pass-threshold enforcement. Treat vote totals as advisory until that is implemented and tested against the chama constitution.
- **Maker-checker coverage:** the approval primitives exist and are used for some corrections/loan actions, but the source comments explicitly note that the schema/helper alone does not convert every high-risk route to dual approval. Review all disbursements, refunds, payouts, and settings changes before calling dual approval complete.
- **Backup restore drill:** a backup export is not yet proof of recoverability. Run a restore drill into an isolated database and document which authentication factors must be reset after restoration.
- **503 incident:** remains unresolved without current Render runtime logs and environment details.

## Before production rollout

1. Set and verify `DATABASE_URL` points to the persistent PostgreSQL database and `AUTH_SECRET` is at least 32 characters.
2. Configure an S3-compatible bucket and credentials for the Render daily cron job (`BACKUP_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`; optionally `S3_ENDPOINT_URL`). Confirm the cron logs show a successful object upload.
3. Configure and verify a mail provider and `MAIL_FROM`; test signup, resend, login verification and password recovery end-to-end before requiring email OTP.
4. Run the full test suite in an environment with `requirements.txt` installed, then exercise financial workflows with test data and verify no production financial records were altered.
5. Resolve the 503 from Render runtime logs rather than assuming it is fixed by syntax compilation.

## Safety notes

- No live production database was accessed or modified by this source edit.
- No new M-Pesa, STK-push, or other mobile-money functionality was added.
- The backup remains a logical export; it is not an independent bank audit. S3 server-side encryption defaults to `AES256` in the existing backup implementation.
