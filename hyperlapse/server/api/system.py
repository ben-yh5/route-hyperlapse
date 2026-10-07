import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import ctx

router = APIRouter()


class SessionIn(BaseModel):
    clear_cache_on_quit: bool


def cache_out(c) -> dict:
    sizes = c.cache.sizes()
    return {"total": sum(sizes.values()), "categories": sizes}


@router.get("/config")
async def config(c=Depends(ctx)):
    return {
        "has_mapillary_token": bool(c.cfg.mapillary_token),
        "style_url": c.cfg.style_url,
        "defaults": c.cfg.defaults.to_dict(),
        "clear_cache_on_quit": c.clear_on_quit,
    }


@router.get("/status")
async def status(c=Depends(ctx)):
    return {"render_running": c.jobs.running, "undownloaded_render": c.jobs.undownloaded}


@router.get("/cache")
async def cache(c=Depends(ctx)):
    return await asyncio.to_thread(cache_out, c)


@router.post("/cache/clear")
async def cache_clear(c=Depends(ctx)):
    if c.jobs.running:
        raise HTTPException(409, "cannot clear the cache while a render is running")
    await asyncio.to_thread(c.cache.clear)
    c.pipeline.forget_coverage()
    for j in list(c.jobs.jobs.values()):
        if j.state != "running":
            del c.jobs.jobs[j.id]
    return await asyncio.to_thread(cache_out, c)


@router.post("/settings/session")
async def session_settings(body: SessionIn, c=Depends(ctx)):
    c.clear_on_quit = body.clear_cache_on_quit
    return {"clear_cache_on_quit": c.clear_on_quit}


@router.post("/quit")
async def quit_(c=Depends(ctx)):
    c.request_shutdown()
    return {"ok": True}
