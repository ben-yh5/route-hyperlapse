"""Render job runner with progress events for SSE."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.pipeline import STAGE_WEIGHTS, Pipeline

STAGES = list(STAGE_WEIGHTS)


@dataclass
class Job:
    id: str
    plan_id: str
    mp4: Path
    dir: Path
    state: str = "running"  # running | done | error | cancelled
    events: list[tuple[str, dict]] = field(default_factory=list)
    downloaded: bool = False
    cancel: bool = False
    last_progress_emit: float = 0.0
    task: asyncio.Task | None = None

    def emit(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))


class JobManager:
    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self.jobs: dict[str, Job] = {}

    @property
    def running(self) -> bool:
        return any(j.state == "running" for j in self.jobs.values())

    @property
    def undownloaded(self) -> bool:
        return any(j.state == "done" and not j.downloaded and j.mp4.exists() for j in self.jobs.values())

    def start(self, plan: dict) -> Job:
        jid = uuid.uuid4().hex[:12]
        renders = self.pipeline.cache.dir("renders")
        job = Job(jid, plan["plan_id"], renders / f"{jid}.mp4", renders / jid)
        job.dir.mkdir(parents=True, exist_ok=True)
        self.jobs[jid] = job
        job.task = asyncio.create_task(self._run(job, plan))
        return job

    async def _run(self, job: Job, plan: dict) -> None:
        def progress(stage: str, frac: float, msg: str) -> None:
            if stage not in STAGE_WEIGHTS:
                return
            now = time.monotonic()
            if frac < 1.0 and now - job.last_progress_emit < 0.15:
                return
            job.last_progress_emit = now
            done = sum(STAGE_WEIGHTS[s] for s in STAGES[: STAGES.index(stage)])
            overall = done + STAGE_WEIGHTS[stage] * max(0.0, min(1.0, frac))
            job.emit("progress", {"stage": stage, "progress": round(frac, 4), "overall": round(overall, 4), "message": msg})

        try:
            result = await self.pipeline.render(plan, job.dir, job.mp4, progress, cancelled=lambda: job.cancel)
            job.state = "done"
            job.emit(
                "done",
                {
                    "video_url": f"/api/jobs/{job.id}/video",
                    "manifest_url": f"/api/jobs/{job.id}/manifest.json",
                    "credits_url": f"/api/jobs/{job.id}/credits.txt",
                    "frames": result["frames"],
                },
            )
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.emit("error", {"message": "cancelled"})
            raise
        except Exception as e:  # report every failure to the client
            job.state = "error"
            job.emit("error", {"message": str(e) or type(e).__name__})

    async def cancel_all(self) -> None:
        for j in self.jobs.values():
            if j.state == "running" and j.task:
                j.cancel = True
                j.task.cancel()
        for j in self.jobs.values():
            if j.task:
                try:
                    await asyncio.wait_for(j.task, 10)
                except BaseException:
                    pass
