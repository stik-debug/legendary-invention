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
    """Paystack Kenya — M-Pesa STK via Charge API."""

    name = "PAYSTACK"
    label = "Paystack M-Pesa STK"
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

    def initiate(self, payment, phone):
        """Send M-Pesa STK prompt via Paystack Charge API (not hosted page)."""
        phone254 = _normalize_phone(phone)
        if not (phone254.startswith("254") and len(phone254) == 12):
            return {
                "ok": False,
                "error": "Enter a valid Safaricom number (07XXXXXXXX or 2547XXXXXXXX).",
            }
        # Paystack Kenya accepts local 07… and international 254…
        phone_local = "0" + phone254[3:]

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
            "chama_id": str(payment["chama_id"]),
            "payment_id": str(payment["id"]),
            "phone": phone254,
        }

        # --- Primary: Charge API → real M-Pesa STK push on the phone ---
        charge_body = {
            "email": email,
            "amount": amount,
            "currency": "KES",
            "reference": reference,
            "mobile_money": {
                "phone": phone_local,
                "provider": "mpesa",
            },
            "metadata": meta,
        }
        try:
            data = self._request("/charge", charge_body)
        except RuntimeError as e:
            err = str(e)
            # Retry once with 254 format if local rejected
            if "phone" in err.lower() or "mobile" in err.lower() or "invalid" in err.lower():
                charge_body["mobile_money"]["phone"] = phone254
                try:
                    data = self._request("/charge", charge_body)
                except RuntimeError as e2:
                    return {"ok": False, "error": str(e2)[:400]}
            else:
                return {"ok": False, "error": err[:400]}

        if not data.get("status"):
            msg = data.get("message") or "Paystack did not accept the charge."
            return {"ok": False, "error": str(msg)[:400]}

        d = data.get("data") or {}
        ref = str(d.get("reference") or reference)
        status = str(d.get("status") or "").lower()
        display = data.get("message") or d.get("display_text") or ""

        # pay_offline / send_otp / pending = prompt sent or waiting on phone
        if status in ("pay_offline", "pending", "send_otp", "ongoing", "open"):
            return {
                "ok": True,
                "checkout_id": ref,
                "authorization_url": None,  # STK on phone — no redirect
                "display_text": (
                    display
                    or f"M-Pesa prompt sent to {phone_local}. Enter your PIN on the phone to complete payment."
                ),
            }
        if status == "success":
            return {
                "ok": True,
                "checkout_id": ref,
                "authorization_url": None,
                "display_text": "Payment completed successfully.",
            }
        if status in ("failed", "false", "abandoned"):
            return {
                "ok": False,
                "error": display or f"Paystack charge status: {status}",
            }

        # Unknown status — still treat as initiated if we have a reference
        return {
            "ok": True,
            "checkout_id": ref,
            "authorization_url": None,
            "display_text": display
            or f"Payment started ({status or 'pending'}). Check your phone for the M-Pesa prompt.",
        }

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
        # Always return provider so UI shows Paystack; initiate() validates the key
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
