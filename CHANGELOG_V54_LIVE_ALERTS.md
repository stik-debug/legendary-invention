# ChamaPay v54 — Live alerts and browser push

- Added app-wide notification polling and accessible, dismissible on-screen toast pop-ups.
- Added background web-push support through the existing service worker.
- Added authenticated, CSRF-protected push subscription and unsubscribe endpoints.
- Added push subscription storage and notification delivery from the existing notification helper.
- Added group-chat message alerts for other active members, linking directly to the relevant chat.
- Added alerts for marketplace interests, provider review decisions, quote requests, submitted offers, selected/closed comparisons, completion confirmations, reviews, welfare requests/decisions, and replies to community discussions.
- Browser push requires VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY in the hosting environment and user permission on each device.
- If VAPID is not configured, on-screen live alerts still work while ChamaPay is open; true closed-browser push is unavailable until configured.


## Production setup for background push

1. Install dependencies from `requirements.txt` (includes `pywebpush`).
2. Generate a VAPID P-256 key pair using Python `cryptography`; put the base64url-encoded uncompressed public point in `VAPID_PUBLIC_KEY`, and the PEM private key in `VAPID_PRIVATE_KEY`. Keep the private key secret.
3. Set `VAPID_CLAIMS_SUBJECT` to a monitored `mailto:` support address.
4. Deploy over HTTPS. Each user must open Notification Settings on each browser/device and press **Enable device notifications**; the browser permission prompt must be accepted.
5. Test with the browser closed and send a real chama alert. If VAPID is not configured, the UI will explicitly say so and foreground polling remains available.

Chat push alerts deliberately avoid including the message body, reducing sensitive text exposure on lock screens.
