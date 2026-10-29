import { Router, Response, NextFunction } from 'express';
import * as authService from '../services/auth.service.js';
import { requireAuth, AuthedRequest } from '../middleware/auth.js';
import { AppError } from '../utils/errors.js';

const router = Router();

router.post('/register', async (req, res, next) => {
  try {
    const result = await authService.register(req.body);
    res.status(201).json({ success: true, data: result });
  } catch (e) {
    next(e);
  }
});

router.post('/login', async (req, res, next) => {
  try {
    const result = await authService.login(req.body);
    res.json({ success: true, data: result });
  } catch (e) {
    next(e);
  }
});

router.get('/me', requireAuth, (req: AuthedRequest, res: Response, next: NextFunction) => {
  try {
    const me = authService.getMe(req.user!.userId);
    res.json({ success: true, data: me });
  } catch (e) {
    next(e);
  }
});

export default router;
