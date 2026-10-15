# ChamaPay v55 — Push delivery reliability fix

- Fixed the service-worker scope: `/sw.js` is served at the site root with `Service-Worker-Allowed: /`, so it can control ChamaPay pages. The old `/static/sw.js` path was scoped only to `/static/` and could cause `navigator.serviceWorker.ready` to wait indefinitely on app pages.
- Added a persistent VAPID keypair stored in the application database when the hosting environment does not supply `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`. Keys remain stable across restarts as long as the database persists. Explicit environment keys still take precedence.
- Added push status diagnostics and a test-notification endpoint/button to show whether keys, the delivery package, and device registration are present.
- Enabling notifications on a device also opts that account into push preference; users can still turn push off in settings.
- Routed the remaining direct notification insertions in subscription reminders and daily obligation reminders through the shared notification sender so push is queued consistently.
- Added server logs for push delivery failures instead of silently discarding all errors.
- Kept lock-screen chat notifications free of message body content.

## Device notes

- HTTPS is required.
- Each member must enable notification permission and register each phone/browser once.
- On iPhone, web push generally requires iOS/iPadOS 16.4+ and ChamaPay added to the Home Screen, then opened as an installed web app.
- Browser/OS settings may block notifications; use the in-app test button to diagnose registration and delivery.
