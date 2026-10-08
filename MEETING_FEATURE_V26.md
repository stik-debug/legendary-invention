# ChamaPay V26 - Jitsi and SMS OTP removed

## Changes
- The Jitsi video room is removed: no Jitsi option when scheduling, no embedded meeting page, no `JITSI_BASE` setting.
- Online meetings now use the chama's own link only (Zoom, Google Meet, Teams; https only). Join button timing and the "Joined online" attendance hint are unchanged.
- Meetings already created with a Jitsi room stay in the list but show no Join button.
- SMS OTP is off by default and no longer forced on in production. Signup, login, forgotten password (chairperson/owner code), password change and phone change all work without an SMS. Set `SMS_OTP_ENABLED=1` to switch it back on later.
- Scheduling, agenda, attendance, action items and minutes are preserved.

## Checks performed
- Full test suite: 250 tests pass.
