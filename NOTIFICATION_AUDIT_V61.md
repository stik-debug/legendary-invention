# ChamaPay v61 — all-important-notification audit

## Scope
This update preserves the existing app and does not add mobile-money integration. It extends the existing notification helper, which creates the in-app notification and attempts web push for subscribed devices when push is enabled.

## Important event coverage checked
- Chat messages, meeting creation/cancellation and reminders, announcements, discussion replies: existing event handlers use the shared notification helper.
- Contributions, recorded/declined payments, loan decisions/disbursement/repayments, fines, guarantor requests, merry-go-round payments/turns, and daily obligation reminders: existing event handlers use the shared notification helper.
- Marketplace quote requests/offers/selections/completions/reviews, welfare request decisions, discussion replies, and guarantee requests: existing event handlers use the shared notification helper.
- Newly covered or strengthened in this update: member added directly or by CSV import; member removed; member voluntarily leaves (officials notified); meeting action assigned/completed (owner notified); dividend finalization requests (officials notified); each member notified when their dividend is finalized; maker-checker requester notified when a request is approved/rejected; subscription payment success/failure/cancellation/mismatch and manual subscription payment/access changes notify group officials.

## Delivery behavior and limits
- The shared `notify()` function creates an in-app notification and calls the web-push delivery path. Push is sent only when a user has a valid device subscription, push preference is enabled, and the server has valid VAPID keys plus `pywebpush` installed.
- Scheduled/one-shot jobs use synchronous push attempts so the process does not exit before a delivery attempt. Normal web requests still use a background thread to avoid delaying financial actions. This means a web-process shutdown can interrupt an in-flight push attempt; a durable retry/outbox is a separate reliability improvement and is not claimed in this package.
- A push provider accepting a message is not proof the operating system displayed it. Real phone delivery still depends on browser/OS permission, device subscription validity, connectivity, and deployment configuration.

## Validation
- Python compilation: passed (`python -m compileall -q .`).
- Focused tests: 38 tests passed (`test_push_scheduled_sync`, `test_services`, `test_push_config`).
- Full test discovery was attempted but 12 test modules could not import because this execution environment lacks `werkzeug`; this is an environment dependency issue, not a passing full-suite result.
- No real-phone or live Render end-to-end test was possible from this source-only build.
