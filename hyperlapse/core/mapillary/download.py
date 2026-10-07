"""Async image downloads (concurrency-limited, cached by image ID)."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from PIL import Image

from ..cache import Cache, write_bytes_atomic
from ..http import CachedHttp
from .entities import fetch_thumb_urls

log = logging.getLogger(__name__)


def image_path(cache: Cache, image_id: str, size: int = 2048) -> Path:
    return cache.dir("images") / f"{image_id}_{size}.jpg"



async def download_images(
    http: CachedHttp,
    cache: Cache,
    ids: Iterable[str],
    *,
    size: int = 2048,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Path]:
    """Download images by ID (cached by ID). Returns paths for those that succeeded."""
    ids = list(dict.fromkeys(str(i) for i in ids))
    have = {i: image_path(cache, i, size) for i in ids if image_path(cache, i, size).exists()}
    missing = [i for i in ids if i not in have]
    total = max(len(ids), 1)
    done = len(have)
    if progress:
        progress(done / total, f"images {done}/{len(ids)}")
    if not missing:
        return have
    urls = await fetch_thumb_urls(http, missing, size)

    async def one(i: str) -> None:
        nonlocal done
        url = urls.get(i)
        if url:
            try:
                data = await http.get_bytes(url, category="image")
            except Exception as e:
                log.warning("image %s download failed: %s", i, e)
                data = None
            if data:
                p = image_path(cache, i, size)
                write_bytes_atomic(p, data)
                have[i] = p
        done += 1
        if progress:
            progress(done / total, f"images {done}/{len(ids)}")

    await asyncio.gather(*(one(i) for i in missing))
    return have


async def get_image(http: CachedHttp, cache: Cache, image_id: str, size: int) -> bytes:
    """JPEG for the image proxy at roughly `size` px wide, reusing the largest cached copy."""
    size = 256 if size <= 256 else 1024 if size <= 1024 else 2048
    exact = image_path(cache, image_id, size)
    if exact.exists():
        return exact.read_bytes()
    for bigger in (2048, 1024):
        src = image_path(cache, image_id, bigger)
        if bigger > size and src.exists():
            data = await asyncio.to_thread(_resize, src.read_bytes(), size)
            write_bytes_atomic(exact, data)
            return data
    got = await download_images(http, cache, [image_id], size=size)
    if image_id not in got:
        raise FileNotFoundError(image_id)
    return got[image_id].read_bytes()


def _resize(data: bytes, width: int) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    h = max(1, round(im.height * width / im.width))
    im = im.resize((width, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()
