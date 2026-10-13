# ChamaPay v39 — Member exit and account/chama closure

## Implemented
- Members can leave a chama from the chama home page.
- Leaving is blocked while the member has an open loan, unpaid fine, active loan guarantee, or is the only active Chama Admin. The admin role must be transferred first.
- The chama creator who is also a Chama Admin can close the chama after typing its exact name to confirm.
- Chama closure cancels the subscription, removes the group from public discovery, and marks active memberships as removed. It does **not** hard-delete financial, payment, or audit records.
- Members can request deletion of their own account directly from Security & Privacy by typing DELETE. The existing anonymisation guard prevents deletion while obligations or admin duties remain, revokes sessions, and retains necessary financial history without personal identifiers.
- All destructive operations use POST forms, CSRF protection, server-side role checks, and audit events.

## Validation
- `python -m compileall -q .` passed.
- Full application integration tests were not run in this environment; test with synthetic data and verify backups before production deployment.
