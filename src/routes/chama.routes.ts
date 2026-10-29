import { Router, Response, NextFunction } from 'express';
import * as chamaService from '../services/chama.service.js';
import {
  requireAuth,
  requireChamaAccess,
  requireChamaRole,
  AuthedRequest,
} from '../middleware/auth.js';

const router = Router();

/** Express params can be string | string[]; coerce to a single string. */
function param(value: string | string[] | undefined): string {
  if (value == null) return '';
  return Array.isArray(value) ? value[0] ?? '' : value;
}

// Create Chama (authenticated user becomes CHAMA_ADMIN, starts on TRIAL)
router.post('/', requireAuth, (req: AuthedRequest, res: Response, next: NextFunction) => {
  try {
    const result = chamaService.createChama({
      name: req.body.name,
      description: req.body.description,
      location: req.body.location,
      createdBy: req.user!.userId,
      planCode: req.body.planCode,
    });
    res.status(201).json({ success: true, data: result });
  } catch (e) {
    next(e);
  }
});

// Get chama details (tenant-scoped)
router.get(
  '/:chamaId',
  requireAuth,
  requireChamaAccess('chamaId'),
  (req: AuthedRequest, res: Response, next: NextFunction) => {
    try {
      const isSuper = req.user!.roles.includes('SUPER_ADMIN');
      const data = chamaService.getChama(param(req.params.chamaId), req.user!.userId, isSuper);
      res.json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

// List members
router.get(
  '/:chamaId/members',
  requireAuth,
  requireChamaAccess('chamaId'),
  (req: AuthedRequest, res: Response, next: NextFunction) => {
    try {
      const members = chamaService.listMembers(param(req.params.chamaId));
      res.json({ success: true, data: members });
    } catch (e) {
      next(e);
    }
  }
);

// Add member — enforces plan max_members server-side
router.post(
  '/:chamaId/members',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'SECRETARY'),
  (req: AuthedRequest, res: Response, next: NextFunction) => {
    try {
      const result = chamaService.addMember({
        chamaId: param(req.params.chamaId),
        userId: req.body.userId,
        role: req.body.role || 'MEMBER',
        addedBy: req.user!.userId,
      });
      res.status(201).json({ success: true, data: result });
    } catch (e) {
      next(e);
    }
  }
);

// Remove member (soft) — preserves financial history
router.delete(
  '/:chamaId/members/:memberId',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN'),
  (req: AuthedRequest, res: Response, next: NextFunction) => {
    try {
      chamaService.removeMember({
        chamaId: param(req.params.chamaId),
        memberId: param(req.params.memberId),
        removedBy: req.user!.userId,
      });
      res.json({ success: true });
    } catch (e) {
      next(e);
    }
  }
);

export default router;
