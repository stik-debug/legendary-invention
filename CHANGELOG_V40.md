# ChamaPro v40 — Strategy, Trust & Continuity

Built on the v39 source archive. This release is additive; it does not delete existing financial or membership records and does not add new mobile-money integration.

## Added
- **What-if simulator:** models contribution reductions, a stressed loan-default rate, a planned payout and a proposed loan using the last 90 days of recorded activity. Results are estimates only; they do not reserve funds or alter the ledger.
- **Saved scenarios:** authorised officials can save named scenario assumptions and results. A saved scenario can be attached to a new vote/proposal so members can consider it during voting.
- **Fairness & consistency review:** flags repeated paid contribution entries in one member-period, concentrated outstanding borrowing and varying fine amounts for similar recorded reasons. Flags are prompts for human review, not fraud findings.
- **Promise Tracker:** central view of meeting commitments with owner, due date, overdue state and completion. Reuses existing meeting-action records rather than creating duplicate tasks.
- **Chama Financial Passport:** staff can export a summary PDF with a QR code and signed verification URL. Public verification confirms that the report snapshot originated from this ChamaPro installation and was not altered; it is not an independent audit, bank confirmation or credit rating.
- **Continuity Vault:** staff can export a read-only ZIP snapshot with group records, financial totals, active-member roles, outstanding loans, rules/history, goals, assets, investments, open commitments and recent audit events. It excludes passwords, OTPs and session tokens.
- **Formal handover:** authorised officials can nominate an active member as successor; the nominee can inspect the continuity package and acknowledge receipt. This records the handover but does not automatically change permissions or certify every record.
- **Living Constitution history:** before each rules update, the previous rules are saved as a versioned snapshot. Server-side validation now rejects invalid ranges before writing changes.
- **Copilot expansion:** deterministic, data-grounded answers now cover ledger balance, overdue loans, goals, meeting commitments, fairness review and what-if planning. It is not presented as a general-purpose external LLM.
- **Low-connectivity guard:** the offline state is shown clearly, and POST changes are blocked while the browser reports offline rather than being silently queued. Only chama names are retained in the local offline summary; financial amounts, member contacts and transaction records are not stored in localStorage. The cached offline shell remains read-only; full offline financial entry/sync is not claimed.
- **Navigation:** Strategy & Continuity and Promise Tracker are linked from the chama navigation and All Functions page.

## Data and security
- Added tables: `chama_scenarios`, `chama_constitution_history`, `chama_handovers`, and `chama_financial_passports`.
- Added `chama_votes.scenario_id` to link a vote to a saved scenario. Database migration is additive and preserves existing rows.
- Role checks are enforced server-side for saving scenarios, creating commitments, exporting the continuity vault and issuing financial passports.
- Export snapshots intentionally exclude authentication secrets.

## Validation performed
- Python syntax compilation passed (`python -m compileall -q .`).
- Database initialisation on a fresh SQLite test database passed; all new tables and the `scenario_id` column were present.
- ReportLab QR/PDF generation smoke test passed.
- Jinja template parsing showed no syntax errors in the new templates; existing templates require app-registered custom filters when parsed standalone.
- Full pytest collection could not run in this environment because `werkzeug` is not installed (`ModuleNotFoundError`). No live Render deployment or end-to-end browser test has been performed.

## Not claimed
- No real external AI model was configured; Copilot uses deterministic queries over authorised application data.
- Full offline write/sync is not implemented. Sensitive financial/governance changes require connectivity.
- The continuity ZIP is an export package, not an automated restore tool.
