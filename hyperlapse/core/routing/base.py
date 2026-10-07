"""Router interface: route(waypoints) -> (LineString lon/lat, distance_m)."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pyproj import Geod
from shapely.geometry import LineString

from ..cache import Cache
from ..http import CachedHttp

GEOD = Geod(ellps="WGS84")


class RoutingError(Exception):
    pass


@dataclass
class Route:
    route_id: str
    line: LineString  # lon/lat
    distance_m: float
    source: str
    waypoints: list[tuple[float, float]] | None = None  # (lat, lon)

    def to_json(self) -> dict:
        return {
            "route_id": self.route_id,
            "coordinates": [list(c) for c in self.line.coords],
            "distance_m": self.distance_m,
            "source": self.source,
            "waypoints": [list(w) for w in self.waypoints] if self.waypoints else None,
        }

    @classmethod
    def from_json(cls, d: dict) -> Route:
        return cls(
            route_id=d["route_id"],
            line=LineString(d["coordinates"]),
            distance_m=d["distance_m"],
            source=d["source"],
            waypoints=[tuple(w) for w in d["waypoints"]] if d.get("waypoints") else None,
        )

    def geojson(self) -> dict:
        return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(c) for c in self.line.coords]}, "properties": {}}


def geodesic_length(line: LineString) -> float:
    return float(GEOD.line_length(*line.xy))


def route_id_for(router: str, payload: object) -> str:
    return hashlib.sha1(f"{router}|{json.dumps(payload, sort_keys=True)}".encode()).hexdigest()[:16]


def clean_line(coords: list[list[float]] | list[tuple[float, float]]) -> LineString:
    out: list[tuple[float, float]] = []
    for c in coords:
        p = (float(c[0]), float(c[1]))
        if not out or p != out[-1]:
            out.append(p)
    if len(out) < 2:
        raise RoutingError("route has fewer than two distinct points")
    return LineString(out)


class Router(ABC):
    name: str

    def __init__(self, http: CachedHttp, cache: Cache):
        self.http = http
        self.cache = cache

    @abstractmethod
    async def _route(self, waypoints: list[tuple[float, float]]) -> tuple[LineString, float]:
        """waypoints are (lat, lon). Returns (lon/lat LineString, distance in metres)."""

    async def route(self, waypoints: list[tuple[float, float]]) -> Route:
        if len(waypoints) < 2:
            raise RoutingError("need at least a start and an end")
        rid = route_id_for(self.name, [[round(a, 6), round(b, 6)] for a, b in waypoints])
        path = self.cache.dir("routes") / f"{rid}.json"
        if path.exists():
            return Route.from_json(json.loads(path.read_text()))
        line, dist = await self._route(waypoints)
        r = Route(rid, line, dist, self.name, list(waypoints))
        path.write_text(json.dumps(r.to_json()))
        return r
