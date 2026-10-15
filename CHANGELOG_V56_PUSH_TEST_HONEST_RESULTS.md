# ChamaPay v56 — Push test delivery verification

## Changes
- The notification settings test now attempts an actual Web Push request to the saved browser subscription(s), instead of creating an ordinary in-app notification and immediately claiming it was queued.
- The test reports success only when at least one push provider accepts the request; otherwise it returns a visible error and logs the exception for server-side diagnosis.
- Expired/unsubscribed endpoints reported as HTTP 404 or 410 are removed from the subscription table so members can re-enable their device cleanly.
- Existing notification delivery remains asynchronous so ordinary ChamaPay actions are not held up by push-provider latency.

## Validation
- Python compilation passed for application, notification, push configuration, database, services and trust-job modules.
- JavaScript syntax check passed.
- `python -m unittest tests.test_push_config tests.test_services -v`: 36 tests passed.
- ZIP integrity check performed before release.

## Important deployment/device note
This package cannot by itself prove delivery to a physical phone. Deploy it, open Notification Settings on each member's device, enable notifications, and run the test. The test now distinguishes a provider-accepted request from a rejected request. OS/browser permissions, supported device/browser, network availability and persistent database storage are still required for reliable background push.
