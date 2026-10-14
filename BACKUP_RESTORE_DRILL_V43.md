# ChamaPay V43 — backup restore drill

A backup is only considered usable after it has been restored into a separate staging database and checked. Do not run a restore drill against production.

## What the included drill validates

`backup.validate_backup(data)` validates gzip/JSON structure and table/row shape. `backup.restore_backup_to_empty_sqlite(data, target_path)` restores the logical backup into a **new, empty SQLite staging file**, then checks restored row counts and foreign-key integrity. It refuses to overwrite a non-empty database and refuses PostgreSQL targets.

## Important security and coverage notes

- Password hashes are retained in the backup so restored accounts are not silently destroyed; these are sensitive credentials and the backup must be stored in private, encrypted storage (S3 server-side encryption is configured by the backup job when supported).
- One-time reset/claim tokens and MFA secrets remain excluded. The restore drill therefore validates business records and schema relationships, not restoration of active MFA sessions or one-time tokens.
- Run the drill using a backup copied from the backup store, not a hand-made export, and inspect member, chama, ledger, contribution, loan, and audit counts plus a few known totals.
- SQLite staging restore is a verification utility. Production PostgreSQL recovery must be rehearsed separately on a disposable PostgreSQL staging database using a PostgreSQL-native backup/restore procedure.
