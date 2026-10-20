# ChamaPower V73 integration

ChamaPower is an additive module in the existing ChamaPay app. It does not replace the existing app, user accounts, chama membership, financial ledger, payment workflows, subscriptions, or permissions.

## Included in this build
- Member-created collective project proposals, with categories, descriptions, target budgets and deadlines.
- One vote per member per project; members may update their vote while voting is open.
- Staff-only project status changes. Approval requires at least one YES vote and more YES than NO votes.
- Staff-only quote comparison records for supplier name, contact, quoted price, scope, validity and evidence reference.
- Quote status defaults to `UNVERIFIED`; no supplier is automatically presented as vetted.
- Staff-only project history for progress, decisions, delivery, receipt/evidence references and maintenance.
- Audit-log entries for proposals, votes, quotes, status changes and progress updates.
- Direct navigation from the chama menu and All Functions page.
- No demo projects, sample suppliers, mock quotes, payment simulation or financial ledger writes.

## Important boundaries
- A quote, budget, progress amount or evidence reference is not a confirmed payment and does not change ledger balances.
- ChamaPower does not hold pooled funds, process payments, guarantee returns, or verify suppliers automatically.
- Staff should independently validate supplier identity, original quotations, scope, taxes, delivery terms and expiry.
- Record real money movements through ChamaPay's existing finance workflows. After acquiring a group-owned asset, record it in the existing Assets module; the ChamaPower project history records the related decision and evidence references.
- This ZIP is source code only. It has not been deployed to or changed the live Render app. Back up the production database before deployment and test on a staging copy first.

## Verification notes
- The database schema initialization test passed in the working environment.
- Full test collection could not run in the working environment because the environment lacks the app dependency `Werkzeug`. Run `pip install -r requirements.txt` and `pytest -q` in a configured development environment before production deployment.
