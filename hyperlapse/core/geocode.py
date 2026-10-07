"""Search box backend: lat,lon literals or Nominatim."""

from __future__ import annotations

import hashlib
import re

from .cache import Cache
from .http import CachedHttp

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_latlon(query: str) -> tuple[float, float] | None:
    m = _LATLON.match(query)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


async def geocode(
    query: str,
    *,
    http: CachedHttp,
    cache: Cache,
    limit: int = 6,
) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    ll = parse_latlon(query)
    if ll:
        return [{"name": f"{ll[0]:.6f}, {ll[1]:.6f}", "lat": ll[0], "lon": ll[1]}]
    key = hashlib.sha1(f"nominatim|{query.lower()}".encode()).hexdigest()
    data = await http.get_json(
        NOMINATIM_URL,
        category="geocode",
        params={"q": query, "format": "jsonv2", "limit": limit},
        cache_path=cache.dir("geocode") / f"{key}.json",
        min_interval=1.0,
    )
    return [{"name": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"])} for r in data]
