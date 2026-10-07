import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from ...core.pipeline import compute_stats
from . import ctx

router = APIRouter()


@router.post("/plan/{plan_id}/render")
async def render(plan_id: str, c=Depends(ctx)):
    if c.jobs.running:
        raise HTTPException(409, "a render is already running")
    plan = c.store.load_plan(plan_id)
    stats = compute_stats(plan)
    if not stats["can_render"]:
        raise HTTPException(400, f"only {stats['kept']} frames kept; at least 150 are needed")
    job = c.jobs.start(plan)
    return {"job_id": job.id}


def _job(c, job_id: str):
    job = c.jobs.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


@router.get("/jobs/{job_id}/events")
async def events(job_id: str, c=Depends(ctx)):
    job = _job(c, job_id)

    async def gen():
        k = 0
        while True:
            while k < len(job.events):
                ev, data = job.events[k]
                k += 1
                yield {"event": ev, "data": json.dumps(data)}
                if ev in ("done", "error"):
                    return
            await asyncio.sleep(0.2)

    return EventSourceResponse(gen(), ping=10)


@router.get("/jobs/{job_id}/video")
async def video(job_id: str, download: int = 0, c=Depends(ctx)):
    job = _job(c, job_id)
    if job.state != "done" or not job.mp4.exists():
        raise HTTPException(404, "video not ready")
    if download:
        job.downloaded = True
        return FileResponse(job.mp4, media_type="video/mp4", filename=f"hyperlapse-{job.id}.mp4")
    return FileResponse(job.mp4, media_type="video/mp4")


@router.get("/jobs/{job_id}/{name}")
async def job_file(job_id: str, name: str, c=Depends(ctx)):
    job = _job(c, job_id)
    if name not in ("manifest.json", "credits.txt") or not (job.dir / name).exists():
        raise HTTPException(404, "not found")
    return FileResponse(job.dir / name, media_type="application/json" if name.endswith(".json") else "text/plain")
