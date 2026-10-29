# ChamaPay V82 — Integration, conversion, and reliability work

## Included in this source package

1. **Homepage activity states:** public dashboard cards now say when no records exist instead of presenting an unexplained zero as if it were proof of inactivity. When activity exists, the cards show recorded totals. The public page uses aggregate totals only; it does not display individual chama records.
2. **Broader feature discovery:** the homepage now surfaces ChamaPower, Chama Health & Trust, and the Marketplace alongside the existing modules.
3. **Pricing clarity:** plan cards explain a more useful progression across the existing Starter (20), Growth (70), and Pro/Business (100) member caps. Pricing copy is not a substitute for server-side entitlement enforcement; do not claim a feature is plan-exclusive unless the backend actually enforces it.
4. **Integration acceptance checklist:** this release includes a practical test checklist for cross-module workflows, customer conversion, and production reliability.

## Important scope note

This package changes the landing-page presentation and adds implementation/QA guidance. It does **not** claim that every integration below has already been implemented or that production has been tested. Existing feature modules are retained. No production database, live account, or deployed website was modified by preparing this ZIP.

## Cross-module integration acceptance checklist

### ChamaPower project lifecycle
- Proposal is created with owner, budget, risks, supporting evidence, and target milestones.
- Voting uses the chama's configured governance rules and records voter eligibility, result, timestamp, and audit event.
- Approval creates/updates a project authorization record; it does **not** post an expense or change the cash ledger by itself.
- Project responsibilities and milestone status are linked to the approved project.
- Actual payments/expenses are entered through the existing authorized financial workflow, with receipts and ledger entries linked back to the project.
- Reports distinguish approved budget, committed amounts, recorded expenditure, and remaining budget.
- Cancellation/corrections preserve the audit trail; no financial transaction is silently deleted.

### Single source of financial truth
- Contribution, loan, repayment, fine, expense, and project expenditure views agree with the canonical ledger.
- A project budget or proposal never counts as cash movement until an authorized financial transaction is recorded.
- Unreconciled items remain visibly unresolved; the app does not claim that a calculated ledger balance proves money exists in a bank or M-Pesa account.
- Cross-chama requests are denied server-side; test with two separate chamas.

### Public homepage and pricing
- When there is no real activity, cards show a clear empty state rather than unexplained zero metrics.
- When activity exists, totals are derived from real, non-test records and only aggregate information is shown publicly.
- Verify totals exclude test/demo data and cannot expose individual members or private chama names.
- Each pricing tier displays the correct price and member cap: Starter KES 500 / 20 members; Growth KES 1,500 / 70 members; Pro/Business KES 2,000 / 100 members.
- Backend rejects membership beyond each plan cap; pricing copy alone is not access control.
- Do not advertise a tier-specific capability until the server enforces it and an automated test covers it.

### Reliability and deployment checks (must be run in the target environment)
- Run `python -m unittest discover -s tests -v` after installing `requirements.txt`.
- Test signup, login, logout, password recovery, member join code, and owner Control Center on desktop and mobile viewport sizes.
- Test member contribution → authorized confirmation → ledger → statement → monthly report.
- Test loan request → independent approval → disbursement ledger entry → repayment → updated balance.
- Test ChamaPower proposal → vote → approval → authorized expense with receipt → project report; verify approval alone does not change cash.
- Test subscription suspension, payment pending/success/failure, duplicate callbacks, wrong amounts, and reactivation only after trusted confirmation.
- Test backups by restoring into a separate non-production database and comparing record counts and financial totals.
- Test production database migrations against a copy before deployment. Never use a live financial database as a test fixture.
- Verify owner navigation and one working logout on desktop and mobile.
- Record failures as reproducible defects; do not label a feature “working” from source inspection alone.

## Deployment safety

Before deploying, back up the database, inspect the diff, run the suite, deploy to a staging environment, run smoke tests, and only then deploy. Keep a rollback path. This ZIP has not been deployed to `chamapay.space` and does not alter the live application until the owner deploys it.
