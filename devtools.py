"""Test-data commands. They are for development and demos, never for real customers' data:
    flask --app app seed               create two demo chamas full of realistic, clearly labelled TEST data
    flask --app app reset-test-data    delete ONLY that test data (dry run unless you add --yes)
    flask --app app validate           check the whole database for broken money or membership rules (exit code 1 if any)
Everything seeded is flagged is_test_data=1. Real chamas and real users are never touched by reset."""
from datetime import date, timedelta

import click
from werkzeug.security import generate_password_hash

import community as C
import finance as F
import mgr as M
import services as S
from db import DB

DEMO_PASSWORD = 'DemoPass123!'
TEST_DOMAIN = 'chamapay.test'

# (chama name, description, plan, members: (name, phone, role))
DEMO = [
    ('Umoja Savers (TEST DATA)', 'Demo chama for testing', 'growth', [
        ('Grace Wanjiku', '0790000001', 'CHAMA_ADMIN'), ('Peter Otieno', '0790000002', 'TREASURER'), ('Mary Achieng', '0790000003', 'SECRETARY'),
        ('John Kamau', '0790000004', 'MEMBER'), ('Faith Mwende', '0790000005', 'MEMBER'), ('Samuel Kiptoo', '0790000006', 'MEMBER'),
        ('Lucy Njeri', '0790000007', 'MEMBER'), ('David Mutua', '0790000008', 'MEMBER')]),
    ('Tujenge Youth (TEST DATA)', 'Second demo chama, for checking that chamas stay separate', 'starter', [
        ('Amina Hassan', '0790000011', 'CHAMA_ADMIN'), ('Brian Ouma', '0790000012', 'TREASURER'), ('Cynthia Wairimu', '0790000013', 'MEMBER'),
        ('Daniel Rotich', '0790000014', 'MEMBER'), ('Esther Naliaka', '0790000015', 'MEMBER')]),
]


def _mark(db, user_ids, chama_ids):
    with db.tx():
        for u in user_ids:
            db.execute('UPDATE users SET is_test_data=1 WHERE id=?', (u,))
        for c in chama_ids:
            db.execute('UPDATE chamas SET is_test_data=1 WHERE id=?', (c,))


def seed(db, today=None):
    """Create the demo data. Returns a list of (chama name, [(person, email, role)]). Skips people who already exist."""
    today = today or date.today()
    pw = generate_password_hash(DEMO_PASSWORD)
    now = S.now_utc()
    out = []
    for cname, desc, plan, people in DEMO:
        if db.val('SELECT COUNT(*) FROM chamas WHERE name=?', (cname,), 0):
            continue
        ids, listing = [], []
        for i, (name, phone, role) in enumerate(people):
            email = f"demo.{phone[-3:]}@{TEST_DOMAIN}"
            u = db.one('SELECT id FROM users WHERE email=?', (email,))
            ids.append(u['id'] if u else S.create_user(db, name, email, phone, pw, is_test=True))
            listing.append((name, email, role))
        admin = ids[0]
        cid = S.create_chama(db, admin, cname, desc, plan)
        _mark(db, ids, [cid])
        S.grant_access(db, cid, 366, None)
        for uid, (name, phone, role) in list(zip(ids, people))[1:]:
            S.add_member(db, cid, uid, role, admin)
        F.update_chama_settings(db, cid, 100000, '10', 3, admin)
        treas = ids[1]
        for months_ago in (2, 1, 0):  # last two months fully paid except one person each month, this month partly
            d = (today.replace(day=1) - timedelta(days=31 * months_ago)).replace(day=min(today.day, 28)) if months_ago else today
            for k, uid in enumerate(ids):
                if months_ago == 0 and k % 3 == 2:
                    continue  # left unpaid so reports show arrears
                if months_ago == 1 and k == len(ids) - 1:
                    continue
                F.record_contribution(db, cid, uid, 100000, d.isoformat(), 'M-Pesa' if k % 2 else 'Cash', f'TEST{cid}{months_ago}{k:02d}ABCD' if k % 2 else None, None, treas, now)
        F.record_ledger_entry(db, cid, 'EXPENSE', 150000, 'Hall hire for annual meeting', today.isoformat(), treas, now)
        borrower = ids[3]
        lid = F.apply_loan(db, cid, borrower, 200000, 'School fees', now)
        F.decide_loan(db, cid, lid, True, admin, now)
        F.disburse_loan(db, cid, lid, treas, now)
        F.repay_loan(db, cid, lid, 100000, today.isoformat(), 'Cash', None, treas, now)
        F.apply_loan(db, cid, ids[4], 150000, 'Stock for my shop', now)  # waiting for a decision
        late = F.create_fine(db, cid, ids[len(ids) - 1], 20000, 'Late contribution', None, treas, now)
        F.pay_fine(db, cid, late, 5000, today.isoformat(), 'Cash', None, treas, now)
        rid = M.create_round(db, cid, 'Demo merry-go-round', 50000, 'MONTHLY', today.isoformat(), ids[:6], admin, order=ids[:6], now=now)
        for uid in ids[1:4]:
            M.record_payment(db, cid, rid, uid, 'Cash', None, today.isoformat(), treas, now)
        past = C.create_meeting(db, cid, 'Monthly meeting', (today - timedelta(days=14)).isoformat() + 'T15:00', 'Chief\'s camp', 'Contributions, loans, AOB', 20000, ids[2], now)
        C.mark_attendance(db, cid, past, {uid: ('PRESENT' if k % 4 else 'ABSENT') for k, uid in enumerate(ids)}, ids[2], now)
        C.save_minutes(db, cid, past, 'Contributions reviewed. Two loans discussed. Next meeting date agreed.', ids[2], now)
        C.create_meeting(db, cid, 'Next monthly meeting', (today + timedelta(days=14)).isoformat() + 'T15:00', 'Chief\'s camp', 'Loan requests, planning', 20000, ids[2], now)
        C.post_notice(db, cid, 'Contributions due by the 5th', 'Please pay your monthly contribution by the 5th. Send your M-Pesa code to the treasurer.', True, admin, now)
        C.post_notice(db, cid, 'Welcome to ChamaPay', 'This chama is demo data. Nothing here is real.', False, ids[2], now)
        for k, text in enumerate(('Habari wote!', 'Reminder: meeting on Saturday.', 'Asante, nimelipa leo.')):
            F.send_message(db, cid, ids[k], text, now - timedelta(minutes=10 - k))
        out.append((cname, listing))
    return out


# ---------- reset ----------
def _in(ids):
    return '(' + ','.join(str(int(i)) for i in ids) + ')'


def reset_test_data(db, dry_run=True):
    """Delete the seeded chamas and the test users who belong only to them. Returns {table: rows deleted (or that would be)}.
    Test users who are also active members of a real chama are kept and reported under 'kept_users'."""
    chamas = [r['id'] for r in db.all('SELECT id FROM chamas WHERE is_test_data=1')]
    cand = [r['id'] for r in db.all('SELECT id FROM users WHERE is_test_data=1 AND is_super_admin=0')]
    keep = [u for u in cand if db.val("SELECT COUNT(*) FROM chama_members m JOIN chamas c ON c.id=m.chama_id WHERE m.user_id=? AND c.is_test_data=0 AND m.status='ACTIVE'", (u,), 0)]
    users = [u for u in cand if u not in keep]
    counts = {'kept_users': len(keep)}
    if not chamas and not users:
        return counts
    cs = _in(chamas or [0])
    us = _in(users or [0])
    steps = [
        ('chama_payments', f'chama_id IN {cs}'), ('chama_pay_config', f'chama_id IN {cs}'), ('meeting_joins', f'chama_id IN {cs}'),
        ('attendance', f'chama_id IN {cs}'), ('meetings', f'chama_id IN {cs}'), ('announcements', f'chama_id IN {cs}'),
        ('notifications', f'chama_id IN {cs} OR user_id IN {us}'), ('message_reads', f'chama_id IN {cs} OR user_id IN {us}'),
        ('messages', f'chama_id IN {cs}'), ('fine_payments', f'chama_id IN {cs}'), ('fines', f'chama_id IN {cs}'),
        ('loan_repayments', f'chama_id IN {cs}'), ('loans', f'chama_id IN {cs}'), ('contributions', f'chama_id IN {cs}'),
        ('mgr_payments', f'chama_id IN {cs}'), ('mgr_slots', f'chama_id IN {cs}'), ('mgr_rounds', f'chama_id IN {cs}'),
        ('ledger_transactions', f'chama_id IN {cs}'),
        ('payment_webhooks', f'payment_id IN (SELECT id FROM payments WHERE chama_id IN {cs})'),
        ('payments', f'chama_id IN {cs}'), ('subscriptions', f'chama_id IN {cs}'), ('chama_members', f'chama_id IN {cs} OR user_id IN {us}'),
        ('audit_logs', f'chama_id IN {cs} OR actor_id IN {us}'), ('chamas', f'id IN {cs}'), ('users', f'id IN {us}'),
    ]
    with db.tx():
        for table, where in steps:
            counts[table] = db.val(f'SELECT COUNT(*) FROM {table} WHERE {where}', (), 0)
            if not dry_run and counts[table]:
                db.execute(f'DELETE FROM {table} WHERE {where}')
    return counts


# ---------- validate ----------
def validate(db, production=False):
    """Returns a list of (severity, message). severity is ERROR (data is wrong) or WARN (needs a look)."""
    bad = []

    def err(msg): bad.append(('ERROR', msg))
    def warn(msg): bad.append(('WARN', msg))

    if not db.val('SELECT COUNT(*) FROM users WHERE is_super_admin=1 AND is_active=1', (), 0):
        warn('There is no active platform owner.')
    for r in db.all("SELECT c.id, c.name FROM chamas c WHERE NOT EXISTS (SELECT 1 FROM chama_members m WHERE m.chama_id=c.id AND m.role='CHAMA_ADMIN' AND m.status='ACTIVE')"):
        err(f"Chama {r['id']} ({r['name']}) has no active administrator.")
    for r in db.all('SELECT c.id, c.name, (SELECT COUNT(*) FROM subscriptions s WHERE s.chama_id=c.id) n FROM chamas c'):
        if r['n'] != 1:
            err(f"Chama {r['id']} ({r['name']}) has {r['n']} subscriptions (should be exactly 1).")
    for r in db.all("""SELECT c.id, c.name, p.max_members, (SELECT COUNT(*) FROM chama_members m WHERE m.chama_id=c.id AND m.status='ACTIVE') n
        FROM chamas c JOIN subscriptions s ON s.chama_id=c.id JOIN subscription_plans p ON p.id=s.plan_id"""):
        if r['n'] > r['max_members']:
            err(f"Chama {r['id']} ({r['name']}) has {r['n']} members but its plan allows {r['max_members']}.")
    for r in db.all("SELECT id, name FROM users WHERE claimed=1 AND (password_hash IS NULL OR password_hash='')"):
        err(f"User {r['id']} ({r['name']}) is registered but has no password.")
    for c in db.all('SELECT id, name FROM chamas'):
        cid, n = c['id'], f"Chama {c['id']} ({c['name']})"
        bal = F.cash_balance(db, cid)
        if bal < 0:
            err(f'{n} has a negative cash balance (KES {bal / 100:,.2f}).')
        net = lambda ref: int(db.val("SELECT SUM(CASE WHEN direction='IN' THEN amount_cents ELSE -amount_cents END) FROM ledger_transactions WHERE chama_id=? AND ref_type=? AND direction!='MEMO'", (cid, ref), 0) or 0)
        for ref, table in (('contribution', 'contributions'), ('loan_repayment', 'loan_repayments'), ('fine_payment', 'fine_payments')):
            paid = int(db.val(f"SELECT SUM(amount_cents) FROM {table} WHERE chama_id=? AND status='PAID'", (cid,), 0) or 0)
            if net(ref) != paid:
                err(f'{n}: the ledger shows KES {net(ref) / 100:,.2f} for {ref}s but the records total KES {paid / 100:,.2f}.')
        out = int(db.val("SELECT SUM(amount_cents) FROM ledger_transactions WHERE chama_id=? AND kind='LOAN_DISBURSEMENT'", (cid,), 0) or 0)
        lent = int(db.val("SELECT SUM(principal_cents) FROM loans WHERE chama_id=? AND disbursed_at IS NOT NULL", (cid,), 0) or 0)
        if out != lent:
            err(f'{n}: loans paid out total KES {lent / 100:,.2f} but the ledger shows KES {out / 100:,.2f}.')
    for c in db.all('SELECT id, name FROM chamas'):
        cid, n = c['id'], f"Chama {c['id']} ({c['name']})"
        pot = M.pot_balance(db, cid)
        held = int(db.val("SELECT SUM(p.amount_cents) FROM mgr_payments p JOIN mgr_slots s ON s.id=p.slot_id WHERE p.chama_id=? AND p.status='PAID' AND s.status='PENDING'", (cid,), 0) or 0)
        if pot < 0:
            err(f'{n}: the merry-go-round pot is negative (KES {pot / 100:,.2f}).')
        if pot != held:
            err(f'{n}: the merry-go-round pot holds KES {pot / 100:,.2f} but the payments waiting for payout total KES {held / 100:,.2f}.')
        if db.val("SELECT COUNT(*) FROM ledger_transactions WHERE chama_id=? AND account='MGR' AND kind NOT IN ('MGR_CONTRIBUTION','MGR_PAYOUT','MGR_REVERSAL')", (cid,), 0):
            err(f'{n}: something other than merry-go-round money is in the pot account.')
        if db.val("SELECT COUNT(*) FROM ledger_transactions WHERE chama_id=? AND account='MAIN' AND kind LIKE 'MGR%'", (cid,), 0):
            err(f'{n}: merry-go-round money was recorded in the main account.')
    for r in db.all("SELECT s.id, s.payout_cents, (SELECT COALESCE(SUM(p.amount_cents),0) FROM mgr_payments p WHERE p.slot_id=s.id AND p.status='PAID') c FROM mgr_slots s WHERE s.status='PAID_OUT'"):
        if r['payout_cents'] != r['c']:
            err(f"Merry-go-round turn {r['id']} paid out KES {r['payout_cents'] / 100:,.2f} but KES {r['c'] / 100:,.2f} was collected.")
    for r in db.all("SELECT r.id FROM mgr_rounds r WHERE r.status='COMPLETED' AND EXISTS (SELECT 1 FROM mgr_slots s WHERE s.round_id=r.id AND s.status='PENDING')"):
        err(f"Merry-go-round {r['id']} is marked complete but has turns left.")
    for r in db.all("""SELECT l.id, l.chama_id, l.paid_cents, l.total_due_cents, l.status,
        (SELECT COALESCE(SUM(r.amount_cents),0) FROM loan_repayments r WHERE r.loan_id=l.id AND r.status='PAID') s FROM loans l"""):
        if r['paid_cents'] != r['s']:
            err(f"Loan {r['id']}: paid_cents {r['paid_cents']} does not match its repayments {r['s']}.")
        if r['status'] == 'PAID' and r['paid_cents'] < r['total_due_cents']:
            err(f"Loan {r['id']} is marked PAID but is not fully repaid.")
        if r['status'] == 'ACTIVE' and r['paid_cents'] >= r['total_due_cents']:
            err(f"Loan {r['id']} is fully repaid but still marked ACTIVE.")
    for r in db.all("""SELECT f.id, f.paid_cents, f.amount_cents, f.status,
        (SELECT COALESCE(SUM(p.amount_cents),0) FROM fine_payments p WHERE p.fine_id=f.id AND p.status='PAID') s FROM fines f"""):
        if r['paid_cents'] != r['s']:
            err(f"Fine {r['id']}: paid_cents {r['paid_cents']} does not match its payments {r['s']}.")
        if r['paid_cents'] > r['amount_cents']:
            err(f"Fine {r['id']} has been paid more than it is worth.")
        if r['status'] == 'PAID' and r['paid_cents'] < r['amount_cents']:
            err(f"Fine {r['id']} is marked PAID but is not fully paid.")
    for r in db.all("SELECT id FROM payments WHERE applied=1 AND status!='SUCCESS'"):
        err(f"Payment {r['id']} was applied to a subscription but is not marked SUCCESS.")
    for r in db.all("SELECT id FROM payments WHERE applied=0 AND status='SUCCESS'"):
        warn(f"Payment {r['id']} succeeded but was never applied to a subscription.")
    for r in db.all("SELECT a.id FROM attendance a JOIN meetings m ON m.id=a.meeting_id WHERE a.chama_id!=m.chama_id"):
        err(f"Attendance {r['id']} belongs to a meeting of a different chama.")
    if production:
        n = db.val('SELECT COUNT(*) FROM chamas WHERE is_test_data=1', (), 0)
        if n:
            warn(f'{n} test chama(s) exist in this production database. Run reset-test-data when you are done testing.')
    return bad


def register(app):
    def open_db():
        return DB(app.config['DATABASE_URL'])

    @app.cli.command('seed')
    @click.option('--yes-production', is_flag=True, help='Allow seeding a production database (you almost never want this).')
    def seed_cmd(yes_production):
        """Create demo chamas with realistic TEST data."""
        if app.config['IS_PRODUCTION'] and not yes_production:
            raise click.ClickException('Refusing to seed demo data into a production database. Use a local or staging database.')
        db = open_db()
        try:
            made = seed(db)
        finally:
            db.close()
        if not made:
            click.echo('Demo chamas already exist. Run reset-test-data first if you want fresh ones.')
        for cname, people in made:
            click.echo(f'\n{cname}')
            for name, email, role in people:
                click.echo(f'  {role:<12} {name:<18} {email}')
        if made:
            click.echo(f'\nEvery demo account uses the password {DEMO_PASSWORD}. This is TEST DATA.')

    @app.cli.command('reset-test-data')
    @click.option('--yes', is_flag=True, help='Actually delete. Without this it only shows what would be deleted.')
    def reset_cmd(yes):
        """Delete only the seeded test chamas and test users."""
        db = open_db()
        try:
            counts = reset_test_data(db, dry_run=not yes)
        finally:
            db.close()
        click.echo(('Deleted:' if yes else 'Dry run. Would delete:'))
        for k, v in counts.items():
            if v:
                click.echo(f'  {k}: {v}')
        if not yes:
            click.echo('Nothing was changed. Add --yes to delete.')

    @app.cli.command('validate')
    def validate_cmd():
        """Check the whole database for broken money and membership rules."""
        db = open_db()
        try:
            problems = validate(db, app.config['IS_PRODUCTION'])
        finally:
            db.close()
        for sev, msg in problems:
            click.echo(f'{sev}: {msg}')
        errors = sum(1 for s, _ in problems if s == 'ERROR')
        click.echo(f'{errors} error(s), {len(problems) - errors} warning(s).' if problems else 'All checks passed.')
        raise SystemExit(1 if errors else 0)
