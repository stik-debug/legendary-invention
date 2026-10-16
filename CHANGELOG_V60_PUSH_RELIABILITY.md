# ChamaPay v60 — Push reliability

- VAPID private key is converted from stored PEM to raw base64url before sending (pywebpush-compatible).
- Daily cron job waits for queued push threads before exiting, so scheduled reminders are actually delivered.
- Expired devices (HTTP 404/410) are removed automatically; sends have a 10s timeout.
- Devices that already allowed notifications are silently re-registered on signed-in pages.
- Service worker handles pushsubscriptionchange; cache bumped to v10, app.js to v=60.
- New tests: tests/test_push_delivery.py.

Deploy: set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY on Render if possible, redeploy, then each member enables notifications once and presses "Send test notification".
Not verified on a physical phone.

## v61 — Meeting reminders
- Fixed: the daily job only reminded meetings starting in a 2-hour window, so most meetings were never reminded.
- Members now get a "tomorrow" reminder (meeting within the next 36 hours) and a separate "today" reminder on the day.
- Each reminder is sent once per meeting. New test added in tests/test_community.py.
