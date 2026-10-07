import mapbox_vector_tile
import mercantile
import numpy as np
import pytest

from hyperlapse.core.mapillary.tiles import ImageIndex, decode_image_layer, tile_cover


def test_tile_cover_of_polyline():
    # A 10 km east-west line at z14 crosses ~5 tile columns in one row (plus buffer neighbours).
    lat = 47.6
    lons = np.linspace(-122.40, -122.40 + 10_000 / (111_320 * np.cos(np.radians(lat))), 1000)
    pts = np.column_stack([lons, np.full_like(lons, lat)])
    tiles = tile_cover(pts, buffer_m=50)
    expected = {(14, t.x, t.y) for t in mercantile.tiles(lons.min(), lat, lons.max(), lat, 14)}
    assert expected <= set(tiles)
    ys = {t[2] for t in tiles}
    assert len(ys) <= 2
    assert len(tiles) <= 2 * len(expected) + 2


def test_tile_cover_includes_buffer_neighbour():
    t = mercantile.tile(-122.35, 47.6, 14)
    b = mercantile.bounds(t)
    # A point 20 m inside the western edge needs the western neighbour within a 50 m buffer.
    lon = b.west + 20 / (111_320 * np.cos(np.radians(47.6)))
    tiles = tile_cover(np.array([[lon, (b.north + b.south) / 2]]), buffer_m=50)
    assert (14, t.x - 1, t.y) in tiles and (14, t.x, t.y) in tiles


def test_decode_known_tile():
    t = mercantile.Tile(x=2622, y=5718, z=14)
    data = mapbox_vector_tile.encode(
        [{"name": "image", "features": [
            {"geometry": "POINT(0 0)", "properties": {"id": 1, "sequence_id": "a", "captured_at": 5, "compass_angle": 90.0, "is_pano": False}},
            {"geometry": "POINT(2048 2048)", "properties": {"id": 2, "sequence_id": "a", "captured_at": 6, "compass_angle": 91.0, "is_pano": True}},
        ]}],
        default_options={"y_coord_down": True, "extents": 4096},
    )
    pts = decode_image_layer(data, t.z, t.x, t.y)
    b = mercantile.bounds(t)
    nw = next(p for p in pts if p["id"] == "1")
    assert nw["lon"] == pytest.approx(b.west, abs=1e-9) and nw["lat"] == pytest.approx(b.north, abs=1e-9)
    centre = next(p for p in pts if p["id"] == "2")
    cx, cy = mercantile.lnglat(*mercantile.xy(b.west, b.north))  # sanity on helper
    ul = mercantile.ul(t.x * 2 + 1, t.y * 2 + 1, 15)  # the tile centre is the NW corner of the z15 child
    assert centre["lon"] == pytest.approx(ul.lng, abs=1e-9) and centre["lat"] == pytest.approx(ul.lat, abs=1e-9)
    assert centre["is_pano"] is True and centre["compass_angle"] == 91.0
    assert nw["quality_score"] == 0.5


def test_sequence_motion():
    pts = [
        {"id": str(k), "sequence_id": "s", "captured_at": k, "compass_angle": 0.0, "is_pano": False, "quality_score": 0.5,
         "lon": -122.35 + k * 1e-4, "lat": 47.6}
        for k in range(5)
    ]
    idx = ImageIndex.build(pts)
    assert np.allclose(idx.seq_motion, 90, atol=1)
