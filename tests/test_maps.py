import io
import os
import tempfile
import time
import unittest
from urllib.error import HTTPError
from coach.core import Invalid, Store
from coach.maps import PNG, TTL, TileCache, preferences, save_preferences


class TileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.bootstrap()
        self.requests = []
        self.body = PNG + b'fixture tile, never from the provider'
        self.headers = {'ETag': '"sample"', 'Last-Modified': 'Thu, 01 Oct 2026 00:00:00 GMT'}
        self.error = None
        self.cache = TileCache(self.store, self.open_tile)
        self.path = '/api/map/tile/12/2048/1361.png'
        self.referer = 'http://127.0.0.1:8765/'

    def open_tile(self, request, timeout):
        self.requests.append(request)
        self.assertEqual(timeout, 7)
        if self.error:
            raise self.error
        response = io.BytesIO(self.body)
        response.headers = self.headers
        return response

    def enable(self):
        save_preferences(self.store, {'street_map_enabled': True})

    def test_disabled_preference_never_requests_or_caches_tiles(self):
        self.assertTrue(preferences(self.store)['street_map_enabled'])
        save_preferences(self.store, {'street_map_enabled': False})
        self.assertFalse(preferences(self.store)['street_map_enabled'])
        with self.assertRaises(Invalid):
            self.cache.get(self.path, self.referer)
        self.assertEqual(self.requests, [])
        self.assertFalse((self.store.path / 'map_tiles').exists())
        for body in ({'street_map_enabled': 'true'}, {'street_map_enabled': True, 'url': 'https://example.com'}):
            with self.assertRaises(Invalid):
                save_preferences(self.store, body)
        self.assertFalse(preferences(self.store)['street_map_enabled'])

    def test_only_fixed_provider_and_valid_tile_bounds(self):
        self.enable()
        for path in ('/api/map/tile/0/0/0.png', '/api/map/tile/20/0/0.png', '/api/map/tile/12/4096/0.png', '/api/map/tile/12/0/4096.png', '/api/map/tile/12/-1/0.png', '/api/map/tile/../../secrets'):
            with self.assertRaises(Invalid):
                self.cache.get(path, self.referer)
        self.assertEqual(self.requests, [])
        self.cache.get(self.path, self.referer)
        self.assertEqual(self.requests[0].full_url, 'https://tile.openstreetmap.org/12/2048/1361.png')

    def test_cached_tile_deduplicates_requests_and_retains_provider_headers(self):
        self.enable()
        self.assertEqual(self.cache.get(self.path, self.referer), self.body)
        self.assertEqual(self.cache.get(self.path, self.referer), self.body)
        self.assertEqual(len(self.requests), 1)
        headers = dict(self.requests[0].header_items())
        self.assertIn('RunningCoachLocal/', headers['User-agent'])
        self.assertEqual(headers['Referer'], self.referer)
        self.assertNotIn('Cache-control', headers)
        target = self.store.path / 'map_tiles/12_2048_1361.png'
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        save_preferences(self.store, {'street_map_enabled': False})
        with self.assertRaises(Invalid):
            self.cache.get(self.path, self.referer)
        self.assertEqual(len(self.requests), 1)

    def test_expired_cache_revalidates_conditionally_and_handles_304(self):
        self.enable()
        self.cache.get(self.path, self.referer)
        target = self.store.path / 'map_tiles/12_2048_1361.png'
        old = time.time() - TTL - 1
        os.utime(target, (old, old))
        self.error = HTTPError(self.requests[0].full_url, 304, 'Not modified', {}, None)
        self.assertEqual(self.cache.get(self.path, self.referer), self.body)
        headers = dict(self.requests[1].header_items())
        self.assertEqual(headers['If-none-match'], '"sample"')
        self.assertEqual(headers['If-modified-since'], self.headers['Last-Modified'])
        self.cache.get(self.path, self.referer)
        self.assertEqual(len(self.requests), 2)

    def test_provider_failure_or_invalid_image_never_overwrites_safe_cache(self):
        self.enable()
        self.cache.get(self.path, self.referer)
        target = self.store.path / 'map_tiles/12_2048_1361.png'
        old = time.time() - TTL - 1
        os.utime(target, (old, old))
        self.body = b'<html>Not a tile</html>'
        with self.assertRaises(Invalid):
            self.cache.get(self.path, self.referer)
        self.assertTrue(target.read_bytes().startswith(PNG))
        self.error = OSError('fixture offline')
        with self.assertRaisesRegex(Invalid, 'GPS route is still shown'):
            self.cache.get(self.path, self.referer)
        self.assertTrue(target.read_bytes().startswith(PNG))


if __name__ == '__main__':
    unittest.main()
