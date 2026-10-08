import os, sys, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web_finance as _tf
import chama_pay as P
import community as C
import finance as F
import services as S

TODAY = _tf.TODAY
SETTINGS = {'instructions': 'Paybill 123456 account Umoja', 'check_mode': 'RECORDS'}


class PayWeb(_tf.FinWeb):
    def last(self): return self.db.one('SELECT * FROM chama_payments ORDER BY id DESC LIMIT 1')

    # ---------- online meetings ----------
    def meeting(self, mode='link', link='https://meet.google.com/abc-defg-hij', when=None, c=None):
        when = when or (datetime.utcnow() + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M')
        self.post(c or self.sec, self.base() + '/meetings/add', {'title': 'Monthly meeting', 'when': when, 'venue': '', 'agenda': '', 'fine': '', 'online': mode, 'link': link})
        return self.db.one('SELECT * FROM meetings ORDER BY id DESC LIMIT 1')

    def test_link_join_and_attendance_hint(self):
        self.team(); m = self.meeting()
        self.assertEqual(m['online_provider'], 'LINK'); self.assertEqual(m['online_url'], 'https://meet.google.com/abc-defg-hij')
        page = self.m1.get(self.base() + f"/meetings/{m['id']}").get_data(as_text=True)
        self.assertIn('Join the online meeting', page)
        r = self.m1.get(self.base() + f"/meetings/{m['id']}/join")
        self.assertEqual(r.status_code, 302); self.assertEqual(r.headers['Location'], 'https://meet.google.com/abc-defg-hij')
        self.m1.get(self.base() + f"/meetings/{m['id']}/join")  # clicking twice is harmless
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM meeting_joins WHERE meeting_id=?', (m['id'],)), 1)
        self.assertIn('Joined online', self.sec.get(self.base() + f"/meetings/{m['id']}").get_data(as_text=True))
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM attendance'), 0)  # a join is only a hint: nobody is marked present automatically

    def test_jitsi_is_gone(self):
        self.team(); n = self.db.val('SELECT COUNT(*) FROM meetings'); self.meeting('jitsi')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM meetings'), n)  # no longer an option
        self.assertNotIn('jitsi', self.sec.get(self.base() + '/meetings').get_data(as_text=True).lower())

    def test_join_button_opens_only_near_the_start(self):
        self.team()
        m = self.meeting(when=(datetime.utcnow() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'))
        self.assertNotIn('Join the online meeting', self.m1.get(self.base() + f"/meetings/{m['id']}").get_data(as_text=True))
        r = self.m1.get(self.base() + f"/meetings/{m['id']}/join")
        self.assertEqual(r.status_code, 302); self.assertIn('/meetings/', r.headers['Location']); self.assertEqual(self.db.val('SELECT COUNT(*) FROM meeting_joins'), 0)

    def test_own_link_rules(self):
        self.team()
        for bad in ('http://zoom.us/j/1', 'javascript:alert(1)', 'https://', 'https://user:pw@zoom.us/j/1', 'zoom.us/j/1', ''):
            n = self.db.val('SELECT COUNT(*) FROM meetings'); self.meeting('link', bad)
            self.assertEqual(self.db.val('SELECT COUNT(*) FROM meetings'), n, bad)
        m = self.meeting('link', 'https://meet.google.com/abc-defg-hij')
        self.assertEqual(m['online_provider'], 'LINK')
        self.assertIn('https://meet.google.com/abc-defg-hij', self.m1.get(self.base() + f"/meetings/{m['id']}/join").headers['Location'])

    def test_room_is_for_this_chama_only_and_members_cannot_schedule(self):
        self.team(); m = self.meeting()
        out, _ = self.signup('Outsider'); self.make_chama(out, name='Other Group')
        self.assertEqual(out.get(self.base() + f"/meetings/{m['id']}/join").status_code, 403)
        self.assertEqual(out.get(self.base() + f"/meetings/{m['id']}").status_code, 403)
        n = self.db.val('SELECT COUNT(*) FROM meetings'); self.meeting(c=self.m1)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM meetings'), n)

    # ---------- settings ----------
    def test_settings_are_for_the_chairperson_only_and_validated(self):
        self.team()
        self.assertEqual(self.treas.get(self.base() + '/pay/settings').status_code, 403)
        self.post(self.treas, self.base() + '/pay/settings', SETTINGS); self.assertIsNone(P.get_config(self.db, self.cid))
        self.post(self.admin, self.base() + '/pay/settings', {'instructions': 'x'}); self.assertIsNone(P.get_config(self.db, self.cid))  # too short
        self.post(self.admin, self.base() + '/pay/settings', {**SETTINGS, 'check_mode': 'WHATEVER'}); self.assertIsNone(P.get_config(self.db, self.cid))
        self.post(self.admin, self.base() + '/pay/settings', SETTINGS)
        self.assertEqual((P.channel(self.db, self.cid), P.check_mode(self.db, self.cid)), ('MANUAL', 'RECORDS'))
        self.post(self.admin, self.base() + '/pay/settings', {**SETTINGS, 'check_mode': 'TRUST'}); self.assertEqual(P.check_mode(self.db, self.cid), 'TRUST')

    def test_nothing_to_pay_until_the_chairperson_sets_it_up(self):
        self.team()
        self.paste(self.m1, self.sms()); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 0)
        r = self.m1.get(self.base() + '/pay'); self.assertEqual(r.status_code, 302); self.assertTrue(r.headers['Location'].endswith(self.base() + '/payments'))  # /pay now sends members to the Payments page
        self.assertEqual(self.m1.get(self.base() + '/payments').status_code, 200)
        self.assertEqual(self.post(self.m1, self.base() + '/pay/start', {'purpose': 'CONTRIBUTION', 'amount': '100', 'phone': '0712345678'}).status_code, 404)  # the PIN-prompt payment no longer exists
        self.assertIn(self.client().post(f'/webhooks/chama-mpesa/{self.cid}/x', json={}).status_code, (302, 403, 404, 405))  # no payment callback exists any more
        self.assertFalse(any('chama-mpesa' in str(r) for r in self.app.url_map.iter_rules()))

    # ---------- pay yourself, treasurer approves ----------
    def test_manual_claim_approval_flow(self):
        self.team(); self.post(self.admin, self.base() + '/pay/settings', SETTINGS)
        self.assertEqual(P.channel(self.db, self.cid), 'MANUAL')
        claim = lambda c, code='RCK1234ABC', kes='1000': self.post(c, self.base() + '/pay/claim', {'what': 'CONTRIBUTION:', 'amount': kes, 'code': code})
        claim(self.m1, 'short'); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 0)
        claim(self.m1); p = self.last(); self.assertEqual(p['status'], 'CLAIMED'); self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)
        claim(self.m2); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 1)  # same M-Pesa code cannot be used twice
        self.post(self.m1, self.base() + f"/pay/{p['id']}/approve"); self.assertEqual(self.last()['status'], 'CLAIMED')  # members cannot approve
        self.post(self.treas, self.base() + f"/pay/{p['id']}/approve"); self.assertEqual(self.last()['status'], 'SUCCESS')
        self.assertEqual(self.db.val('SELECT SUM(amount_cents) FROM contributions'), 100000)
        self.post(self.treas, self.base() + f"/pay/{p['id']}/approve"); self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 1)  # twice is harmless
        claim(self.treas, 'MYOWN12345'); q = self.last()
        self.post(self.treas, self.base() + f"/pay/{q['id']}/approve"); self.assertEqual(self.last()['status'], 'CLAIMED')  # cannot approve your own
        self.post(self.admin, self.base() + f"/pay/{q['id']}/reject", {'reason': ''}); self.assertEqual(self.last()['status'], 'CLAIMED')
        self.post(self.admin, self.base() + f"/pay/{q['id']}/reject", {'reason': 'Not on the statement'}); self.assertEqual(self.last()['status'], 'REJECTED')

    # ---------- paste the M-Pesa message; verified against the chama's own records ----------
    def sms(self, code='QGH7XY12AB', kes='1,000.00', to='UMOJA CHAMA'):
        return f'{code} Confirmed. Ksh{kes} sent to {to} for account CHAMA1 on 12/6/26 at 3:45 PM New M-PESA balance is Ksh5,230.00. Transaction cost, Ksh0.00.'

    def received(self, code='QGH7XY12AB', kes='1,000.00'):
        return f'{code} Confirmed.You have received Ksh{kes} from JOHN DOE 0712345678 on 12/6/26 at 3:45 PM  New Account balance is Ksh9,000.00.'

    def manual(self):
        self.team(); self.post(self.admin, self.base() + '/pay/settings', SETTINGS)

    def paste(self, c, msg, what='CONTRIBUTION:', **extra):
        return self.post(c, self.base() + '/pay/claim', dict({'what': what, 'message': msg}, **extra))

    def records(self, c, text):
        return self.post(c, self.base() + '/pay/records', {'text': text})

    def test_parser_reads_common_message_layouts(self):
        import mpesa_sms as M
        a = M.parse_one(self.sms()); self.assertEqual((a['code'], a['amount_cents'], a['date'], a['time']), ('QGH7XY12AB', 100000, '2026-06-12', '15:45'))
        self.assertEqual(M.parse_one(self.received('QGH7XY12AD', '2,500.00'))['amount_cents'], 250000)  # the balance is never mistaken for the amount
        self.assertIsNone(M.parse_one('hello there')); self.assertIsNone(M.parse_one(''))
        self.assertEqual(len(M.parse_many(self.received() + '\n\n' + self.received('QGH7XY12AC', '500') + '\nQGH7XY12AE 750\nKENYAKENYA 100')), 3)

    def test_message_waits_then_is_recorded_when_the_chama_adds_its_record(self):
        self.manual()
        self.paste(self.m1, self.sms()); p = self.last()
        self.assertEqual((p['status'], p['amount_cents'], p['receipt']), ('CLAIMED', 100000, 'QGH7XY12AB'))
        self.assertNotIn('5,230', p['sms_text'] or '')  # the member's balance is not kept
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)
        self.records(self.treas, self.received())
        p = self.last(); self.assertEqual((p['status'], p['verified']), ('SUCCESS', 'RECORD'))
        self.assertEqual(self.db.val('SELECT SUM(amount_cents) FROM contributions'), 100000); self.assertEqual(self.bal(), 100000)
        self.records(self.treas, self.received())  # pasting again changes nothing
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 1); self.assertEqual(self.db.val('SELECT COUNT(*) FROM mpesa_records'), 1)

    def test_message_is_recorded_instantly_when_the_record_already_exists(self):
        self.manual(); self.records(self.treas, self.received())
        self.paste(self.m1, self.sms()); self.assertEqual(self.last()['status'], 'SUCCESS'); self.assertEqual(self.bal(), 100000)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM audit_logs WHERE action='PAY_VERIFIED_BY_RECORD'"), 1)
        self.paste(self.m2, self.sms()); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 1)  # the same code cannot be used by someone else

    def test_edited_amount_never_gets_recorded(self):
        self.manual(); self.records(self.treas, self.received(kes='1,000.00'))
        self.paste(self.m1, self.sms(kes='5,000.00')); p = self.last()  # the member raised the amount in the message
        self.assertEqual(p['status'], 'CLAIMED'); self.assertIn('may have been edited', p['result_desc'])
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0); self.assertEqual(self.bal(), 0)

    def test_invented_code_is_not_recorded_by_itself(self):
        self.manual(); self.records(self.treas, self.received())
        self.paste(self.m1, self.sms(code='ZZZ9ZZZ9ZZ')); self.assertEqual(self.last()['status'], 'CLAIMED'); self.assertEqual(self.bal(), 0)

    def test_nobody_confirms_their_own_payment_with_their_own_record(self):
        self.manual()
        self.paste(self.treas, self.sms()); self.assertEqual(self.last()['status'], 'CLAIMED')
        self.records(self.treas, self.received()); p = self.last()
        self.assertEqual(p['status'], 'CLAIMED'); self.assertIn('Another official', p['result_desc']); self.assertEqual(self.bal(), 0)
        self.post(self.admin, self.base() + f"/pay/{p['id']}/approve"); self.assertEqual(self.last()['status'], 'SUCCESS')

    def test_loan_repayment_and_fine_by_message(self):
        self.manual(); self.add_contrib('m1', 5000); self.add_contrib('m2', 5000)
        F.update_chama_settings(self.db, self.cid, 100000, '10', 3, 1)
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '10000', 'purpose': 'stock'}); lid = self.db.val('SELECT id FROM loans')
        self.post(self.treas, self.base() + f'/loans/{lid}/approve'); self.post(self.admin, self.base() + f'/loans/{lid}/disburse')
        self.records(self.treas, self.received('LON1234ABC', '4,000.00'))
        self.paste(self.m1, self.sms('LON1234ABC', '4,000.00'), what=f'LOAN:{lid}'); self.assertEqual(self.last()['status'], 'SUCCESS')
        self.assertEqual(self.db.val('SELECT paid_cents FROM loans WHERE id=?', (lid,)), 400000)
        self.paste(self.m2, self.sms('LON1234ABD', '1,000.00'), what=f'LOAN:{lid}'); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 1)  # m2 cannot repay m1's loan

    def test_unreadable_future_and_mismatching_input_is_refused(self):
        self.manual()
        for kw in ({'message': 'I paid, trust me'}, {'message': self.sms().replace('12/6/26', '12/6/36')},
                   {'message': self.sms(), 'code': 'OTHER12345'}, {'message': self.sms(), 'amount': '999'}):
            self.post(self.m1, self.base() + '/pay/claim', dict({'what': 'CONTRIBUTION:'}, **kw))
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 0)
        self.post(self.m1, self.base() + '/pay/claim', {'what': 'CONTRIBUTION:', 'code': 'TYPD123456', 'amount': '700'})  # typing code + amount still works
        self.assertEqual(self.last()['status'], 'CLAIMED')

    def test_records_page_is_for_finance_officials_and_each_chama_is_separate(self):
        self.manual()
        for c in (self.m1, self.sec):
            self.assertEqual(c.get(self.base() + '/pay/records').status_code, 403)
            self.assertEqual(self.records(c, self.received()).status_code, 403)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM mpesa_records'), 0)
        self.assertEqual(self.treas.get(self.base() + '/pay/records').status_code, 200)
        self.records(self.treas, 'no messages in here'); self.assertEqual(self.db.val('SELECT COUNT(*) FROM mpesa_records'), 0)
        other = self.admin; first = self.cid
        self.records(self.treas, self.received('UNQ9ABC1XY'))
        self.assertIn('UNQ9ABC1XY', self.treas.get(self.base() + '/pay/records').get_data(as_text=True))
        o, _ = self.signup('Other Admin'); ocid = self.make_chama(o, name='Other Group')
        self.post(o, f'/chamas/{ocid}/pay/settings', {'instructions': 'Till 55555 Other'})
        self.assertEqual(o.get(f'/chamas/{ocid}/pay/records').status_code, 200)
        self.assertNotIn('UNQ9ABC1XY', o.get(f'/chamas/{ocid}/pay/records').get_data(as_text=True))  # another chama's records are invisible
        self.post(o, f'/chamas/{ocid}/pay/claim', {'what': 'CONTRIBUTION:', 'message': self.sms('UNQ9ABC1XY')})  # ...and unusable
        self.assertEqual(self.last()['status'], 'CLAIMED'); self.assertEqual(F.cash_balance(self.db, ocid), 0)

    def trust(self):
        self.manual(); self.post(self.admin, self.base() + '/pay/settings', {**SETTINGS, 'check_mode': 'TRUST'})

    def fresh_sms(self, code='NEW1234ABC', kes='1,000.00', days=0):
        d = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=days))
        return f'{code} Confirmed. Ksh{kes} sent to UMOJA CHAMA for account CHAMA1 on {d.day}/{d.month}/{d.strftime("%y")} at 3:45 PM New M-PESA balance is Ksh5,230.00.'

    def test_straight_away_mode_records_at_once_and_tells_officials(self):
        self.trust(); self.paste(self.m1, self.fresh_sms())
        p = self.last(); self.assertEqual((p['status'], p['verified']), ('SUCCESS', 'TRUSTED')); self.assertEqual(self.bal(), 100000)
        self.assertEqual(p['receipt'], 'NEW1234ABC')
        self.paste(self.m2, self.fresh_sms()); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 1)  # code used once

    def test_straight_away_mode_still_needs_a_readable_recent_message(self):
        self.trust()
        self.post(self.m1, self.base() + '/pay/claim', {'what': 'CONTRIBUTION:', 'code': 'TYPD123456', 'amount': '700'})  # typed code only: no message to check
        self.assertEqual(self.last()['status'], 'CLAIMED')
        self.paste(self.m2, self.fresh_sms('OLD1234ABC', days=20)); self.assertEqual(self.last()['status'], 'CLAIMED')  # too old: an official decides
        self.assertEqual(self.bal(), 0)

    def test_records_added_later_confirm_or_flag_a_trusted_payment(self):
        self.trust(); self.paste(self.m1, self.fresh_sms('GOOD1234AB', '1,000.00')); self.paste(self.m2, self.fresh_sms('EDIT1234AB', '9,000.00'))
        self.assertEqual(self.bal(), 1000 * 100 + 9000 * 100)
        self.records(self.treas, self.received('GOOD1234AB', '1,000.00') + '\n\n' + self.received('EDIT1234AB', '1,000.00'))
        good = self.db.one("SELECT * FROM chama_payments WHERE receipt='GOOD1234AB'"); bad = self.db.one("SELECT * FROM chama_payments WHERE receipt='EDIT1234AB'")
        self.assertEqual(good['verified'], 'RECORD'); self.assertEqual(bad['verified'], 'TRUSTED'); self.assertIn('WARNING', bad['result_desc'])

    def test_loan_repayment_in_straight_away_mode(self):
        self.trust(); self.add_contrib('m1', 5000); self.add_contrib('m2', 5000)
        F.update_chama_settings(self.db, self.cid, 100000, '10', 3, 1)
        self.post(self.m1, self.base() + '/loans/apply', {'amount': '10000', 'purpose': 'stock'}); lid = self.db.val('SELECT id FROM loans')
        self.post(self.treas, self.base() + f'/loans/{lid}/approve'); self.post(self.admin, self.base() + f'/loans/{lid}/disburse')
        self.paste(self.m1, self.fresh_sms('LOAN1234AB', '2,500.00'), what=f'LOAN:{lid}')
        self.assertEqual(self.last()['status'], 'SUCCESS'); self.assertEqual(self.db.val('SELECT paid_cents FROM loans WHERE id=?', (lid,)), 250000)

    def test_all_new_pages_render(self):
        self.manual(); self.paste(self.m1, self.sms())
        for c in (self.admin, self.treas, self.sec, self.m1):
            self.assertEqual(c.get(self.base() + '/pay').status_code, 302)  # redirects to the Payments page
            self.assertEqual(c.get(self.base() + '/payments').status_code, 200)
        self.assertEqual(self.admin.get(self.base() + '/pay/settings').status_code, 200)
        self.assertEqual(self.treas.get(self.base() + '/pay/records').status_code, 200)
        self.assertEqual(self.m1.get(self.base() + f"/pay/{self.last()['id']}").status_code, 200)


del _tf.FinWeb  # run only this module's tests, not the inherited ones twice
if __name__ == '__main__':
    unittest.main()
