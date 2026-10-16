# ChamaPay v63 — Profile photo experience refresh

## Changes
- Replaced the plain upload controls on the member dashboard and Chama Functions page with a responsive, modern profile-photo card.
- Added a circular current-photo/initial avatar, drag-and-drop target, clear image guidance, filename/size feedback, and an in-place preview before submission.
- Added client-side checks for supported image MIME types and the 3 MB limit; server-side validation remains authoritative.
- Added accessible focus styling, responsive mobile layout, and a disabled Save button until a valid image is selected.
- Reused one shared template partial to keep the member and chama upload experiences consistent.

## Limitations to know
- Existing server routes still store images under the app's static directory. On hosts with ephemeral filesystems, uploaded photos may not persist across redeploys/restarts; configure persistent disk or object storage for production.
- Current server-side validation checks filename extension, declared MIME type, size, and basic file signatures. Production hardening should decode and re-encode images (e.g. Pillow) and strip metadata before saving.
- This refresh does not change database records or existing photo URLs.
