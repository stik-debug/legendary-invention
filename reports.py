"""Officials' reports and CSV exports. Everything is read from the same tables the screens use; nothing is stored twice."""
import community as C
import finance as F
import mgr as M


def _n(db, sql, params=()):
    return int(db.val(sql, params, 0) or 0)


def monthly_collection(db, chama_id, months=12):
    rows = db.all("SELECT period, SUM(amount_cents) total, COUNT(DISTINCT user_id) payers FROM contributions WHERE chama_id=? AND status='PAID' "
                  "GROUP BY period ORDER BY period DESC LIMIT ?", (chama_id, months))
    return list(reversed([{'period': r['period'], 'total': int(r['total']), 'payers': r['payers']} for r in rows]))


def chama_report(db, chama_id, period):
    status = F.month_status(db, chama_id, period)
    money_in, money_out = F.cash_totals(db, chama_id)
    c = db.one('SELECT contribution_cents FROM chamas WHERE id=?', (chama_id,))
    loans = {r['status']: r for r in db.all('SELECT status, COUNT(*) n, SUM(principal_cents) principal, SUM(total_due_cents) due, SUM(paid_cents) paid FROM loans WHERE chama_id=? GROUP BY status', (chama_id,))}
    active = loans.get('ACTIVE')
    meetings = db.all("SELECT id FROM meetings WHERE chama_id=? AND status='HELD'", (chama_id,))
    rates = C.attendance_rates(db, chama_id)
    marks = sum(r['marked'] for r in rates)
    present = sum(r['present'] for r in rates)
    return {
        'period': period, 'cash': F.cash_balance(db, chama_id), 'pot': M.pot_balance(db, chama_id),
        'mgr_active': _n(db, "SELECT COUNT(*) FROM mgr_rounds WHERE chama_id=? AND status='ACTIVE'", (chama_id,)), 'money_in': money_in, 'money_out': money_out,
        'savings_total': _n(db, "SELECT SUM(amount_cents) FROM contributions WHERE chama_id=? AND status='PAID'", (chama_id,)),
        'members': len(status), 'expected_each': c['contribution_cents'],
        'collected': sum(s['paid'] for s in status), 'expected': sum(s['expected'] for s in status),
        'paid_count': sum(1 for s in status if s['status'] == 'PAID'),
        'unpaid': [s for s in status if s['status'] != 'PAID'],
        'loans_out': int((active['due'] - active['paid']) if active else 0), 'loans_active': active['n'] if active else 0,
        'loans_pending': loans['PENDING']['n'] if 'PENDING' in loans else 0,
        'loans_repaid': loans['PAID']['n'] if 'PAID' in loans else 0,
        'fines_owed': _n(db, "SELECT SUM(amount_cents-paid_cents) FROM fines WHERE chama_id=? AND status IN ('UNPAID','PARTIAL')", (chama_id,)),
        'fines_open': _n(db, "SELECT COUNT(*) FROM fines WHERE chama_id=? AND status IN ('UNPAID','PARTIAL')", (chama_id,)),
        'meetings_held': len(meetings), 'attendance_pct': round(present * 100 / marks) if marks else None,
        'history': monthly_collection(db, chama_id), 'attendance': rates,
    }


def _kes(c):
    return f'{(c or 0) / 100:.2f}'


EXPORTS = ('members', 'contributions', 'loans', 'fines', 'attendance', 'merrygoround')


def export(db, chama_id, kind):
    """Returns (header, rows) for a CSV. Cells are made spreadsheet-safe by F.to_csv."""
    if kind == 'members':
        rows = db.all("""SELECT u.name, u.phone, m.role, m.joined_at,
            (SELECT COALESCE(SUM(c.amount_cents),0) FROM contributions c WHERE c.chama_id=m.chama_id AND c.user_id=u.id AND c.status='PAID') saved,
            (SELECT COALESCE(SUM(l.total_due_cents-l.paid_cents),0) FROM loans l WHERE l.chama_id=m.chama_id AND l.user_id=u.id AND l.status='ACTIVE') loan_owed,
            (SELECT COALESCE(SUM(f.amount_cents-f.paid_cents),0) FROM fines f WHERE f.chama_id=m.chama_id AND f.user_id=u.id AND f.status IN ('UNPAID','PARTIAL')) fines_owed
            FROM chama_members m JOIN users u ON u.id=m.user_id WHERE m.chama_id=? AND m.status='ACTIVE' ORDER BY u.name""", (chama_id,))
        return (['Name', 'Phone', 'Role', 'Joined', 'Savings KES', 'Loan owed KES', 'Fines owed KES'],
                [[r['name'], r['phone'], r['role'], r['joined_at'][:10], _kes(r['saved']), _kes(r['loan_owed']), _kes(r['fines_owed'])] for r in rows])
    if kind == 'contributions':
        rows = db.all("SELECT c.paid_on, c.period, u.name, c.amount_cents, c.method, c.reference, c.status FROM contributions c JOIN users u ON u.id=c.user_id "
                      "WHERE c.chama_id=? ORDER BY c.paid_on, c.id LIMIT 20000", (chama_id,))
        return (['Date', 'Month', 'Member', 'Amount KES', 'Method', 'Reference', 'Status'],
                [[r['paid_on'], r['period'], r['name'], _kes(r['amount_cents']), r['method'], r['reference'], r['status']] for r in rows])
    if kind == 'loans':
        rows = db.all("SELECT l.applied_at, u.name, l.principal_cents, l.interest_cents, l.total_due_cents, l.paid_cents, l.status, l.purpose FROM loans l "
                      "JOIN users u ON u.id=l.user_id WHERE l.chama_id=? ORDER BY l.id LIMIT 5000", (chama_id,))
        return (['Applied', 'Member', 'Principal KES', 'Interest KES', 'Total due KES', 'Paid KES', 'Status', 'Purpose'],
                [[r['applied_at'][:10], r['name'], _kes(r['principal_cents']), _kes(r['interest_cents']), _kes(r['total_due_cents']), _kes(r['paid_cents']), r['status'], r['purpose']] for r in rows])
    if kind == 'fines':
        rows = db.all("SELECT f.created_at, u.name, f.amount_cents, f.paid_cents, f.status, f.reason FROM fines f JOIN users u ON u.id=f.user_id "
                      "WHERE f.chama_id=? ORDER BY f.id LIMIT 5000", (chama_id,))
        return (['Date', 'Member', 'Fine KES', 'Paid KES', 'Status', 'Reason'],
                [[r['created_at'][:10], r['name'], _kes(r['amount_cents']), _kes(r['paid_cents']), r['status'], r['reason']] for r in rows])
    if kind == 'attendance':
        rows = db.all("SELECT m.held_at, m.title, u.name, a.status FROM attendance a JOIN meetings m ON m.id=a.meeting_id JOIN users u ON u.id=a.user_id "
                      "WHERE a.chama_id=? ORDER BY m.held_at, u.name LIMIT 20000", (chama_id,))
        return ['Meeting date', 'Meeting', 'Member', 'Attendance'], [[r['held_at'], r['title'], r['name'], r['status']] for r in rows]
    if kind == 'merrygoround':
        rows = db.all("""SELECT 'Paid in' what, p.paid_on d, r.name rname, u.name who, rec.name recipient, p.amount_cents, p.method, p.reference, p.status
            FROM mgr_payments p JOIN mgr_rounds r ON r.id=p.round_id JOIN users u ON u.id=p.user_id JOIN mgr_slots s ON s.id=p.slot_id JOIN users rec ON rec.id=s.user_id
            WHERE p.chama_id=? UNION ALL
            SELECT 'Payout', substr(s.paid_out_at,1,10), r.name, u.name, u.name, s.payout_cents, s.method, s.reference, 'PAID'
            FROM mgr_slots s JOIN mgr_rounds r ON r.id=s.round_id JOIN users u ON u.id=s.user_id WHERE s.chama_id=? AND s.status='PAID_OUT'
            ORDER BY 2 LIMIT 20000""", (chama_id, chama_id))
        return (['Type', 'Date', 'Round', 'Member', 'Turn of', 'Amount KES', 'Method', 'Reference', 'Status'],
                [[x['what'], x['d'], x['rname'], x['who'], x['recipient'], _kes(x['amount_cents']), x['method'], x['reference'], x['status']] for x in rows])
    raise KeyError(kind)
