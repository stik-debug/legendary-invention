/** Money: always integer KES. Never floating-point for financial math. */
export function toKesInteger(amount: number | string): number {
  const n = typeof amount === 'string' ? parseFloat(amount) : amount;
  if (!Number.isFinite(n) || n < 0) throw new Error('Invalid money amount');
  return Math.round(n);
}
export function formatKes(amount: number): string {
  return `KES ${amount.toLocaleString('en-KE')}`;
}
export function addMoney(a: number, b: number): number { return a + b; }
export function subtractMoney(a: number, b: number): number {
  const result = a - b;
  if (result < 0) throw new Error('Insufficient balance — cannot go negative');
  return result;
}
