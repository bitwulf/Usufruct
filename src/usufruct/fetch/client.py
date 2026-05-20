"""Polite cached HTTP client.

* Rate-limits per host (default 1 req/s) to avoid hammering legis.la.gov.
* Retries on 5xx and network errors with exponential backoff + jitter.
* Caches every successful response body keyed by SHA256, stored under
  ``data/raw/`` with a sidecar ``index.json`` mapping URL -> hash.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

DEFAULT_USER_AGENT = (
    "Usufruct/0.1 (Louisiana Civil Code archival scraper; "
    "contact: logicalappeals@gmail.com)"
)


@dataclass
class FetchResult:
    url: str
    text: str
    sha256: str
    from_cache: bool


class CachedClient:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str = DEFAULT_USER_AGENT,
        rate_limit_per_host: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_dir / "index.json"
        self.user_agent = user_agent
        self.rate_limit_per_host = rate_limit_per_host
        self.max_retries = max_retries
        self.timeout = timeout

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._last_request_at: Dict[str, float] = {}
        self._index: Dict[str, str] = self._load_index()

    def _load_index(self) -> Dict[str, str]:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_index(self) -> None:
        self.index_path.write_text(json.dumps(self._index, indent=2, sort_keys=True))

    def _wait_for_host(self, host: str) -> None:
        gap = 1.0 / self.rate_limit_per_host if self.rate_limit_per_host > 0 else 0
        if gap == 0:
            return
        last = self._last_request_at.get(host)
        if last is None:
            return
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)

    def _cache_path(self, sha: str) -> Path:
        # 2-level fanout to avoid 10k+ files in one directory
        return self.cache_dir / sha[:2] / f"{sha}.html"

    def get(self, url: str, force_refetch: bool = False) -> FetchResult:
        cached_sha = self._index.get(url)
        if cached_sha and not force_refetch:
            path = self._cache_path(cached_sha)
            if path.exists():
                return FetchResult(url=url, text=path.read_text(), sha256=cached_sha, from_cache=True)

        host = urlparse(url).netloc
        self._wait_for_host(host)

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout)
                self._last_request_at[host] = time.monotonic()
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"{resp.status_code} for {url}")
                resp.raise_for_status()
                text = resp.text
                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                path = self._cache_path(sha)
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text(text)
                self._index[url] = sha
                self._save_index()
                return FetchResult(url=url, text=text, sha256=sha, from_cache=False)
            except (requests.RequestException, requests.HTTPError) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    break
                # exponential backoff with jitter: 1, 2, 4, 8 seconds + 0-1 jitter
                backoff = (2 ** attempt) + random.random()
                time.sleep(backoff)
                self._wait_for_host(host)
        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries + 1} attempts: {last_exc}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
