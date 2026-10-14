# ChamaPay v48 — Community post ownership controls

- Original poster can edit their own active community post. The edit form validates title/body using the existing respectful-language and spam checks.
- Original poster can remove their own active community post. Removal is a soft delete (`status='DELETED'`); the row, replies, and audit history are retained.
- Other users cannot edit/delete posts they do not own; ownership is enforced server-side and unauthorized/non-active posts return 404.
- Post edit and deletion are recorded in the existing audit log.
- Added owner-only edit/delete controls to each post card.
- No financial records, subscriptions, or mobile-money features changed.
