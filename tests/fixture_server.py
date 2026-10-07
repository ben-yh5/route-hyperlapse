"""Run the app against recorded fixtures (no network) for the Playwright smoke test.

    uv run python -m tests.fixture_server --port 8765 --cache /tmp/hl-fixture
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import respx
import uvicorn
from fastapi.routing import APIRoute

from hyperlapse.config import Config
from hyperlapse.core.http import CachedHttp
from hyperlapse.server.app import create_app

from . import fixture_data

BLANK_STYLE = {"version": 8, "sources": {}, "layers": [{"id": "bg", "type": "background", "paint": {"background-color": "#e8e6e1"}}]}


def build_app(cache_dir: Path, port: int):
    cfg = Config(cache_dir=cache_dir, port=port, open_browser=False, mapillary_token="MLY|fixture", style_url=f"http://127.0.0.1:{port}/fixture/style.json")
    app = create_app(cfg, http=CachedHttp(mapillary_token=cfg.mapillary_token, backoff_initial=0.01))

    async def style():
        return BLANK_STYLE

    app.router.routes.insert(0, APIRoute("/fixture/style.json", style, methods=["GET"]))
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cache", type=Path, required=True)
    a = ap.parse_args()
    with respx.mock(assert_all_called=False) as router:
        fixture_data.install(router)
        router.route(host="127.0.0.1").pass_through()
        router.route(host="localhost").pass_through()
        app = build_app(a.cache, a.port)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning"))
        app.state.ctx.server = server
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
