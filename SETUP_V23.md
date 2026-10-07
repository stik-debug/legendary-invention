# ChamaPay V23 — launch setup notes

## 1. Payment method for members

A Chama Chairperson / Chama Admin can open:

`Chama Home → Payment setup`

or:

`Pay → Payment setup`

They can configure the primary method as:

- M-Pesa PayBill
- M-Pesa Till / Buy Goods
- M-Pesa Send Money
- Bank transfer
- Cash
- Other

The admin enters the payment number/account, account or business name where applicable, and clear instructions. Members see these details directly on the **Pay** screen.

This is **manual payment recording**, not M-Pesa API integration. ChamaPay does not hold the Chama's money or initiate an M-Pesa transaction.

## 2. Secure member joining

The Chairperson / Chama Admin first adds the member using the member's Kenyan phone number.

If the person does not already have a ChamaPay account, ChamaPay generates a one-time 8-digit join code. The code is shown directly to the authenticated admin with **Copy join code** and **Share code** actions.

The member must:

1. Register using the same phone number the admin added.
2. Enter the 8-digit join code.
3. Complete SMS phone verification.

The join code is hashed in the database, shown in plaintext only to the admin at issuance, and destroyed after successful registration. Five incorrect attempts lock the code until the admin issues a new one.

The admin can issue a new code from the member list with **New join code**. The old code stops working.

## 3. Branding

The supplied ChamaPay logo is now the application logo in:

- Desktop/mobile header
- PWA 192px icon
- PWA 512px icon
- Apple touch icon
- Browser favicon
- Offline screen

The service-worker cache was bumped so installed PWAs can receive the new branding.

## 4. Render

The project remains configured for Render with:

- Python 3.13.5
- Gunicorn
- PostgreSQL through `DATABASE_URL`
- Africa's Talking SMS environment variables
- `/healthz` health check

The deployment configuration follows Render's Flask deployment model: install `requirements.txt` and start the Flask WSGI application with Gunicorn.

## 5. Important security note

Do not put `AUTH_SECRET`, `DATABASE_URL` credentials, owner passwords, or `AT_API_KEY` into source code or chat. Keep them in Render Environment Variables.
