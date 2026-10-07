from __future__ import annotations

from shapely.geometry import LineString

from ..http import HttpError
from .base import Router, RoutingError, clean_line


class OSRMRouter(Router):
    name = "osrm"

    def __init__(self, http, cache, base_url: str):
        super().__init__(http, cache)
        self.base_url = base_url.rstrip("/")

    async def _route(self, waypoints: list[tuple[float, float]]) -> tuple[LineString, float]:
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
        url = f"{self.base_url}/route/v1/driving/{coords}"
        params = {"overview": "full", "geometries": "geojson", "exclude": "ferry"}
        try:
            data = await self.http.get_json(url, category="route", params=params)
        except HttpError as e:
            if e.status != 400:
                raise
            # Servers whose profile lacks the ferry exclude class reject the parameter.
            params.pop("exclude")
            data = await self.http.get_json(url, category="route", params=params)
        if data.get("code") != "Ok" or not data.get("routes"):
            raise RoutingError(f"OSRM: {data.get('message') or data.get('code')}")
        r = data["routes"][0]
        return clean_line(r["geometry"]["coordinates"]), float(r["distance"])
