# ChamaPay payment and Chama membership upgrade

This upgrade deliberately does **not** connect a live payment gateway.

## Member payments

Members now have one Pay screen where they can choose:

- Contribution
- Savings
- Loan repayment
- Merry-go-round

A payment request records the member, Chama, amount, method and optional reference. It remains **PENDING** until a Chama Admin or Treasurer confirms it.

On confirmation the existing finance rules are called so the database updates automatically:

- Contribution/Savings -> contribution record + member savings + main ledger
- Loan repayment -> loan paid amount + installment schedule + main ledger
- Merry-go-round -> merry-go-round payment + MGR pot

No frontend success state can create a financial record by itself.

## Merry-go-round

- Members can submit their own current-turn payment for confirmation.
- Admins/Treasurers can confirm or reject those payments from the round page.
- Confirmed payments enter the MGR pot automatically.
- Before the first payment, a Chama Admin can add additional active Chama members to the round. New participants are appended to the turn order.
- The existing start-round screen already lets officials select the initial participants and choose random or explicit turn order.

## Chama membership

The existing member-add workflow is made clearer. A Chama Admin adds a Kenyan phone number; if the person is not registered, ChamaPay creates the member record and generates a one-time join code. The member uses that code during registration and then sees the Chama on their own account/phone.

## Mobile homepage

The logged-in homepage now explicitly presents **My Chamas** and makes each Chama immediately clickable.

## Existing digital features retained

- Digital member passport/ID
- Activity/financial timeline
- Notifications and merry-go-round reminders
- Unified financial ledger
- Receipts and reports
- PWA/mobile navigation
- Owner-only Control Center hidden from normal Chama navigation

## Live gateway later

A future Virtual Pay integration can replace the manual payment submission step while keeping the same internal payment request and reconciliation architecture.
