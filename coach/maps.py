"""Street tiles by default; visible tiles cache locally, offline preference persists."""
import os
import re
import tempfile
import threading
import time
import urllib.request
from urllib.error import HTTPError
from .core import Invalid, require, load_json, atomic_json

TTL = 7 * 86400
PNG = b'\x89PNG\r\n\x1a\n'
PROVIDER = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'


def preferences(store):
    saved = store.read('map_preferences', {'street_map_enabled': True})
    return {'street_map_enabled': saved.get('street_map_enabled') is True, 'provider': 'openstreetmap'}


def save_preferences(store, body):
    require(set(body) == {'street_map_enabled'} and isinstance(body['street_map_enabled'], bool), 'Invalid map preference')
    with store.lock():
        store.write('map_preferences', body)


class TileCache:
    def __init__(self, store, opener=None):
        self.store = store
        self.opener = opener or urllib.request.urlopen
        self.lock = threading.Lock()

    def get(self, path, referer):
        require(preferences(self.store)['street_map_enabled'], 'Street-map downloads are disabled')
        match = re.fullmatch(r'/api/map/tile/(\d{1,2})/(\d{1,6})/(\d{1,6})\.png', path)
        require(match is not None, 'Invalid map tile')
        z, x, y = map(int, match.groups())
        require(1 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z, 'Map tile outside bounds')
        folder = self.store.path / 'map_tiles'
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / f'{z}_{x}_{y}.png'
        metadata = folder / f'{z}_{x}_{y}.json'
        with self.lock:
            if target.exists() and time.time() - target.stat().st_mtime < TTL:
                return target.read_bytes()
            tiles = list(folder.glob('*.png'))
            if not target.exists() and len(tiles) >= 2048:
                expired = sorted((p for p in tiles if time.time()-p.stat().st_mtime >= TTL),key=lambda p:p.stat().st_mtime)
                require(bool(expired), 'Map cache is full; the GPS route remains available')
                for old in expired[:32]:
                    old.unlink()
                    old.with_suffix('.json').unlink(missing_ok=True)
            headers = {'User-Agent':'RunningCoachLocal/1.0 (+https://github.com/kingarthur321/GarminCoach)', 'Referer':referer, 'Accept':'image/png'}
            if metadata.exists() and target.exists():
                saved = load_json(metadata)
                for key, field in (('etag','If-None-Match'),('last_modified','If-Modified-Since')):
                    if saved.get(key):headers[field] = saved[key]
            request = urllib.request.Request(PROVIDER.format(z=z,x=x,y=y),headers=headers)
            try:
                with self.opener(request,timeout=7) as response:
                    payload = response.read(1024*1024+1)
                    require(payload.startswith(PNG) and len(payload) <= 1024*1024, 'Street-map tile unavailable')
                    cache_metadata = {'etag':response.headers.get('ETag'),'last_modified':response.headers.get('Last-Modified')}
            except HTTPError as exc:
                if exc.code == 304 and target.exists():
                    os.utime(target,None)
                    return target.read_bytes()
                raise Invalid('Street map unavailable; the GPS route is still shown') from None
            except OSError:
                raise Invalid('Street map unavailable; the GPS route is still shown') from None
            fd, temporary = tempfile.mkstemp(dir=folder,prefix='.tile-')
            try:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(payload)
                os.replace(temporary,target)
                atomic_json(metadata,cache_metadata)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
            return payload
