"""Paystack subscription payments, with the Paystack HTTP API mocked (no network needed)."""
import hashlib, hmac, json, os, re, sys, unittest
from datetime import timedelta
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import services as S
from providers import PaystackPaymentProvider as PP
import test_web

KEY = 'sk_test_unit'


def ok(status='pay_offline', ref='REF1', amount=50000, currency='KES'):
    return {'status': True, 'data': {'status': status, 'reference': ref, 'amount': amount, 'currency': currency,
                                     'display_text': 'Please complete the prompt on your phone'}}


class PaystackFlow(unittest.TestCase):
    # borrow the HTTP helpers from the main web test-case without inheriting its tests
    setUp = test_web.Web.setUp
    client, tok, post, signup, make_chama, sub = (test_web.Web.client, test_web.Web.tok, test_web.Web.post,
                                                  test_web.Web.signup, test_web.Web.make_chama, test_web.Web.sub)

    def tearDown(self):
        self.env.stop()
        self.db.close()

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'PAYSTACK_SECRET_KEY': KEY, 'PAYMENT_PROVIDER': 'paystack'})
        self.env.start()
        test_web.Web.setUp(self)
        self.app.config['PROVIDER'] = PP()
        self.c, _ = self.signup()
        self.cid = self.make_chama(self.c)
        self.plan_id = S.get_plan(self.db, code='starter')['id']

    # ---- helpers ----
    def pay(self, req=None, **form):
        data = {'plan_id': self.plan_id, 'months': '1', 'phone': '0712 345 678'}
        data.update(form)
        data['_csrf'] = self.tok(self.c)
        return self.c.post(f'/chamas/{self.cid}/subscription/pay', data=data,
                           headers={'X-Requested-With': 'fetch', 'Accept': 'application/json'} if req != 'form' else {})

    def payment(self):
        return self.db.one('SELECT * FROM payments WHERE chama_id=? ORDER BY id DESC', (self.cid,))

    def status(self, pid):
        return self.c.get(f'/chamas/{self.cid}/subscription/payment/{pid}/status').get_json()

    def api(self, charge=None, verify=None):
        def fake(self_, path, body=None):
            if path == '/charge':
                if isinstance(charge, Exception): raise charge
                return charge
            if path == '/transaction/initialize':
                return {'status': True, 'data': {'authorization_url': 'https://checkout.paystack.com/abc', 'reference': body['reference']}}
            if path.startswith('/transaction/verify/'):
                return verify(path) if callable(verify) else verify
            raise AssertionError(path)
        return mock.patch.object(PP, '_request', fake)

    # ---- provider unit tests ----
    def test_stk_push_body_is_correct(self):
        seen = {}
        def fake(self_, path, body=None):
            seen.update(path=path, body=body); return ok(ref=body['reference'])
        with mock.patch.object(PP, '_request', fake):
            r = PP().initiate({'id': 5, 'chama_id': 2, 'amount_cents': 50000}, '0712345678', email='a@b.co')
        self.assertTrue(r['ok']); self.assertEqual(r['mode'], 'stk')
        b = seen['body']
        self.assertEqual((seen['path'], b['currency'], b['amount'], b['email']), ('/charge', 'KES', 50000, 'a@b.co'))
        self.assertEqual(b['mobile_money'], {'phone': '+254712345678', 'provider': 'mpesa'})

    def test_stk_failure_falls_back_to_hosted_checkout(self):
        with self.api(charge=RuntimeError('Paystack error 400: nope')):
            r = PP().initiate({'id': 5, 'chama_id': 2, 'amount_cents': 50000}, '0712345678')
        self.assertTrue(r['ok']); self.assertEqual(r['mode'], 'checkout')
        self.assertTrue(r['authorization_url'].startswith('https://checkout.paystack.com/'))

    def test_checkout_channels_are_kenyan(self):
        seen = {}
        def fake(self_, path, body=None):
            seen.update(body or {}); return {'status': True, 'data': {'authorization_url': 'https://x', 'reference': body['reference']}}
        with mock.patch.object(PP, '_request', fake):
            PP().initiate({'id': 1, 'chama_id': 1, 'amount_cents': 50000}, '0712345678', mode='checkout')
        self.assertEqual(seen['channels'], ['mobile_money', 'card'])

    def test_bad_phone_and_amount_rejected(self):
        self.assertFalse(PP().initiate({'id': 1, 'chama_id': 1, 'amount_cents': 50000}, '12345')['ok'])
        self.assertFalse(PP().initiate({'id': 1, 'chama_id': 1, 'amount_cents': 0}, '0712345678')['ok'])

    def test_verify_states(self):
        for state, code in (('success', 0), ('failed', 1), ('abandoned', 1), ('pending', None), ('ongoing', None), ('pay_offline', None)):
            with self.api(verify={'status': True, 'data': {'status': state, 'reference': 'R', 'amount': 50000, 'currency': 'KES'}}):
                self.assertEqual(PP().verify_transaction('R')['result_code'], code, state)
        with self.api(verify={'status': True, 'data': {'status': 'success', 'reference': 'R', 'amount': 50000, 'currency': 'USD'}}):
            self.assertIsNone(PP().verify_transaction('R')['amount_cents'])

    # ---- end to end over HTTP ----
    def test_full_success_flow_activates_plan_once(self):
        with self.api(charge=ok(ref='CPX')):
            r = self.pay()
        j = r.get_json(); self.assertTrue(j['ok']); self.assertEqual(j['mode'], 'stk')
        p = self.payment(); self.assertEqual((p['status'], p['provider_txn_id'], p['amount_cents']), ('PENDING', 'CPX', 50000))
        before = self.sub(self.cid)['due_at']
        # still pending at Paystack -> must stay PENDING (never FAILED)
        with self.api(verify={'status': True, 'data': {'status': 'pay_offline', 'reference': 'CPX', 'amount': 50000, 'currency': 'KES'}}):
            self.assertEqual(self.status(p['id']), {'status': 'PENDING', 'done': False})
        self.assertEqual(self.payment()['status'], 'PENDING')
        done = {'status': True, 'data': {'status': 'success', 'reference': 'CPX', 'amount': 50000, 'currency': 'KES'}}
        with self.api(verify=done):
            self.assertEqual(self.status(p['id'])['status'], 'SUCCESS')
            self.assertEqual(self.status(p['id'])['status'], 'SUCCESS')  # idempotent
        sub = self.sub(self.cid); self.assertEqual(sub['status'], 'ACTIVE'); self.assertGreater(sub['due_at'], before)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM payments WHERE status='SUCCESS'"), 1)

    def test_wrong_amount_never_activates(self):
        with self.api(charge=ok(ref='CPY')):
            self.pay()
        p = self.payment()
        bad = {'status': True, 'data': {'status': 'success', 'reference': 'CPY', 'amount': 100, 'currency': 'KES'}}
        with self.api(verify=bad):
            self.status(p['id'])
        self.assertEqual(self.payment()['status'], 'FAILED')
        self.assertNotEqual(self.sub(self.cid)['status'], 'ACTIVE')

    def test_failed_payment_is_marked_failed(self):
        with self.api(charge=ok(ref='CPZ')):
            self.pay()
        p = self.payment()
        with self.api(verify={'status': True, 'data': {'status': 'failed', 'reference': 'CPZ', 'amount': 50000, 'currency': 'KES'}}):
            self.assertEqual(self.status(p['id'])['status'], 'FAILED')

    def test_hosted_checkout_redirect_and_return(self):
        with self.api(charge=RuntimeError('boom')):
            j = self.pay().get_json()
        self.assertEqual(j['mode'], 'checkout'); self.assertTrue(j['redirect_url'].startswith('https://checkout.paystack.com/'))
        p = self.payment()
        # browser returns before M-Pesa confirms: must NOT fail the payment, lands on status page
        pend = {'status': True, 'data': {'status': 'ongoing', 'reference': p['provider_txn_id'], 'amount': 50000, 'currency': 'KES'}}
        with self.api(verify=pend):
            r = self.c.get('/subscription/paystack-return?reference=' + p['provider_txn_id'])
        self.assertEqual(r.status_code, 302); self.assertIn(f'/subscription/payment/{p["id"]}', r.headers['Location'])
        self.assertEqual(self.payment()['status'], 'PENDING')

    def test_plain_form_post_redirects_to_paystack_with_csp_allowing_it(self):
        with self.api(charge=RuntimeError('boom')):
            r = self.pay('form', mode='checkout')
        self.assertEqual(r.status_code, 302); self.assertTrue(r.headers['Location'].startswith('https://checkout.paystack.com/'))
        csp = self.c.get('/terms').headers['Content-Security-Policy']
        self.assertRegex(csp, r"form-action[^;]*https://checkout\.paystack\.com")

    def test_invalid_phone_gives_friendly_json_error(self):
        r = self.pay(phone='123'); self.assertEqual(r.status_code, 400)
        self.assertIn('Safaricom', r.get_json()['error']); self.assertIsNone(self.payment())

    def test_provider_failure_marks_payment_failed_and_reports(self):
        with self.api(charge=RuntimeError('x')), mock.patch.object(PP, '_checkout', side_effect=RuntimeError('Paystack forbidden (403).')):
            r = self.pay()
        self.assertEqual(r.status_code, 502); self.assertIn('403', r.get_json()['error'])
        self.assertEqual(self.payment()['status'], 'FAILED')

    def test_webhook_signature_and_success(self):
        with self.api(charge=ok(ref='CPW')):
            self.pay()
        body = json.dumps({'event': 'charge.success', 'data': {'reference': 'CPW', 'status': 'success', 'amount': 50000, 'currency': 'KES'}}).encode()
        self.assertEqual(self.client().post('/webhooks/paystack', data=body, headers={'X-Paystack-Signature': 'bad'}).status_code, 401)
        sig = hmac.new(KEY.encode(), body, hashlib.sha512).hexdigest()
        done = {'status': True, 'data': {'status': 'success', 'reference': 'CPW', 'amount': 50000, 'currency': 'KES'}}
        with self.api(verify=done):
            r = self.client().post('/webhooks/paystack', data=body, headers={'X-Paystack-Signature': sig, 'Content-Type': 'application/json'})
            r2 = self.client().post('/webhooks/paystack', data=body, headers={'X-Paystack-Signature': sig, 'Content-Type': 'application/json'})
        self.assertEqual((r.status_code, r2.status_code), (200, 200))
        self.assertEqual(self.payment()['status'], 'SUCCESS')
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM payments WHERE status='SUCCESS'"), 1)

    def test_late_success_after_timeout_still_activates(self):
        with self.api(charge=ok(ref='CPL')):
            self.pay()
        S.expire_pending_payments(self.db, S.now_utc() + timedelta(minutes=30))
        self.assertEqual(self.payment()['status'], 'TIMEOUT')
        ev = {'event_id': 'CPL', 'checkout_id': 'CPL', 'result_code': 0, 'amount_cents': 50000, 'receipt': 'CPL'}
        self.assertEqual(S.process_webhook(self.db, 'PAYSTACK', ev), 'SUCCESS')

    # ---- pages ----
    def test_pages_render_and_have_no_inline_script(self):
        h = self.c.get(f'/chamas/{self.cid}/subscription').get_data(as_text=True)
        for needle in ('data-pp-form', 'Send M-Pesa prompt', 'js/subpay.js', 'css/subpay.css', 'data-plans='):
            self.assertIn(needle, h)
        self.assertNotIn('onclick=', h)
        self.assertIsNone(re.search(r'<script(?![^>]*\bsrc=)[^>]*>', h), 'inline <script> would be blocked by the CSP')
        with self.api(charge=ok(ref='CPP')):
            self.pay()
        p = self.payment()
        with self.api(verify={'status': True, 'data': {'status': 'pay_offline', 'reference': 'CPP', 'amount': 50000, 'currency': 'KES'}}):
            h2 = self.c.get(f'/chamas/{self.cid}/subscription/payment/{p["id"]}')
        self.assertEqual(h2.status_code, 200); self.assertIn('Waiting for your payment', h2.get_data(as_text=True))
        self.assertEqual(self.c.get(f'/chamas/{self.cid}/subscription/payment/99999').status_code, 404)

    def test_other_chama_cannot_see_payment(self):
        with self.api(charge=ok(ref='CPQ')):
            self.pay()
        p = self.payment()
        other, _ = self.signup('Other Person'); other_cid = self.make_chama(other, name='Other Group')
        self.assertEqual(other.get(f'/chamas/{other_cid}/subscription/payment/{p["id"]}').status_code, 404)
        self.assertEqual(other.get(f'/chamas/{self.cid}/subscription/payment/{p["id"]}/status').status_code, 403)


if __name__ == '__main__':
    unittest.main()
