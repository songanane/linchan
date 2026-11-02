"""Networking: thread pool + 4chan API rate limiting + on-disk cache."""

import gzip
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

UA = "linchan/0.1 (GTK4 4chan client; +https://github.com/)"
API = "https://a.4cdn.org"
IMG = "https://i.4cdn.org"
WEB = "https://boards.4chan.org"
CACHE = Path(GLib.get_user_cache_dir()) / "linchan"

_API_INTERVAL = 1.05  # 4chan API rules: max one request per second


class Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


def _call(cb, *args):
    try:
        cb(*args)
    except Exception:
        traceback.print_exc()
    return False


class Net:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="net")
        self._api_lock = threading.Lock()
        self._last_api = 0.0
        self._inflight = {}  # dest -> [callbacks]; main thread only

    # ---- JSON / generic GET -------------------------------------------------
    def _open(self, url, headers=None, api=False):
        if api:
            with self._api_lock:
                wait = self._last_api + _API_INTERVAL - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last_api = time.monotonic()
        h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
        h.update(headers or {})
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body, status, hdr = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            body, status, hdr = e.read(), e.code, e.headers
        if body and hdr.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return Response(status, hdr, body)

    def get(self, url, cb, *, api=False, headers=None):
        """cb(response, error) is invoked on the GTK main thread."""

        def work():
            try:
                res, err = self._open(url, headers, api), None
            except Exception as e:  # network errors
                res, err = None, e
            GLib.idle_add(_call, cb, res, err)

        self.pool.submit(work)

    # ---- file download --------------------------------------------------------
    def download(self, url, dest, cb, progress=None):
        """Download url -> dest (if missing). cb(path, error) on main thread."""
        dest = Path(dest)
        if dest.exists():
            GLib.idle_add(_call, cb, dest, None)
            return
        if dest in self._inflight:
            self._inflight[dest].append(cb)
            return
        self._inflight[dest] = [cb]

        def finish(path, err):
            for c in self._inflight.pop(dest, []):
                _call(c, path, err)
            return False

        def work():
            tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.part")
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    got, last = 0, 0.0
                    while chunk := r.read(65536):
                        f.write(chunk)
                        got += len(chunk)
                        now = time.monotonic()
                        if progress and total and now - last > 0.08:
                            last = now
                            GLib.idle_add(_call, progress, got / total)
                os.replace(tmp, dest)
                GLib.idle_add(finish, dest, None)
            except Exception as e:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                GLib.idle_add(finish, None, e)

        self.pool.submit(work)


# ---- texture cache -------------------------------------------------------------
_textures = OrderedDict()
_TEX_MAX = 600


def get_texture(net, url, dest, cb):
    """cb(texture_or_None) on the main thread."""
    dest = Path(dest)
    tex = _textures.get(dest)
    if tex is not None:
        _textures.move_to_end(dest)
        cb(tex)
        return

    def done(path, err):
        if err:
            cb(None)
            return
        try:
            tex = Gdk.Texture.new_from_filename(str(path))
        except GLib.Error:
            cb(None)
            return
        _textures[dest] = tex
        while len(_textures) > _TEX_MAX:
            _textures.popitem(last=False)
        cb(tex)

    net.download(url, dest, done)


_scaled = OrderedDict()
_SCALED_MAX = 300


def get_scaled_texture(net, url, dest, size, cb):
    """Download the full image, decode it downscaled to fit size x size in a
    worker thread, and call cb(texture_or_None) on the main thread."""
    dest = Path(dest)
    key = (dest, size)
    tex = _scaled.get(key)
    if tex is not None:
        _scaled.move_to_end(key)
        cb(tex)
        return

    def decode(path):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
        except GLib.Error:
            pb = None
        GLib.idle_add(finish, pb)

    def finish(pb):
        tex = Gdk.Texture.new_for_pixbuf(pb) if pb else None
        if tex:
            _scaled[key] = tex
            while len(_scaled) > _SCALED_MAX:
                _scaled.popitem(last=False)
        _call(cb, tex)
        return False

    def downloaded(path, err):
        if err:
            _call(cb, None)
        else:
            net.pool.submit(decode, path)

    net.download(url, dest, downloaded)


def prune_cache(max_age_days=7):
    """Delete cached media older than max_age_days (runs in a worker thread)."""
    cutoff = time.time() - max_age_days * 86400
    for sub in ("media", "thumbs"):
        for p in (CACHE / sub).rglob("*"):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
