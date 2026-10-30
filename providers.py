"""Payment providers for ChamaPay subscriptions.

TEST     — development simulation
MPESA    — Safaricom Daraja STK
PAYSTACK — Paystack Kenya checkout / M-Pesa
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime


def _normalize_phone(phone: str) -> str:
    p = str(phone or "").replace(" ", "").replace("+", "").replace("-", "")
    if p.startswith("0") and len(p) == 10:
        p = "254" + p[1:]
    if len(p) == 9 and p[0] in ("7", "1"):
        p = "254" + p
    return p


class TestPaymentProvider:
    name = "TEST"
    label = "Test payments (development only)"

    def configured(self) -> bool:
        return True

    def initiate(self, payment, phone):
        return {
            "ok": True,
            "checkout_id": "TEST-CHK-" + uuid.uuid4().hex[:12].upper(),
            "display_text": "Test mode: use the simulate buttons under Payments.",
        }


class MpesaPaymentProvider:
    name = "MPESA"
    label = "M-Pesa (Safaricom)"
    REQUIRED = (
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE",
        "MPESA_PASSKEY",
        "MPESA_CALLBACK_URL",
        "MPESA_CALLBACK_SECRET",
    )

    def configured(self) -> bool:
        return all(os.environ.get(k) for k in self.REQUIRED)

    @property
    def base(self) -> str:
        if os.environ.get("MPESA_ENV", "sandbox") == "production":
            return "https://api.safaricom.co.ke"
        return "https://sandbox.safaricom.co.ke"

    def _token(self) -> str:
        cred = base64.b64encode(
            f"{os.environ['MPESA_CONSUMER_KEY']}:{os.environ['MPESA_CONSUMER_SECRET']}".encode()
        ).decode()
        req = urllib.request.Request(
            self.base + "/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": "Basic " + cred},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["access_token"]

    def initiate(self, payment, phone):
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            code = os.environ["MPESA_SHORTCODE"]
            pw = base64.b64encode((code + os.environ["MPESA_PASSKEY"] + ts).encode()).decode()
            body = {
                "BusinessShortCode": code,
                "Password": pw,
                "Timestamp": ts,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": int(payment["amount_cents"] // 100),
                "PartyA": phone,
                "PartyB": code,
                "PhoneNumber": phone,
                "CallBackURL": os.environ["MPESA_CALLBACK_URL"],
                "AccountReference": f"CHAMA{payment['chama_id']}",
                "TransactionDesc": "ChamaPay subscription",
            }
            req = urllib.request.Request(
                self.base + "/mpesa/stkpush/v1/processrequest",
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": "Bearer " + self._token(),
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
            if data.get("ResponseCode") == "0":
                return {
                    "ok": True,
                    "checkout_id": data["CheckoutRequestID"],
                    "display_text": "M-Pesa prompt sent. Enter your PIN on the phone.",
                }
            return {
                "ok": False,
                "error": data.get("errorMessage")
                or data.get("ResponseDescription")
                or "M-Pesa refused the request",
            }
        except Exception:
            return {"ok": False, "error": "Could not reach M-Pesa. Please try again."}

    @staticmethod
    def parse_callback(data):
        try:
            cb = data["Body"]["stkCallback"]
            items = {
                i["Name"]: i.get("Value")
                for i in (cb.get("CallbackMetadata") or {}).get("Item", [])
            }
            amount = items.get("Amount")
            return {
                "event_id": cb["CheckoutRequestID"],
                "checkout_id": cb["CheckoutRequestID"],
                "result_code": int(cb["ResultCode"]),
                "amount_cents": round(float(amount) * 100) if amount is not None else None,
                "receipt": str(items["MpesaReceiptNumber"])
                if items.get("MpesaReceiptNumber")
                else None,
            }
        except Exception:
            return {"event_id": None, "checkout_id": None}


class PaystackPaymentProvider:
    """Paystack Kenya.

    Default flow ("stk"): Paystack's Charge API sends an M-Pesa prompt straight to the
    customer's phone, so they never leave ChamaPay. If that cannot be started for any
    reason (or PAYSTACK_MODE=checkout), we fall back to Paystack's hosted checkout page
    (M-Pesa / card), which always works for a valid KES-enabled account.
    """

    name = "PAYSTACK"
    label = "Paystack M-Pesa"
    base = "https://api.paystack.co"
    # Paystack transaction/charge states that mean "still waiting for the customer".
    PENDING_STATES = {"pending", "ongoing", "processing", "queued", "pay_offline", "send_otp",
                      "send_pin", "send_phone", "send_birthday", "open_url", "send_address"}
    FAILED_STATES = {"failed", "abandoned", "reversed", "cancelled", "timeout", "declined"}

    def configured(self) -> bool:
        key = self._secret()
        return bool(key) and key.startswith("sk_")

    def _secret(self) -> str:
        return (os.environ.get("PAYSTACK_SECRET_KEY") or "").strip().strip('"').strip("'")

    def _request(self, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST" if body is not None else "GET",
            headers={
                "Authorization": "Bearer " + self._secret(),
                "Content-Type": "application/json",
                "User-Agent": "ChamaPay/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(raw).get("message") or raw
            except Exception:
                msg = raw or e.reason or str(e)
            if e.code == 401:
                raise RuntimeError(
                    "Paystack rejected the secret key (401). Check PAYSTACK_SECRET_KEY on Render."
                ) from e
            if e.code == 403:
                raise RuntimeError(
                    "Paystack forbidden (403). Use a valid Kenya secret key, enable M-Pesa/KES, "
                    "and match test vs live keys."
                ) from e
            raise RuntimeError(f"Paystack error {e.code}: {str(msg)[:200]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError("Could not reach Paystack. Please try again in a moment.") from e
        except ValueError as e:
            raise RuntimeError("Paystack returned an unreadable response.") from e

    @staticmethod
    def _valid_email(e) -> bool:
        e = str(e or "").strip()
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", e)) and len(e) <= 120

    def _payer_email(self, payment, email):
        if self._valid_email(email):
            return str(email).strip()
        default = (os.environ.get("PAYSTACK_DEFAULT_EMAIL") or "").strip()
        if self._valid_email(default):
            return default
        return f"chama{payment['chama_id']}@subscribers.chamapay.app"

    @staticmethod
    def _reference(payment) -> str:
        return f"CP{payment['chama_id']}P{payment['id']}{uuid.uuid4().hex[:8]}".upper()[:50]

    @staticmethod
    def _callback_url() -> str:
        callback = (os.environ.get("PAYSTACK_CALLBACK_URL") or "").strip()
        if callback:
            return callback
        app_url = (os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
        return app_url + "/subscription/paystack-return" if app_url else ""

    def initiate(self, payment, phone, email=None, mode=None):
        phone = _normalize_phone(phone)
        if not (phone.startswith("254") and len(phone) == 12):
            return {"ok": False, "error": "Enter a valid Safaricom number (2547XXXXXXXX or 07XXXXXXXX)."}
        if not self.configured():
            return {"ok": False, "error": "PAYSTACK_SECRET_KEY missing or invalid (must start with sk_test_ or sk_live_)."}
        try:
            amount = int(payment["amount_cents"])
        except (TypeError, ValueError, KeyError):
            amount = 0
        if amount <= 0:
            return {"ok": False, "error": "Invalid payment amount."}

        mode = (mode or os.environ.get("PAYSTACK_MODE") or "stk").strip().lower()
        payer = self._payer_email(payment, email)
        meta = {"chama_id": payment["chama_id"], "payment_id": payment["id"], "phone": phone}

        fallback_note = ""
        if mode != "checkout":
            try:
                return self._charge_mpesa(payment, phone, payer, amount, meta)
            except RuntimeError as e:
                # Do not fail the customer: fall back to the hosted checkout page.
                fallback_note = str(e)[:200]
        try:
            res = self._checkout(payment, phone, payer, amount, meta)
        except RuntimeError as e:
            return {"ok": False, "error": str(e)[:400]}
        if fallback_note:
            res["fallback_reason"] = fallback_note
        return res

    def _charge_mpesa(self, payment, phone, payer, amount, meta):
        reference = self._reference(payment)
        data = self._request("/charge", {
            "email": payer,
            "amount": amount,
            "currency": "KES",
            "reference": reference,
            "mobile_money": {"phone": "+" + phone, "provider": "mpesa"},
            "metadata": meta,
        })
        d = data.get("data") or {}
        state = str(d.get("status") or "").lower()
        if not data.get("status") or state in self.FAILED_STATES:
            raise RuntimeError(str(d.get("message") or data.get("message") or "M-Pesa prompt could not be sent")[:200])
        if state not in self.PENDING_STATES and state != "success":
            raise RuntimeError(f"Unexpected Paystack state: {state or 'unknown'}")
        return {
            "ok": True,
            "mode": "stk",
            "checkout_id": str(d.get("reference") or reference),
            "display_text": str(d.get("display_text") or "M-Pesa prompt sent. Enter your PIN on your phone to approve."),
        }

    def _checkout(self, payment, phone, payer, amount, meta):
        reference = self._reference(payment)
        body = {
            "email": payer,
            "amount": amount,
            "currency": "KES",
            "reference": reference,
            # Kenya supports M-Pesa (mobile_money) and cards.
            "channels": ["mobile_money", "card"],
            "metadata": meta,
        }
        callback = self._callback_url()
        if callback:
            body["callback_url"] = callback
        data = self._request("/transaction/initialize", body)
        d = data.get("data") or {}
        url = d.get("authorization_url")
        if not url:
            raise RuntimeError("Paystack did not return a payment link. Confirm KES is enabled on your account.")
        return {
            "ok": True,
            "mode": "checkout",
            "checkout_id": str(d.get("reference") or reference),
            "authorization_url": str(url),
            "display_text": "Opening Paystack\u2026 complete your M-Pesa (or card) payment there.",
        }

    def verify_transaction(self, reference):
        """Ask Paystack for the real state. result_code: 0 paid, 1 failed, None still pending."""
        data = self._request(f"/transaction/verify/{urllib.parse.quote(str(reference), safe='')}")
        d = data.get("data") or {}
        status = str(d.get("status") or "").lower()
        if status == "success":
            code = 0
        elif status in self.FAILED_STATES:
            code = 1
        else:
            code = None  # pending / ongoing / pay_offline: never treat as failure
        amount = d.get("amount")
        currency = str(d.get("currency") or "KES").upper()
        ref = str(d.get("reference") or reference)
        return {
            "event_id": ref,
            "checkout_id": ref,
            "result_code": code,
            # A payment in any other currency must never activate a KES subscription.
            "amount_cents": int(amount) if amount is not None and currency == "KES" else None,
            "receipt": ref,
            "status": status,
            "currency": currency,
        }

    @staticmethod
    def parse_webhook(data):
        try:
            event = str(data.get("event") or "")
            d = data.get("data") or {}
            ref = str(d.get("reference") or "")
            if not ref:
                return {"event_id": None, "checkout_id": None}
            amount = d.get("amount")
            status = str(d.get("status") or "").lower()
            ev = event.lower()
            if ev == "charge.success" or (not ev and status == "success"):
                code = 0
            elif "fail" in ev or status in PaystackPaymentProvider.FAILED_STATES:
                code = 1
            else:
                code = None
            currency = str(d.get("currency") or "KES").upper()
            return {
                "event_id": ref + ":" + (event or "webhook"),
                "checkout_id": ref,
                "result_code": code,
                "amount_cents": int(amount) if amount is not None and currency == "KES" else None,
                "receipt": ref,
            }
        except Exception:
            return {"event_id": None, "checkout_id": None}

    @staticmethod
    def verify_signature(raw_body, signature) -> bool:
        secret = (
            (os.environ.get("PAYSTACK_SECRET_KEY") or "").strip().strip('"').strip("'").encode()
        )
        if not secret or not signature:
            return False
        body = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode()
        digest = hmac.new(secret, body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(digest, str(signature))


def get_provider(is_production: bool):
    mode = (os.environ.get("PAYMENT_PROVIDER") or "").strip().lower()

    if mode == "paystack":
        p = PaystackPaymentProvider()
        return p if p.configured() else None
    if mode == "mpesa":
        p = MpesaPaymentProvider()
        return p if p.configured() else None
    if mode == "test":
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
