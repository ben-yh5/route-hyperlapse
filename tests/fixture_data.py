"""Deterministic recorded-style HTTP fixtures for one short route, served through respx.

The route is ~3.3 km: 2 km east then ~1.3 km north. Mapillary coverage is a forward-facing
sequence along the travel side, a wrong-carriageway sequence facing backwards, a few panos,
some images that the Graph API marks as rejectable (on foot / portrait), and a 300 m gap (tunnel).
"""

from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass, field
from functools import cache
from urllib.parse import parse_qs, urlparse

import httpx
import mapbox_vector_tile
import numpy as np
from PIL import Image, ImageDraw
from pyproj import Geod

GEOD = Geod(ellps="WGS84")
START = (47.6000, -122.3500)  # lat, lon
CORNER_EAST_M = 2000.0
NORTH_M = 1300.0
GAP = (1000.0, 1300.0)  # distance range along the route without coverage
STEP_M = 6.0
OSRM_URL = "https://router.project-osrm.org"
IMAGE_HOST = "https://images.fixture.test"
CAPTURED = 1_690_000_000_000  # 2023-07-22


def _fwd(lat: float, lon: float, az: float, d: float) -> tuple[float, float]:
    lo, la, _ = GEOD.fwd(lon, lat, az, d)
    return la, lo


@dataclass
class FixtureData:
    route_lonlat: list[tuple[float, float]] = field(default_factory=list)
    images: dict[str, dict] = field(default_factory=dict)  # id -> tile props + lon/lat + entity extras

    @property
    def waypoints(self) -> list[dict]:
        (lo0, la0), (lo1, la1) = self.route_lonlat[0], self.route_lonlat[-1]
        return [{"lat": la0, "lon": lo0}, {"lat": la1, "lon": lo1}]


def _route() -> list[tuple[float, float]]:
    lat, lon = START
    pts = [(lon, lat)]
    for d in np.arange(100, CORNER_EAST_M + 1, 100):
        la, lo = _fwd(lat, lon, 90, d)
        pts.append((lo, la))
    clat, clon = pts[-1][1], pts[-1][0]
    for d in np.arange(100, NORTH_M + 1, 100):
        la, lo = _fwd(clat, clon, 0, d)
        pts.append((lo, la))
    return pts


@cache
def build() -> FixtureData:
    fx = FixtureData(route_lonlat=_route())
    lat, lon = START
    clat, clon = fx.route_lonlat[int(CORNER_EAST_M / 100)][1], fx.route_lonlat[int(CORNER_EAST_M / 100)][0]
    n = 0

    def pos(d: float, lateral: float) -> tuple[float, float, float]:
        """lat, lon, bearing at distance d along the route with a lateral offset (right positive)."""
        if d <= CORNER_EAST_M:
            la, lo = _fwd(lat, lon, 90, d)
            brg = 90.0
        else:
            la, lo = _fwd(clat, clon, 0, d - CORNER_EAST_M)
            brg = 0.0
        if lateral:
            la, lo = _fwd(la, lo, (brg + 90) % 360, lateral)
        return la, lo, brg

    total = CORNER_EAST_M + NORTH_M
    for k, d in enumerate(np.arange(2.0, total, STEP_M)):
        if GAP[0] <= d < GAP[1]:
            continue
        seq = "seq-fwd-a" if d < GAP[0] else "seq-fwd-b"
        la, lo, brg = pos(d, 2.0)
        n += 1
        iid = str(1_000_000 + k)
        pano = k % 25 == 0
        fx.images[iid] = {
            "id": iid, "sequence_id": seq, "captured_at": CAPTURED + int(d * 1000), "compass_angle": (brg + 3) % 360,
            "is_pano": pano, "lon": lo, "lat": la,
            "camera_type": "equirectangular" if pano else "perspective", "width": 640 if pano else 400, "height": 320 if pano else 300,
            "on_foot": k % 40 == 7, "creator": "alice" if d < GAP[0] else "bob",
        }
        # Opposite carriageway, travelling the other way.
        la, lo, brg = pos(d + 3, -14.0)
        rid = str(2_000_000 + k)
        fx.images[rid] = {
            "id": rid, "sequence_id": "seq-rev", "captured_at": CAPTURED + 10_000_000 - int(d * 1000), "compass_angle": (brg + 180) % 360,
            "is_pano": False, "lon": lo, "lat": la, "camera_type": "perspective", "width": 400, "height": 300,
            "on_foot": False, "creator": "carol",
        }
    # A portrait image placed right on the line that would otherwise win.
    la, lo, brg = pos(500.0, 0.0)
    fx.images["3000001"] = {
        "id": "3000001", "sequence_id": "seq-portrait", "captured_at": CAPTURED, "compass_angle": brg, "is_pano": False,
        "lon": lo, "lat": la, "camera_type": "perspective", "width": 300, "height": 400, "on_foot": False, "creator": "dave",
    }
    return fx


def _tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180) / 360 * n
    lr = math.radians(lat)
    y = (1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n
    return x, y


@cache
def tile_bytes(z: int, x: int, y: int) -> bytes:
    fx = build()
    feats = []
    for im in fx.images.values():
        tx, ty = _tile_xy(im["lon"], im["lat"], z)
        if int(tx) == x and int(ty) == y:
            px, py = (tx - x) * 4096, (ty - y) * 4096
            props = {k: im[k] for k in ("id", "sequence_id", "captured_at", "compass_angle", "is_pano")}
            props["id"] = int(props["id"])
            feats.append({"geometry": f"POINT({px:.3f} {py:.3f})", "properties": props})
    if not feats:
        return b""
    layers = [{"name": "image", "features": feats}]
    if z <= 14:
        layers.append({"name": "sequence", "features": []})
    return mapbox_vector_tile.encode(layers, default_options={"y_coord_down": True, "extents": 4096, "quantize_bounds": None})


@cache
def jpeg(image_id: str, size: int) -> bytes:
    im = build().images[image_id]
    w, h = (im["width"], im["height"])
    scale = size / max(w, h) if size < max(w, h) else 1
    w, h = max(2, int(w * scale)), max(2, int(h * scale))
    k = int(image_id) % 997
    img = Image.new("RGB", (w, h), (40 + k % 160, 80 + (k * 7) % 120, 120 + (k * 13) % 100))
    d = ImageDraw.Draw(img)
    d.rectangle((0, h // 2, w, h), fill=(70, 70, 75))  # road
    d.line((w // 2, h // 2, w // 2, h), fill=(240, 240, 240), width=max(1, w // 80))
    d.text((5, 5), image_id, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def osrm_response() -> dict:
    fx = build()
    line = fx.route_lonlat
    dist = sum(GEOD.inv(a[0], a[1], b[0], b[1])[2] for a, b in zip(line, line[1:]))
    return {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": [list(p) for p in line]}, "distance": dist, "duration": dist / 15}], "waypoints": []}


def _graph(request: httpx.Request) -> httpx.Response:
    q = parse_qs(urlparse(str(request.url)).query)
    ids = q.get("ids", [""])[0].split(",")
    fields = q.get("fields", [""])[0].split(",")
    fx = build()
    out = {}
    for i in ids:
        im = fx.images.get(i)
        if not im:
            continue
        e = {"id": i}
        for f in fields:
            if f == "creator":
                e["creator"] = {"username": im["creator"], "id": "1"}
            elif f in ("camera_type", "width", "height", "on_foot"):
                e[f] = im[f]
            elif f in ("computed_compass_angle", "compass_angle"):
                e[f] = im["compass_angle"]
            elif m := re.match(r"thumb_(\d+)_url", f):
                e[f] = f"{IMAGE_HOST}/{i}_{m.group(1)}.jpg?sig=expiring"
        out[i] = e
    return httpx.Response(200, json=out)


def _tile(request: httpx.Request) -> httpx.Response:
    z, x, y = (int(v) for v in request.url.path.rstrip("/").split("/")[-3:])
    data = tile_bytes(z, x, y)
    return httpx.Response(200, content=data, headers={"content-type": "application/x-protobuf"}) if data else httpx.Response(204)


def _image(request: httpx.Request) -> httpx.Response:
    name = request.url.path.rsplit("/", 1)[-1]
    iid, size = name.removesuffix(".jpg").split("_")
    if iid not in build().images:
        return httpx.Response(404)
    return httpx.Response(200, content=jpeg(iid, int(size)), headers={"content-type": "image/jpeg"})


def _nominatim(request: httpx.Request) -> httpx.Response:
    lo, la = build().route_lonlat[-1]
    return httpx.Response(200, json=[{"display_name": "Fixture End, Testville", "lat": str(la), "lon": str(lo)}])


def install(router) -> None:
    """Register fixture routes on a respx router (respx.mock or respx.MockRouter)."""
    router.get(url__regex=r"https://router\.project-osrm\.org/route/v1/driving/.*").mock(return_value=httpx.Response(200, json=osrm_response()))
    router.get(url__regex=r"https://tiles\.mapillary\.com/maps/vtp/.*").mock(side_effect=_tile)
    router.get(url__regex=r"https://graph\.mapillary\.com/.*").mock(side_effect=_graph)
    router.get(url__regex=rf"{re.escape(IMAGE_HOST)}/.*").mock(side_effect=_image)
    router.get(url__regex=r"https://nominatim\.openstreetmap\.org/.*").mock(side_effect=_nominatim)

