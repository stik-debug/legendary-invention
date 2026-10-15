from datetime import date
from unittest.mock import patch

from test_services import Base
import services as S
import trust_jobs
import notify


class FixedDate(date):
    @classmethod
    def today(cls):
        real = date.today()
        return cls(real.year, real.month, 15)


class PremiumFeatureTests(Base):
    def test_constitution_late_fine_runs_once_only_when_due_day_enabled(self):
        owner, cid = self.chama('starter')
        member = self.user()
        S.add_member(self.db, cid, member, 'MEMBER', owner, self.t0)
        first_of_month = date.today().replace(day=1).isoformat()
        self.db.execute('UPDATE chama_members SET joined_at=? WHERE chama_id=?', (first_of_month, cid))
        self.db.execute('UPDATE chama_constitutions SET monthly_contribution_cents=100000,late_fine_cents=10000,contribution_due_day=1 WHERE chama_id=?', (cid,))
        self.db.commit()
        with patch.object(trust_jobs, 'date', FixedDate):
            trust_jobs.daily_obligation_reminders(self.db, cid)
            self.db.commit()
            first_count = self.db.val("SELECT COUNT(*) FROM fines WHERE chama_id=? AND reason=?", (cid, f'Late contribution for {date.today():%Y-%m}'), 0)
            trust_jobs.daily_obligation_reminders(self.db, cid)
            self.db.commit()
            second_count = self.db.val("SELECT COUNT(*) FROM fines WHERE chama_id=? AND reason=?", (cid, f'Late contribution for {date.today():%Y-%m}'), 0)
        self.assertEqual(first_count, 2)
        self.assertEqual(second_count, first_count)

    def test_whatsapp_reminders_require_explicit_opt_in_and_provider_config(self):
        user = self.user()
        now = S.iso(self.t0)
        self.db.execute('INSERT INTO notification_preferences(user_id,in_app,email,sms,push,whatsapp,updated_at) VALUES(?,1,0,0,1,0,?)', (user, now))
        self.db.commit()
        env = {'WHATSAPP_ACCESS_TOKEN':'test-token','WHATSAPP_PHONE_NUMBER_ID':'test-phone-id','WHATSAPP_REMINDER_TEMPLATE':'approved_reminder'}
        with patch.dict('os.environ', env, clear=False):
            self.assertIsNone(notify.queue_opted_in_whatsapp(self.db, user, 'Reminder', dedupe_key='test-off'))
            self.db.execute('UPDATE notification_preferences SET whatsapp=1 WHERE user_id=?', (user,))
            self.db.commit()
            queued = notify.queue_opted_in_whatsapp(self.db, user, 'Reminder', dedupe_key='test-on')
        self.assertIsNotNone(queued)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM whatsapp_outbox WHERE user_id=?', (user,), 0), 1)

    def test_statement_csv_matching_is_read_only_and_flags_uncertain_rows(self):
        import statement_audit as SA
        parsed = SA.parse_statement_csv('date,amount_kes,reference,description\n2026-10-01,1000,ABC123,Contribution\n2026-10-02,500,,Other')
        ledger = [
            {'id': 1, 'amount_cents': 100000, 'occurred_on': '2026-10-01', 'direction': 'IN', 'reference': 'ABC123', 'kind': 'CONTRIBUTION', 'description': 'Contribution'},
            {'id': 2, 'amount_cents': 50000, 'occurred_on': '2026-10-02', 'direction': 'IN', 'reference': '', 'kind': 'OTHER_INCOME', 'description': 'Other'},
        ]
        result = SA.match_statement_rows(parsed, ledger)
        self.assertEqual([r['status'] for r in result], ['MATCHED', 'POSSIBLE_MATCH'])
        self.assertEqual([r['ledger_id'] for r in result], [1, 2])

    def test_statement_csv_rejects_bad_dates_and_overprecision(self):
        import statement_audit as SA
        for raw in ('date,amount_kes\nnot-a-date,100', 'date,amount_kes\n2026-10-01,1.001'):
            with self.assertRaises(ValueError): SA.parse_statement_csv(raw)
