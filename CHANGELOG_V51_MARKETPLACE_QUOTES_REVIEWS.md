# ChamaPay v51 — Marketplace trust, reviews and quote comparison

## Added
- Provider ratings and recent written reviews on reviewed marketplace listings.
- Quote requests with a scope, optional budget guide, needed-by note and optional chama association; one brief can be sent to up to eight selected providers for a genuine multi-provider comparison.
- Provider-owned listing inbox to submit or update a written quote, with price in KSh, scope/exclusions, timeline and validity.
- Side-by-side quote comparison across multiple selected providers with total price, scope, timeline, validity, provider location and available experience-based rating; accepting one offer closes sibling requests in that comparison group.
- Quote selection tracking and completion confirmation by both requester and provider.
- Reviews unlocked only after both parties confirm a selected engagement is completed; one review per request, enforced by a database unique constraint.
- Audit events for quote requests, quote submission, quote acceptance, completion confirmation and review publication.
- Basic checks against profanity/spam and explicit guaranteed-return / risk-free profit claims in new listing descriptions, quote scopes and reviews.
- Trust guidance: ratings are not certifications, platform review is not an endorsement, and selecting a quote does not move money or create a signed contract.

## Safety and compatibility
- Uses additive `CREATE TABLE IF NOT EXISTS` schema definitions for quote requests, quotes and reviews, plus an additive comparison-group column; existing provider listings and marketplace interest records are preserved.
- Quote response is restricted to the account that owns the reviewed provider listing; request details and comparisons are limited to the requester and that listing owner.
- No mobile-money integration, payment processing, or automatic provider payments added.
- Keyword screening is a basic safeguard, not a guarantee that every misleading claim is detected. Provider credentials and reviews still require independent judgment/moderation.

## Validation
- Python syntax compilation passed for changed Python modules.
- Jinja template parsing passed for marketplace, quote workspace and comparison templates.
- Fresh SQLite schema initialization passed and created all three new tables.
- Full Flask route/browser integration tests were not run in this environment because Flask is not installed here. This package has not been deployed to the live ChamaPay site.
