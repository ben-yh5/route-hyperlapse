"""Projection to local UTM, frame-budget spacing, sample points with smoothed bearings."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from pyproj import Geod, Transformer
from shapely.geometry import LineString

GEOD = Geod(ellps="WGS84")


def spacing_for(length_m: float, min_spacing_m: float, max_frames: int) -> tuple[float, int]:
    """s = max(s_min, L / N_max), n = floor(L / s)."""
    s = max(min_spacing_m, length_m / max_frames)
    n = int(math.floor(length_m / s + 1e-9))
    return s, min(n, max_frames)


def utm_zone(lon: float) -> int:
    lon = ((lon + 180) % 360) - 180
    return min(int((lon + 180) // 6) + 1, 60)


def utm_epsg(zone: int, lat: float) -> int:
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=64)
def _transformers(epsg: int) -> tuple[Transformer, Transformer]:
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = Transformer.from_crs(epsg, 4326, always_xy=True)
    return fwd, inv


def split_at_zone_boundaries(ll: np.ndarray) -> np.ndarray:
    """Insert a vertex wherever a segment crosses a 6-degree UTM zone boundary."""
    out = [ll[0]]
    for a, b in zip(ll[:-1], ll[1:]):
        if abs(b[0] - a[0]) < 180:
            lo, hi = sorted((a[0], b[0]))
            bounds = [x for x in np.arange(math.ceil((lo + 180) / 6) * 6 - 180, hi, 6) if lo < x < hi]
            for x in sorted(bounds, reverse=a[0] > b[0]):
                t = (x - a[0]) / (b[0] - a[0])
                out.append(np.array([x, a[1] + t * (b[1] - a[1])]))
        out.append(b)
    return np.array(out)


@dataclass
class Sample:
    i: int
    lon: float
    lat: float
    distance_m: float
    bearing: float


class ProjectedRoute:
    """A lon/lat route measured in local UTM metres, split into per-zone pieces when it crosses zones."""

    def __init__(self, line: LineString):
        ll = split_at_zone_boundaries(np.asarray(line.coords, dtype=float)[:, :2])
        self.lonlat = ll
        lat_c = float(ll[:, 1].mean())
        # Each segment is measured in the zone of its midpoint (segments never straddle a boundary).
        zones = [utm_zone((a + b) / 2) for a, b in zip(ll[:-1, 0], ll[1:, 0])]
        self.seg_epsg = np.array([utm_epsg(z, lat_c) for z in zones])
        seg_len = np.empty(len(ll) - 1)
        self._seg_xy = np.empty((len(ll) - 1, 2, 2))
        for epsg in np.unique(self.seg_epsg):
            fwd, _ = _transformers(int(epsg))
            idx = np.nonzero(self.seg_epsg == epsg)[0]
            x0, y0 = fwd.transform(ll[idx, 0], ll[idx, 1])
            x1, y1 = fwd.transform(ll[idx + 1, 0], ll[idx + 1, 1])
            self._seg_xy[idx, 0, 0], self._seg_xy[idx, 0, 1] = x0, y0
            self._seg_xy[idx, 1, 0], self._seg_xy[idx, 1, 1] = x1, y1
            seg_len[idx] = np.hypot(np.asarray(x1) - x0, np.asarray(y1) - y0)
        self.seg_len = seg_len
        self.cum = np.concatenate([[0.0], np.cumsum(seg_len)])
        self.length = float(self.cum[-1])
        self.zones = sorted({int(e) for e in self.seg_epsg})

    def interpolate(self, d: np.ndarray) -> np.ndarray:
        """Lon/lat (N, 2) at distances d along the route."""
        d = np.clip(np.asarray(d, dtype=float), 0, self.length)
        j = np.clip(np.searchsorted(self.cum, d, side="right") - 1, 0, len(self.seg_len) - 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.where(self.seg_len[j] > 0, (d - self.cum[j]) / self.seg_len[j], 0.0)
        xy = self._seg_xy[j, 0] + (self._seg_xy[j, 1] - self._seg_xy[j, 0]) * t[:, None]
        out = np.empty_like(xy)
        for epsg in np.unique(self.seg_epsg[j]):
            m = self.seg_epsg[j] == epsg
            _, inv = _transformers(int(epsg))
            lon, lat = inv.transform(xy[m, 0], xy[m, 1])
            out[m, 0], out[m, 1] = lon, lat
        return out

    def bearings(self, d: np.ndarray, window_m: float) -> np.ndarray:
        """Route bearing (degrees clockwise from north) smoothed over d ± window."""
        d = np.asarray(d, dtype=float)
        a = self.interpolate(np.clip(d - window_m, 0, self.length))
        b = self.interpolate(np.clip(d + window_m, 0, self.length))
        az, _, _ = GEOD.inv(a[:, 0], a[:, 1], b[:, 0], b[:, 1])
        return np.mod(np.asarray(az), 360.0)

    def densify(self, step_m: float) -> np.ndarray:
        n = max(2, int(math.ceil(self.length / step_m)) + 1)
        return self.interpolate(np.linspace(0, self.length, n))


def make_samples(route: ProjectedRoute, spacing_m: float, n: int) -> list[Sample]:
    d = (np.arange(n) + 0.5) * spacing_m
    pts = route.interpolate(d)
    brg = route.bearings(d, max(50.0, spacing_m / 2))
    return [Sample(i, float(pts[i, 0]), float(pts[i, 1]), float(d[i]), float(brg[i])) for i in range(n)]


def angle_diff(a: np.ndarray | float, b: np.ndarray | float) -> np.ndarray | float:
    """Absolute smallest difference between two bearings in degrees."""
    return np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)
