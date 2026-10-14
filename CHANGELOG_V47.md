# ChamaPro v47 — Open Community Discussions and Safety

- Made the Chama-to-Chama Community explicitly discussion-first: all signed-in users can create posts and reply to other users. Linking a post to a chama remains restricted to officials of that chama.
- Added threaded replies, reply counts, and report controls for posts and replies.
- Added server-side common-profanity and obvious repeated-character spam checks for new posts and replies. These are starter safeguards, not a guarantee that every harmful phrase is detected.
- Published clear community rules covering respectful disagreement, harassment, hate speech, spam/scams, impersonation, and sharing confidential personal/financial data.
- Added an owner-only moderation queue. Owners can hide reported posts/replies or dismiss reports; hidden content is retained for audit/history rather than deleted.
- Added additive database tables only; existing records are not dropped or rewritten.
- No mobile-money integration added.

Validation: Python compileall passed and the SQLite schema was initialized successfully with the three collaboration tables. Full web tests could not run in this environment because `werkzeug` is not installed.
