from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...config import Settings
from ...core.pipeline import compute_stats, override_map, sample_status, samples_geojson
from ..store import NotFound
from . import ctx

router = APIRouter()


class PlanIn(BaseModel):
    route_id: str
    settings: dict[str, Any] = {}
    plan_id: str | None = None


class OverrideIn(BaseModel):
    action: Literal["swap", "exclude", "lock", "unlock", "reset"]
    image_id: str | None = None


def thumb(image_id: str | None, size: int = 256) -> str | None:
    return f"/api/image/{image_id}?size={size}" if image_id else None


def plan_out(c, plan: dict) -> dict:
    try:
        route = c.store.load_route(plan["route_id"])
        waypoints = [{"lat": a, "lon": b} for a, b in route.waypoints] if route.waypoints else None
    except NotFound:
        waypoints = None
    return {
        "plan_id": plan["plan_id"],
        "route_id": plan["route_id"],
        "settings": plan["settings"],
        "waypoints": waypoints,
        "samples": samples_geojson(plan),
        "stats": compute_stats(plan),
    }


def frame_out(plan: dict, i: int, ovs: dict | None = None) -> dict:
    s = plan["samples"][i]
    status, locked = sample_status(plan, i, ovs)
    ch = s["chosen"]
    return {
        "i": i,
        "status": status,
        "locked": locked,
        "image_id": ch["id"] if ch else None,
        "lat": s["lat"],
        "lon": s["lon"],
        "image_lat": ch["lat"] if ch else None,
        "image_lon": ch["lon"] if ch else None,
        "distance_m": round(s["d"], 1),
        "bearing": round(s["bearing"], 1),
        "captured_at": ch["captured_at"] if ch else None,
        "is_pano": ch["is_pano"] if ch else None,
        "thumb_url": thumb(ch["id"]) if ch else None,
    }


@router.post("/plan")
async def plan_ep(body: PlanIn, c=Depends(ctx)):
    if not c.cfg.mapillary_token:
        raise HTTPException(400, "MAPILLARY_TOKEN is not set; add it to .env and restart")
    route = c.store.load_route(body.route_id)
    overrides: list[dict] = []
    plan_id = c.store.new_plan_id()
    if body.plan_id:
        try:
            old = c.store.load_plan(body.plan_id)
        except NotFound:
            old = None
        if old and old["route_id"] == route.route_id:
            plan_id, overrides = old["plan_id"], old.get("overrides", [])
    try:
        settings = Settings.from_dict(body.settings, base=c.cfg.defaults).validated()
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    async with c.plan_locks[plan_id]:
        plan = await c.pipeline.plan(route, settings, plan_id=plan_id, overrides=overrides)
        c.store.save_plan(plan)
    return plan_out(c, plan)


@router.get("/plan/{plan_id}")
async def plan_get(plan_id: str, c=Depends(ctx)):
    return plan_out(c, c.store.load_plan(plan_id))


@router.get("/plan/{plan_id}/frames")
async def frames(plan_id: str, offset: int = 0, limit: int = 500, c=Depends(ctx)):
    plan = c.store.load_plan(plan_id)
    ovs = override_map(plan)
    n = plan["n"]
    lo = max(0, offset)
    hi = min(n, lo + max(1, min(limit, 2500)))
    return {"total": n, "frames": [frame_out(plan, i, ovs) for i in range(lo, hi)]}


@router.get("/plan/{plan_id}/samples/{i}/candidates")
async def candidates(plan_id: str, i: int, c=Depends(ctx)):
    plan = c.store.load_plan(plan_id)
    if not 0 <= i < plan["n"]:
        raise HTTPException(404, "no such sample")
    s = plan["samples"][i]
    chosen_id = s["chosen"]["id"] if s["chosen"] else None
    cands = list(s["candidates"][: c.cfg.selection.top_k])
    if s["chosen"] and all(x["id"] != chosen_id for x in cands):
        cands.insert(0, s["chosen"])
    return {
        "candidates": [
            {
                "image_id": x["id"],
                "lat": x["lat"],
                "lon": x["lon"],
                "distance_m": x.get("distance_m"),
                "heading_error": x.get("heading_error"),
                "score": x.get("score"),
                "captured_at": x["captured_at"],
                "is_pano": x["is_pano"],
                "sequence_id": x["sequence_id"],
                "thumb_url": thumb(x["id"]),
                "chosen": x["id"] == chosen_id,
            }
            for x in cands
        ]
    }


@router.patch("/plan/{plan_id}/samples/{i}")
async def override(plan_id: str, i: int, body: OverrideIn, c=Depends(ctx)):
    async with c.plan_locks[plan_id]:
        plan = c.store.load_plan(plan_id)
        if body.action == "swap" and not body.image_id:
            raise HTTPException(400, "swap needs image_id")
        plan = await c.pipeline.override(plan, i, body.action, body.image_id)
        c.store.save_plan(plan)
    return {"frame": frame_out(plan, i), "stats": compute_stats(plan), "samples": samples_geojson(plan)}
