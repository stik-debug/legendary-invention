# ChamaPay v65 — Modern Marketplace & Community

## Marketplace
- New hero with one big search box, stats and a "My quotes" link.
- Category chips with live counts (All, each category, ♥ Saved) that scroll on phones and wrap on desktop.
- Filters & sorting tucked into one panel: area, minimum rating (3★/4★+), sort (recommended, highest rated, recently listed, name).
- Cleaner provider cards: logo initial, Reviewed / Top rated / New badges, rating line, short description, one-tap Call / Email / Website, up to 2 recent reviews.
- NEW: Save providers with the heart (private to each member) and a Saved filter.
- "Request a quote" and "Quick enquiry" sit side by side on each card; "Compare several providers" is one collapsible panel.
- "List your service" form is collapsed until needed. All existing forms and routes are unchanged.

## Community
- New hero with live counts (posts, replies, voices).
- "Start a conversation" box with type chips (Discussion, Template, Event, Project); long guidelines collapsed at the bottom.
- Filter tabs with counts, "My posts", search, and sort (Latest, Most helpful, Most replies).
- Feed-style post cards with initials avatars, Helpful vote (toggle), reply count, Report, and Edit/Delete for your own posts.
- Replies are collapsible with threaded sub-replies; "Show more posts" paging.
- Fixed: Delete used an inline handler blocked by the app's security policy, so it deleted without asking. It now uses the app's confirmation.
- Community avatars are initials only so one chama's members' photos are never exposed to strangers.

## Database (additive only)
- New tables: `post_likes`, `provider_saves`.

## Tests
- New `tests/test_account_hub.py` (8 tests).
