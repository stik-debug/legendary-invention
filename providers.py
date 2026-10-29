"""Payment providers.

- TEST: development only (never in real production when configured correctly)
- MPESA: Safaricom Daraja STK (existing)
- PAYSTACK: Paystack Kenya — M-Pesa STK/USSD prompt via mobile_money charge

Existing call sites use: configured(), initiate(payment, phone) -> {ok, checkout_id|error},
and for MPESA: parse_callback(data). Paystack adds parse_webhook(data).
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
        return {'ok': True, 'checkout_id': 'TEST-CHK-' + uuid.uuid4().hex[:12].upper()}


class MpesaPaymentProvider:
    name = 'MPESA'
    label = 'M-Pesa (Safaricom Daraja)'
    REQUIRED = (
        'MPESA_CONSUMER_KEY', 'MPESA_CONSUMER_SECRET', 'MPESA_SHORTCODE',
        'MPESA_PASSKEY', 'MPESA_CALLBACK_URL', 'MPESA_CALLBACK_SECRET',
    )

    def configured(self):
        return all(os.environ.get(k) for k in self.REQUIRED)

    @property
    def base(self):
        return (
            'https://api.safaricom.co.ke'
            if os.environ.get('MPESA_ENV', 'sandbox') == 'production'
            else 'https://sandbox.safaricom.co.ke'
        )

    def _token(self):
        cred = base64.b64encode(
            f"{os.environ['MPESA_CONSUMER_KEY']}:{os.environ['MPESA_CONSUMER_SECRET']}".encode()
        ).decode()
        req = urllib.request.Request(
            self.base + '/oauth/v1/generate?grant_type=client_credentials',
            headers={'Authorization': 'Basic ' + cred},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())['access_token']

    def initiate(self, payment, phone):
        try:
            ts = datetime.now().strftime('%Y%m%d%H%M%S')
            code = os.environ['MPESA_SHORTCODE']
            pw = base64.b64encode((code + os.environ['MPESA_PASSKEY'] + ts).encode()).decode()
            body = {
                'BusinessShortCode': code,
                'Password': pw,
                'Timestamp': ts,
                'TransactionType': 'CustomerPayBillOnline',
                'Amount': int(payment['amount_cents'] // 100),
                'PartyA': phone,
                'PartyB': code,
                'PhoneNumber': phone,
                'CallBackURL': os.environ['MPESA_CALLBACK_URL'],
                'AccountReference': f"CHAMA{payment['chama_id']}",
                'TransactionDesc': 'ChamaPay subscription',
            }
            req = urllib.request.Request(
                self.base + '/mpesa/stkpush/v1/processrequest',
                data=json.dumps(body).encode(),
                headers={
                    'Authorization': 'Bearer ' + self._token(),
                    'Content-Type': 'application/json',
                },
            )
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
            if data.get('ResponseCode') == '0':
                return {'ok': True, 'checkout_id': data['CheckoutRequestID']}
            return {
                'ok': False,
                'error': data.get('errorMessage')
                or data.get('ResponseDescription')
                or 'M-Pesa refused the request',
            }
        except Exception:
            return {'ok': False, 'error': 'Could not reach M-Pesa. Please try again.'}

    @staticmethod
    def parse_callback(data):
        """Turns Safaricom's callback JSON into our event. Malformed input is ignored."""
        try:
            cb = data['Body']['stkCallback']
            items = {i['Name']: i.get('Value') for i in (cb.get('CallbackMetadata') or {}).get('Item', [])}
            amount = items.get('Amount')
            return {
                'event_id': cb['CheckoutRequestID'],
                'checkout_id': cb['CheckoutRequestID'],
                'result_code': int(cb['ResultCode']),
                'amount_cents': round(float(amount) * 100) if amount is not None else None,
                'receipt': str(items['MpesaReceiptNumber']) if items.get('MpesaReceiptNumber') else None,
            }
        except Exception:
            return {'event_id': None, 'checkout_id': None}


class PaystackPaymentProvider:
    """Paystack (Kenya): sends an M-Pesa prompt to the customer's phone via /charge."""

    name = 'PAYSTACK'
    label = 'Paystack (M-Pesa prompt)'
    REQUIRED = ('PAYSTACK_SECRET_KEY',)
    base = 'https://api.paystack.co'

    def configured(self):
        return bool(os.environ.get('PAYSTACK_SECRET_KEY'))

    def _request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                'Authorization': 'Bearer ' + os.environ['PAYSTACK_SECRET_KEY'],
                'Content-Type': 'application/json',
            },
            method='POST' if body is not None else 'GET',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode())
                msg = err.get('message') or str(e)
            except Exception:
                msg = str(e)
            raise RuntimeError(msg) from e

    def initiate(self, payment, phone):
        """Send M-Pesa STK/USSD prompt via Paystack mobile_money charge."""
        phone = _normalize_phone(phone)
        if not (phone.startswith('254') and len(phone) == 12):
            return {'ok': False, 'error': 'Enter a valid Safaricom number (2547XXXXXXXX).'}
        amount = int(payment['amount_cents'])
        if amount <= 0:
            return {'ok': False, 'error': 'Invalid payment amount.'}
        reference = f"CP{payment['chama_id']}P{payment['id']}{uuid.uuid4().hex[:8]}".upper()[:50]
        email = (
            os.environ.get('PAYSTACK_DEFAULT_EMAIL')
            or f"chama{payment['chama_id']}@subscribers.chamapay.app"
        )
        try:
            data = self._request('/charge', {
                'email': email,
                'amount': amount,  # already in KES cents / subunit
                'currency': 'KES',
                'reference': reference,
                'mobile_money': {
                    'phone': phone,
                    'provider': 'mpesa',
                },
                'metadata': {
                    'chama_id': payment['chama_id'],
                    'payment_id': payment['id'],
                    'custom_fields': [
                        {
                            'display_name': 'Purpose',
                            'variable_name': 'purpose',
                            'value': 'ChamaPay subscription',
                        },
                    ],
                },
            })
            d = data.get('data') or {}
            ref = str(d.get('reference') or reference)
            # Paystack may return pay_offline / pending while customer enters PIN
            return {
                'ok': True,
                'checkout_id': ref,
                'display_text': (
                    d.get('display_text')
                    or d.get('gateway_response')
                    or 'Check your phone and enter your M-Pesa PIN to complete payment.'
                ),
            }
        except RuntimeError as e:
            return {'ok': False, 'error': str(e)[:200]}
        except Exception:
            return {'ok': False, 'error': 'Could not reach Paystack. Please try again.'}

    def verify_transaction(self, reference):
        """Server-side verify (used by webhook + optional poll)."""
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
            'raw_status': status,
        }

    @staticmethod
    def parse_webhook(data):
        """Map Paystack webhook JSON to process_webhook event shape."""
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
                'event_id': ref + ':' + event,
                'checkout_id': ref,
                'result_code': 0 if success else 1,
                'amount_cents': int(amount) if amount is not None else None,
                'receipt': ref,
            }
        except Exception:
            return {'event_id': None, 'checkout_id': None}

    @staticmethod
    def verify_signature(raw_body, signature):
        secret = os.environ.get('PAYSTACK_SECRET_KEY') or ''
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode(), raw_body if isinstance(raw_body, bytes) else raw_body.encode(), hashlib.sha512).hexdigest()
        try:
            return hmac.compare_digest(digest, str(signature))
        except Exception:
            return digest == str(signature)


def get_provider(is_production):
    """
    Choose provider without mixing test into production accidentally.
    Order:
      1. PAYMENT_PROVIDER=paystack → Paystack if configured
      2. PAYMENT_PROVIDER=mpesa → Daraja if configured
      3. Production default: Daraja if configured, else Paystack if configured, else None
      4. Non-production default: TEST (unless forced to mpesa/paystack)
    """
    mode = (os.environ.get('PAYMENT_PROVIDER') or '').strip().lower()

    if mode == 'paystack':
        p = PaystackPaymentProvider()
        return p if p.configured() else None

    if mode == 'mpesa':
        p = MpesaPaymentProvider()
        return p if p.configured() else None

    if mode == 'test':
        if is_production:
            return None
        return TestPaymentProvider()

    # No explicit mode
    if is_production:
        mpesa = MpesaPaymentProvider()
        if mpesa.configured():
            return mpesa
        paystack = PaystackPaymentProvider()
        if paystack.configured():
            return paystack
        return None

    # Development: prefer Paystack if keys present, else TEST
    paystack = PaystackPaymentProvider()
    if paystack.configured():
        return paystack
    return TestPaymentProvider()
