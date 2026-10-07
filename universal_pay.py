"""Universal manual payment requests.

No payment gateway is used here. A member declares what they paid, how they paid,
and a reference/proof. A Chama finance official confirms it. Only after confirmation
is the real finance/merry-go-round record created, so the existing ledgers remain the
single source of truth.
"""
from datetime import date

from flask import abort, flash, g, redirect, render_template, request, url_for

import finance as F
import chama_pay as CP
import mgr as M
from db import audit, IntegrityError
from notify import notify, notify_roles
from services import BusinessError, iso, now_utc

PURPOSES = {
    'CONTRIBUTION': 'Contribution',
    'SAVING': 'Savings',
    'LOAN': 'Loan repayment',
    'MGR': 'Merry-go-round',
}

METHODS = F.METHODS


def _member(db, chama_id, user_id):
    if not db.one("SELECT 1 FROM chama_members WHERE chama_id=? AND user_id=? AND status='ACTIVE'", (chama_id, user_id)):
        raise BusinessError('You must be an active member of this Chama.')


def _amount(raw):
    return F.parse_kes(raw, 100)


def targets(db, chama_id, user_id):
    """Return current payment choices available to this member."""
    period = date.today().isoformat()[:7]
    expected = int(db.val('SELECT contribution_cents FROM chamas WHERE id=?', (chama_id,), 0) or 0)
    paid = int(db.val("SELECT COALESCE(SUM(amount_cents),0) FROM contributions WHERE chama_id=? AND user_id=? AND period=? AND status='PAID'", (chama_id, user_id, period), 0) or 0)
    loans = db.all("SELECT id,total_due_cents,paid_cents FROM loans WHERE chama_id=? AND user_id=? AND status='ACTIVE' ORDER BY id DESC", (chama_id, user_id))
    rounds = []
    for rnd in db.all("SELECT * FROM mgr_rounds WHERE chama_id=? AND status='ACTIVE' ORDER BY id DESC", (chama_id,)):
        turn = M.turn_status(db, rnd)
        if turn and any(u['user_id'] == user_id for u in turn['unpaid']):
            rounds.append({'id': rnd['id'], 'name': rnd['name'], 'amount_cents': rnd['amount_cents'],
                           'recipient': turn['slot']['name'], 'due_date': turn['slot']['due_date']})
    return {
        'period': period,
        'expected': expected,
        'paid': paid,
        'contribution_left': max(0, expected - paid),
        'loans': loans,
        'rounds': rounds,
    }


def create_request(db, chama_id, user_id, purpose, target_id, amount_cents, method, reference, note, actor=None, now=None):
    now = now or now_utc()
    _member(db, chama_id, user_id)
    purpose = (purpose or '').upper().strip()
    if purpose not in PURPOSES:
        raise BusinessError('Choose Contribution, Savings, Loan repayment or Merry-go-round.')
    if method not in METHODS:
        raise BusinessError('Choose Cash, M-Pesa or Bank.')
    amount_cents = int(amount_cents)
    if amount_cents < 100 or amount_cents > 500_000_000:
        raise BusinessError('Enter a valid payment amount.')
    reference = (reference or '').strip().upper()[:80] or None
    note = (note or '').strip()[:300] or None

    # Validate the target and cap before creating a pending request.
    if purpose in ('CONTRIBUTION', 'SAVING'):
        target_id = None
    elif purpose == 'LOAN':
        loan = db.one("SELECT * FROM loans WHERE id=? AND chama_id=? AND user_id=? AND status='ACTIVE'", (target_id, chama_id, user_id))
        if not loan:
            raise BusinessError('Choose one of your active loans.')
        remaining = int(loan['total_due_cents']) - int(loan['paid_cents'])
        if amount_cents > remaining:
            raise BusinessError(f'The loan balance is only KES {remaining / 100:,.0f}.')
    elif purpose == 'MGR':
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            raise BusinessError('Choose the merry-go-round you are paying.')
        rnd = M.get_round(db, chama_id, target_id)
        turn = M.turn_status(db, rnd)
        if not turn or not any(u['user_id'] == user_id for u in turn['unpaid']):
            raise BusinessError('You do not currently owe a payment on that merry-go-round turn.')
        if amount_cents != int(rnd['amount_cents']):
            raise BusinessError(f'This merry-go-round payment must be KES {rnd["amount_cents"] / 100:,.0f}.')

    try:
        with db.tx():
            pid = db.insert('payment_requests', chama_id=chama_id, user_id=user_id, purpose=purpose,
                            target_id=target_id, amount_cents=amount_cents, method=method,
                            reference=reference, note=note, status='PENDING', created_at=iso(now))
            audit(db, user_id, 'PAYMENT_REQUESTED', 'payment_request', pid, chama_id,
                  {'purpose': purpose, 'amount_cents': amount_cents, 'method': method})
            notify_roles(db, chama_id, F.FINANCE_ROLES,
                         f'{PURPOSES[purpose]} payment submitted: KES {amount_cents / 100:,.0f}. Please confirm it.',
                         f'/chamas/{chama_id}/pay', user_id, now)
    except IntegrityError:
        raise BusinessError('That payment request could not be created. Please try again.')
    return pid


def _apply(db, p, actor, now):
    paid_on = now.date().isoformat()
    ref = p['reference'] or f'CP-{p["id"]}'
    if p['purpose'] == 'CONTRIBUTION':
        return F.record_contribution(db, p['chama_id'], p['user_id'], p['amount_cents'], paid_on,
                                     p['method'], ref, p['note'] or 'Confirmed member payment', actor, now)
    if p['purpose'] == 'SAVING':
        return F.record_contribution(db, p['chama_id'], p['user_id'], p['amount_cents'], paid_on,
                                     p['method'], ref, p['note'] or 'Confirmed savings payment', actor, now)
    if p['purpose'] == 'LOAN':
        return F.repay_loan(db, p['chama_id'], p['target_id'], p['amount_cents'], paid_on,
                            p['method'], ref, actor, now)
    return M.record_payment(db, p['chama_id'], p['target_id'], p['user_id'], p['method'], ref, paid_on, actor, now)


def decide(db, chama_id, pid, approve, actor, reason=None, now=None):
    now = now or now_utc()
    with db.tx():
        db.lock('chamas', chama_id)
        p = db.one("SELECT * FROM payment_requests WHERE id=? AND chama_id=? AND status='PENDING'", (pid, chama_id))
        if not p:
            raise BusinessError('Payment request not found or already decided.')
        if not approve:
            reason = (reason or '').strip()
            if len(reason) < 3:
                raise BusinessError('Give a reason when rejecting a payment.')
            db.execute("UPDATE payment_requests SET status='REJECTED', decided_by=?, decided_at=?, decision_note=? WHERE id=?",
                       (actor, iso(now), reason[:300], pid))
            audit(db, actor, 'PAYMENT_REJECTED', 'payment_request', pid, chama_id, {'reason': reason[:300]})
            notify(db, p['user_id'], chama_id,
                   f'Your {PURPOSES[p["purpose"]].lower()} payment of KES {p["amount_cents"] / 100:,.0f} was rejected: {reason[:100]}',
                   f'/chamas/{chama_id}/payments', actor, now)
            return
        # Reserve the request while the actual finance operation runs. This prevents
        # two officials from confirming the same payment concurrently.
        db.execute("UPDATE payment_requests SET status='PROCESSING', decided_by=?, decided_at=? WHERE id=?", (actor, iso(now), pid))

    try:
        with db.tx():
            db.lock('chamas', chama_id)
            p = db.one("SELECT * FROM payment_requests WHERE id=? AND chama_id=? AND status='PROCESSING'", (pid, chama_id))
            if not p:
                raise BusinessError('Payment request is no longer available for confirmation.')
            ref_id = _apply(db, p, actor, now)
            db.execute("UPDATE payment_requests SET status='CONFIRMED', applied_ref_id=?, decision_note=NULL WHERE id=?", (ref_id, pid))
            audit(db, actor, 'PAYMENT_CONFIRMED', 'payment_request', pid, chama_id,
                  {'purpose': p['purpose'], 'amount_cents': p['amount_cents'], 'applied_ref_id': ref_id})
            notify(db, p['user_id'], chama_id,
                   f'Confirmed: your {PURPOSES[p["purpose"]].lower()} payment of KES {p["amount_cents"] / 100:,.0f} is now posted.',
                   f'/chamas/{chama_id}/payments', actor, now)
    except Exception:
        # The finance operation and this status update are in one DB transaction.
        # If it fails, the transaction rolls back to PENDING so an official can fix it.
        raise


def pending(db, chama_id):
    return db.all("""SELECT p.*,u.name FROM payment_requests p JOIN users u ON u.id=p.user_id
                    WHERE p.chama_id=? AND p.status='PENDING' ORDER BY p.id ASC""", (chama_id,))


def mine(db, chama_id, user_id):
    return db.all("SELECT * FROM payment_requests WHERE chama_id=? AND user_id=? ORDER BY id DESC LIMIT 30", (chama_id, user_id))


def register(app, db, ctx, login_required):
    @app.route('/chamas/<int:chama_id>/payments')
    @login_required
    def universal_pay_page(chama_id):
        chama, me, sub = ctx(chama_id)
        t = targets(db(), chama_id, g.user['id'])
        pay_config = CP.get_config(db(), chama_id)
        return render_template('universal_pay.html', chama=chama, me=me, data=t, pay_config=pay_config,
                               requests=mine(db(), chama_id, g.user['id']), pending=pending(db(), chama_id) if me['role'] in F.FINANCE_ROLES else [],
                               staff=me['role'] in F.FINANCE_ROLES, methods=METHODS, purposes=PURPOSES)

    @app.route('/chamas/<int:chama_id>/payments/request', methods=['POST'])
    @login_required
    def universal_pay_request(chama_id):
        ctx(chama_id)
        try:
            purpose = (request.form.get('purpose') or '').upper()
            target_id = request.form.get('target_id', type=int)
            amount = _amount(request.form.get('amount'))
            pid = create_request(db(), chama_id, g.user['id'], purpose, target_id, amount,
                                 request.form.get('method'), request.form.get('reference'), request.form.get('note'), g.user['id'])
            flash('Payment submitted. It will update your Chama records after an official confirms it.', 'success')
        except BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('universal_pay_page', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/payments/<int:pid>/<action>', methods=['POST'])
    @login_required
    def universal_pay_decide(chama_id, pid, action):
        ctx(chama_id, roles=F.FINANCE_ROLES)
        if action not in ('approve', 'reject'):
            abort(404)
        try:
            decide(db(), chama_id, pid, action == 'approve', g.user['id'], request.form.get('reason'))
            flash('Payment confirmed and the relevant Chama record was updated automatically.' if action == 'approve' else 'Payment rejected.', 'success')
        except BusinessError as e:
            flash(str(e), 'warning')
        return redirect(url_for('universal_pay_page', chama_id=chama_id))
