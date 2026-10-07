"""Graph API entity lookups (batched)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path

from ..cache import Cache, write_json_atomic
from ..http import CachedHttp

GRAPH_URL = "https://graph.mapillary.com/"
META_FIELDS = ("camera_type", "computed_compass_angle", "compass_angle", "width", "height", "on_foot", "creator")
THUMB_FIELDS = {256: "thumb_256_url", 1024: "thumb_1024_url", 2048: "thumb_2048_url"}
BATCH = 50
PANO_TYPES = ("equirectangular", "spherical")


def _meta_path(cache: Cache, image_id: str) -> Path:
    return cache.dir("entities") / f"{image_id}.json"


def _clean(e: dict) -> dict:
    creator = e.get("creator") or {}
    return {
        "id": str(e.get("id")),
        "camera_type": e.get("camera_type") or "perspective",
        "computed_compass_angle": e.get("computed_compass_angle", e.get("compass_angle")),
        "width": e.get("width"),
        "height": e.get("height"),
        "on_foot": bool(e.get("on_foot", False)),
        "creator": creator.get("username") if isinstance(creator, dict) else str(creator),
    }


def is_pano_entity(meta: dict) -> bool:
    return meta.get("camera_type") in PANO_TYPES


def reject_reason(meta: dict) -> str | None:
    if meta.get("on_foot"):
        return "on_foot"
    if meta.get("camera_type") == "fisheye":
        return "fisheye"
    if not is_pano_entity(meta) and meta.get("width") and meta.get("height") and meta["height"] > meta["width"]:
        return "portrait"
    return None


async def _graph_batch(http: CachedHttp, ids: list[str], fields: Iterable[str]) -> dict[str, dict]:
    data = await http.get_json(GRAPH_URL, category="entity", params={"ids": ",".join(ids), "fields": ",".join(["id", *fields])})
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        return {str(e["id"]): e for e in data["data"]}
    return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}


async def fetch_metadata(http: CachedHttp, cache: Cache, ids: Iterable[str]) -> dict[str, dict]:
    """Entity metadata for ids. Cached forever on disk, minus the signed (expiring) thumbnail URLs."""
    ids = list(dict.fromkeys(str(i) for i in ids))
    out: dict[str, dict] = {}
    missing = []
    for i in ids:
        p = _meta_path(cache, i)
        if p.exists():
            out[i] = json.loads(p.read_text())
        else:
            missing.append(i)
    batches = [missing[k : k + BATCH] for k in range(0, len(missing), BATCH)]
    results = await asyncio.gather(*(_graph_batch(http, b, META_FIELDS) for b in batches))
    for res in results:
        for i, e in res.items():
            meta = _clean({**e, "id": i})
            write_json_atomic(_meta_path(cache, i), meta)
            out[i] = meta
    return out


async def fetch_thumb_urls(http: CachedHttp, ids: list[str], size: int) -> dict[str, str]:
    field = THUMB_FIELDS[size]
    batches = [ids[k : k + BATCH] for k in range(0, len(ids), BATCH)]
    results = await asyncio.gather(*(_graph_batch(http, b, [field]) for b in batches))
    return {i: e[field] for res in results for i, e in res.items() if e.get(field)}
