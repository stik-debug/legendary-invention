# ChamaPay v60 — Phone-screen payment reminder delivery

## Root cause addressed
Render Cron jobs run `scheduled.py` and exit when finished. Previously, every push was launched in a daemon thread. Daemon threads are stopped when the Cron process exits, so daily contribution and loan reminders could be written to in-app notifications without the phone push being delivered.

## Fix
- `scheduled.py` enables synchronous push delivery for the one-shot scheduled job.
- `notify.py` sends push delivery attempts synchronously only in that scheduled job; normal web requests remain asynchronous to avoid blocking user actions.
- Monthly contribution reminders are worded as payment reminders and continue to use the existing idempotency check.

## Expected behavior
When the daily Render Cron runs, unpaid/partly paid members receive a ChamaPay push notification if they have registered a device subscription, have push enabled, and their browser/OS permits notifications. Loan installment and meeting-action reminders use the same path. New notifications generated during normal web requests continue to trigger asynchronous web push.

## Validation
This source change must still be deployed to Render and tested on an actual enrolled phone. Browser/OS notification permission and iOS Home Screen installation requirements still apply. A push service accepting a message does not by itself prove that a phone banner appeared.
