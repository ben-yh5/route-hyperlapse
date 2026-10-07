"""Defaults from hyperlapse.toml, secrets from .env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OSRM_URL = "https://router.project-osrm.org"
MAX_DURATION_S = 180
MAX_FRAMES = 12 * MAX_DURATION_S


@dataclass
class Settings:
    """Per-plan settings (stored with each plan)."""

    duration_s: float = 60
    max_frames: int | None = None
    min_spacing_m: float = 20
    output_fps: int = 30
    width: int = 1280
    height: int = 720
    captured_since: str | None = None
    include_pano: bool = True
    blend: bool = True
    normalize_colour: bool = False
    drive_on_left: bool = False

    @property
    def frame_budget(self) -> int:
        return int(self.max_frames) if self.max_frames else int(round(12 * self.duration_s))

    def validated(self) -> Settings:
        if not 30 <= self.duration_s <= MAX_DURATION_S:
            raise ValueError(f"duration_s must be between 30 and {MAX_DURATION_S}")
        if self.max_frames is not None and not 1 <= self.max_frames <= MAX_FRAMES:
            raise ValueError(f"max_frames must be between 1 and {MAX_FRAMES}")
        if self.min_spacing_m <= 0:
            raise ValueError("min_spacing_m must be positive")
        if not 1 <= self.output_fps <= 120:
            raise ValueError("output_fps must be between 1 and 120")
        if self.width < 16 or self.height < 16 or self.width % 2 or self.height % 2:
            raise ValueError("width and height must be even and at least 16")
        return self

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None, base: Settings | None = None) -> Settings:
        base = base or cls()
        names = {f.name for f in fields(cls)}
        return replace(base, **{k: v for k, v in (d or {}).items() if k in names})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelectionWeights:
    radius_m: float = 25
    retry_radius_m: float = 40
    max_heading_error_deg: float = 35
    top_k: int = 5  # alternates shown in the Inspector
    pool_k: int = 15  # candidates per sample the chooser considers
    w_distance: float = 1.0
    w_heading: float = 1.0
    w_quality: float = 0.5
    w_age: float = 2.0
    w_wrong_side: float = 0.6
    w_wrong_direction: float = 1.5
    t_sequence_change: float = 3.5
    t_time_gap: float = 1.5
    t_camera_change: float = 2.0
    t_backwards: float = 1.5
    t_duplicate: float = 3.0
    min_luminance: float = 0


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    cache_dir: Path = Path("~/.cache/hyperlapse").expanduser()
    clear_cache_on_quit: bool = True
    style_url: str = "https://tiles.openfreemap.org/styles/liberty"
    defaults: Settings = field(default_factory=Settings)
    selection: SelectionWeights = field(default_factory=SelectionWeights)
    concurrency: int = 16
    nominatim_user_agent: str = "route-hyperlapse/0.1 (local app)"
    tile_ttl_days: float = 30
    mapillary_token: str | None = None
    osrm_url: str = DEFAULT_OSRM_URL



def _find(name: str) -> Path | None:
    for d in (Path.cwd(), PROJECT_ROOT):
        if (d / name).exists():
            return d / name
    return None


def load_config(toml_path: Path | None = None, env_path: Path | None = None) -> Config:
    env_file = env_path or _find(".env")
    if env_file:
        load_dotenv(env_file, override=False)
    path = toml_path or (Path(os.environ["HYPERLAPSE_CONFIG"]) if "HYPERLAPSE_CONFIG" in os.environ else _find("hyperlapse.toml"))
    raw: dict[str, Any] = tomllib.loads(path.read_text()) if path else {}
    app = raw.get("app", {})
    net = raw.get("network", {})
    sel_names = {f.name for f in fields(SelectionWeights)}
    cfg = Config(
        host=app.get("host", "127.0.0.1"),
        port=int(app.get("port", 8000)),
        open_browser=bool(app.get("open_browser", True)),
        cache_dir=Path(os.environ.get("HYPERLAPSE_CACHE_DIR", app.get("cache_dir", "~/.cache/hyperlapse"))).expanduser(),
        clear_cache_on_quit=bool(app.get("clear_cache_on_quit", True)),
        style_url=app.get("style_url", Config.style_url),
        defaults=Settings.from_dict(raw.get("defaults")),
        selection=SelectionWeights(**{k: v for k, v in raw.get("selection", {}).items() if k in sel_names}),
        concurrency=int(net.get("concurrency", 16)),
        nominatim_user_agent=net.get("nominatim_user_agent", Config.nominatim_user_agent),
        tile_ttl_days=float(net.get("tile_ttl_days", 30)),
        mapillary_token=os.environ.get("MAPILLARY_TOKEN") or None,
        osrm_url=(os.environ.get("OSRM_URL") or DEFAULT_OSRM_URL).rstrip("/"),
    )
    cfg.defaults.validated()
    return cfg
