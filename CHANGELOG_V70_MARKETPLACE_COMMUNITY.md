# ChamaPro V70 — Marketplace and Community layout repair

- Explicitly scopes high-contrast responsive layouts to both Marketplace (`.mx-*`) and Community (`.cx-*`) pages.
- Marketplace: two-column desktop provider cards, responsive single-column mobile cards, clear hero/search, category chips, filters, provider metadata and actions.
- Community: aligned hero/statistics, category navigation, integrated search/sort controls and readable discussion cards.
- Shared form controls use explicit sizing, spacing, borders and text colors to reduce theme inheritance problems.
- Bumped the shared stylesheet URL cache token to `v=70-market-community-fix` to force clients to request the updated CSS.
- No database schema changes or data migrations.

Validation is limited to source-level checks and archive integrity. Live browser rendering and end-to-end workflows still need staging verification.
