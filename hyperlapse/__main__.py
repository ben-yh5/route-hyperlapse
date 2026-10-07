"""Launcher: `uv run hyperlapse` starts the server on localhost:8000 and opens the browser."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import threading
import webbrowser

import uvicorn

from .config import PROJECT_ROOT, load_config


def ensure_frontend() -> None:
    web = PROJECT_ROOT / "web"
    if (web / "dist" / "index.html").exists() or not (web / "package.json").exists():
        return
    npm = shutil.which("npm")
    if not npm:
        print("web/dist is missing and npm was not found; the UI will not be available.", file=sys.stderr)
        return
    print("Building the frontend (first run)...")
    if not (web / "node_modules").exists():
        subprocess.run([npm, "install"], cwd=web, check=True)
    subprocess.run([npm, "run", "build"], cwd=web, check=True)


def main() -> None:
    cfg = load_config()
    ensure_frontend()
    from .server.app import create_app

    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info"))
    app.state.ctx.server = server
    url = f"http://localhost:{cfg.port}/"
    if not cfg.mapillary_token:
        print("MAPILLARY_TOKEN is not set: add it to .env (see .env.example).", file=sys.stderr)
    if cfg.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Route Hyperlapse on {url} (Ctrl+C to quit)")
    try:
        asyncio.run(server.serve())
    finally:
        # Lifespan shutdown normally cleans up; this covers a failed startup.
        if not app.state.ctx.closed:
            asyncio.run(app.state.ctx.close())


if __name__ == "__main__":
    main()
