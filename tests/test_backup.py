import os
import tempfile
import unittest
from unittest.mock import patch

import backup


class BackupDurabilityTests(unittest.TestCase):
    def test_render_refuses_ephemeral_backup_when_bucket_missing(self):
        env = {"RENDER": "true"}
        with patch.dict(os.environ, env, clear=True), patch.object(backup, "export_database") as export:
            with self.assertRaisesRegex(RuntimeError, "Durable backup is not configured"):
                backup.save_backup("sqlite:///unused.db")
            export.assert_not_called()

    def test_local_development_backup_uses_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"BACKUP_DIR": tmp}, clear=True), patch.object(backup, "export_database", return_value=b"compressed-test-data"):
                result = backup.save_backup("sqlite:///unused.db")
                self.assertTrue(result.startswith(tmp + os.sep))
                with open(result, "rb") as f:
                    self.assertEqual(f.read(), b"compressed-test-data")


if __name__ == "__main__":
    unittest.main()
