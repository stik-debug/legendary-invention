"""Payment providers.

TEST  — development simulation only
MPESA — Safaricom Daraja STK (existing)
PAYSTACK — M-Pesa PIN prompt via Paystack /charge (Kenya)

Interface used by app.py:
  configured() -> bool
  initiate(payment, phone) -> {ok: True, checkout_id, display_text?} | {ok: False, error}
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime


def _normalize_phone(phone):
    p = str(phone or '').replace(' ', '').replace('+', '').replace('-', '')
    if p.startswith('0') and len(p) == 10:
        p = '254' + p[1:]
    if len(p) == 9 and p[0] in ('7', '1'):
        p = '254' + p
    return p


class TestPaymentProvider:
    name = 'TEST'
    label = 'Test payments (development only)'

    def configured(self):
        return True

    def initiate(self, payment, phone):
        """Always create a Paystack checkout link (opens Paystack page).

        Mobile-money charge is attempted first when possible; if it only returns
        a pending prompt, that is fine. If charge fails, hosted checkout is used
        so the user always gets a page that opens.
        """
        phone = _normalize_phone(phone)
        if not (phone.startswith('254') and len(phone) == 12):
            return {'ok': False, 'error': 'Enter a valid Safaricom number (2547XXXXXXXX or 07XXXXXXXX).'}
        if not self.configured():
            return {'ok': False, 'error': 'PAYSTACK_SECRET_KEY is missing or invalid (must start with sk_test_ or sk_live_).'}
        amount = int(payment['amount_cents'])
        if amount <= 0:
            return {'ok': False, 'error': 'Invalid payment amount.'}

        reference = f"CP{payment['chama_id']}P{payment['id']}{uuid.uuid4().hex[:8]}".upper()[:50]
        email = (os.environ.get('PAYSTACK_DEFAULT_EMAIL') or '').strip() or (
            f"chama{payment['chama_id']}@subscribers.chamapay.app"
        )
        meta = {'chama_id': payment['chama_id'], 'payment_id': payment['id'], 'phone': phone}

        # Public site URL for return after payment
        app_url = (os.environ.get('APP_URL') or os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/')
        callback = (os.environ.get('PAYSTACK_CALLBACK_URL') or '').strip()
        if not callback and app_url:
            callback = app_url + '/subscription/paystack-return'

        # --- Primary: hosted checkout (reliable "open Paystack page") ---
        try:
            body = {
                'email': email,
                'amount': amount,
                'currency': 'KES',
                'reference': reference,
                'channels': ['mobile_money', 'card', 'bank', 'ussd'],
                'metadata': meta,
            }
            if callback:
                body['callback_url'] = callback
            data = self._request('/transaction/initialize', body)
            d = data.get('data') or {}
            ref = str(d.get('reference') or reference)
            url = d.get('authorization_url')
            if not url:
                return {
                    'ok': False,
                    'error': 'Paystack did not return a payment link. Confirm KES is enabled and your secret key is valid.',
                }
            return {
                'ok': True,
                'checkout_id': ref,
                'authorization_url': str(url),
                'display_text': 'Opening Paystack… Complete M-Pesa (or card) on the next page.',
            }
        except RuntimeError as e:
            init_err = str(e)

        # --- Fallback: direct mobile_money charge (phone prompt, no page) ---
        try:
            data = self._request('/charge', {
                'email': email,
                'amount': amount,
                'currency': 'KES',
                'reference': reference + 'C',
                'mobile_money': {'phone': phone, 'provider': 'mpesa'},
                'metadata': meta,
            })
            d = data.get('data') or {}
            ref = str(d.get('reference') or reference)
            text = (
                d.get('display_text')
                or d.get('gateway_response')
                or 'Check your phone for the M-Pesa prompt and enter your PIN.'
            )
            return {'ok': True, 'checkout_id': ref, 'display_text': text}
        except RuntimeError as e:
            return {
                'ok': False,
                'error': (
                    f'Could not open Paystack page: {init_err}. '
                    f'Phone charge also failed: {e}. '
                    'Check PAYSTACK_SECRET_KEY, enable KES / M-Pesa on Paystack, and set APP_URL to your https site.'
                )[:450],
            }

    def verify_transaction(self, reference):
        data = self._request(f'/transaction/verify/{urllib.parse.quote(str(reference))}')
        d = data.get('data') or {}
        status = str(d.get('status') or '').lower()
        amount = d.get('amount')
        return {
            'event_id': str(d.get('reference') or reference),
            'checkout_id': str(d.get('reference') or reference),
            'result_code': 0 if status == 'success' else 1,
            'amount_cents': int(amount) if amount is not None else None,
            'receipt': str(d.get('reference') or reference),
        }

    @staticmethod
    def parse_webhook(data):
        try:
            event = str(data.get('event') or '')
            d = data.get('data') or {}
            ref = str(d.get('reference') or '')
            if not ref:
                return {'event_id': None, 'checkout_id': None}
            amount = d.get('amount')
            status = str(d.get('status') or '').lower()
            success = status == 'success' or 'success' in event.lower()
            return {
                'event_id': ref + ':' + (event or 'webhook'),
                'checkout_id': ref,
                'result_code': 0 if success else 1,
                'amount_cents': int(amount) if amount is not None else None,
                'receipt': ref,
            }
        except Exception:
            return {'event_id': None, 'checkout_id': None}

    @staticmethod
    def verify_signature(raw_body, signature):
        secret = (os.environ.get('PAYSTACK_SECRET_KEY') or '').strip().strip('"').strip("'").encode()
        if not secret or not signature:
            return False
        body = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode()
        digest = hmac.new(secret, body, hashlib.sha512).hexdigest()
        try:
            return hmac.compare_digest(digest, str(signature))
        except Exception:
            return digest == str(signature)



def get_provider(is_production):
    """
    Priority:
      PAYMENT_PROVIDER=paystack → Paystack (if key set)
      PAYMENT_PROVIDER=mpesa    → Daraja (if keys set)
      PAYMENT_PROVIDER=test     → TEST (blocked in production)
      else production: Daraja if configured, else Paystack if configured, else None
      else development: Paystack if key set, else TEST
    """
    mode = (os.environ.get('PAYMENT_PROVIDER') or '').strip().lower()

    if mode == 'paystack':
        p = PaystackPaymentProvider()
        return p if p.configured() else None
    if mode == 'mpesa':
        p = MpesaPaymentProvider()
        return p if p.configured() else None
    if mode == 'test':
        return None if is_production else TestPaymentProvider()

    if is_production:
        mpesa = MpesaPaymentProvider()
        if mpesa.configured():
            return mpesa
        paystack = PaystackPaymentProvider()
        if paystack.configured():
            return paystack
        return None

    paystack = PaystackPaymentProvider()
    if paystack.configured():
        return paystack
    return TestPaymentProvider()
