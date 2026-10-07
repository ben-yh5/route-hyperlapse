"""ffmpeg assembly, manifest.json and credits.txt."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path


class AssembleError(Exception):
    pass


def ffmpeg_command(frames_dir: Path, out: Path, *, n_frames: int, duration_s: float, output_fps: int, blend: bool) -> list[str]:
    src_fps = n_frames / duration_s
    total = round(duration_s * output_fps)
    if blend:
        # Full-strength crossfades (no hold at either end, no scene-change cut).
        rate = f"framerate=fps={output_fps}:interp_start=0:interp_end=255:scene=100"
    else:
        rate = f"fps={output_fps}"
    vf = f"{rate},tpad=stop_mode=clone:stop_duration=2,format=yuv420p"
    return [
        shutil.which("ffmpeg") or "ffmpeg",
        "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", f"{src_fps:.6f}",
        "-i", str(frames_dir / "%06d.jpg"),
        "-vf", vf,
        "-frames:v", str(total),
        "-r", str(output_fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out),
    ]


def assemble(
    frames_dir: Path,
    out: Path,
    *,
    n_frames: int,
    duration_s: float,
    output_fps: int,
    blend: bool,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    if n_frames < 1:
        raise AssembleError("no frames to assemble")
    cmd = ffmpeg_command(frames_dir, out, n_frames=n_frames, duration_s=duration_s, output_fps=output_fps, blend=blend)
    total = round(duration_s * output_fps)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancelled and cancelled():
            proc.kill()
            raise AssembleError("cancelled")
        if line.startswith("frame=") and progress:
            try:
                f = int(line.split("=", 1)[1])
            except ValueError:
                continue
            progress(min(1.0, f / total), f"encoding {f}/{total}")
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise AssembleError(f"ffmpeg failed: {err.strip()[-500:]}")


def write_manifest(path: Path, frames: list[dict]) -> None:
    keys = ("sample_index", "image_id", "lat", "lon", "distance_m", "captured_at")
    path.write_text(json.dumps({"frames": [{k: f[k] for k in keys} for f in frames]}, indent=1))


def write_credits(path: Path, creators: list[str | None]) -> None:
    names = sorted({c for c in creators if c}, key=str.lower)
    header = "Imagery: Mapillary contributors, CC BY-SA 4.0\nhttps://www.mapillary.com\n\nContributors:\n"
    path.write_text(header + "".join(f"{n}\n" for n in names))
