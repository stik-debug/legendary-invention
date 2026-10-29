import crypto from 'crypto';
import bcrypt from 'bcryptjs';

const ROUNDS = parseInt(process.env.BCRYPT_ROUNDS || '12', 10);

export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, ROUNDS);
}
export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  return bcrypt.compare(password, hash);
}
export function generateSecureToken(bytes = 32): string {
  return crypto.randomBytes(bytes).toString('hex');
}
export function generateInviteToken(): string {
  return crypto.randomBytes(24).toString('base64url');
}
