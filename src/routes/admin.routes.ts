import { Router, Response, NextFunction } from 'express';
import { requireAuth, requireRoles, AuthedRequest } from '../middleware/auth.js';
import * as admin from '../services/admin.service.js';

const router = Router();

router.use(requireAuth, requireRoles('SUPER_ADMIN'));

router.get('/stats', (_req, res) => {
  res.json({ success: true, data: admin.platformStats() });
});

router.get('/chamas', (req, res) => {
  res.json({ success: true, data: admin.listChamas(String(req.query.q || '')) });
});

router.post('/chamas/:chamaId/suspend', (req: AuthedRequest, res: Response, next: NextFunction) => {
  try {
    const data = admin.suspendChama({
      chamaId: req.params.chamaId as string,
      reason: req.body.reason,
      adminId: req.user!.userId,
    });
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

router.post('/chamas/:chamaId/reactivate', (req: AuthedRequest, res: Response, next: NextFunction) => {
  try {
    const data = admin.reactivateChama({
      chamaId: req.params.chamaId as string,
      adminId: req.user!.userId,
      reason: req.body.reason,
    });
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

router.post('/chamas/:chamaId/extend', (req: AuthedRequest, res: Response, next: NextFunction) => {
  try {
    const data = admin.extendSubscription({
      chamaId: req.params.chamaId as string,
      days: Number(req.body.days) || 30,
      adminId: req.user!.userId,
    });
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

router.post('/chamas/:chamaId/manual-payment', (req: AuthedRequest, res: Response, next: NextFunction) => {
  try {
    const data = admin.recordManualPayment({
      chamaId: req.params.chamaId as string,
      amountKes: Number(req.body.amountKes),
      method: req.body.method || 'manual',
      reference: req.body.reference,
      notes: req.body.notes,
      adminId: req.user!.userId,
    });
    res.json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

export default router;
