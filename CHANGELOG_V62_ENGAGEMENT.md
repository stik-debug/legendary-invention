# ChamaPay v62 — Engagement and profile upgrades

Implemented on top of the supplied v61 archive without intentionally changing existing contribution, loan, fine, subscription, or audit records.

## Added
- Meeting RSVP (`I'm coming` / `I can't attend`) stored per member and meeting, with a live quorum forecast on the meeting page. Forecast uses active membership and a simple majority (`floor(n/2)+1`); it is informational, not a replacement for the chama's constitution or official attendance.
- Notice-read receipts and an official-only readership list, including a one-click reminder to members who have not marked a notice read.
- Payment promises with amount and expected date. Promises are separate from the financial ledger and never count as a payment.
- Chama Wrapped year-end recap using recorded contributions, loans, finalized dividend allocations, and meetings, with a WhatsApp share link.
- Referral code tracking page.
- Member and chama profile-photo upload routes, restricted to PNG/JPG/WebP, 3 MB maximum, with randomized filenames. Chama photo upload is limited to CHAMA_ADMIN.
- Notification preference fields and settings UI for alert categories and quiet hours.

## Explicit limitations / follow-up required
- Referral codes are tracked, but automatic qualifying-chama attribution and the two-sided free-month subscription credit are not yet wired into the subscription state machine. The UI states this rather than promising a reward.
- Category preferences and quiet hours are saved, but enforcement must be applied consistently at every notification producer/provider path before they can be represented as guaranteed delivery controls.
- Push action buttons are not enabled yet. Secure actions such as RSVP/payment acknowledgement need authenticated, CSRF-protected endpoints and action-specific push payloads; a generic service-worker click is not enough.
- Quorum is currently a simple majority forecast. If the chama has a configurable quorum rule, that rule should be connected instead of assuming majority.
- Photo uploads are stored under the app's static directory. For multi-instance/ephemeral hosting, use durable object storage before relying on these images in production.

## Validation
- Python source compiles with `python -m compileall -q .`.
- Full pytest suite could not be collected in this environment because runtime dependency `werkzeug` is not installed. This is an environment limitation, not a passing test result.
