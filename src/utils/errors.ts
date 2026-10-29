export class AppError extends Error {
  constructor(
    public statusCode: number,
    message: string,
    public code?: string,
    public details?: unknown
  ) {
    super(message);
    this.name = 'AppError';
  }
}
export class UnauthorizedError extends AppError {
  constructor(message = 'Unauthorized') { super(401, message, 'UNAUTHORIZED'); }
}
export class ForbiddenError extends AppError {
  constructor(message = 'Forbidden') { super(403, message, 'FORBIDDEN'); }
}
export class NotFoundError extends AppError {
  constructor(message = 'Not found') { super(404, message, 'NOT_FOUND'); }
}
export class ValidationError extends AppError {
  constructor(message: string, details?: unknown) {
    super(400, message, 'VALIDATION_ERROR', details);
  }
}
export class ConflictError extends AppError {
  constructor(message: string) { super(409, message, 'CONFLICT'); }
}
export class PaymentError extends AppError {
  constructor(message: string, code = 'PAYMENT_ERROR') { super(402, message, code); }
}
