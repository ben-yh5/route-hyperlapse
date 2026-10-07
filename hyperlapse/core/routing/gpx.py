from __future__ import annotations

import hashlib
import json

import gpxpy

from .base import Route, RoutingError, clean_line, geodesic_length


def parse_gpx(content: bytes) -> list[tuple[float, float]]:
    """Concatenate all track segments (falling back to routes) as lon/lat."""
    try:
        g = gpxpy.parse(content.decode("utf-8", errors="replace"))
    except Exception as e:  # gpxpy raises several exception types
        raise RoutingError(f"invalid GPX: {e}") from e
    pts = [(p.longitude, p.latitude) for t in g.tracks for s in t.segments for p in s.points]
    if not pts:
        pts = [(p.longitude, p.latitude) for r in g.routes for p in r.points]
    if len(pts) < 2:
        raise RoutingError("GPX contains no track")
    return pts


class GpxRouter:
    name = "gpx"

    def __init__(self, cache):
        self.cache = cache

    def route_from(self, content: bytes) -> Route:
        rid = "gpx-" + hashlib.sha1(content).hexdigest()[:12]
        line = clean_line(parse_gpx(content))
        r = Route(rid, line, geodesic_length(line), "gpx", None)
        (self.cache.dir("routes") / f"{rid}.json").write_text(json.dumps(r.to_json()))
        return r
