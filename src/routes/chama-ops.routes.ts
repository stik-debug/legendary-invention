import { Router, Response, NextFunction } from 'express';
import {
  requireAuth,
  requireChamaAccess,
  requireChamaRole,
  AuthedRequest,
} from '../middleware/auth.js';
import * as finance from '../services/finance.service.js';
import * as loan from '../services/loan.service.js';
import * as fine from '../services/fine.service.js';
import * as meeting from '../services/meeting.service.js';
import * as message from '../services/message.service.js';
import * as payment from '../services/payment.service.js';

const router = Router({ mergeParams: true });

function param(v: string | string[] | undefined): string {
  if (v == null) return '';
  return Array.isArray(v) ? v[0] ?? '' : v;
}

// Dashboard summary
router.get(
  '/summary',
  requireAuth,
  requireChamaAccess('chamaId'),
  (req: AuthedRequest, res: Response, next: NextFunction) => {
    try {
      const id = param(req.params.chamaId);
      res.json({
        success: true,
        data: {
          contributionsTotal: finance.getContributionTotal(id),
          savingsNet: finance.getSavingsTotal(id),
          loansOutstanding: loan.outstandingLoansTotal(id),
          finesOutstanding: fine.outstandingFinesTotal(id),
          unreadMessages: message.unreadCount(id, req.user!.userId),
        },
      });
    } catch (e) {
      next(e);
    }
  }
);

// Contributions
router.get('/contributions', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    res.json({ success: true, data: finance.listContributions(param(req.params.chamaId)) });
  } catch (e) {
    next(e);
  }
});

router.post(
  '/contributions',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'TREASURER'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = finance.recordContribution({
        chamaId: param(req.params.chamaId),
        memberId: req.body.memberId,
        amountKes: req.body.amountKes,
        contributionDate: req.body.contributionDate,
        paymentMethod: req.body.paymentMethod,
        reference: req.body.reference,
        notes: req.body.notes,
        createdBy: req.user!.userId,
      });
      res.status(201).json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

router.get('/ledger', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    res.json({ success: true, data: finance.listLedger(param(req.params.chamaId)) });
  } catch (e) {
    next(e);
  }
});

// Loans
router.get('/loans', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    res.json({ success: true, data: loan.listLoans(param(req.params.chamaId)) });
  } catch (e) {
    next(e);
  }
});

router.post('/loans', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    const data = loan.applyLoan({
      chamaId: param(req.params.chamaId),
      memberId: req.body.memberId,
      amountKes: req.body.amountKes,
      purpose: req.body.purpose,
      userId: req.user!.userId,
    });
    res.status(201).json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

router.post(
  '/loans/:loanId/approve',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'TREASURER'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = loan.approveLoan({
        chamaId: param(req.params.chamaId),
        loanId: param(req.params.loanId),
        approvedBy: req.user!.userId,
        approve: req.body.approve !== false,
      });
      res.json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

router.post(
  '/loans/:loanId/disburse',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'TREASURER'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = loan.disburseLoan({
        chamaId: param(req.params.chamaId),
        loanId: param(req.params.loanId),
        by: req.user!.userId,
      });
      res.json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

router.post(
  '/loans/:loanId/repay',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'TREASURER', 'MEMBER'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = loan.repayLoan({
        chamaId: param(req.params.chamaId),
        loanId: param(req.params.loanId),
        amountKes: req.body.amountKes,
        by: req.user!.userId,
        paymentMethod: req.body.paymentMethod,
        reference: req.body.reference,
      });
      res.json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

// Fines
router.get('/fines', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    res.json({ success: true, data: fine.listFines(param(req.params.chamaId)) });
  } catch (e) {
    next(e);
  }
});

router.post(
  '/fines',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'SECRETARY', 'TREASURER'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = fine.createFine({
        chamaId: param(req.params.chamaId),
        memberId: req.body.memberId,
        amountKes: req.body.amountKes,
        reason: req.body.reason,
        dueDate: req.body.dueDate,
        createdBy: req.user!.userId,
      });
      res.status(201).json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

router.post(
  '/fines/:fineId/pay',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'TREASURER'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = fine.payFine({
        chamaId: param(req.params.chamaId),
        fineId: param(req.params.fineId),
        amountKes: req.body.amountKes,
        by: req.user!.userId,
      });
      res.json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

// Meetings
router.get('/meetings', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    res.json({ success: true, data: meeting.listMeetings(param(req.params.chamaId)) });
  } catch (e) {
    next(e);
  }
});

router.post(
  '/meetings',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'SECRETARY'),
  (req: AuthedRequest, res, next) => {
    try {
      const data = meeting.createMeeting({
        chamaId: param(req.params.chamaId),
        title: req.body.title,
        meetingDate: req.body.meetingDate,
        location: req.body.location,
        agenda: req.body.agenda,
        createdBy: req.user!.userId,
      });
      res.status(201).json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

// Messages
router.get('/messages', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    res.json({
      success: true,
      data: message.listMessages(param(req.params.chamaId), req.user!.userId),
      unread: message.unreadCount(param(req.params.chamaId), req.user!.userId),
    });
  } catch (e) {
    next(e);
  }
});

router.post('/messages', requireAuth, requireChamaAccess('chamaId'), (req: AuthedRequest, res, next) => {
  try {
    const data = message.sendMessage({
      chamaId: param(req.params.chamaId),
      senderId: req.user!.userId,
      body: req.body.body,
    });
    res.status(201).json({ success: true, data });
  } catch (e) {
    next(e);
  }
});

// Subscription payment
router.post(
  '/pay',
  requireAuth,
  requireChamaAccess('chamaId'),
  requireChamaRole('CHAMA_ADMIN', 'TREASURER'),
  async (req: AuthedRequest, res, next) => {
    try {
      const data = await payment.initiateSubscriptionPayment({
        chamaId: param(req.params.chamaId),
        phone: req.body.phone,
        userId: req.user!.userId,
      });
      res.status(201).json({ success: true, data });
    } catch (e) {
      next(e);
    }
  }
);

export default router;
