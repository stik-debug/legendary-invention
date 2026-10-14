"""Regression checks for nested community replies."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_community as _tc

class NestedCommunityReplies(_tc.CommWeb):
    def test_reply_table_supports_parent_reply_id(self):
        cols = {r['name'] for r in self.db.all('PRAGMA table_info(collaboration_replies)')}
        self.assertIn('parent_reply_id', cols)

if __name__ == '__main__': unittest.main()
