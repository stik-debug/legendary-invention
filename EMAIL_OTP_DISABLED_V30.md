# ChamaPay V30 — Email OTP disabled

## Change
- Disabled email OTP by default in all environments, including Render production.
- Removed the `EMAIL_OTP_ENABLED=1` setting from `render.yaml` so deployment configuration no longer enables the broken flow.
- Signup and ordinary login now use the existing password flow without waiting for Gmail/email delivery.
- Direct visits to `/otp/verify` and `/otp/resend` are redirected to login while email OTP is disabled.
- Phone/password change routes use their existing non-email-OTP path.
- Updated README to reflect the current behavior.
- Kept SMTP settings intact because they may still be used for ordinary email notifications.

## Validation
- Python compileall: passed.
- ZIP integrity: passed after packaging.
- Full unit tests could not complete in this environment because the runtime dependencies are not installed (first reported error: `ModuleNotFoundError: No module named 'werkzeug'`). No claim is made that the full runtime suite passed.
- This does not test the live Render deployment; deploy and verify registration/login with a test account.
