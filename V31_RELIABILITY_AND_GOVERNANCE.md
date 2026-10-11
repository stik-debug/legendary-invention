# ChamaPay v31 reliability and governance additions

## Notifications
- Transactional email supports Resend, Brevo, or existing SMTP. Resend takes priority, then Brevo, then SMTP.
- Added WhatsApp Cloud API send/queue/retry primitives. Configure `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`; obtain explicit member opt-in and use approved templates for business-initiated reminders outside the customer-service window.
- The existing in-app notification badge is now a blue chat bubble with a red unread counter, matching the supplied visual reference.
- Outbox retry is bounded to five attempts; provider acceptance is not proof of final delivery. Monitor provider delivery webhooks separately for production-grade delivery status.

## Governance and privacy foundations
- Added maker-checker request records and helper functions. A requester cannot approve their own request, and a request can be decided only once. Financial endpoints must call these primitives before committing a high-risk action; this schema addition alone does not silently change every existing approval path.
- Added versioned privacy-consent records to complement existing privacy requests. Implement user-facing export/deletion workflows only with verified identity and retention/legal-hold checks; never hard-delete accounting/audit records to satisfy a deletion request.

## PWA, language and monitoring
- Existing service worker and manifest remain in place; authenticated financial pages are intentionally not cached by the service worker to avoid exposing private statements on shared devices. Only static assets/offline shell are cached. Do not queue or replay financial POST actions offline until idempotency keys and server-side conflict checks are implemented.
- Added English/Kiswahili language preference selector in the app menu. This sets the preference; comprehensive translation of every screen remains a separate localization pass.
- `SENTRY_DSN` is documented for deployment; install and initialize the Sentry SDK in the hosting environment before expecting error events. Uptime alerts should be configured against the deployed health endpoint/host.

## Environment
Set `RESEND_API_KEY` or `BREVO_API_KEY` plus a verified `MAIL_FROM`, or retain SMTP configuration. WhatsApp API credentials are optional. No mobile-money integration was added.
