"""Plan + override persistence (JSON in the cache dir) and route lookup."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..core.cache import Cache, read_json, write_json_atomic
from ..core.routing import Route


class NotFound(Exception):
    pass


class Store:
    def __init__(self, cache: Cache):
        self.cache = cache

    def _plan_path(self, plan_id: str) -> Path:
        if not plan_id.replace("-", "").isalnum():
            raise NotFound(plan_id)
        return self.cache.dir("plans") / f"{plan_id}.json"

    @staticmethod
    def new_plan_id() -> str:
        return uuid.uuid4().hex[:12]

    def save_plan(self, plan: dict) -> None:
        write_json_atomic(self._plan_path(plan["plan_id"]), plan)

    def load_plan(self, plan_id: str) -> dict:
        p = self._plan_path(plan_id)
        if not p.exists():
            raise NotFound(plan_id)
        return read_json(p)

    def save_route(self, route: Route) -> None:
        write_json_atomic(self.cache.dir("routes") / f"{route.route_id}.json", route.to_json())
        # Plans keep a copy too, so clearing the cache never orphans the current plan.
        write_json_atomic(self.cache.dir("plans") / "routes" / f"{route.route_id}.json", route.to_json())

    def load_route(self, route_id: str) -> Route:
        if not route_id.replace("-", "").isalnum():
            raise NotFound(route_id)
        for p in (self.cache.dir("routes") / f"{route_id}.json", self.cache.dir("plans") / "routes" / f"{route_id}.json"):
            if p.exists():
                return Route.from_json(json.loads(p.read_text()))
        raise NotFound(route_id)
