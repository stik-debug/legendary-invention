# ChamaPro V71 — 3D Home, Real Chama Signals & Pilot Scorecard

## Added
- Replaced the account dashboard with a responsive, high-contrast 3D-inspired home experience (CSS perspective, layered gradients, glass-like orb, tactile cards, mobile-first layout).
- Dashboard summaries are queried from current database records: active chama membership, members with paid contributions for the current month, pending payment requests, open meeting actions, overdue active loans, and active goals.
- Each Chama card now links directly to its home, savings, loans, monthly report, goals, meetings, financial reconciliation check, and staff-only pilot scorecard.
- Added `/chamas/<id>/pilot-metrics` for authorized staff. Activation uses claimed active members; weekly activity uses recorded audit events; subscription payment signals use successful, applied, non-test subscription payments. Staff can submit/update a weekly self-reported estimate of treasurer minutes saved.
- Added additive `pilot_time_savings` table. No existing data tables or records are dropped or rewritten.
- Versioned shared CSS URL to reduce stale-cache issues.
- Existing monthly reports continue to use `reports.chama_report()` and actual contribution, loan, cash movement, meeting and attendance records.

## Trust and limitations
- Dashboard values are live queries, not seeded demo statistics.
- The member contribution metric is a count of members with at least one `PAID` contribution record for the current period; it does not by itself prove external cash receipt or full reconciliation.
- The payment request count covers requests with status `PENDING`; other payment flows may use different tables/statuses.
- Weekly activity counts distinct actors with audit events tied to the chama in the last seven days; it is not limited to logins.
- Renewal signal is deliberately simple: two or more successful, applied, non-test subscription payment records for this chama. It is not a statistically mature cohort-retention metric.
- Treasurer time saved is self-reported, not independently verified.
- Marketplace expansion was not changed in this release; it should be expanded only after supply quality and provider verification are validated.

## Validation
- Python compilation, template parsing, config parsing, fresh SQLite initialization, route smoke tests and ZIP integrity are attempted as part of packaging. No live Render/browser E2E run is claimed unless explicitly reported in delivery notes.
