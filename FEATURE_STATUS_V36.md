# ChamaPay v36 — implementation and verification report

## What changed in v36

1. **Configurable voting rules:** Each new proposal records a quorum percentage and a YES/NO approval threshold. Closing a vote (manually or after its close time) calculates and records `PASSED`, `REJECTED`, or `NO_QUORUM`. Abstentions count toward quorum but do not count in the YES/NO threshold denominator. The rule is saved on the proposal so later constitution changes do not silently change the result.
2. **Second-official approval for member exits and share payouts:** Submitting an exit now creates a maker-checker request. Membership status and ledger payout are not changed until a different authorized official approves. The business logic rechecks the member balance at execution time and blocks duplicate pending requests.
3. **Dividend closing workflow:** Officials can snapshot a year’s allocation and request independent approval. The reviewer verifies that item allocations sum exactly to the pool before the run is finalized. Rejected runs can be recalculated and resubmitted. Finalizing an allocation is not the same as paying dividends; payments still need to be recorded separately.
4. **Member money dashboard:** Adds total savings, outstanding loan balance, outstanding fines, fines paid, share balance before dividends, recorded payouts, and estimated current-year dividend/net position. Estimates are labelled as estimates and are not represented as finalized entitlements.
5. **Reconciliation duplicate-reference warnings:** Reconciliation now flags repeated non-empty reference/amount pairs within contribution, repayment, and fine-payment records. These are review signals, not automatic fraud conclusions.
6. **Chama Health Score:** Missing evidence is shown as “Unknown” and excluded from the overall average instead of being silently scored as healthy. The UI shows how many indicators remain unknown.
7. **Tests and schema:** Added pure voting-rule tests and additive database tables/columns for voting rules and dividend runs/items. Existing records are not intentionally rewritten or deleted.

## Validation performed

- `python -m compileall -q .`: passed.
- `pytest -q tests/test_governance.py tests/test_backup.py`: **7 passed**.
- Fresh SQLite `init_db` smoke test: passed; verified new voting columns and dividend tables exist.
- Full pytest suite: **blocked at test collection** because `werkzeug` is not installed in the provided runtime (`ModuleNotFoundError`). This is an environment dependency issue; the full web/finance tests have not run.
- No live production database was accessed or modified.
- The earlier live Render `503` has not been diagnosed from source alone. Current Render deploy/runtime logs and environment values are still needed.

## Requirement coverage status

Status labels mean: **Implemented in this release** = a source-code change was made; **Existing, not fully verified** = source functionality was found but the end-to-end behaviour has not been proven here; **Still required** = a gap remains or a production validation step is outstanding.

| Requirement | v36 status | Notes |
|---|---|---|
| Private member money view | Implemented in this release | Shows balances and explicitly labels the dividend amount as an estimate. |
| Financial reconciliation and duplicate warnings | Implemented in this release | Duplicate warnings are heuristic; bank statement import/matching is not established by this change. |
| Reversals with reasons instead of silent deletion | Existing, not fully verified | Approval primitives and correction routes exist; the full correction route matrix still needs integration tests. |
| Loan applications, schedules, guarantor consent, repayments | Existing, not fully verified | Existing source contains these flows; E2E tests did not run. |
| Available-funds impact before loan approval | Still required | Must be confirmed/finished in the loan approval interface. |
| Year-end dividend calculation and independent finalization | Implemented in this release | Finalization snapshots allocation; actual payments remain separate. Contribution timing-weighted formulas are not configurable yet. |
| Configurable vote quorum and approval threshold | Implemented in this release | New proposals store the rule and closed outcomes. Existing old votes default to 50%/50%. |
| Dual approval for loan disbursement, corrections and member exit payout | Implemented in this release / existing | These paths use maker-checker; all high-risk transaction types have not been proven covered. |
| Configurable amount thresholds for all sensitive transactions | Still required | Current controls are action-based, not a complete threshold policy engine. |
| Tamper-evident audit chain | Still required | Audit events exist, but cryptographic hash chaining/independent tamper verification is not implemented by this release. |
| Daily durable backup | Existing, configuration-dependent | Render needs S3-compatible bucket and credentials. This release has not performed a live upload or a restore drill. |
| Restore drill and documented recovery | Still required | Must be tested against a disposable isolated database before production reliance. |
| Sensitive-activity alerts | Existing, not fully verified | Notification/outbox paths exist; event coverage and delivery need E2E tests. |
| Independent downloadable audit pack | Existing, not fully verified | Export route exists; completeness and reconciliation labels need sample-pack verification. |
| Daily action centre and contribution compliance | Existing, not fully verified | Existing dashboard/attention logic found; E2E validation outstanding. |
| Member exit payout | Implemented in this release | Now waits for second-official approval. |
| Multiple chama support | Existing, not fully verified | Existing organization/workspace code found. |
| Offline-friendly entry and conflict-safe sync | Still required | Static offline shell exists; safe financial write synchronization is not established. |
| Email OTP and secure account recovery | Existing, configuration-dependent | Email provider configuration and delivery still require deployment tests. SMS OTP remains disabled per prior direction. |
| WhatsApp sharing/reminders | Existing, not fully verified | Share/reminder code exists; delivery and permissions require E2E checks. |
| Document vault and disputes | Existing, not fully verified | Existing routes/templates found; full authorization and lifecycle testing remains. |
| Subscription transparency and upgrade/downgrade | Existing, not fully verified | Existing subscription routes found; expiry, upgrade and downgrade tests still required. |
| Group investment tracking | Existing, not fully verified | Existing asset/investment records found; financial statement integration needs validation. |
| Chama Health Score unknown-data handling | Implemented in this release | Unknown indicators are not scored as healthy. |
| Live 503 resolution | Still required | Needs current Render logs, build output, health-check response and environment configuration. |

## Production rollout gates

1. Run the full test suite in a clean environment after installing `requirements.txt`.
2. Test the new vote and dividend flows through authenticated web requests using synthetic data, including member access, role restrictions, rejected approvals, repeated submissions, and cross-chama ID tampering.
3. Confirm that `DATABASE_URL` points to persistent PostgreSQL and `AUTH_SECRET` is at least 32 characters.
4. Verify the Render backup job uploads a durable object, then restore into an isolated database and record the result.
5. Diagnose the live `503` from current Render logs. This source package alone cannot establish the runtime root cause.

## Safety and scope

- No new mobile-money integration was added.
- No live production records were accessed or changed.
- Do not treat this report as certification that every listed feature is production-ready; several pre-existing flows remain unverified because the full test environment could not be installed here.
