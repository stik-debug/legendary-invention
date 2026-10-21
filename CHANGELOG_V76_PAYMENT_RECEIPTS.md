# ChamaPay V76 — confirmed payment PDF receipts

- Adds a styled downloadable PDF receipt to the existing payment status page.
- Receipt access is restricted to the payment's member or authorized finance staff in that chama.
- Receipt generation requires status SUCCESS and applied=1.
- Pending, claimed, review, and rejected payments cannot download a receipt.
- No database schema changes and no changes to payment verification or ledger-writing logic.
- Production deployment and full application tests are still required before release.
