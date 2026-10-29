# ChamaPay

Production-oriented **Kenyan Chama management SaaS** backend.

> **Status: INCOMPLETE MVP** — Vertical 1 implemented: **Auth + Chama creation + Starter member-limit enforcement**.

Pricing is **per Chama / month**, not per member.

---

## What's implemented

| Feature | Details |
|---------|---------|
| Auth | Register, login, bcrypt password hashing, JWT |
| Roles | SUPER_ADMIN, CHAMA_ADMIN, TREASURER, SECRETARY, MEMBER (middleware ready) |
| Multi-tenancy | Every tenant record has `chama_id`; cross-Chama access → 403 |
| Chama | Create Chama → creator becomes CHAMA_ADMIN, starts on **TRIAL** |
| Plans (DB) | STARTER KES 500 / 15 members · GROWTH 1500 / 70 · BUSINESS 2000 / 100 |
| Member limits | **Server-side** enforcement (Starter 15 ok, 16 rejected) |
| Soft remove | Members deactivated; financial history preserved by design |
| Audit | Register, login, chama create, member add/remove logged |
| Security | Helmet, CORS, rate limit, no plaintext passwords, clean errors |

### Acceptance criteria covered

- **AC-001** Register  
- **AC-003** Login  
- **AC-010** Create Chama  
- **AC-013** Starter max 15 enforced  
- **AC-014** Soft-remove member without destroying history  

---

## Requirements

- **Node.js 22+** (uses built-in `node:sqlite` — no native SQLite addon)
- npm 9+

---

## Quick start (local test)

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/chamapay.git
cd chamapay

# 2. Install
npm install

# 3. Environment
cp .env.example .env
# Edit JWT_SECRET to a long random string before any real use

# 4. Migrate + seed plans
npx tsx src/db/migrate.ts

# 5. Smoke test (Auth + Chama + member limit)
npm run smoke
# Expect: === SMOKE PASSED: Auth + Chama + Starter limit enforcement ===

# 6. Run API
npm run dev
# → http://localhost:3000/health
```

### Manual API checks

```bash
# Health
curl http://localhost:3000/health

# Plans (public)
curl http://localhost:3000/api/plans

# Register
curl -s -X POST http://localhost:3000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","phone":"254712345678","password":"SecurePass1!","fullName":"Your Name"}'

# Login
curl -s -X POST http://localhost:3000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"emailOrPhone":"you@example.com","password":"SecurePass1!"}'
# Copy the token from the response

# Create Chama
curl -s -X POST http://localhost:3000/api/chamas \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"My Chama","description":"Savings group","planCode":"STARTER"}'
```

---

## Project structure

```
src/
  db/           schema.sql, migrate, connection (node:sqlite)
  middleware/   auth + tenant guards
  routes/       auth, chamas
  services/     auth.service, chama.service
  scripts/      smoke-auth-chama.ts
  types/        shared TypeScript types
  utils/        money (integer KES), crypto, errors
  server.ts     Express entry
```

---

## Not yet implemented

- Password reset, MFA  
- TestPaymentProvider / M-Pesa + webhook idempotency  
- Subscription lifecycle jobs (expiry → grace → suspend → reactivate)  
- Contributions, ledger, loans, fines, meetings, messaging  
- SUPER_ADMIN control centre  
- Reports / CSV export  
- Premium 3D frontend  

Next recommended vertical: **payments + subscription reactivation**.

---

## License

Private / proprietary until published otherwise.
