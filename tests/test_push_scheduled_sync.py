import json
import os
import unittest
from unittest.mock import patch

import notify


class FakeDB:
    def one(self, sql, params=()):
        if 'notification_preferences' in sql:
            return {'push': 1}
        return None

    def all(self, sql, params=()):
        if 'push_subscriptions' in sql:
            return [{'subscription_json': json.dumps({'endpoint': 'https://push.example.test/x', 'keys': {'p256dh': 'x', 'auth': 'y'}})}]
        return []


class ScheduledPushTests(unittest.TestCase):
    def test_one_shot_jobs_wait_for_push_delivery(self):
        db = FakeDB()
        with patch.dict(os.environ, {'CHAMAPAY_SYNC_PUSH': '1'}), \
             patch.object(notify, 'get_vapid_keys', return_value=('public', 'private')), \
             patch.object(notify, '_deliver_web_push') as deliver, \
             patch.object(notify.threading, 'Thread') as thread:
            notify._queue_web_push(db, 12, 'Payment reminder', '/notifications', 77)
        deliver.assert_called_once()
        thread.assert_not_called()

    def test_normal_web_requests_keep_async_push_delivery(self):
        db = FakeDB()
        with patch.dict(os.environ, {'CHAMAPAY_SYNC_PUSH': '0'}), \
             patch.object(notify, 'get_vapid_keys', return_value=('public', 'private')), \
             patch.object(notify, '_deliver_web_push'), \
             patch.object(notify.threading, 'Thread') as thread:
            notify._queue_web_push(db, 12, 'New message', '/notifications', 78)
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
