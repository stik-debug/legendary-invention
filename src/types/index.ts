/**
 * ChamaPay Core Types
 * Production-ready type definitions for multi-tenant Kenyan Chama SaaS
 */

export type Role = 'SUPER_ADMIN' | 'CHAMA_ADMIN' | 'TREASURER' | 'SECRETARY' | 'MEMBER';

export type SubscriptionStatus =
  | 'TRIAL'
  | 'ACTIVE'
  | 'PAST_DUE'
  | 'GRACE_PERIOD'
  | 'SUSPENDED'
  | 'CANCELLED';

export type PaymentStatus = 'PENDING' | 'SUCCESS' | 'FAILED' | 'CANCELLED' | 'TIMEOUT';

export type LoanStatus =
  | 'PENDING'
  | 'APPROVED'
  | 'REJECTED'
  | 'DISBURSED'
  | 'REPAYING'
  | 'COMPLETED'
  | 'DEFAULTED';

export type FineStatus = 'PENDING' | 'PARTIAL' | 'PAID' | 'WAIVED';

export type InviteStatus = 'PENDING' | 'ACCEPTED' | 'REJECTED' | 'EXPIRED';

export type LedgerType =
  | 'CONTRIBUTION'
  | 'LOAN_DISBURSEMENT'
  | 'LOAN_REPAYMENT'
  | 'FINE'
  | 'FINE_PAYMENT'
  | 'EXPENSE'
  | 'OTHER_INCOME'
  | 'SUBSCRIPTION_PAYMENT';

export interface User {
  id: string;
  email: string;
  phone: string;
  password_hash: string;
  full_name: string;
  is_active: boolean;
  is_test_data: boolean;
  email_verified: boolean;
  phone_verified: boolean;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface Chama {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  location: string | null;
  is_active: boolean;
  is_test_data: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ChamaMember {
  id: string;
  chama_id: string;
  user_id: string;
  role: Role;
  is_active: boolean;
  joined_at: string;
  left_at: string | null;
  created_at: string;
}

export interface SubscriptionPlan {
  id: string;
  code: string;
  name: string;
  price_kes: number; // stored as integer cents or whole KES * 100 for precision
  max_members: number;
  is_active: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface Subscription {
  id: string;
  chama_id: string;
  plan_id: string;
  status: SubscriptionStatus;
  trial_ends_at: string | null;
  current_period_start: string;
  current_period_end: string;
  grace_ends_at: string | null;
  cancelled_at: string | null;
  suspended_at: string | null;
  suspension_reason: string | null;
  is_test_data: boolean;
  created_at: string;
  updated_at: string;
}

export interface Payment {
  id: string;
  chama_id: string;
  subscription_id: string | null;
  amount_kes: number; // integer (KES units * 100 or whole shillings — we use whole KES as integer)
  currency: string;
  status: PaymentStatus;
  provider: string;
  provider_ref: string | null;
  phone: string | null;
  idempotency_key: string;
  metadata: string | null; // JSON
  verified_at: string | null;
  is_test_data: boolean;
  created_at: string;
  updated_at: string;
}

export interface PaymentWebhook {
  id: string;
  provider: string;
  provider_ref: string;
  payload: string;
  processed: boolean;
  processing_result: string | null;
  created_at: string;
}

export interface Contribution {
  id: string;
  chama_id: string;
  member_id: string; // chama_members.id
  user_id: string;
  amount_kes: number;
  contribution_date: string;
  payment_method: string | null;
  reference: string | null;
  status: string;
  notes: string | null;
  created_by: string;
  is_test_data: boolean;
  created_at: string;
}

export interface LedgerTransaction {
  id: string;
  chama_id: string;
  type: LedgerType;
  amount_kes: number; // positive = credit to chama, negative = debit
  balance_after: number | null;
  reference_type: string | null;
  reference_id: string | null;
  description: string | null;
  created_by: string | null;
  is_test_data: boolean;
  created_at: string;
}

export interface Loan {
  id: string;
  chama_id: string;
  member_id: string;
  user_id: string;
  amount_kes: number;
  interest_rate: number; // basis points or percentage * 100
  outstanding_kes: number;
  status: LoanStatus;
  purpose: string | null;
  applied_at: string;
  approved_at: string | null;
  approved_by: string | null;
  disbursed_at: string | null;
  due_date: string | null;
  is_test_data: boolean;
  created_at: string;
  updated_at: string;
}

export interface LoanRepayment {
  id: string;
  loan_id: string;
  chama_id: string;
  amount_kes: number;
  payment_method: string | null;
  reference: string | null;
  created_by: string;
  is_test_data: boolean;
  created_at: string;
}

export interface Fine {
  id: string;
  chama_id: string;
  member_id: string;
  user_id: string;
  amount_kes: number;
  outstanding_kes: number;
  reason: string;
  due_date: string | null;
  status: FineStatus;
  created_by: string;
  is_test_data: boolean;
  created_at: string;
  updated_at: string;
}

export interface Meeting {
  id: string;
  chama_id: string;
  title: string;
  meeting_date: string;
  location: string | null;
  agenda: string | null;
  created_by: string;
  is_test_data: boolean;
  created_at: string;
}

export interface MeetingAttendance {
  id: string;
  meeting_id: string;
  chama_id: string;
  member_id: string;
  user_id: string;
  present: boolean;
  notes: string | null;
  recorded_by: string;
  created_at: string;
}

export interface Announcement {
  id: string;
  chama_id: string;
  title: string;
  body: string;
  published: boolean;
  created_by: string;
  is_test_data: boolean;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  chama_id: string;
  sender_id: string;
  body: string;
  is_test_data: boolean;
  created_at: string;
}

export interface MessageRead {
  id: string;
  message_id: string;
  user_id: string;
  read_at: string;
}

export interface Notification {
  id: string;
  user_id: string;
  chama_id: string | null;
  type: string;
  title: string;
  body: string;
  read: boolean;
  metadata: string | null;
  created_at: string;
}

export interface Invite {
  id: string;
  chama_id: string;
  token: string;
  email: string | null;
  phone: string | null;
  intended_role: Role;
  invited_by: string;
  status: InviteStatus;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
}

export interface AuditLog {
  id: string;
  actor_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  chama_id: string | null;
  metadata: string | null;
  ip_address: string | null;
  created_at: string;
}

export interface AuthPayload {
  userId: string;
  email: string;
  roles: Role[];
  chamaIds: string[];
}

export interface ApiError {
  error: string;
  code?: string;
  details?: unknown;
}
