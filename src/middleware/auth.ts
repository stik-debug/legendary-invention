/**
 * Auth + Authorization middleware.
 * Membership is verified from the database (not only JWT claims) to avoid stale tokens.
 */
import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import { getDb } from '../db/index.js';
import { UnauthorizedError, ForbiddenError } from '../utils/errors.js';
import type { Role, AuthPayload } from '../types/index.js';

const JWT_SECRET = process.env.JWT_SECRET || 'dev-only-change-me-in-production-min-32-chars!!';

export interface AuthedRequest extends Request {
  user?: AuthPayload;
  chamaId?: string;
  memberRole?: Role;
}

function asString(value: unknown): string {
  if (value == null) return '';
  if (Array.isArray(value)) return String(value[0] ?? '');
  return String(value);
}

export function signToken(payload: AuthPayload): string {
  const expiresIn = process.env.JWT_EXPIRES_IN || '7d';
  return jwt.sign(payload, JWT_SECRET, { expiresIn } as jwt.SignOptions);
}

export function verifyToken(token: string): AuthPayload {
  return jwt.verify(token, JWT_SECRET) as AuthPayload;
}

export function requireAuth(req: AuthedRequest, _res: Response, next: NextFunction): void {
  try {
    const header = req.headers.authorization;
    if (!header?.startsWith('Bearer ')) {
      throw new UnauthorizedError('Missing or invalid Authorization header');
    }
    const token = header.slice(7);
    const payload = verifyToken(token);
    req.user = payload;
    next();
  } catch (err) {
    if (err instanceof UnauthorizedError) return next(err);
    next(new UnauthorizedError('Invalid or expired token'));
  }
}

export function requireRoles(...roles: Role[]) {
  return (req: AuthedRequest, _res: Response, next: NextFunction): void => {
    if (!req.user) return next(new UnauthorizedError());
    const has = roles.some((r) => req.user!.roles.includes(r));
    if (!has) return next(new ForbiddenError('Insufficient role'));
    next();
  };
}

/**
 * Require membership in the chama. Always verified from DB.
 * SUPER_ADMIN bypasses membership but still receives chamaId.
 */
export function requireChamaAccess(paramName = 'chamaId') {
  return (req: AuthedRequest, _res: Response, next: NextFunction): void => {
    if (!req.user) return next(new UnauthorizedError());

    const chamaId =
      asString(req.params[paramName]) ||
      asString(req.body?.chamaId) ||
      asString(req.query.chamaId);

    if (!chamaId) return next(new ForbiddenError('Chama context required'));

    const db = getDb();

    // Platform SUPER_ADMIN (role on JWT or any membership)
    if (req.user.roles.includes('SUPER_ADMIN')) {
      req.chamaId = chamaId;
      req.memberRole = 'SUPER_ADMIN';
      return next();
    }

    const member = db
      .prepare(
        `SELECT role FROM chama_members
         WHERE chama_id = ? AND user_id = ? AND is_active = 1`
      )
      .get(chamaId, req.user.userId) as { role: Role } | undefined;

    if (!member) {
      return next(new ForbiddenError('You are not a member of this Chama'));
    }

    req.chamaId = chamaId;
    req.memberRole = member.role;
    next();
  };
}

export function requireChamaRole(...roles: Role[]) {
  return (req: AuthedRequest, _res: Response, next: NextFunction): void => {
    if (!req.user) return next(new UnauthorizedError());
    if (req.user.roles.includes('SUPER_ADMIN')) return next();
    if (!req.memberRole || !roles.includes(req.memberRole)) {
      return next(new ForbiddenError('Chama role insufficient'));
    }
    next();
  };
}
