# ChamaPro v34 — Comparison follow-up

This release builds on v33 and adds two opt-in/official tools without replacing existing merry-go-round, savings, loan, or subscription flows.

## Added
- Chama-scoped downloadable audit pack ZIP from Reports, including the existing CSV exports, ledger export, and audit-log export, with a README explaining limitations.
- Optional public chama discovery. A CHAMA_ADMIN must explicitly publish a listing. The public directory only displays the chama's name, existing description, optional tagline, and active member count.
- Join-interest requests for listed groups. Requests are not memberships and never grant access. An official must still add a member through the normal membership workflow.
- Admin can remove a public listing at any time. Publishing/unpublishing and request reviews are audit logged.
- Added navigation to public listing controls for chama admins.

## Safety boundaries
- No financial records or member contact details are exposed in the public directory.
- Public listing is opt-in and private by default.
- Approving a discovery request does not grant app access.
- The audit pack is an export of application records, not an independent bank audit.
- Existing merry-go-round support and three-plan subscription UI are retained; this release does not add mobile-money functionality.

## Validation
- Python syntax compilation passed for changed modules.
- Full Flask application tests could not run in this build environment because Flask is not installed. Run the project's tests in the configured deployment/test environment before production rollout.
