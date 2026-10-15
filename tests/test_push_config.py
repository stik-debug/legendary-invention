import os
import tempfile
import unittest
from unittest.mock import patch

from db import DB, init_db
from push_config import ensure_vapid_keys, get_vapid_keys


class PersistentPushKeys(unittest.TestCase):
    def test_keypair_is_generated_once_and_survives_reopen(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            with patch.dict(os.environ, {'VAPID_PUBLIC_KEY': '', 'VAPID_PRIVATE_KEY': ''}):
                db = DB('sqlite:///' + path)
                init_db(db)
                self.assertTrue(ensure_vapid_keys(db))
                first = get_vapid_keys(db)
                self.assertTrue(first[0].startswith('B'))
                self.assertIn('BEGIN PRIVATE KEY', first[1])
                self.assertTrue(ensure_vapid_keys(db))
                self.assertEqual(first, get_vapid_keys(db))
                db.close()
                db2 = DB('sqlite:///' + path)
                self.assertEqual(first, get_vapid_keys(db2))
                db2.close()
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
