# ChamaPay V26 — In-app meetings

## Changes
- Jitsi meetings now open in an embedded meeting room page within ChamaPay after the existing membership, meeting-time and join checks.
- The embedded room requests browser camera, microphone, fullscreen and screen-sharing permissions through the iframe `allow` attribute.
- Meeting details now direct members to join inside ChamaPay.
- Existing scheduling, agenda, attendance, action items and minutes are preserved.
- User-provided Zoom/Google Meet/Teams links continue to redirect to their provider; those services do not support reliable embedding in every browser.
- No meeting recording is added. No SMS OTP or M-Pesa integration is required by this change.

## Important limitation
The video service is hosted by Jitsi (`meet.jit.si` by default), not by ChamaPay. Browser policies, service availability, network restrictions or Jitsi's embedding rules can affect whether the embedded room loads. A live deployment/browser test is still required; static checks cannot prove real-time audio/video works on every device.

## Checks performed
- Python source compilation
- Jinja template syntax parsing (if Jinja2 is available in the build environment)
- ZIP archive integrity check
