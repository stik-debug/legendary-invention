# ChamaPay v58 — Push notification registration fix

## Root causes addressed
- The application sends a strict Content Security Policy (`script-src 'self'`), which blocks inline JavaScript. Service-worker registration was in an inline script, so the browser could block it silently and `navigator.serviceWorker.ready` could wait indefinitely.
- The CSRF meta tag read `session['_csrf']` before the template's later `csrf_input()` created the token. Push subscribe/test requests could therefore send an empty CSRF token and be redirected instead of reaching the push routes.

## Changes
- Expose a safe `csrf_token()` template helper and render it in the CSRF meta tag before page scripts load.
- Move service-worker registration into external `static/js/app.js`, which is allowed by the CSP.
- Make the enable flow explicitly register `/sw.js` rather than waiting indefinitely on `navigator.serviceWorker.ready`.
- Version the app JS URL and service-worker cache to encourage clients to load the fix.

## Checks run
- `node --check static/js/app.js`
- `python -m py_compile app.py db.py notify.py push_config.py services.py trust_jobs.py`
- Parse all 105 Jinja templates.
- `python -m unittest tests.test_push_config tests.test_services` — 36 tests passed.

## Important limitation
This package has not been deployed to Render and real phone delivery has not been verified from this environment. After deploying, sign in on each phone/browser, enable notifications, and use the test notification. If the test fails, use its error text and Render logs to diagnose the next layer.
