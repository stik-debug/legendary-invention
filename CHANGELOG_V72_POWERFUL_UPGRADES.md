# ChamaPro V72 — Command Centre and grounded Ask ChamaPro

## Added in this package
- **Role-aware Command Centre:** aggregates only live records and creates actionable review links for pending payment requests, overdue meeting actions and paid contributions missing ledger links. A clean result is not presented as proof that every record is correct.
- **Ask ChamaPro (deterministic first release):** answers a limited set of questions from the current chama's records, shows the source/period and distinguishes confirmed from pending money. It is not a connected generative AI model; unsupported questions are declined rather than guessed.
- **Feature hub:** puts existing reconciliation, goals/assets, decisions/scenarios, promises, handover, loans, verified-provider marketplace, reports, backup export, meetings and votes in one mobile-accessible route.
- **Chama navigation:** adds a direct Command Centre link.

## Existing modules surfaced, not newly invented
The source already includes reconciliation/trust workflows, goals and assets, decision scenarios/fairness flags, meeting action tracking, loan records, marketplace provider verification/quote requests, backup exports, meeting/vote workflows, and owner analytics. This change connects these routes in a role-aware hub rather than claiming they were all newly implemented here.

## Important limitations / production gates
- A downloadable backup is not the same as a tested restore. This package does not claim to have run a production restore drill.
- Offline financial writes remain disabled; server confirmation remains authoritative.
- Ask ChamaPro supports a bounded set of deterministic queries only. It is not a general-purpose AI assistant.
- Owner analytics is the existing owner-only Growth Centre; it still requires production review of tenant isolation, billing reconciliation and renewal definitions.
- No live Render deployment, production database migration, browser-based end-to-end test, or external payment test was run in this environment.
- Test collection could not run because Flask/Werkzeug dependencies are not installed and this environment could not reach the package index. Python compile checks passed.
