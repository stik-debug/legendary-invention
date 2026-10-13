# ChamaPay v37 — Alert and Chat Indicator Update

## Changes
- Replaced the message-bubble artwork in the global Alerts menu with a conventional notification bell.
- The bell turns red whenever unread alerts exist; its count badge remains red for counts of 1, 2, or more.
- Chat unread-count badges use the same red/white high-contrast styling.
- Menu and mobile navigation unread counters use the same red alert styling.
- Added a regression assertion checking that the standard bell icon is rendered.

## Verification
- Python compileall passed.
- Focused pytest collection could not start because Werkzeug is not installed in this environment (`ModuleNotFoundError: werkzeug`). No claim is made that those tests passed.
