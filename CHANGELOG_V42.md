# ChamaPro V42 — member onboarding and data portability

## Added
- Admin-only CSV member import with a preview step before writes.
- CSV import validates required `name` and `phone` headers, limits batches to 100 rows / 256 KB, validates allowed roles, and refuses to assign `CHAMA_ADMIN` from a bulk file.
- Existing service-level membership checks and plan capacity rules are used for every imported row.
- New-account join codes are shown only in the authenticated import result so the chairperson can distribute each code privately. Codes are not added to the CSV export.
- Authorized officials can export the member roster as CSV for record portability.
- Import and export actions are written to the audit log.
- The member import screen explains that it imports membership only, not financial balances or historical transactions.

## Retained
- V41 pricing: Starter up to 20 members KSh 500/month; Growth up to 70 members KSh 1,500/month; Pro up to 100 members KSh 2,000/month.
- Email OTP remains disabled; no SMS OTP or M-Pesa API integration added.
- Existing Strategy & Continuity features and existing financial-check workflow are retained.

## Validation and limitations
- Python compile check passed.
- ZIP integrity check passed.
- Full pytest suite could not collect in this build environment because `werkzeug` is not installed (`ModuleNotFoundError`).
- No live Render, PostgreSQL, browser, or end-to-end onboarding test was performed.
- CSV member import does not import opening balances or financial history; these must not be bulk-created without a separate reconciliation and migration workflow.
