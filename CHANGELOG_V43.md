# ChamaPay V43 — financial trust and follow-up

- Added a role-protected Trust & Reconciliation centre with source-to-ledger missing-link and amount-mismatch checks, cross-feature duplicate payment reference reporting, and cash-total comparison.
- Added chama-wide duplicate payment reference enforcement to contribution and loan-repayment entry.
- Added a member home summary for current-month contribution status, active loan count, and open meeting actions.
- Added in-app reminders for unrecorded/partial monthly contributions, active loan balances, and due/overdue assigned meeting actions. Daily deduplication prevents repeated identical notifications on the same day. Reminders are not proof of payment and do not charge members.
- Added a reviewed CSV financial-history import for contributions, expenses, other income, and a single opening cash balance. Imports are size/row limited, previewed, duplicate-reference checked, transactional, and audited. Loan repayment history, loan principal, and payouts remain outside this importer.
- Added logical backup validation and a safe SQLite staging restore drill with row-count and foreign-key checks; it refuses to overwrite a non-empty database.
- Kept existing app modules, pricing, and login OTP-disabled setting.

## Validation status

Python compilation, YAML parsing, ZIP integrity, and focused local SQLite restore/reconciliation smoke tests are intended validation steps. Full pytest and production PostgreSQL/Render end-to-end tests must be run in an environment with the project's dependencies and a disposable staging database. Do not claim production readiness from static checks alone.
- Added an actual-cash reconciliation form: an authorized treasurer/admin enters combined bank + physical cash for a chosen date; ChamaPay compares it with ledger movements through that date and writes the comparison/difference to the audit log without altering any transaction.
- Connected the idempotent obligation reminders to the existing daily Render job, so contribution, due/overdue loan installment, and assigned meeting-action reminders are generated automatically each day as in-app notifications. The Trust Centre button can also run the same deduplicated process on demand.
