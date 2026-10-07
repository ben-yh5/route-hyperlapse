"""Per-frame rendering: crop / pano reprojection, colour normalisation, overlays."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import py360convert
from PIL import Image, ImageDraw, ImageFont

ATTRIBUTION = "Imagery: Mapillary contributors, CC BY-SA 4.0"
H_FOV = 90.0


def pano_yaw(route_bearing: float, compass_angle: float) -> float:
    """Yaw (degrees, positive = right) from the pano's centre to the direction of travel."""
    return (route_bearing - compass_angle + 180.0) % 360.0 - 180.0


def reproject_pano(img: Image.Image, yaw: float, width: int, height: int) -> Image.Image:
    v_fov = math.degrees(2 * math.atan(math.tan(math.radians(H_FOV / 2)) * height / width))
    e = np.asarray(img.convert("RGB"))
    out = py360convert.e2p(e, fov_deg=(H_FOV, v_fov), u_deg=yaw, v_deg=0.0, out_hw=(height, width), mode="bilinear")
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def crop_resize(img: Image.Image, width: int, height: int) -> Image.Image:
    img = img.convert("RGB")
    target = width / height
    w, h = img.size
    if w / h > target:
        nw = round(h * target)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / target)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def base_frame(img: Image.Image, *, is_pano: bool, route_bearing: float, compass_angle: float | None, width: int, height: int) -> Image.Image:
    if is_pano:
        return reproject_pano(img, pano_yaw(route_bearing, compass_angle or 0.0), width, height)
    return crop_resize(img, width, height)


def mean_luminance(img: Image.Image) -> float:
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean())


class HistogramMatcher:
    """Match each frame's per-channel histogram to a slowly moving reference to reduce flicker."""

    def __init__(self, momentum: float = 0.9):
        self.momentum = momentum
        self.ref_cdf: np.ndarray | None = None  # (3, 256)

    @staticmethod
    def _cdf(a: np.ndarray) -> np.ndarray:
        cdfs = []
        for c in range(3):
            h = np.bincount(a[..., c].ravel(), minlength=256).astype(np.float64)
            cdf = np.cumsum(h)
            cdfs.append(cdf / cdf[-1])
        return np.stack(cdfs)

    def __call__(self, img: Image.Image) -> Image.Image:
        a = np.asarray(img.convert("RGB"))
        cdf = self._cdf(a)
        if self.ref_cdf is None:
            self.ref_cdf = cdf
            return img
        out = np.empty_like(a)
        for c in range(3):
            lut = np.clip(np.searchsorted(self.ref_cdf[c], cdf[c] - 1e-9), 0, 255).astype(np.uint8)
            out[..., c] = lut[a[..., c]]
        self.ref_cdf = self.momentum * self.ref_cdf + (1 - self.momentum) * self._cdf(out)
        return Image.fromarray(out)


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def overlay(img: Image.Image, *, distance_m: float, total_m: float) -> Image.Image:
    """Attribution bottom-right, thin progress bar along the bottom, km counter bottom-left."""
    img = img.convert("RGBA")
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fs = max(10, round(h / 40))
    font = _font(fs)
    pad = max(4, fs // 2)
    bar_h = max(2, round(h / 180))

    tw = d.textlength(ATTRIBUTION, font=font)
    box = (w - tw - 3 * pad, h - bar_h - fs - 3 * pad, w - pad, h - bar_h - pad)
    d.rounded_rectangle(box, radius=pad // 2, fill=(0, 0, 0, 140))
    d.text((box[0] + pad, box[1] + pad), ATTRIBUTION, font=font, fill=(255, 255, 255, 235))

    km = f"{distance_m / 1000:.1f} / {total_m / 1000:.1f} km"
    kw = d.textlength(km, font=font)
    kbox = (pad, h - bar_h - fs - 3 * pad, pad + kw + 2 * pad, h - bar_h - pad)
    d.rounded_rectangle(kbox, radius=pad // 2, fill=(0, 0, 0, 140))
    d.text((kbox[0] + pad, kbox[1] + pad), km, font=font, fill=(255, 255, 255, 235))

    frac = 0.0 if total_m <= 0 else min(1.0, max(0.0, distance_m / total_m))
    d.rectangle((0, h - bar_h, w, h), fill=(0, 0, 0, 110))
    d.rectangle((0, h - bar_h, round(w * frac), h), fill=(255, 255, 255, 220))
    return Image.alpha_composite(img, layer).convert("RGB")
