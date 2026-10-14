# ChamaPay v52 — Brand correction, chat refresh and marketplace polish

- Corrected the product branding to ChamaPay across source and docs.
- Refreshed the group chat layout with a group identity header, members-only context, clearer message hierarchy, softer depth, and a more usable message composer. Existing CSRF, routes and polling contract are retained.
- Improved marketplace provider cards, surface depth, focus on readable controls, hover states, and reduced-motion/data-saver behavior.
- No new mobile-money integration; no financial schema or transaction behavior changed by this UI/branding revision.
- Validation status is recorded in the handoff report; production/live browser tests require the deployed environment and runtime dependencies.

- Tightened quote-comparison privacy: requesters can compare all offers, while provider owners can only see their own submitted offers in a shared comparison group.
