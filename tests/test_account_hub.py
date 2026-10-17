# Keep this file name sorting before test_pay.py, which removes _tf.FinWeb after loading.
import os, sys, unittest
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import test_web_finance as _tf


def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Hub(unittest.TestCase):
    _W, _T = _tw.Web, _tf.FinWeb
    setUp, tearDown, client, tok, post, signup, make_chama, sub, phone_of, owner = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.sub, _W.phone_of, _W.owner
    team, base = _T.team, _T.base

    def provider(self, name, category='Training', county='Nairobi', status='VERIFIED', user='m2'):
        return self.db.insert('marketplace_providers', user_id=self.uid[user], name=name, category=category, description=f'{name} helps chamas grow.',
                              county=county, status=status, created_at=now())

    def review(self, pid, rating, n):
        self.db.insert('marketplace_reviews', provider_id=pid, user_id=self.uid['m1'], request_id=900 + n, rating=rating, title='Solid', body='Good work.', status='PUBLISHED', created_at=now())
        self.db.commit()

    def page(self, c, path):
        r = c.get(path); self.assertEqual(r.status_code, 200); return r.get_data(as_text=True)

    # ---------------- marketplace ----------------
    def test_marketplace_shows_only_verified_with_chips_and_search_filters(self):
        self.team()
        a = self.provider('Alpha Books', 'Bookkeeping', 'Nairobi'); self.provider('Beta Legal', 'Legal support', 'Eldoret')
        self.provider('Hidden Pending', 'Training', status='PENDING'); self.db.commit()
        html = self.page(self.m1, '/marketplace')
        self.assertIn('Alpha Books', html); self.assertIn('Beta Legal', html); self.assertNotIn('Hidden Pending', html)
        self.assertIn('mx-chip', html); self.assertIn('/marketplace/%d/quote-request' % a, html); self.assertIn('/marketplace/%d/interest' % a, html)
        html = self.page(self.m1, '/marketplace?category=Bookkeeping'); self.assertIn('Alpha Books', html); self.assertNotIn('Beta Legal', html)
        html = self.page(self.m1, '/marketplace?q=legal'); self.assertNotIn('Alpha Books', html); self.assertIn('Beta Legal', html)
        html = self.page(self.m1, '/marketplace?county=Eldoret'); self.assertNotIn('Alpha Books', html); self.assertIn('Beta Legal', html)
        for odd in ('?sort=zzz', '?min_rating=9', '?category=Nope', '?min_rating=abc'): self.page(self.m1, '/marketplace' + odd)

    def test_rating_filter_and_sort(self):
        self.team()
        lo = self.provider('Low Rated'); hi = self.provider('High Rated'); self.db.commit()
        self.review(lo, 2, 1); self.review(hi, 5, 2)
        html = self.page(self.m1, '/marketplace?min_rating=4'); self.assertIn('High Rated', html); self.assertNotIn('Low Rated', html)
        html = self.page(self.m1, '/marketplace?sort=rating'); self.assertLess(html.index('High Rated'), html.index('Low Rated'))

    def test_save_provider_toggle_is_private_and_verified_only(self):
        self.team()
        pid = self.provider('Saveable Co'); pend = self.provider('Not Public', status='PENDING'); self.db.commit()
        self.assertIn('Nothing saved yet', self.page(self.m1, '/marketplace?saved=1'))
        self.assertEqual(self.post(self.m1, f'/marketplace/{pid}/save').status_code, 302)
        self.assertIn('Saveable Co', self.page(self.m1, '/marketplace?saved=1'))
        self.assertIn('Nothing saved yet', self.page(self.m3 if hasattr(self, 'm3') else self.admin, '/marketplace?saved=1'))  # someone else's list is separate
        self.post(self.m1, f'/marketplace/{pid}/save')  # toggle off
        self.assertIn('Nothing saved yet', self.page(self.m1, '/marketplace?saved=1'))
        self.assertEqual(self.post(self.m1, f'/marketplace/{pend}/save').status_code, 404)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM provider_saves'), 0)

    def test_marketplace_requires_login(self):
        self.assertEqual(self.client().get('/marketplace').status_code, 302)
        self.assertEqual(self.client().post('/marketplace/1/save').status_code in (302, 400, 403), True)

    # ---------------- community ----------------
    def new_post(self, c, title, kind='DISCUSSION', body='This is a helpful question about our chama.', county=''):
        r = self.post(c, '/collaboration/post', {'kind': kind, 'title': title, 'body': body, 'county': county}); self.assertEqual(r.status_code, 302)
        return self.db.val('SELECT id FROM collaboration_posts WHERE title=?', (title,))

    def test_feed_filters_search_mine_and_paging(self):
        self.team()
        a = self.new_post(self.m1, 'How to grow savings', 'DISCUSSION', county='Eldoret')
        b = self.new_post(self.m2, 'Meeting minutes template', 'TEMPLATE')
        html = self.page(self.m1, '/collaboration')
        self.assertIn('How to grow savings', html); self.assertIn('Meeting minutes template', html)
        self.assertIn('cx-post', html); self.assertIn('Start a conversation', html)
        html = self.page(self.m1, '/collaboration?kind=TEMPLATE'); self.assertNotIn('How to grow savings', html); self.assertIn('Meeting minutes template', html)
        html = self.page(self.m1, '/collaboration?q=savings'); self.assertIn('How to grow savings', html); self.assertNotIn('Meeting minutes template', html)
        html = self.page(self.m1, '/collaboration?mine=1'); self.assertIn('How to grow savings', html); self.assertNotIn('Meeting minutes template', html)
        html = self.page(self.m1, '/collaboration?limit=1'); self.assertIn('Show more posts', html)
        self.assertNotIn('Show more posts', self.page(self.m1, '/collaboration?limit=5'))
        for odd in ('?sort=zzz', '?kind=zzz', '?limit=abc', '?limit=-4'): self.page(self.m1, '/collaboration' + odd)

    def test_helpful_votes_toggle_and_sort_most_helpful(self):
        self.team()
        old = self.new_post(self.m1, 'Older useful post'); new = self.new_post(self.m1, 'Newer quiet post')
        self.assertEqual(self.post(self.m2, f'/collaboration/{old}/like').status_code, 302)
        self.assertEqual(self.post(self.admin, f'/collaboration/{old}/like').status_code, 302)
        html = self.page(self.m1, '/collaboration?sort=helpful')
        self.assertIn('Helpful · 2', html); self.assertLess(html.index('Older useful post'), html.index('Newer quiet post'))
        self.post(self.m2, f'/collaboration/{old}/like')  # same person again removes their vote
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM post_likes WHERE post_id=?', (old,)), 1)
        self.assertEqual(self.post(self.m1, '/collaboration/999999/like').status_code, 404)
        self.db.execute("UPDATE collaboration_posts SET status='REMOVED' WHERE id=?", (new,)); self.db.commit()
        self.assertEqual(self.post(self.m1, f'/collaboration/{new}/like').status_code, 404)

    def test_replies_and_safe_markup(self):
        self.team()
        pid = self.new_post(self.m1, 'Reply to me please')
        self.assertEqual(self.post(self.m2, f'/collaboration/{pid}/reply', {'body': 'Here is a respectful reply.'}).status_code, 302)
        html = self.page(self.m1, '/collaboration')
        self.assertIn('Here is a respectful reply.', html); self.assertIn('1 reply', html)
        self.assertIn('data-confirm=', html)                      # delete asks for confirmation via the app's script file
        self.assertNotIn('onsubmit=', html)                       # inline handlers are blocked by the security policy
        self.assertNotRegex(html, r'<script(?![^>]*src=)[^>]*>')  # no inline scripts

    def test_escaping_of_user_text(self):
        self.team()
        self.new_post(self.m1, 'Safe title', body='<script>alert(1)</script> but long enough to post')
        html = self.page(self.m2, '/collaboration')
        self.assertNotIn('<script>alert(1)</script>', html); self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__':
    unittest.main()
