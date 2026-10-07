import numpy as np
from PIL import Image

from hyperlapse.core.render import HistogramMatcher, base_frame, crop_resize, overlay, pano_yaw


def _pano_with_red_east(w=720, h=360):
    """Equirectangular pano whose centre faces the camera compass; a red stripe at +90 deg (right)."""
    a = np.full((h, w, 3), 30, np.uint8)
    x = int(w * 0.75)
    a[:, x - 6 : x + 6] = (255, 0, 0)
    return Image.fromarray(a)


def test_pano_yaw_sign():
    # Camera centre faces north (compass 0) and the red stripe is east. Travelling east must show red.
    pano = _pano_with_red_east()
    out = np.asarray(base_frame(pano, is_pano=True, route_bearing=90, compass_angle=0, width=160, height=90))
    assert tuple(out[45, 80]) == (255, 0, 0) or out[45, 76:84, 0].max() > 200
    # Travelling west shows the opposite side, no red in the centre.
    out_w = np.asarray(base_frame(pano, is_pano=True, route_bearing=270, compass_angle=0, width=160, height=90))
    assert out_w[45, 70:90, 0].max() < 100
    # Camera centre facing east already: travelling east looks straight ahead (yaw 0).
    assert pano_yaw(90, 90) == 0
    assert pano_yaw(10, 350) == 20


def test_crop_resize_16_9():
    out = crop_resize(Image.new("RGB", (400, 300)), 1280, 720)
    assert out.size == (1280, 720)


def test_overlay_and_histogram():
    img = Image.new("RGB", (320, 180), (100, 120, 140))
    out = overlay(img, distance_m=500, total_m=1000)
    a = np.asarray(out)
    assert a.shape == (180, 320, 3)
    assert a[-1, 10].mean() > 200 and a[-1, 300].mean() < 120  # progress bar half filled
    m = HistogramMatcher()
    m(Image.new("RGB", (32, 32), (100, 100, 100)))
    out2 = np.asarray(m(Image.new("RGB", (32, 32), (180, 180, 180))))
    assert out2.mean() < 150
