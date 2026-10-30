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
    """Paystack Kenya — hosted checkout page (M-Pesa / card). No STK prompt from app."""

    name = "PAYSTACK"
    label = "Paystack (secure page)"
    base = "https://api.paystack.co"

    def configured(self) -> bool:
        key = (os.environ.get("PAYSTACK_SECRET_KEY") or "").strip().strip('"').strip("'")
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
                err = json.loads(raw)
                msg = err.get("message") or raw
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
            raise RuntimeError(f"Paystack error {e.code}: {msg}") from e

    def initiate(self, payment, phone=None):
        """Hosted Paystack checkout page (M-Pesa/card). No STK from this app."""
        phone = _normalize_phone(phone) if phone else ""
        if not self.configured():
            return {
                "ok": False,
                "error": "PAYSTACK_SECRET_KEY missing or invalid (must start with sk_test_ or sk_live_).",
            }
        amount = int(payment["amount_cents"])
        if amount <= 0:
            return {"ok": False, "error": "Invalid payment amount."}

        reference = f"CP{payment['chama_id']}P{payment['id']}{uuid.uuid4().hex[:8]}".upper()[:50]
        email = (os.environ.get("PAYSTACK_DEFAULT_EMAIL") or "").strip() or (
            f"chama{payment['chama_id']}@subscribers.chamapay.app"
        )
        meta = {
            "chama_id": payment["chama_id"],
            "payment_id": payment["id"],
        }
        if phone:
            meta["phone"] = phone
        app_url = (
            os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
        ).rstrip("/")
        callback = (os.environ.get("PAYSTACK_CALLBACK_URL") or "").strip()
        if not callback and app_url:
            callback = app_url + "/subscription/paystack-return"

        try:
            body = {
                "email": email,
                "amount": amount,
                "currency": "KES",
                "reference": reference,
                "channels": ["mobile_money", "card", "bank", "ussd"],
                "metadata": meta,
            }
            if callback:
                body["callback_url"] = callback
            data = self._request("/transaction/initialize", body)
            d = data.get("data") or {}
            ref = str(d.get("reference") or reference)
            url = d.get("authorization_url")
            if not url:
                return {
                    "ok": False,
                    "error": "Paystack did not return a payment link. Confirm KES is enabled.",
                }
            return {
                "ok": True,
                "checkout_id": ref,
                "authorization_url": str(url),
                "display_text": "Opening Paystack… Complete M-Pesa (or card) on the next page.",
            }
        except RuntimeError as e:
            return {"ok": False, "error": str(e)[:400]}

    def verify_transaction(self, reference):
        data = self._request(f"/transaction/verify/{urllib.parse.quote(str(reference))}")
        d = data.get("data") or {}
        status = str(d.get("status") or "").lower()
        amount = d.get("amount")
        return {
            "event_id": str(d.get("reference") or reference),
            "checkout_id": str(d.get("reference") or reference),
            "result_code": 0 if status == "success" else 1,
            "amount_cents": int(amount) if amount is not None else None,
            "receipt": str(d.get("reference") or reference),
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
            success = status == "success" or "success" in event.lower()
            return {
                "event_id": ref + ":" + (event or "webhook"),
                "checkout_id": ref,
                "result_code": 0 if success else 1,
                "amount_cents": int(amount) if amount is not None else None,
                "receipt": ref,
            }
        except Exception:
            return {"event_id": None, "checkout_id": None}

    @staticmethod
    def verify_signature(raw_body, signature) -> bool:
        secret = (
            (os.environ.get("PAYSTACK_SECRET_KEY") or "")
            .strip()
            .strip('"')
            .strip("'")
            .encode()
        )
        if not secret or not signature:
            return False
        body = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode()
        digest = hmac.new(secret, body, hashlib.sha512).hexdigest()
        try:
            return hmac.compare_digest(digest, str(signature))
        except Exception:
            return digest == str(signature)


def get_provider(is_production: bool):
    mode = (os.environ.get("PAYMENT_PROVIDER") or "").strip().lower()

    if mode == "paystack":
        return PaystackPaymentProvider()
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
