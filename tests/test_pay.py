import os, sys, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['CHAMA_SECRETS_KEY'] = 'k' * 40
import test_web_finance as _tf
import chama_pay as P
import community as C
import finance as F
import services as S

TODAY = _tf.TODAY
CREDS = {'mode': 'DARAJA', 'shortcode': '174379', 'env': 'sandbox', 'key': 'KEY123', 'secret': 'SECRET123', 'passkey': 'PASSKEY123', 'instructions': 'Paybill 174379'}


def cb(checkout, code=0, amount=None, receipt='QJK1234ABC'):
    items = [{'Name': 'Amount', 'Value': amount}, {'Name': 'MpesaReceiptNumber', 'Value': receipt}] if code == 0 else []
    return {'Body': {'stkCallback': {'MerchantRequestID': 'm', 'CheckoutRequestID': checkout, 'ResultCode': code, 'ResultDesc': 'x', 'CallbackMetadata': {'Item': items}}}}


class PayWeb(_tf.FinWeb):
    def hook(self, checkout, **kw):
        return self.client().post(f'/webhooks/chama-mpesa/{self.cid}/{P.callback_secret(self.cid)}', json=cb(checkout, **kw))

    def last(self): return self.db.one('SELECT * FROM chama_payments ORDER BY id DESC LIMIT 1')

    def go(self, c, kes='1000', purpose='CONTRIBUTION', tid=''):
        return self.post(c, self.base() + '/pay/start', {'purpose': purpose, 'target_id': tid, 'amount': kes, 'phone': '0712345678'})

    def daraja(self):
        r = self.post(self.admin, self.base() + '/pay/settings', CREDS); self.assertEqual(r.status_code, 302)

    # ---------- online meetings ----------
    def meeting(self, mode='jitsi', link='', when=None, c=None):
        when = when or (datetime.utcnow() + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M')
        self.post(c or self.sec, self.base() + '/meetings/add', {'title': 'Monthly meeting', 'when': when, 'venue': '', 'agenda': '', 'fine': '', 'online': mode, 'link': link})
        return self.db.one('SELECT * FROM meetings ORDER BY id DESC LIMIT 1')

    def test_jitsi_room_join_and_attendance_hint(self):
        self.team(); m = self.meeting()
        self.assertEqual(m['online_provider'], 'JITSI'); self.assertTrue(m['online_room'].startswith('ChamaPay-')); self.assertGreaterEqual(len(m['online_room']), 25)
        page = self.m1.get(self.base() + f"/meetings/{m['id']}").get_data(as_text=True)
        self.assertIn('Join the online meeting', page)
        r = self.m1.get(self.base() + f"/meetings/{m['id']}/join")
        self.assertEqual(r.status_code, 302); self.assertIn(m['online_room'], r.headers['Location']); self.assertIn('userInfo.displayName', r.headers['Location'])
        self.m1.get(self.base() + f"/meetings/{m['id']}/join")  # clicking twice is harmless
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM meeting_joins WHERE meeting_id=?', (m['id'],)), 1)
        self.assertIn('Joined online', self.sec.get(self.base() + f"/meetings/{m['id']}").get_data(as_text=True))
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM attendance'), 0)  # a join is only a hint: nobody is marked present automatically

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
    def test_settings_admin_only_encrypted_and_never_echoed(self):
        self.team()
        self.assertEqual(self.treas.get(self.base() + '/pay/settings').status_code, 403)
        self.post(self.treas, self.base() + '/pay/settings', CREDS); self.assertIsNone(P.get_config(self.db, self.cid))
        self.daraja(); c = P.get_config(self.db, self.cid)
        self.assertNotIn('SECRET123', c['secret_enc']); self.assertEqual(P.dec(c['secret_enc']), 'SECRET123'); self.assertEqual(P.channel(self.db, self.cid), 'DARAJA')
        html = self.admin.get(self.base() + '/pay/settings').get_data(as_text=True)
        for s in ('SECRET123', 'PASSKEY123', 'KEY123'): self.assertNotIn(s, html)
        self.post(self.admin, self.base() + '/pay/settings', {**CREDS, 'key': '', 'secret': '', 'passkey': '', 'shortcode': '600000'})
        self.assertEqual(P.dec(P.get_config(self.db, self.cid)['secret_enc']), 'SECRET123')  # blank keeps the saved secret

    def test_settings_validation(self):
        self.team()
        self.post(self.admin, self.base() + '/pay/settings', {'mode': 'MANUAL', 'instructions': 'x'}); self.assertIsNone(P.get_config(self.db, self.cid))
        self.post(self.admin, self.base() + '/pay/settings', {**CREDS, 'shortcode': 'abc'}); self.assertIsNone(P.get_config(self.db, self.cid))
        with self.assertRaises(S.BusinessError): P.save_config(self.db, self.cid, CREDS, 1, production=True)  # a live chama cannot use sandbox keys

    # ---------- automatic payments ----------
    def test_contribution_paid_in_app_is_recorded_once(self):
        self.team(); self.daraja(); before = self.bal()
        self.assertEqual(self.go(self.m1).status_code, 302)
        p = self.last(); self.assertEqual((p['status'], p['amount_cents']), ('PENDING', 100000)); self.assertTrue(p['checkout_id'])
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)  # sending the request records nothing
        self.assertEqual(self.hook(p['checkout_id'], amount=1000).status_code, 200)
        self.assertEqual(self.last()['status'], 'SUCCESS'); self.assertEqual(self.bal(), before + 100000)
        c = self.db.one('SELECT * FROM contributions'); self.assertEqual((c['user_id'], c['method'], c['reference']), (self.uid['m1'], 'M-Pesa', 'QJK1234ABC'))
        for _ in range(3): self.hook(p['checkout_id'], amount=1000)  # Safaricom retries
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 1); self.assertEqual(self.bal(), before + 100000)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM ledger_transactions WHERE kind='CONTRIBUTION'"), 1)

    def test_wrong_amount_failed_and_forged_callbacks_change_nothing(self):
        self.team(); self.daraja(); self.go(self.m1); p = self.last()
        self.assertEqual(self.client().post(f'/webhooks/chama-mpesa/{self.cid}/nottherightsecret', json=cb(p['checkout_id'], amount=1000)).status_code, 403)
        self.hook(p['checkout_id'], amount=5)
        self.assertEqual(self.last()['status'], 'FAILED'); self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)
        self.go(self.m1); q = self.last(); self.hook(q['checkout_id'], code=1032)
        self.assertEqual(self.last()['status'], 'CANCELLED'); self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)
        self.hook('does-not-exist', amount=1000); self.hook(None)

    def test_a_chama_secret_does_not_work_for_another_chama(self):
        self.team(); self.daraja(); self.go(self.m1); p = self.last()
        other, _ = self.signup('Other Admin'); oc = self.make_chama(other, name='Other Group')
        r = self.client().post(f'/webhooks/chama-mpesa/{oc}/{P.callback_secret(self.cid)}', json=cb(p['checkout_id'], amount=1000))
        self.assertEqual(r.status_code, 403)
        P.settle(self.db, oc, p['checkout_id'], 0, 100000, 'ZZZ1234567'); self.assertEqual(self.last()['status'], 'PENDING')  # even with the right id, another chama cannot settle it
        self.assertEqual(other.get(self.base() + '/pay').status_code, 403)

    def test_members_can_only_pay_their_own_fines_and_not_more_than_owed(self):
        self.team(); self.daraja()
        fid = F.create_fine(self.db, self.cid, self.uid['m2'], 20000, 'Late', TODAY, self.uid['treas'])
        n = self.db.val('SELECT COUNT(*) FROM chama_payments')
        self.go(self.m1, '200', 'FINE', str(fid)); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), n)  # someone else's fine
        self.go(self.m2, '500', 'FINE', str(fid)); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), n)  # more than owed
        self.go(self.m2, '200', 'FINE', str(fid)); p = self.last(); self.hook(p['checkout_id'], amount=200, receipt='FINE123456')
        f = self.db.one('SELECT * FROM fines WHERE id=?', (fid,)); self.assertEqual((f['status'], f['paid_cents']), ('PAID', 20000))
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM ledger_transactions WHERE kind='FINE_PAYMENT'"), 1)

    def test_loan_repayment_paid_in_app(self):
        self.team(); self.daraja(); self.add_contrib('m1', 3000, c=self.treas); self.add_contrib('m2', 3000, c=self.treas)
        lid = F.apply_loan(self.db, self.cid, self.uid['m1'], 100000, 'Stock'); F.decide_loan(self.db, self.cid, lid, True, self.uid['treas']); F.disburse_loan(self.db, self.cid, lid, self.uid['treas'])
        due = self.db.one('SELECT * FROM loans WHERE id=?', (lid,))['total_due_cents']
        self.go(self.m1, str(due / 100 + 1), 'LOAN', str(lid)); self.assertEqual(self.db.val("SELECT COUNT(*) FROM chama_payments WHERE purpose='LOAN'"), 0)
        self.go(self.m1, '500', 'LOAN', str(lid)); self.hook(self.last()['checkout_id'], amount=500, receipt='LOAN123456')
        self.assertEqual(self.db.one('SELECT * FROM loans WHERE id=?', (lid,))['paid_cents'], 50000)

    def test_not_configured_or_not_a_member(self):
        self.team(); self.go(self.m1); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 0)  # nothing set up yet
        self.daraja(); self.go(self.m1); self.go(self.m1); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 1)  # one pending payment at a time
        out, _ = self.signup('Outsider'); self.make_chama(out, name='Other Group')
        out.post(self.base() + '/pay/start', data={'purpose': 'CONTRIBUTION', 'amount': '100', 'phone': '0712345678', '_csrf': self.tok(out)})
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 1)
        self.assertEqual(out.get(self.base() + f"/pay/{self.last()['id']}").status_code, 403)
        self.assertEqual(self.m2.get(self.base() + f"/pay/{self.last()['id']}").status_code, 404)  # another member cannot see my payment

    def test_status_check_never_invents_a_result(self):
        self.team(); self.daraja(); self.go(self.m1); p = self.last()
        self.post(self.m1, self.base() + f"/pay/{p['id']}/check"); self.assertEqual(self.last()['status'], 'PENDING')
        old = (S.now_utc() - timedelta(minutes=30)).isoformat(sep=' ')
        self.db.execute('UPDATE chama_payments SET created_at=? WHERE id=?', (old, p['id'])); self.db.commit()
        self.post(self.m1, self.base() + f"/pay/{p['id']}/check"); self.assertEqual(self.last()['status'], 'TIMEOUT')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM contributions'), 0)

    # ---------- pay yourself, treasurer approves ----------
    def test_manual_claim_approval_flow(self):
        self.team(); self.post(self.admin, self.base() + '/pay/settings', {'mode': 'MANUAL', 'instructions': 'Paybill 123456 account Umoja'})
        self.assertEqual(P.channel(self.db, self.cid), 'MANUAL')
        self.go(self.m1); self.assertEqual(self.db.val('SELECT COUNT(*) FROM chama_payments'), 0)  # no PIN prompt without Daraja
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
        self.team(); self.post(self.admin, self.base() + '/pay/settings', {'mode': 'MANUAL', 'instructions': 'Paybill 123456 account Umoja'})

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
        self.assertIn('WARNING', self.treas.get(self.base() + '/pay').get_data(as_text=True))

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
        self.post(o, f'/chamas/{ocid}/pay/settings', {'mode': 'MANUAL', 'instructions': 'Till 55555 Other'})
        self.assertEqual(o.get(f'/chamas/{ocid}/pay/records').status_code, 200)
        self.assertNotIn('UNQ9ABC1XY', o.get(f'/chamas/{ocid}/pay/records').get_data(as_text=True))  # another chama's records are invisible
        self.post(o, f'/chamas/{ocid}/pay/claim', {'what': 'CONTRIBUTION:', 'message': self.sms('UNQ9ABC1XY')})  # ...and unusable
        self.assertEqual(self.last()['status'], 'CLAIMED'); self.assertEqual(F.cash_balance(self.db, ocid), 0)

    def test_all_new_pages_render(self):
        self.team(); self.daraja(); self.go(self.m1)
        for c in (self.admin, self.treas, self.sec, self.m1):
            self.assertEqual(c.get(self.base() + '/pay').status_code, 200)
        self.assertEqual(self.admin.get(self.base() + '/pay/settings').status_code, 200)
        self.assertEqual(self.treas.get(self.base() + '/pay/records').status_code, 200)
        self.assertEqual(self.m1.get(self.base() + f"/pay/{self.last()['id']}").status_code, 200)


del _tf.FinWeb  # run only this module's tests, not the inherited ones twice
if __name__ == '__main__':
    unittest.main()
