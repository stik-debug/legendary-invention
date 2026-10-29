/**
 * ChamaPay — API + product frontend
 */
import 'dotenv/config';
import path from 'path';
import { fileURLToPath } from 'url';
import express, { Request, Response, NextFunction } from 'express';
import cors from 'cors';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import { runMigration, getDb, generateId, nowIso } from './db/index.js';
import { AppError } from './utils/errors.js';
import authRoutes from './routes/auth.routes.js';
import chamaRoutes from './routes/chama.routes.js';
import chamaOpsRoutes from './routes/chama-ops.routes.js';
import paymentRoutes from './routes/payment.routes.js';
import adminRoutes from './routes/admin.routes.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = parseInt(process.env.PORT || process.env.APP_PORT || '3000', 10);
const APP_ENV = process.env.APP_ENV || 'development';
const publicDir = path.join(process.cwd(), 'public');

runMigration();
seedPlansIfNeeded();

function seedPlansIfNeeded(): void {
  const db = getDb();
  const count = (db.prepare('SELECT COUNT(*) as c FROM subscription_plans').get() as { c: number }).c;
  if (count > 0) return;
  const now = nowIso();
  const plans = [
    { code: 'STARTER', name: 'Starter', price_kes: 500, max_members: 15, description: 'Up to 15 members. Per Chama / month.' },
    { code: 'GROWTH', name: 'Growth', price_kes: 1500, max_members: 70, description: 'Up to 70 members. Per Chama / month.' },
    { code: 'BUSINESS', name: 'Business', price_kes: 2000, max_members: 100, description: 'Up to 100 members. Per Chama / month.' },
  ];
  for (const p of plans) {
    db.prepare(
      `INSERT INTO subscription_plans (id, code, name, price_kes, max_members, is_active, description, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`
    ).run(generateId(), p.code, p.name, p.price_kes, p.max_members, p.description, now, now);
  }
  console.log('[seed] Default subscription plans created');
}

const app = express();

app.use(
  helmet({
    contentSecurityPolicy: false,
    crossOriginEmbedderPolicy: false,
  })
);
app.use(
  cors({
    origin: (process.env.CORS_ORIGIN || '*').split(',').map((s) => s.trim()),
    credentials: true,
  })
);
app.use(
  express.json({
    limit: '100kb',
    verify: (req, _res, buf) => {
      // Needed for Paystack webhook HMAC verification
      (req as any).rawBody = buf.toString('utf8');
    },
  })
);
app.use(
  rateLimit({
    windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS || '900000', 10),
    max: parseInt(process.env.RATE_LIMIT_MAX || '300', 10),
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Too many requests', code: 'RATE_LIMIT' },
  })
);

// Static frontend
app.use(express.static(publicDir, { index: false, maxAge: APP_ENV === 'production' ? '15m' : 0 }));

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', env: APP_ENV, service: 'chamapay', node: process.version });
});

app.get('/api/plans', (_req, res) => {
  const db = getDb();
  const plans = db
    .prepare(
      `SELECT code, name, price_kes, max_members, description
       FROM subscription_plans WHERE is_active = 1 ORDER BY price_kes`
    )
    .all();
  res.json({
    success: true,
    data: plans,
    note: 'Pricing is per Chama / month, not per member.',
  });
});

app.use('/api/auth', authRoutes);
app.use('/api/chamas', chamaRoutes);
app.use('/api/chamas/:chamaId', chamaOpsRoutes);
app.use('/api/payments', paymentRoutes);
app.use('/api/admin', adminRoutes);

// SPA-ish: serve index for /
app.get('/', (_req, res) => {
  res.sendFile(path.join(publicDir, 'index.html'));
});

// API 404
app.use('/api', (req, res) => {
  res.status(404).json({
    success: false,
    error: `Cannot ${req.method} ${req.path}`,
    code: 'NOT_FOUND',
  });
});

// Frontend 404 → home
app.use((req, res) => {
  if (req.accepts('html')) {
    return res.status(404).sendFile(path.join(publicDir, 'index.html'));
  }
  res.status(404).json({ success: false, error: 'Not found', code: 'NOT_FOUND' });
});

app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  if (err instanceof AppError) {
    return res.status(err.statusCode).json({
      success: false,
      error: err.message,
      code: err.code,
      details: APP_ENV === 'development' ? err.details : undefined,
    });
  }
  console.error('[error]', err);
  res.status(500).json({
    success: false,
    error: APP_ENV === 'development' ? err.message : 'Internal server error',
    code: 'INTERNAL_ERROR',
  });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[chamapay] http://0.0.0.0:${PORT} (${APP_ENV}) public=${publicDir}`);
});
