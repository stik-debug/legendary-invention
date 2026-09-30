import hmac
import os
import secrets
from datetime import timedelta
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

import services as S
from db import DB, audit, init_db
from providers import MpesaPaymentProvider

_db = None


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=overrides.get('secret_key') if overrides else os.environ.get('SECRET_KEY', 'dev'),
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    if overrides:
        app.config.update(overrides)

    init_db(app)
    register_views(app)
    return app


def db():
    global _db
    if _db is None:
        from flask import current_app
        _db = DB(current_app.config.get('DATABASE_URL', 'sqlite:///app.db'))
    return _db


def register_views(app):
    @app.before_request
    def load_session():
        session.permanent = True
        g.user = None
        if 'user_id' in session:
            user = db().one('SELECT * FROM users WHERE id=?', (session['user_id'],))
            if user:
                g.user = user

    @app.route('/')
    def index():
        if g.user:
            return redirect(url_for('dashboard'))
        return render_template('index.html')

    @app.route('/dashboard')
    def dashboard():
        if not g.user:
            return redirect(url_for('index'))
        chamas = db().all(
            'SELECT c.* FROM chama c JOIN chama_members m ON c.id=m.chama_id WHERE m.user_id=? AND m.status="ACTIVE"',
            (g.user['id'],),
        )
        return render_template('dashboard.html', chamas=chamas)

    @app.route('/chamas/<int:chama_id>/finance')
    def finance(chama_id):
        if not g.user:
            return redirect(url_for('index'))
        
        chama = db().one('SELECT * FROM chama WHERE id=?', (chama_id,))
        if not chama:
            abort(404)
        
        member = db().one(
            'SELECT * FROM chama_members WHERE chama_id=? AND user_id=?',
            (chama_id, g.user['id']),
        )
        if not member or member['status'] != 'ACTIVE':
            abort(403)
        
        is_admin = member['role'] in ('CHAMA_ADMIN', 'TREASURER')
        
        members = db().all(
            'SELECT u.id, u.name FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status="ACTIVE" ORDER BY u.name',
            (chama_id,),
        )

        loans = db().all(
            'SELECT l.*, u.name AS member_name FROM loans l JOIN users u ON u.id=l.user_id WHERE l.chama_id=? ORDER BY l.id DESC',
            (chama_id,),
        )

        fines = db().all(
            'SELECT f.*, u.name AS member_name FROM fines f JOIN users u ON u.id=f.user_id WHERE f.chama_id=? ORDER BY f.id DESC',
            (chama_id,),
        )

        summary = S.get_finance_summary(db(), chama_id)

        return render_template(
            'finance.html',
            chama=chama,
            is_admin=is_admin,
            members=members,
            loans=loans,
            fines=fines,
            summary=summary,
        )

    @app.route('/chamas/<int:chama_id>/finance/loan', methods=['POST'])
    def finance_loan(chama_id):
        if not g.user:
            return redirect(url_for('index'))
        
        member = db().one(
            'SELECT * FROM chama_members WHERE chama_id=? AND user_id=?',
            (chama_id, g.user['id']),
        )
        if not member or member['role'] not in ('CHAMA_ADMIN', 'TREASURER'):
            abort(403)

        try:
            member_id = int(request.form.get('member_id'))
            amount = float(request.form.get('amount', '0'))
            due_date = request.form.get('due_date') or None
            reason = request.form.get('reason', '')
        except (TypeError, ValueError):
            flash('Please enter a valid member, amount, and due date.', 'danger')
            return redirect(url_for('finance', chama_id=chama_id))

        try:
            amount_cents = int(round(amount * 100))
            S.create_loan(
                db(),
                chama_id,
                member_id,
                amount_cents,
                reason,
                g.user['id'],
                due_date=due_date,
            )
            flash('Loan approved.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')

        return redirect(url_for('finance', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/finance/fine', methods=['POST'])
    def finance_fine(chama_id):
        if not g.user:
            return redirect(url_for('index'))
        
        member = db().one(
            'SELECT * FROM chama_members WHERE chama_id=? AND user_id=?',
            (chama_id, g.user['id']),
        )
        if not member or member['role'] not in ('CHAMA_ADMIN', 'TREASURER'):
            abort(403)

        try:
            member_id = int(request.form.get('member_id'))
            amount = float(request.form.get('amount', '0'))
            reason = request.form.get('reason', '')
        except (TypeError, ValueError):
            flash('Please enter a valid member and amount.', 'danger')
            return redirect(url_for('finance', chama_id=chama_id))

        try:
            amount_cents = int(round(amount * 100))
            S.create_fine(
                db(),
                chama_id,
                member_id,
                amount_cents,
                reason,
                g.user['id'],
            )
            flash('Fine recorded.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')

        return redirect(url_for('finance', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/finance/loan/<int:loan_id>/repay', methods=['POST'])
    def loan_repay(chama_id, loan_id):
        if not g.user:
            return redirect(url_for('index'))
        
        member = db().one(
            'SELECT * FROM chama_members WHERE chama_id=? AND user_id=?',
            (chama_id, g.user['id']),
        )
        if not member or member['role'] not in ('CHAMA_ADMIN', 'TREASURER'):
            abort(403)

        try:
            amount = float(request.form.get('amount', '0'))
        except (TypeError, ValueError):
            flash('Please enter a valid repayment amount.', 'danger')
            return redirect(url_for('finance', chama_id=chama_id))

        try:
            amount_cents = int(round(amount * 100))
            S.record_loan_repayment(
                db(),
                loan_id,
                amount_cents,
                g.user['id'],
                notes='Manual repayment',
            )
            flash('Loan repayment recorded.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')

        return redirect(url_for('finance', chama_id=chama_id))

    @app.route('/chamas/<int:chama_id>/finance/fine/<int:fine_id>/pay', methods=['POST'])
    def fine_pay(chama_id, fine_id):
        if not g.user:
            return redirect(url_for('index'))
        
        member = db().one(
            'SELECT * FROM chama_members WHERE chama_id=? AND user_id=?',
            (chama_id, g.user['id']),
        )
        if not member or member['role'] not in ('CHAMA_ADMIN', 'TREASURER'):
            abort(403)

        try:
            amount = float(request.form.get('amount', '0'))
        except (TypeError, ValueError):
            flash('Please enter a valid fine payment amount.', 'danger')
            return redirect(url_for('finance', chama_id=chama_id))

        try:
            amount_cents = int(round(amount * 100))
            S.pay_fine(
                db(),
                fine_id,
                amount_cents,
                g.user['id'],
                receipt='manual-' + str(fine_id),
            )
            flash('Fine payment recorded.', 'success')
        except S.BusinessError as e:
            flash(str(e), 'warning')

        return redirect(url_for('finance', chama_id=chama_id))


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
