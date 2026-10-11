# ChamaPay v31 follow-up fixes

- Added explicit Werkzeug dependency so the framework security imports are declared directly, not only transitively through Flask.
- Added Render environment-variable declarations for Resend/Brevo email, WhatsApp Cloud API, and Sentry. These remain secret values entered in the Render dashboard; no credentials are included in source.
- Added email/WhatsApp provider variables to the scheduled worker environment so background reminders can use the same configured providers as the web process.
- Mobile-money integration remains unchanged and is not enabled by these changes.

## Still required before production

Provider credentials, verified sender/domain, WhatsApp opt-in and approved templates, and a Sentry DSN must be configured in Render. The app's current maker-checker primitives are not yet wired into every financial mutation, and the language preference is not a complete translation of every screen. Do not represent these as fully completed until each financial route and template is covered by tests.


## Follow-up hardening in this archive
- Email outbox processing now runs when Resend or Brevo is configured, not only when SMTP_HOST is set.
- The scheduled worker processes the WhatsApp outbox as well as email and backups.
- Added an approvals screen and independent review workflow for contribution voids, repayment voids, fine-payment voids, and loan disbursement. The requester cannot approve their own request; financial business rules are rechecked when approval is applied.
- Removed a duplicate WhatsApp statement-share link.

## Validation note
Python compile checks pass. The full pytest suite could not be run in this environment because network access is unavailable to install missing project dependencies. Test this archive against a staging database before production deployment. A full translation of every page and offline queuing of financial mutations are not claimed as complete by this build.
