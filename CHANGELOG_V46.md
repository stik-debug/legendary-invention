# ChamaPay v46 — Trial Conversion and Member-Based Subscription

## Subscription pricing
- Public and in-app pricing now communicates KSh 25 per active member per 30-day billing period.
- Payment totals are calculated from the active member count, selected capacity plan, and selected number of months.
- Capacity plans continue to limit group size; legacy plan price fields are retained internally for safe compatibility and do not set the amount charged.
- New trials default to 30 days. Existing subscription trial/due timestamps are not rewritten by the migration.
- The default post-due grace period is extended to 7 days. Existing financial records are not deleted.

## Trial activation and retention
- Subscription page now includes a guided checklist with links to core group workflows.
- Monthly value report shows real counts for active members, paid contributions, ledger entries, meetings, loans, and recorded contribution value.
- In-app reminders are queued by the existing daily scheduled job at 7, 3, 1, and 0 days before trial end or renewal due date. The nudge job is idempotent and does not send SMS.
- Subscription page provides a billing-period total estimate and a feedback form for cancellation/non-conversion reasons. Feedback is stored in the audit log.
- Suspended groups retain read-only GET access to selected history pages and exports; mutating routes still require an eligible subscription. Subscription history and financial records remain preserved.

## Safety
- No mobile-money provider or integration was added. Existing payment-provider configuration remains unchanged.
- No existing subscription end dates or financial transactions are rewritten by the migration.
- New settings are added only when absent; the legacy default trial/grace settings are migrated to 30/7 days.

## Validation performed
- Python compileall passed.
- Service-level unit tests passed (34 tests).
- Jinja templates parsed successfully.
- Full HTTP/browser integration tests could not be run in this environment because the installed runtime lacks `werkzeug`, despite it being declared in `requirements.txt`.
- Not deployed to Render by this build.
