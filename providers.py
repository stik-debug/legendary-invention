"""Payment providers. TEST never runs in production; M-Pesa only runs when fully configured."""
import base64
import json
import os
import urllib.request
import uuid
from datetime import datetime


class TestPaymentProvider:
    name = 'TEST'
    label = 'Test payments (development only)'

    def configured(self):
        return True

    def initiate(self, payment, phone):
        return {'ok': True, 'checkout_id': 'TEST-CHK-' + uuid.uuid4().hex[:12].upper()}


class MpesaPaymentProvider:
    name = 'MPESA'
    label = 'M-Pesa'
    REQUIRED = ('MPESA_CONSUMER_KEY', 'MPESA_CONSUMER_SECRET', 'MPESA_SHORTCODE', 'MPESA_PASSKEY', 'MPESA_CALLBACK_URL', 'MPESA_CALLBACK_SECRET')

    def configured(self):
        return all(os.environ.get(k) for k in self.REQUIRED)

    @property
    def base(self):
        return 'https://api.safaricom.co.ke' if os.environ.get('MPESA_ENV', 'sandbox') == 'production' else 'https://sandbox.safaricom.co.ke'

    def _token(self):
        cred = base64.b64encode(f"{os.environ['MPESA_CONSUMER_KEY']}:{os.environ['MPESA_CONSUMER_SECRET']}".encode()).decode()
        req = urllib.request.Request(self.base + '/oauth/v1/generate?grant_type=client_credentials', headers={'Authorization': 'Basic ' + cred})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())['access_token']

    def initiate(self, payment, phone):
        try:
            ts = datetime.now().strftime('%Y%m%d%H%M%S')
            code = os.environ['MPESA_SHORTCODE']
            pw = base64.b64encode((code + os.environ['MPESA_PASSKEY'] + ts).encode()).decode()
            body = {'BusinessShortCode': code, 'Password': pw, 'Timestamp': ts, 'TransactionType': 'CustomerPayBillOnline',
                    'Amount': int(payment['amount_cents'] // 100), 'PartyA': phone, 'PartyB': code, 'PhoneNumber': phone,
                    'CallBackURL': os.environ['MPESA_CALLBACK_URL'], 'AccountReference': f"CHAMA{payment['chama_id']}",
                    'TransactionDesc': 'ChamaPay subscription'}
            req = urllib.request.Request(self.base + '/mpesa/stkpush/v1/processrequest', data=json.dumps(body).encode(),
                                         headers={'Authorization': 'Bearer ' + self._token(), 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
            if data.get('ResponseCode') == '0':
                return {'ok': True, 'checkout_id': data['CheckoutRequestID']}
            return {'ok': False, 'error': data.get('errorMessage') or data.get('ResponseDescription') or 'M-Pesa refused the request'}
        except Exception:
            return {'ok': False, 'error': 'Could not reach M-Pesa. Please try again.'}

    @staticmethod
    def parse_callback(data):
        """Turns Safaricom's callback JSON into our event. Anything malformed yields an event that is ignored."""
        try:
            cb = data['Body']['stkCallback']
            items = {i['Name']: i.get('Value') for i in (cb.get('CallbackMetadata') or {}).get('Item', [])}
            amount = items.get('Amount')
            return {'event_id': cb['CheckoutRequestID'], 'checkout_id': cb['CheckoutRequestID'], 'result_code': int(cb['ResultCode']),
                    'amount_cents': round(float(amount) * 100) if amount is not None else None,
                    'receipt': str(items['MpesaReceiptNumber']) if items.get('MpesaReceiptNumber') else None}
        except Exception:
            return {'event_id': None, 'checkout_id': None}


def get_provider(is_production):
    """Production: M-Pesa only, and only if configured. Otherwise the test provider. They never mix."""
    if is_production:
        p = MpesaPaymentProvider()
        return p if p.configured() else None
    if os.environ.get('PAYMENT_PROVIDER', 'test').lower() == 'mpesa':
        p = MpesaPaymentProvider()
        return p if p.configured() else None
    return TestPaymentProvider()
