# ChamaPro V72 — Trust, Memory & Sustainable Growth

## Upgrade focus
V72 builds on V71 rather than replacing the app. It adds a searchable Chama Memory page that indexes existing records, with role-based filtering for staff-only history. It also makes Memory and the Decision Simulator discoverable from the strategy page and Command Centre.

## Existing capabilities carried forward from V71 and earlier
- Financial Trust Center: source-to-ledger integrity checks, duplicate receipt-reference checks across contribution/loan repayment/fine payment records, statement-to-ledger comparison, pending payment requests, and open/overdue meeting actions.
- Financial correction workflows that submit repayment/fine corrections for independent approval.
- Decision Simulator with explicit estimates, saved scenarios, rule-based fairness/risk flags, and a warning that projections are not ledger facts.
- Financial Passport PDF with snapshot hash and public verification route; Continuity Vault and formal handover workflow.
- Command Centre, goals, monthly reports, pilot scorecard, in-app meeting actions/reminders, provider directory, quote requests and quote comparison.
- Existing payment and subscription architecture is preserved. No M-Pesa integration is added.

## New Chama Memory
Route: `/chamas/<chama_id>/memory`

Searches existing meeting records, action commitments, votes, constitution history, audit logs, saved scenarios and handovers. All queries are scoped to the active chama. Constitution history, audit activity, scenarios and handovers are restricted to staff roles. Search is capped and read-only; it does not create or alter source records. Each result links back to the original feature.

## Commercial principles
Keep current tiers unchanged: KSh 500/month up to 20 members, KSh 1,500/month up to 70 members, and KSh 2,000/month up to 100 members. Potential add-on revenue should be transparent and tied to real cost/value: paid onboarding and historical-data migration, assisted training, multi-chama administration for umbrella groups, and optional premium report/administrative support. Marketplace fees, if introduced later, must be disclosed before a booking. Do not charge a fee just to record a contribution and do not sell member financial data.

## Pilot scorecard
Track invited-member activation, weekly active members, treasurer time saved (labelled self-reported), payment/reconciliation exceptions, support burden, first-month renewal and later renewals. Treat small samples as signals, not proof of product-market fit.

## Validation limitations
This upgrade does not claim that a live Render deployment was changed. The ZIP must be deployed by the project owner. Automated source-level checks are performed for this build; full browser flows, live database/PostgreSQL behavior, payment reconciliation and permission testing still need staging verification before production launch. Financial flags are prompts for human review, not fraud accusations or automatic corrections.
