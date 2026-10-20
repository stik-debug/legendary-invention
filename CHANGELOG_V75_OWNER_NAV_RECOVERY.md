# V75 — Owner Control Center and logout recovery

- Added a persistent header logout form outside the collapsible menu, so sign-out remains visible when the menu is closed or clipped on mobile.
- Added a direct owner Control Center link in the header for platform owners.
- If the configured `OWNER_EMAIL` matches a logged-in user but the database owner flag was accidentally cleared, the app restores `is_super_admin=1` for that exact account. The fallback trusts server environment configuration only; it does not use user-submitted values.
- Preserved existing routes and database tables; no financial ledger or member data migration is included in this fix.

## Verification

Python compilation and ZIP integrity checks are required before deployment. Run the full project test suite in the deployment environment before replacing production.
