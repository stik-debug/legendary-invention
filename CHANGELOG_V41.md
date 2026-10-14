# ChamaPay v41 — Pricing and sign-in reliability correction

This is a conservative update to the uploaded v40 strategy-and-continuity source. It preserves existing modules and records and adds no new payment integration.

## Changes
- Email/Gmail OTP is forced off in application configuration, even if an old Render environment variable says `EMAIL_OTP_ENABLED=1` or `auto`. Registration and normal password login must not depend on Gmail delivery. SMS OTP remains off.
- Render blueprint default for `EMAIL_OTP_ENABLED` is set to `0`.
- Startup migration enforces the published monthly subscription tiers on existing plan rows:
  - Starter: KSh 500, up to 20 members.
  - Growth: KSh 1,500, up to 70 members.
  - Pro: KSh 2,000, up to 100 members.
- Existing financial/membership records are not deleted or rewritten by this patch.
- Updated documentation so it no longer says Gmail OTP is automatically enabled.

## Verification limits
Static Python compilation, YAML parsing, archive integrity and focused source checks should be run on this package. Live Render sign-in, existing PostgreSQL migration, financial end-to-end workflows, backup restoration and real-device meeting tests still require deployment/staging verification. This source archive alone cannot prove the live service is healthy.
