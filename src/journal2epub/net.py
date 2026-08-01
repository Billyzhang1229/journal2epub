"""Polite, cached HTTP.

Every response is written to a durable on-disk cache keyed by URL, so a rebuild
touches the network zero times. Each host gets its own rate limit and the client
identifies itself on every request, as the providers' terms ask.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

DEFAULT_CONTACT = os.environ.get("JOURNAL2EPUB_CONTACT", "")
USER_AGENT_TEMPLATE = "journal2epub/{version} (+https://github.com/journal2epub; mailto:{contact})"

# Documented limits. NCBI asks for <=3 requests/second without an API key
# (10/s with one); Crossref's public pool is unmetered but asks for a contact
# address in the User-Agent and rewards it with the "polite" pool.
HOST_RATE_LIMITS: dict[str, float] = {
    "eutils.ncbi.nlm.nih.gov": 3.0,
    "pmc.ncbi.nlm.nih.gov": 3.0,
    "www.ncbi.nlm.nih.gov": 3.0,
    "ftp.ncbi.nlm.nih.gov": 3.0,
    "api.crossref.org": 20.0,
    "pmc-oa-opendata.s3.amazonaws.com": 20.0,
}
DEFAULT_RATE = 5.0


class OfflineError(RuntimeError):
    """Raised when a resource is needed but absent from the cache in offline mode."""


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str = ""):
        self.url, self.status, self.detail = url, status, detail
        super().__init__(f"{status or 'ERR'} fetching {url}{': ' + detail if detail else ''}")


class _RateLimiter:
    """Simple per-host minimum-interval limiter, safe across threads."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def wait(self, host: str) -> None:
        rate = HOST_RATE_LIMITS.get(host, DEFAULT_RATE)
        interval = 1.0 / rate
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            now = time.monotonic()
            prev = self._last.get(host, 0.0)
            delay = interval - (now - prev)
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


@dataclass(slots=True)
class CacheEntry:
    body: bytes
    status: int
    url: str
    fetched: str
    from_cache: bool
    content_type: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


class Fetcher:
    """Cached HTTP client.

    `offline=True` serves only from cache and raises OfflineError otherwise,
    which is how reproducible rebuilds are proved.
    """

    def __init__(
        self,
        cache_dir: Path,
        contact: str = "",
        version: str = "0.1.0",
        offline: bool = False,
        timeout: float = 60.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.contact = contact or DEFAULT_CONTACT
        self.user_agent = USER_AGENT_TEMPLATE.format(version=version, contact=self.contact or "unset")
        self._limiter = _RateLimiter()
        self._client: httpx.Client | None = None
        self._timeout = timeout
        self.stats = {"hits": 0, "misses": 0, "bytes": 0}

    # -- cache layout ----------------------------------------------------
    def _paths(self, url: str) -> tuple[Path, Path]:
        h = hashlib.sha256(url.encode()).hexdigest()
        host = httpx.URL(url).host or "unknown"
        d = self.cache_dir / host / h[:2]
        return d / f"{h}.body", d / f"{h}.json"

    def cached(self, url: str) -> bool:
        body, meta = self._paths(url)
        return body.exists() and meta.exists()

    # -- fetching --------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    def get(self, url: str, *, accept: str | None = None, retries: int = 4,
            cache_key: str | None = None) -> CacheEntry:
        """Fetch `url`, serving from and writing to the durable cache.

        `cache_key` distinguishes requests that share a URL but are not the same
        request. Cursor-paged APIs need it: Crossref's deep paging re-sends one
        stateful scroll token and advances server-side state, so every page
        after the first has an identical URL. Keyed by URL alone, the cache
        would replay one page forever and paging would silently stall.
        """
        body_p, meta_p = self._paths(cache_key or url)
        if body_p.exists() and meta_p.exists():
            meta = json.loads(meta_p.read_text())
            self.stats["hits"] += 1
            return CacheEntry(
                body=body_p.read_bytes(), status=meta.get("status", 200), url=url,
                fetched=meta.get("fetched", ""), from_cache=True,
                content_type=meta.get("content_type", ""),
            )

        if self.offline:
            raise OfflineError(f"not in cache and running offline: {url}")

        host = httpx.URL(url).host or "unknown"
        headers = {"Accept": accept} if accept else {}
        last: Exception | None = None
        for attempt in range(retries):
            self._limiter.wait(host)
            try:
                r = self.client.get(url, headers=headers)
            except httpx.HTTPError as e:
                last = e
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 30))
                log.warning("%s on %s, retrying in %.0fs", r.status_code, url, wait)
                last = FetchError(url, r.status_code)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise FetchError(url, r.status_code, r.text[:200])

            body_p.parent.mkdir(parents=True, exist_ok=True)
            tmp = body_p.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            os.replace(tmp, body_p)
            fetched = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            meta_p.write_text(json.dumps({
                "url": url, "status": r.status_code, "fetched": fetched,
                "content_type": r.headers.get("content-type", ""),
                "sha256": hashlib.sha256(r.content).hexdigest(),
                "bytes": len(r.content),
            }, indent=1))
            self.stats["misses"] += 1
            self.stats["bytes"] += len(r.content)
            return CacheEntry(r.content, r.status_code, url, fetched, False,
                              r.headers.get("content-type", ""))

        raise FetchError(url, None, f"exhausted retries ({last})")

    def get_json(self, url: str, *, cache_key: str | None = None):
        return self.get(url, accept="application/json", cache_key=cache_key).json()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
