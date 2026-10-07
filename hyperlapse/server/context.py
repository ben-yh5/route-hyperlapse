"""Shared application state."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.cache import Cache, close_session, open_session
from ..core.http import CachedHttp
from ..core.pipeline import Pipeline
from .jobs import JobManager
from .store import Store


@dataclass
class AppContext:
    cfg: Config
    cache_base: Path
    cache: Cache
    http: CachedHttp
    pipeline: Pipeline
    store: Store
    jobs: JobManager
    clear_on_quit: bool
    server: Any = None  # uvicorn.Server when launched via __main__
    closed: bool = False
    plan_locks: defaultdict[str, asyncio.Lock] = field(default_factory=lambda: defaultdict(asyncio.Lock))

    @classmethod
    def create(cls, cfg: Config, http: CachedHttp | None = None) -> AppContext:
        root = open_session(cfg.cache_dir, cfg.clear_cache_on_quit)
        cache = Cache(root)
        http = http or CachedHttp(mapillary_token=cfg.mapillary_token, concurrency=cfg.concurrency, user_agent=cfg.nominatim_user_agent)
        pipeline = Pipeline(cfg, cache, http)
        return cls(cfg, cfg.cache_dir, cache, http, pipeline, Store(cache), JobManager(pipeline), cfg.clear_cache_on_quit)

    def request_shutdown(self) -> None:
        if self.server is not None:
            self.server.should_exit = True

    async def close(self) -> None:
        """Quit cleanup: stop renders, close the client, delete (or keep) the session cache."""
        if self.closed:
            return
        self.closed = True
        await self.jobs.cancel_all()
        await self.http.aclose()
        close_session(self.cache.root, self.cache_base, self.clear_on_quit)
