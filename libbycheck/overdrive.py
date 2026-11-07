"""Client for OverDrive's Thunder API (the unauthenticated catalog API that library
websites and Libby use).

This API is unofficial and undocumented; OverDrive can change it without notice. The
endpoints and fields used here were checked against live responses on 2026-11-07:

  GET /v2/libraries/{key}                      -> library info (404 for an unknown key)
  GET /v2/libraries?perPage=100&page=N         -> library directory (perPage max is 100;
                                                  name-search parameters are ignored)
  GET /v2/libraries/{key}/media?query=...      -> catalog search with per-title availability
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .models import Listing

BASE_URL = "https://thunder.api.overdrive.com/v2"
USER_AGENT = "libbycheck/0.1 (personal library availability checker)"
MAX_PER_PAGE = 100
CACHE_VERSION = 2  # bump when the cached record shape changes so old entries are ignored
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(Exception):
    pass


class LibraryNotFound(ApiError):
    pass


class Client:
    def __init__(
        self,
        cache_dir: str | Path | None = None,
        availability_ttl: float = 30 * 60,
        directory_ttl: float = 30 * 24 * 3600,
        min_interval: float = 0.15,
        timeout: float = 30,
        retries: int = 4,
        refresh: bool = False,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.availability_ttl = availability_ttl
        self.directory_ttl = directory_ttl
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.refresh = refresh
        self._opener = opener
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    # -- HTTP ---------------------------------------------------------------

    def _throttle(self) -> None:
        """Space requests at least ``min_interval`` apart across all worker threads."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            self._sleep(wait)

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last_error = ""
        for attempt in range(self.retries + 1):
            self._throttle()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            delay = 2.0 * (2**attempt)
            try:
                with self._opener(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise LibraryNotFound(f"404 Not Found: {path}") from e
                last_error = f"HTTP {e.code} for {path}"
                if e.code not in _RETRY_STATUSES:
                    raise ApiError(last_error) from e
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            except (OSError, json.JSONDecodeError) as e:  # URLError, timeouts, resets, bad body
                last_error = f"{type(e).__name__} for {path}: {e}"
            if attempt < self.retries:
                self._sleep(delay)
        raise ApiError(f"{last_error} (gave up after {self.retries + 1} attempts)")

    # -- cache --------------------------------------------------------------

    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(f"v{CACHE_VERSION}|{key}".encode()).hexdigest()[:32] + ".json")

    def _cache_get(self, key: str, ttl: float):
        path = self._cache_path(key)
        if self.refresh or not path or not path.exists():
            return None
        try:
            blob = json.loads(path.read_text())
            if time.time() - blob["fetched_at"] <= ttl:
                return blob["data"]
        except (OSError, ValueError, KeyError):
            pass  # unreadable or stale cache is just a miss
        return None

    def _cache_put(self, key: str, data) -> None:
        path = self._cache_path(key)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"fetched_at": time.time(), "key": key, "data": data}, f)
            os.replace(tmp, path)  # atomic: safe with concurrent workers
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # -- API ----------------------------------------------------------------

    def library_info(self, key: str) -> dict:
        """Raises LibraryNotFound for an unknown key."""
        cache_key = f"library:{key}"
        hit = self._cache_get(cache_key, self.directory_ttl)
        if hit is not None:
            return hit
        raw = self._get(f"/libraries/{urllib.parse.quote(key, safe='')}")
        info = {
            "key": raw.get("preferredKey") or raw.get("id") or key,
            "name": raw.get("name", ""),
            "status": raw.get("status", ""),
            "isConsortium": bool(raw.get("isConsortium")),
        }
        self._cache_put(cache_key, info)
        return info

    def search(self, library: str, query: str, per_page: int = 24) -> list[Listing]:
        """Catalog search at one library. Results are cached for ``availability_ttl``."""
        cache_key = f"search:{library}:{per_page}:{query}"
        hit = self._cache_get(cache_key, self.availability_ttl)
        if hit is not None:
            return [Listing.from_dict(d) for d in hit]
        raw = self._get(
            f"/libraries/{urllib.parse.quote(library, safe='')}/media",
            {"query": query, "perPage": min(per_page, MAX_PER_PAGE), "page": 1},
        )
        listings = [Listing.from_api(item, library) for item in raw.get("items") or []]
        self._cache_put(cache_key, [l.to_dict() for l in listings])
        return listings

    def directory(self, progress: Callable[[int, int], None] | None = None) -> list[dict]:
        """Every library OverDrive knows about, as {key, name, status, isConsortium}.

        There is no server-side name search, so this pages through the whole directory
        (~130 requests) once and caches it for ``directory_ttl``.
        """
        cache_key = "directory"
        hit = self._cache_get(cache_key, self.directory_ttl)
        if hit is not None:
            return hit
        out: list[dict] = []
        page, total = 1, None
        while total is None or len(out) < total:
            raw = self._get("/libraries", {"perPage": MAX_PER_PAGE, "page": page})
            items = raw.get("items") or []
            total = raw.get("totalItems", 0)
            if not items:
                break
            out.extend(
                {
                    "key": i.get("preferredKey") or i.get("id"),
                    "name": i.get("name", ""),
                    "status": i.get("status", ""),
                    "isConsortium": bool(i.get("isConsortium")),
                }
                for i in items
            )
            if progress:
                progress(len(out), total)
            page += 1
        self._cache_put(cache_key, out)
        return out
