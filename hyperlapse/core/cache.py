"""On-disk cache layout and session folders."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

CATEGORIES = ("geocode", "routes", "tiles", "entities", "images", "plans", "renders")
CLEARABLE = ("geocode", "routes", "tiles", "entities", "images", "renders")
SESSION_PREFIX = "session-"


class Cache:
    def __init__(self, root: Path):
        self.root = root
        for c in CATEGORIES:
            (root / c).mkdir(parents=True, exist_ok=True)

    def dir(self, category: str) -> Path:
        d = self.root / category
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sizes(self) -> dict[str, int]:
        out = {}
        for c in CATEGORIES:
            total = 0
            for dirpath, _, files in os.walk(self.root / c):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
            out[c] = total
        return out

    def clear(self, categories: tuple[str, ...] = CLEARABLE) -> None:
        for c in categories:
            shutil.rmtree(self.root / c, ignore_errors=True)
            (self.root / c).mkdir(parents=True, exist_ok=True)


def fresh(path: Path, ttl_s: float | None) -> bool:
    if not path.exists():
        return False
    return ttl_s is None or (time.time() - path.stat().st_mtime) < ttl_s


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json_atomic(path: Path, obj: Any) -> None:
    write_bytes_atomic(path, json.dumps(obj, separators=(",", ":")).encode())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def open_session(base: Path, clear_on_quit: bool) -> Path:
    """Pick the cache root. Leftover session folders (from crashes / force-kills) are deleted."""
    base.mkdir(parents=True, exist_ok=True)
    for p in base.glob(f"{SESSION_PREFIX}*"):
        shutil.rmtree(p, ignore_errors=True)
    if not clear_on_quit:
        return base / "persistent"
    return base / f"{SESSION_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"


def close_session(root: Path, base: Path, clear_on_quit: bool) -> None:
    """Delete the session cache, or keep it by merging into the persistent cache."""
    if not root.name.startswith(SESSION_PREFIX):
        if clear_on_quit:
            shutil.rmtree(root, ignore_errors=True)
        return
    if clear_on_quit:
        shutil.rmtree(root, ignore_errors=True)
        return
    dest = base / "persistent"
    for src in root.rglob("*"):
        if src.is_file():
            target = dest / src.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, target)
    shutil.rmtree(root, ignore_errors=True)
