"""Business rules. Everything that touches money, plans or subscriptions lives here so it can be tested without a browser."""
import json
import re
import secrets
from datetime import datetime, timedelta

from db import IntegrityError, audit

ACCESS_OK = {'TRIAL', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD'}
ROLES = ('CHAMA_ADMIN', 'TREASURER', 'SECRETARY', 'MEMBER')
PAYMENT_DONE = {'SUCCESS', 'FAILED', 'CANCELLED'}


class BusinessError(Exception):
    pass


class PlanLimitError(BusinessError):
    pass


class SubscriptionInactive(BusinessError):
    pass


def now_utc():
    return datetime.utcnow()


def iso(d):
    return d.isoformat() if d else None


def parse(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise BusinessError(f'Invalid date: {s}')


def kes(cents):
    return f'KES {cents // 100:,}.{cents % 100:02d}'


def normalize_phone(raw):
    digits = re.sub(r'\D', '', raw or '')
    if not digits.endswith(('6', '7', '1', '0')):
        raise BusinessError('Invalid phone number.')
    if len(digits) < 9:
        raise BusinessError('Phone number too short.')
    if len(digits) > 13:
        raise BusinessError('Phone number too long.')
    if not digits.startswith('254'):
        if digits.startswith('0'):
            digits = '254' + digits[1:]
        else:
            digits = '254' + digits
    return digits


def create_loan(db, chama_id, user_id, amount_cents, reason, approved_by, due_date=None, now=None):
    now = now or now_utc()
    if amount_cents <= 0:
        raise BusinessError('Loan amount must be greater than zero.')

    with db.tx():
        loan_id = db.insert(
            'loans',
            chama_id=chama_id,
            user_id=user_id,
            amount_cents=amount_cents,
            principal_cents=amount_cents,
            repaid_cents=0,
            reason=(reason or '').strip()[:200],
            approved_by=approved_by,
            status='APPROVED',
            due_date=due_date or None,
            created_at=iso(now),
        )
        audit(
            db,
            approved_by,
            'LOAN_CREATED',
            'loan',
            loan_id,
            chama_id,
            {'user_id': user_id, 'amount_cents': amount_cents},
        )
    return loan_id


def record_loan_repayment(db, loan_id, amount_cents, paid_by, notes=None, now=None):
    now = now or now_utc()
    if amount_cents <= 0:
        raise BusinessError('Repayment amount must be greater than zero.')

    loan = db.one('SELECT * FROM loans WHERE id=?', (loan_id,))
    if not loan:
        raise BusinessError('Loan not found.')

    remaining = max(0, loan['principal_cents'] - loan['repaid_cents'])
    if amount_cents > remaining:
        raise BusinessError(f'Amount exceeds remaining principal of {kes(remaining)}.')

    with db.tx():
        db.insert(
            'loan_repayments',
            loan_id=loan_id,
            amount_cents=amount_cents,
            paid_by=paid_by,
            notes=(notes or '').strip()[:200] or None,
            created_at=iso(now),
        )
        new_repaid = loan['repaid_cents'] + amount_cents
        status = 'REPAID' if new_repaid >= loan['principal_cents'] else 'PARTIAL'
        db.execute(
            'UPDATE loans SET repaid_cents=?, status=? WHERE id=?',
            (new_repaid, status, loan_id),
        )
        audit(
            db,
            paid_by,
            'LOAN_REPAYMENT',
            'loan',
            loan_id,
            loan['chama_id'],
            {'amount_cents': amount_cents, 'remaining': max(0, loan['principal_cents'] - new_repaid)},
        )
    return new_repaid


def create_fine(db, chama_id, user_id, amount_cents, reason, created_by, now=None):
    now = now or now_utc()
    if amount_cents <= 0:
        raise BusinessError('Fine amount must be greater than zero.')

    with db.tx():
        fine_id = db.insert(
            'fines',
            chama_id=chama_id,
            user_id=user_id,
            amount_cents=amount_cents,
            reason=(reason or '').strip()[:200],
            created_by=created_by,
            status='OPEN',
            paid_cents=0,
            created_at=iso(now),
        )
        audit(
            db,
            created_by,
            'FINE_CREATED',
            'fine',
            fine_id,
            chama_id,
            {'user_id': user_id, 'amount_cents': amount_cents},
        )
    return fine_id


def pay_fine(db, fine_id, amount_cents, paid_by, receipt=None, now=None):
    now = now or now_utc()
    if amount_cents <= 0:
        raise BusinessError('Payment amount must be greater than zero.')

    fine = db.one('SELECT * FROM fines WHERE id=?', (fine_id,))
    if not fine:
        raise BusinessError('Fine not found.')

    remaining = max(0, fine['amount_cents'] - fine['paid_cents'])
    if amount_cents > remaining:
        raise BusinessError(f'Amount exceeds remaining balance of {kes(remaining)}.')

    with db.tx():
        db.insert(
            'fine_payments',
            fine_id=fine_id,
            amount_cents=amount_cents,
            paid_by=paid_by,
            receipt=(receipt or '').strip()[:80] or None,
            created_at=iso(now),
        )
        new_paid = fine['paid_cents'] + amount_cents
        status = 'PAID' if new_paid >= fine['amount_cents'] else 'PARTIAL'
        db.execute(
            'UPDATE fines SET paid_cents=?, status=? WHERE id=?',
            (new_paid, status, fine_id),
        )
        audit(
            db,
            paid_by,
            'FINE_PAID',
            'fine',
            fine_id,
            fine['chama_id'],
            {'amount_cents': amount_cents, 'remaining': max(0, fine['amount_cents'] - new_paid)},
        )
    return new_paid


def get_finance_summary(db, chama_id):
    loans = db.all('SELECT * FROM loans WHERE chama_id=? ORDER BY id DESC', (chama_id,))
    fines = db.all('SELECT * FROM fines WHERE chama_id=? ORDER BY id DESC', (chama_id,))
    active_loans = sum(l['amount_cents'] - l['repaid_cents'] for l in loans if l['status'] != 'REPAID')
    outstanding_fines = sum(f['amount_cents'] - f['paid_cents'] for f in fines if f['status'] != 'PAID')

    return {
        'loan_total': sum(l['amount_cents'] for l in loans),
        'loan_outstanding': active_loans,
        'fine_total': sum(f['amount_cents'] for f in fines),
        'fine_outstanding': outstanding_fines,
        'loans': loans,
        'fines': fines,
    }
