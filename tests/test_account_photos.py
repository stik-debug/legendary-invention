import io, os, re, struct, sys, unittest, zlib
# Keep this file name sorting before test_pay.py, which removes _tf.FinWeb after loading.
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import test_web as _tw
import test_web_finance as _tf


def tiny_png():
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


class Photos(unittest.TestCase):
    _W, _T = _tw.Web, _tf.FinWeb
    setUp, tearDown, client, tok, post, signup, make_chama, sub, phone_of, owner = _W.setUp, _W.tearDown, _W.client, _W.tok, _W.post, _W.signup, _W.make_chama, _W.sub, _W.phone_of, _W.owner
    team, base = _T.team, _T.base

    def upload(self, c, path, data, name='me.png', page='/'):
        return c.post(path, data={'_csrf': self.tok(c, page), 'photo': (io.BytesIO(data), name)}, content_type='multipart/form-data')

    def test_member_can_upload_see_and_remove_own_photo(self):
        self.team()
        r = self.upload(self.m1, '/profile/photo', tiny_png())
        self.assertEqual(r.status_code, 302)
        url = self.db.val('SELECT profile_image FROM users WHERE id=?', (self.uid['m1'],))
        self.assertTrue(url.startswith(f"/photo/user/{self.uid['m1']}?v="))
        img = self.m1.get(url)
        self.assertEqual(img.status_code, 200); self.assertEqual(img.mimetype, 'image/png')
        self.assertEqual(img.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM profile_images'), 1)
        # A fellow member of the same chama can see it.
        self.assertEqual(self.m2.get(url).status_code, 200)
        # Remove it again.
        self.assertEqual(self.post(self.m1, '/profile/photo/remove').status_code, 302)
        self.assertIsNone(self.db.val('SELECT profile_image FROM users WHERE id=?', (self.uid['m1'],)))
        self.assertEqual(self.m1.get(url).status_code, 404)

    def test_replacing_photo_keeps_one_row(self):
        self.team()
        for _ in range(2): self.upload(self.m1, '/profile/photo', tiny_png())
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM profile_images WHERE owner_type=?', ('user',)), 1)

    def test_bad_files_are_rejected(self):
        self.team()
        for data, name in ((b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 'x.png'),
                           (b'GIF89a....', 'x.png'), (b'', 'x.png'), (b'\x89PNG\r\n\x1a\n' + b'0' * (2 * 1024 * 1024 + 10), 'big.png')):
            self.upload(self.m1, '/profile/photo', data, name)
        self.assertEqual(self.db.val('SELECT COUNT(*) FROM profile_images'), 0)
        self.assertIsNone(self.db.val('SELECT profile_image FROM users WHERE id=?', (self.uid['m1'],)))

    def test_photo_visible_only_to_people_who_share_a_chama(self):
        self.team()
        self.upload(self.m1, '/profile/photo', tiny_png())
        stranger, _ = self.signup('Stranger Person')
        self.assertEqual(stranger.get(f"/photo/user/{self.uid['m1']}").status_code, 404)
        self.assertEqual(self.client().get(f"/photo/user/{self.uid['m1']}").status_code, 302)  # not signed in -> login

    def test_only_admin_changes_the_chama_picture_and_members_can_see_it(self):
        self.team()
        self.assertEqual(self.upload(self.m1, f'/chamas/{self.cid}/profile-photo', tiny_png(), page='/').status_code, 403)
        self.assertEqual(self.upload(self.admin, f'/chamas/{self.cid}/profile-photo', tiny_png()).status_code, 302)
        url = self.db.val('SELECT profile_image FROM chamas WHERE id=?', (self.cid,))
        self.assertEqual(self.m1.get(url).status_code, 200)
        outsider, _ = self.signup('Outside Person')
        self.assertEqual(outsider.get(url).status_code, 404)
        self.assertEqual(self.post(self.m1, f'/chamas/{self.cid}/profile-photo/remove').status_code, 403)
        self.assertEqual(self.post(self.admin, f'/chamas/{self.cid}/profile-photo/remove').status_code, 302)
        self.assertIsNone(self.db.val('SELECT profile_image FROM chamas WHERE id=?', (self.cid,)))

    def test_pages_render_with_compact_picker_and_avatars(self):
        self.team()
        self.upload(self.m1, '/profile/photo', tiny_png())
        self.upload(self.admin, f'/chamas/{self.cid}/profile-photo', tiny_png())
        more = self.admin.get(self.base() + '/more').get_data(as_text=True)
        self.assertIn('data-pp-form', more); self.assertIn('js/photo.js', more)
        self.assertNotIn('profile-photo-card', more)
        self.assertNotIn('data-pp-form', self.m1.get(self.base() + '/more').get_data(as_text=True))  # members only view the group picture
        home = self.m2.get(self.base()).get_data(as_text=True)
        self.assertIn('has-img', home); self.assertIn('/photo/chama/', home); self.assertIn(f"/photo/user/{self.uid['m1']}", home)
        mine = self.m1.get(self.base() + '/my-dashboard')
        self.assertEqual(mine.status_code, 200); self.assertIn('data-pp-form', mine.get_data(as_text=True))
        # Inline scripts are blocked by the app's security policy, so the page must use a script file only.
        self.assertNotRegex(mine.get_data(as_text=True), r'<script(?![^>]*src=)[^>]*>')


if __name__ == '__main__':
    unittest.main()
