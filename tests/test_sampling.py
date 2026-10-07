import math

import numpy as np
import pytest
from shapely.geometry import LineString

from hyperlapse.core.sampling import ProjectedRoute, angle_diff, make_samples, spacing_for

# The Frame budget table (T = 90 s, N_max = 1080, s_min = 15 m).
# SPEC lists 667 frames for 10 km, but n = floor(L / s) = floor(666.67) = 666 by its own formula.
BUDGET_TABLE = [
    (10_000, 15.0, 666, 7.4),
    (16_200, 15.0, 1080, 12.0),
    (50_000, 46.3, 1080, 12.0),
    (150_000, 138.9, 1080, 12.0),
    (300_000, 277.8, 1080, 12.0),
]


@pytest.mark.parametrize("length,spacing,frames,src_fps", BUDGET_TABLE)
def test_frame_budget_table(length, spacing, frames, src_fps):
    s, n = spacing_for(length, 15, 12 * 90)
    assert s == pytest.approx(spacing, abs=0.05)
    assert n == frames
    assert n / 90 == pytest.approx(src_fps, abs=0.05)


def test_frame_budget_120s():
    assert spacing_for(21_600, 15, 1440) == (15, 1440)
    s, n = spacing_for(300_000, 15, 1440)
    assert round(s) == 208 and n == 1440


def _line_east(km: float, lat=47.6, lon=-122.35, n=50):
    dlon = km * 1000 / (111_320 * math.cos(math.radians(lat)))
    return LineString([(lon + dlon * t, lat) for t in np.linspace(0, 1, n)])


def test_projected_length_and_samples():
    pr = ProjectedRoute(_line_east(10))
    assert pr.length == pytest.approx(10_000, rel=0.01)
    s, n = spacing_for(pr.length, 15, 1080)
    samples = make_samples(pr, s, n)
    assert len(samples) == n
    assert samples[1].distance_m - samples[0].distance_m == pytest.approx(s)
    assert all(abs(x.bearing - 90) < 1 for x in samples)


def test_bearing_smoothing_removes_zigzag():
    # A northbound line with +-3 m lateral wiggles every 10 m.
    lat0, lon0 = 47.6, -122.35
    pts = []
    for k in range(200):
        dy = k * 10
        dx = 3 if k % 2 else -3
        pts.append((lon0 + dx / (111_320 * math.cos(math.radians(lat0))), lat0 + dy / 111_320))
    pr = ProjectedRoute(LineString(pts))
    d = np.linspace(200, 1600, 30)
    raw_seg = pr.bearings(d, 5)  # tiny window follows the wiggle
    smooth = pr.bearings(d, 50)
    assert float(np.max(angle_diff(smooth, 0))) < 5
    assert float(np.max(angle_diff(raw_seg, 0))) > 10


def test_utm_zone_crossing_split():
    # Crosses the zone 10/11 boundary at -120 deg.
    line = LineString([(-120.2, 45.0), (-119.8, 45.0)])
    pr = ProjectedRoute(line)
    assert len(pr.zones) == 2
    assert pr.length == pytest.approx(31_500, rel=0.02)
    pts = pr.interpolate(np.array([0, pr.length / 2, pr.length]))
    assert pts[0, 0] == pytest.approx(-120.2, abs=1e-6)
    assert pts[-1, 0] == pytest.approx(-119.8, abs=1e-6)


def test_antimeridian_route_projects():
    line = LineString([(179.95, -16.8), (179.99, -16.8)])
    pr = ProjectedRoute(line)
    assert pr.length == pytest.approx(4_260, rel=0.02)


def test_frame_budget_180s():
    # T = 180 s: N_max = 2160, the 15 m floor binds below 32.4 km.
    assert spacing_for(32_400, 15, 2160) == (15, 2160)
    s, n = spacing_for(300_000, 15, 2160)
    assert round(s) == 139 and n == 2160


def test_duration_limits():
    from hyperlapse.config import Settings

    assert Settings(duration_s=180).validated().frame_budget == 2160
    with pytest.raises(ValueError):
        Settings(duration_s=181).validated()
