# ChamaPay v62 — premium software features

This is an additive update built on v61. It does not add custody of group funds, payment processing, or mobile-money integration.

## Added

- **Monthly signed reconciliation PDF:** officials can choose a month and download a PDF snapshot with a SHA-256 digest and signed verification URL. It is tamper-evident, not an independent audit or proof of bank/provider balances.
- **Read-only bank/M-Pesa CSV matching:** upload/paste up to 500 rows (256 KB) with `date,amount_kes` and optional `reference,description`. It compares rows with existing ledger entries, prevents reusing a ledger entry for multiple statement rows, and distinguishes exact matches, possible matches, mismatches, ambiguous matches and unmatched rows. It never posts or edits money records.
- **Member participation passport:** a transparent score/badge based on recorded meeting attendance and, when a monthly contribution rule is configured, paid contribution periods. It is explicitly not a credit score and does not automatically change loan limits or interest.
- **Meeting resolution tracker:** all group members can view action items, owners, due dates and completion status; authorized officials retain editing controls.
- **Constitution-driven late contribution fine:** the group administrator can configure a due day (1–28) and fine. Automatic fines are disabled by default (`0`), only run after the configured due day, are idempotent per member/month, and are paused if the constitution contribution amount differs from the Savings contribution target. Review and align those amounts before enabling.
- **Opt-in WhatsApp reminders:** contribution, overdue loan instalment, action-item and 24-hour meeting reminders can enter the WhatsApp outbox only when the member explicitly opts in and Meta Cloud API credentials plus an approved template are configured. The approved template must accept one body text parameter.
- **Scheduled SMS queue processing:** the scheduled job now drains the SMS outbox after generating reminders, if Africa's Talking credentials are configured.

## Already present and reused

- Financial Check reconciliation and duplicate-reference flags.
- CSV financial history import and printable reports.
- Meeting minutes, attendance, action items and absence fines.
- Constitution change history, audit log, financial passport, SMS/push/email notification queues, and WhatsApp share links.

## Provider/configuration requirements

For automated WhatsApp reminders, set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_REMINDER_TEMPLATE`, `WHATSAPP_TEMPLATE_LANGUAGE`, and optionally `WHATSAPP_API_VERSION`. The Meta template must be approved and match the single body-text parameter. Users must opt in under Notification Settings. SMS requires Africa's Talking credentials. Browser push still depends on device permission, a registered subscription and stable VAPID/secret configuration.

## Limitations

- Photo OCR and AI extraction from a bank/M-Pesa statement image are not enabled in this build; use a CSV export. A real OCR/AI provider and its credentials would be required for photo extraction.
- Statement matching is an aid for human review, not a fraud verdict. External statements are not uploaded to or independently verified by a bank connection.
- The PDF signature is verified against this ChamaPay installation's `SECRET_KEY`; keep that secret stable across deploys. This is a tamper-evident software report, not a regulated independent audit.
- Real provider delivery and full live-phone end-to-end behavior have not been verified from this build environment.
