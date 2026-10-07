"""FastAPI app: JSON API under /api, built frontend (web/dist) at /."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import PROJECT_ROOT, Config, load_config
from ..core.assemble import AssembleError
from ..core.http import CachedHttp, HttpError, RateLimited
from ..core.pipeline import PlanError
from ..core.routing import RoutingError
from .api import geocode, images, jobs, plan, route, system, tiles
from .context import AppContext
from .store import NotFound

WEB_DIST = PROJECT_ROOT / "web" / "dist"


def create_app(cfg: Config | None = None, *, http: CachedHttp | None = None, web_dist: Path = WEB_DIST) -> FastAPI:
    cfg = cfg or load_config()
    context = AppContext.create(cfg, http=http)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await context.close()

    app = FastAPI(title="Route Hyperlapse", lifespan=lifespan)
    app.state.ctx = context

    for mod in (geocode, route, plan, jobs, tiles, images, system):
        app.include_router(mod.router, prefix="/api")

    def err(status: int):
        async def handler(request: Request, exc: Exception):
            return JSONResponse({"detail": str(exc)}, status_code=status)
        return handler

    app.add_exception_handler(NotFound, err(404))
    app.add_exception_handler(PlanError, err(400))
    app.add_exception_handler(RoutingError, err(400))
    app.add_exception_handler(AssembleError, err(500))
    app.add_exception_handler(HttpError, err(502))
    app.add_exception_handler(RateLimited, err(503))

    if (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (web_dist / path).resolve()
            if path and f.is_file() and web_dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(web_dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return HTMLResponse("<h1>Frontend not built</h1><p>Run <code>npm install && npm run build</code> in <code>web/</code>.</p>")

    return app
