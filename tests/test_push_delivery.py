import base64
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from db import DB, init_db
from push_config import ensure_vapid_keys, get_vapid_keys, signing_key
import notify as N


class FakeResponse:
    def __init__(self, code):
        self.status_code = code


class FakePushError(Exception):
    def __init__(self, code):
        super().__init__('push failed')
        self.response = FakeResponse(code)


class PushDelivery(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.url = 'sqlite:///' + self.path
        self.env = patch.dict(os.environ, {'VAPID_PUBLIC_KEY': '', 'VAPID_PRIVATE_KEY': ''})
        self.env.start()
        self.db = DB(self.url)
        init_db(self.db)
        ensure_vapid_keys(self.db)

    def tearDown(self):
        self.env.stop()
        self.db.close()
        os.unlink(self.path)

    def test_signing_key_converts_stored_pem_to_raw_32_byte_key(self):
        _, pem = get_vapid_keys(self.db)
        self.assertIn('BEGIN PRIVATE KEY', pem)
        raw = signing_key(pem)
        self.assertNotIn('BEGIN', raw)
        decoded = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
        self.assertEqual(len(decoded), 32)

    def test_signing_key_leaves_env_style_raw_key_unchanged(self):
        self.assertEqual(signing_key('abcDEF-_123'), 'abcDEF-_123')

    def _install_fake_webpush(self, behaviour):
        calls = []
        def fake_webpush(**kwargs):
            calls.append(kwargs)
            return behaviour(kwargs)
        module = types.ModuleType('pywebpush')
        module.webpush = fake_webpush
        module.WebPushException = FakePushError
        return patch.dict(sys.modules, {'pywebpush': module}), calls

    def test_delivery_uses_raw_key_and_high_urgency(self):
        _, pem = get_vapid_keys(self.db)
        ctx, calls = self._install_fake_webpush(lambda kw: None)
        with ctx:
            N._deliver_web_push([{'endpoint': 'https://fcm.googleapis.com/x', 'keys': {}}], {'title': 't'}, pem, 'mailto:a@b.co', self.url)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('BEGIN', calls[0]['vapid_private_key'])
        self.assertEqual(calls[0]['headers'], {'Urgency': 'high'})

    def test_expired_endpoint_is_removed_but_working_one_kept(self):
        _, pem = get_vapid_keys(self.db)
        uid = self._make_user()
        for ep in ('https://fcm.googleapis.com/dead', 'https://fcm.googleapis.com/live'):
            self.db.insert('push_subscriptions', user_id=uid, endpoint=ep, subscription_json='{}', created_at='2026-01-01', last_used_at='2026-01-01')
        self.db.commit()
        def behave(kw):
            if kw['subscription_info']['endpoint'].endswith('dead'):
                raise FakePushError(410)
        ctx, _ = self._install_fake_webpush(behave)
        with ctx:
            N._deliver_web_push([{'endpoint': 'https://fcm.googleapis.com/dead', 'keys': {}},
                                 {'endpoint': 'https://fcm.googleapis.com/live', 'keys': {}}], {'title': 't'}, pem, 'mailto:a@b.co', self.url)
        remaining = [r['endpoint'] for r in self.db.all('SELECT endpoint FROM push_subscriptions')]
        self.assertEqual(remaining, ['https://fcm.googleapis.com/live'])

    def test_wait_for_pending_push_blocks_until_threads_finish(self):
        import threading, time
        done = []
        t = threading.Thread(target=lambda: (time.sleep(0.3), done.append(1)), daemon=True)
        with N._PUSH_LOCK:
            N._PUSH_THREADS.append(t)
        t.start()
        self.assertEqual(N.wait_for_pending_push(5), 0)
        self.assertEqual(done, [1])

    def _make_user(self):
        cols = [r['name'] for r in self.db.all("PRAGMA table_info(users)")]
        values = {}
        for r in self.db.all("PRAGMA table_info(users)"):
            if r['notnull'] and r['dflt_value'] is None and not r['pk']:
                values[r['name']] = 'x' if 'INT' not in (r['type'] or '').upper() else 1
        values.update({'name': 'A', 'phone': '254700000001'} if 'name' in cols else {'phone': '254700000001'})
        return self.db.insert('users', **values)


if __name__ == '__main__':
    unittest.main()
