"""
Polite JSON GETs for the cover/album sources (Deezer, iTunes): one shared
User-Agent, a minimum gap between calls per host (each service's rate limit,
with room to spare), and None for "nothing there" rather than an exception.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

UA = "tggr/0.3.0 (https://github.com/beartums/tggr)"


class Throttle:
    """At most one call per `gap` seconds, across threads."""

    def __init__(self, gap: float):
        self.gap, self._next, self._lock = gap, 0.0, threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.gap
        if delay > 0:
            time.sleep(delay)


class SourceError(Exception):
    """The service couldn't be reached or answered with an error (not "no data")."""


def get_json(url: str, throttle: Throttle, timeout: float = 15) -> dict | list | None:
    throttle.wait()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise SourceError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(str(getattr(e, "reason", e))) from e
    except ValueError as e:
        raise SourceError("not JSON") from e
