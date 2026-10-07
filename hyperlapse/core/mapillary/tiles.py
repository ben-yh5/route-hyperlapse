"""Mapillary vector-tile coverage: tile cover, download, decode, metric KD-tree."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass

import mapbox_vector_tile
import numpy as np
from scipy.spatial import cKDTree

from ..cache import Cache
from ..http import CachedHttp

COMPUTED_TILES = "https://tiles.mapillary.com/maps/vtp/mly1_computed_public/2/{z}/{x}/{y}"
PUBLIC_TILES = "https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}"
EARTH_R = 6_371_008.8
Z = 14
BUFFER_M = 50.0
DEFAULT_QUALITY = 0.5


def to_ecef(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Spherical earth-centred coordinates in metres; chord distance ~ ground distance at these scales."""
    lo, la = np.radians(lon), np.radians(lat)
    c = np.cos(la)
    return np.column_stack([EARTH_R * c * np.cos(lo), EARTH_R * c * np.sin(lo), EARTH_R * np.sin(la)])


def lonlat_to_tile(lon: np.ndarray, lat: np.ndarray, z: int) -> tuple[np.ndarray, np.ndarray]:
    n = 2**z
    lat = np.clip(lat, -85.05112878, 85.05112878)
    x = np.floor((lon + 180.0) / 360.0 * n).astype(int)
    lr = np.radians(lat)
    y = np.floor((1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / math.pi) / 2.0 * n).astype(int)
    return np.clip(x, 0, n - 1), np.clip(y, 0, n - 1)


def tile_cover(points_lonlat: np.ndarray, buffer_m: float = BUFFER_M, z: int = Z) -> list[tuple[int, int, int]]:
    """z-tiles intersecting a buffer around a densely sampled polyline (points spaced << tile size)."""
    lon, lat = points_lonlat[:, 0], points_lonlat[:, 1]
    dlat = buffer_m / 111_320.0
    dlon = buffer_m / (111_320.0 * np.maximum(np.cos(np.radians(lat)), 1e-6))
    tiles: set[tuple[int, int, int]] = set()
    for sx in (-1, 1):
        for sy in (-1, 1):
            xs, ys = lonlat_to_tile(lon + sx * dlon, lat + sy * dlat, z)
            tiles.update(zip([z] * len(xs), xs.tolist(), ys.tolist()))
    return sorted(tiles)


def tile_px_to_lonlat(z: int, x: int, y: int, px: np.ndarray, py: np.ndarray, extent: int = 4096) -> tuple[np.ndarray, np.ndarray]:
    n = 2**z
    fx = (x + px / extent) / n
    fy = (y + py / extent) / n
    lon = fx * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * fy))))
    return lon, lat


def decode_image_layer(data: bytes, z: int, x: int, y: int) -> list[dict]:
    """Decode the `image` layer of a tile into dicts with lon/lat (tile y grows downward)."""
    if not data:
        return []
    layers = mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    layer = layers.get("image")
    if not layer:
        return []
    extent = layer.get("extent", 4096)
    out = []
    for f in layer["features"]:
        g = f["geometry"]
        pts = [g["coordinates"]] if g["type"] == "Point" else g["coordinates"] if g["type"] == "MultiPoint" else []
        for px, py in pts:
            lon, lat = tile_px_to_lonlat(z, x, y, np.float64(px), np.float64(py), extent)
            p = f["properties"]
            out.append(
                {
                    "id": str(p.get("id", f.get("id"))),
                    "sequence_id": str(p.get("sequence_id", "")),
                    "captured_at": int(p.get("captured_at") or 0),
                    "compass_angle": float(p.get("compass_angle") or 0.0),
                    "is_pano": bool(p.get("is_pano", False)),
                    "quality_score": float(p["quality_score"]) if p.get("quality_score") is not None else DEFAULT_QUALITY,
                    "lon": float(lon),
                    "lat": float(lat),
                }
            )
    return out


@dataclass
class ImageIndex:
    ids: np.ndarray
    sequence_id: np.ndarray
    captured_at: np.ndarray  # ms epoch
    compass_angle: np.ndarray
    is_pano: np.ndarray
    quality_score: np.ndarray
    lon: np.ndarray
    lat: np.ndarray
    tree: cKDTree | None
    seq_motion: np.ndarray  # bearing towards the next image of the same sequence, NaN if unknown

    def __len__(self) -> int:
        return len(self.ids)

    @classmethod
    def build(cls, points: list[dict]) -> ImageIndex:
        uniq = {p["id"]: p for p in points}
        pts = list(uniq.values())
        def arr(k, dt):
            return np.array([p[k] for p in pts], dtype=dt)
        lon, lat = arr("lon", float), arr("lat", float)
        idx = cls(
            ids=np.array([p["id"] for p in pts], dtype=object),
            sequence_id=np.array([p["sequence_id"] for p in pts], dtype=object),
            captured_at=arr("captured_at", np.int64),
            compass_angle=arr("compass_angle", float),
            is_pano=arr("is_pano", bool),
            quality_score=arr("quality_score", float),
            lon=lon,
            lat=lat,
            tree=cKDTree(to_ecef(lon, lat)) if pts else None,
            seq_motion=np.full(len(pts), np.nan),
        )
        idx._compute_sequence_motion()
        return idx

    def _compute_sequence_motion(self) -> None:
        from pyproj import Geod

        geod = Geod(ellps="WGS84")
        order = np.lexsort((self.captured_at, self.sequence_id.astype(str)))
        seq = self.sequence_id[order]
        same_next = np.zeros(len(order), dtype=bool)
        same_next[:-1] = seq[:-1] == seq[1:]
        a = order[:-1][same_next[:-1]]
        b = order[1:][same_next[:-1]]
        if len(a):
            az, _, dist = geod.inv(self.lon[a], self.lat[a], self.lon[b], self.lat[b])
            ok = np.asarray(dist) > 0.5
            self.seq_motion[a[ok]] = np.mod(np.asarray(az)[ok], 360)
        # The last image of a sequence inherits its predecessor's motion.
        last = order[1:][same_next[:-1]]
        prev = order[:-1][same_next[:-1]]
        missing = np.isnan(self.seq_motion[last])
        self.seq_motion[last[missing]] = self.seq_motion[prev[missing]]

    def query(self, lon: float, lat: float, radius_m: float) -> np.ndarray:
        if self.tree is None:
            return np.empty(0, dtype=int)
        return np.asarray(self.tree.query_ball_point(to_ecef(np.array([lon]), np.array([lat]))[0], radius_m), dtype=int)

    def record(self, j: int) -> dict:
        return {
            "id": str(self.ids[j]),
            "sequence_id": str(self.sequence_id[j]),
            "captured_at": int(self.captured_at[j]),
            "compass_angle": float(self.compass_angle[j]),
            "is_pano": bool(self.is_pano[j]),
            "quality_score": float(self.quality_score[j]),
            "lon": float(self.lon[j]),
            "lat": float(self.lat[j]),
        }


def tile_cache_path(cache: Cache, z: int, x: int, y: int, layer: str = "computed"):
    base = cache.dir("tiles") if layer == "computed" else cache.dir("tiles") / "public"
    return base / str(z) / str(x) / f"{y}.pbf"


async def fetch_tile(http: CachedHttp, cache: Cache, z: int, x: int, y: int, *, ttl_s: float, layer: str = "computed") -> bytes:
    url = (COMPUTED_TILES if layer == "computed" else PUBLIC_TILES).format(z=z, x=x, y=y)
    data = await http.get_bytes(url, category="tile", cache_path=tile_cache_path(cache, z, x, y, layer), ttl_s=ttl_s, missing_ok=True)
    return data or b""


async def fetch_coverage(
    route_points: np.ndarray,
    *,
    http: CachedHttp,
    cache: Cache,
    ttl_s: float,
    progress: Callable[[float, str], None] | None = None,
) -> ImageIndex:
    """Download z14 computed tiles around the route and index images within the 50 m buffer."""
    tiles = tile_cover(route_points)
    done = 0
    points: list[dict] = []

    async def one(t: tuple[int, int, int]) -> None:
        nonlocal done
        z, x, y = t
        data = await fetch_tile(http, cache, z, x, y, ttl_s=ttl_s)
        pts = await asyncio.to_thread(decode_image_layer, data, z, x, y)
        points.extend(pts)
        done += 1
        if progress:
            progress(done / len(tiles), f"tiles {done}/{len(tiles)}")

    await asyncio.gather(*(one(t) for t in tiles))
    if not points:
        return ImageIndex.build([])
    lon = np.array([p["lon"] for p in points])
    lat = np.array([p["lat"] for p in points])
    route_tree = cKDTree(to_ecef(route_points[:, 0], route_points[:, 1]))
    dist, _ = route_tree.query(to_ecef(lon, lat), distance_upper_bound=BUFFER_M + 10)
    keep = dist <= BUFFER_M
    return ImageIndex.build([p for p, k in zip(points, keep) if k])
