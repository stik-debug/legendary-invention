# ChamaPay v64 — Compact profile pictures

## Fixed from v63
- Upload could not work: the v63 card used an inline script, which the app's security policy (`script-src 'self'`) blocks, so the Save button stayed disabled. The picker now uses a script file (`static/js/photo.js`).
- Photos were saved as files in `static/`, which Render erases on every deploy. They are now stored in the database (`profile_images` table), so they survive redeploys.
- Photos were only visible inside the upload card. They now show on the chama header, the members list and the meeting attendance sheet.
- No way to remove a photo. Added Remove for members and for chama admins.

## New look
- Small 64px round avatar with a camera badge, placed in the page header (My Dashboard, All Chama Functions) instead of a large card above the page.
- Tap, pick a photo; it is cropped square, shrunk to 384px on the phone, previewed and saved automatically. No disabled button.
- Members see the group picture read-only; only CHAMA_ADMIN can change it.

## Safety
- File type is checked from the real file bytes (PNG/JPEG/WebP only; SVG and others rejected), 2 MB server limit, served with `nosniff`.
- A photo is visible only to the person and to people who share an active chama with them; the group picture only to its active members.
- Old photos uploaded under v62/v63 keep working but are not copied to the database; re-upload once to make them permanent.

## Tests
- New `tests/test_account_photos.py` (6 tests). Full suite: same 15 failures/errors as the unmodified v63.

## v64.1
- The group picture control is now in the chama home header (next to the chama name) for CHAMA_ADMIN. Other members see the picture read-only. It is also still available on All Chama Functions.
