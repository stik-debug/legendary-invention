"""Persistent Web Push VAPID configuration.

When explicit VAPID environment variables are supplied they take precedence.
Otherwise create one keypair and persist it in the application database so a
restart/redeploy does not invalidate device subscriptions.
"""
import base64
import os
from datetime import datetime, timezone


def get_vapid_keys(db):
    public = os.environ.get('VAPID_PUBLIC_KEY', '').strip()
    private = os.environ.get('VAPID_PRIVATE_KEY', '').strip()
    if public and private:
        return public, private
    row = db.one('SELECT public_key, private_key_pem FROM push_vapid_config WHERE id=1')
    if row:
        return row['public_key'], row['private_key_pem']
    return '', ''


def ensure_vapid_keys(db):
    if os.environ.get('VAPID_PUBLIC_KEY', '').strip() and os.environ.get('VAPID_PRIVATE_KEY', '').strip():
        return True
    if db.one('SELECT id FROM push_vapid_config WHERE id=1'):
        return True
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        private = ec.generate_private_key(ec.SECP256R1())
        public_numbers = private.public_key().public_numbers()
        raw_public = b'\x04' + public_numbers.x.to_bytes(32, 'big') + public_numbers.y.to_bytes(32, 'big')
        public = base64.urlsafe_b64encode(raw_public).rstrip(b'=').decode('ascii')
        private_pem = private.private_bytes(encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()).decode('ascii')
        with db.tx():
            if not db.one('SELECT id FROM push_vapid_config WHERE id=1'):
                db.execute('INSERT INTO push_vapid_config(id,public_key,private_key_pem,created_at) VALUES(1,?,?,?)',
                    (public, private_pem, datetime.now(timezone.utc).isoformat()))
        return True
    except Exception:
        # Another worker may have won the first-start race and committed the keypair.
        try:
            return bool(db.one('SELECT id FROM push_vapid_config WHERE id=1'))
        except Exception:
            return False
