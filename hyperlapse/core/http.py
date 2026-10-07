"""The one cached HTTP client with retry. Core stages never touch the network directly."""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .cache import fresh, write_bytes_atomic, write_json_atomic


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class RateLimited(Exception):
    """HTTP 429 or Mapillary Graph API error code 4 (retried with backoff)."""


class _Transient(Exception):
    pass


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RateLimited, _Transient, httpx.TransportError))


class CachedHttp:
    def __init__(
        self,
        *,
        mapillary_token: str | None = None,
        concurrency: int = 16,
        user_agent: str = "route-hyperlapse/0.1",
        max_attempts: int = 6,
        backoff_initial: float = 1.0,
    ):
        self.mapillary_token = mapillary_token
        self.user_agent = user_agent
        self.requests: Counter[str] = Counter()
        self._concurrency = concurrency
        self._sem: asyncio.Semaphore | None = None
        self._client: httpx.AsyncClient | None = None
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._max_attempts = max_attempts
        self._backoff = backoff_initial
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            # Each event loop gets its own client (render jobs and tests may run in other loops).
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True, headers={"User-Agent": self.user_agent})
            self._loop = loop
            self._sem = asyncio.Semaphore(self._concurrency)
            self._host_locks = {}
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except RuntimeError:
                pass
            self._client = None

    def _auth(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        params = dict(params or {})
        host = urlparse(url).hostname or ""
        if host.endswith("mapillary.com") and self.mapillary_token and "access_token" not in params:
            params["access_token"] = self.mapillary_token
        return params

    async def _throttle(self, url: str, min_interval: float | None) -> None:
        if not min_interval:
            return
        host = urlparse(url).hostname or ""
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0) + min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def request(
        self,
        method: str,
        url: str,
        *,
        category: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        min_interval: float | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        params = self._auth(url, params)
        client = self.client
        sem = self._sem
        assert sem is not None

        async def once() -> httpx.Response:
            async with sem:
                await self._throttle(url, min_interval)
                self.requests[category] += 1
                # params=None leaves the URL untouched; even {} re-encodes the query and breaks signed CDN URLs.
                r = await client.request(method, url, params=params or None, headers=headers, json=json_body)
            if r.status_code == 429:
                raise RateLimited(url)
            if r.status_code >= 500:
                raise _Transient(f"{r.status_code} {url}")
            if "json" in r.headers.get("content-type", "") and r.status_code in (200, 400, 403):
                try:
                    body = r.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("code") == 4:
                    raise RateLimited(url)
            if r.status_code not in ok_statuses:
                raise HttpError(r.status_code, r.text[:300])
            return r

        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_retryable),
            wait=wait_exponential_jitter(initial=self._backoff, max=60),
            stop=stop_after_attempt(self._max_attempts),
            reraise=True,
        ):
            with attempt:
                return await once()
        raise AssertionError("unreachable")

    async def get_json(
        self,
        url: str,
        *,
        category: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cache_path: Path | None = None,
        ttl_s: float | None = None,
        min_interval: float | None = None,
    ) -> Any:
        if cache_path is not None and fresh(cache_path, ttl_s):
            return json.loads(cache_path.read_text())
        r = await self.request("GET", url, category=category, params=params, headers=headers, min_interval=min_interval)
        data = r.json()
        if cache_path is not None:
            write_json_atomic(cache_path, data)
        return data

    async def post_json(
        self,
        url: str,
        body: Any,
        *,
        category: str,
        headers: dict[str, str] | None = None,
        cache_path: Path | None = None,
    ) -> Any:
        if cache_path is not None and cache_path.exists():
            return json.loads(cache_path.read_text())
        r = await self.request("POST", url, category=category, json_body=body, headers=headers)
        data = r.json()
        if cache_path is not None:
            write_json_atomic(cache_path, data)
        return data

    async def get_bytes(
        self,
        url: str,
        *,
        category: str,
        params: dict[str, Any] | None = None,
        cache_path: Path | None = None,
        ttl_s: float | None = None,
        missing_ok: bool = False,
    ) -> bytes | None:
        """Fetch bytes. With missing_ok, 204/404 return b"" (and are cached as empty)."""
        if cache_path is not None and fresh(cache_path, ttl_s):
            return cache_path.read_bytes()
        ok = (200, 204, 404) if missing_ok else (200,)
        r = await self.request("GET", url, category=category, params=params, ok_statuses=ok)
        data = r.content if r.status_code == 200 else b""
        if cache_path is not None:
            write_bytes_atomic(cache_path, data)
        return data
