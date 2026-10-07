"""Stage orchestration: plan (sample → coverage → score → choose → enrich), overrides, render."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import Config, Settings
from .assemble import assemble, write_credits, write_manifest
from .cache import Cache
from .http import CachedHttp
from .mapillary.download import download_images
from .mapillary.entities import fetch_metadata, is_pano_entity, reject_reason
from .mapillary.tiles import ImageIndex, fetch_coverage
from .render import HistogramMatcher, base_frame, mean_luminance, overlay
from .routing import Route
from .sampling import ProjectedRoute, Sample, angle_diff, make_samples, spacing_for
from .select import choose, local_offsets, score_all, transition_cost

Progress = Callable[[str, float, str], None]
MIN_RENDER_FRAMES = 150
ENRICH_ROUNDS = 4
STAGE_WEIGHTS = {"enrich": 0.05, "download": 0.35, "render": 0.45, "assemble": 0.15}


class PlanError(Exception):
    pass


def _noop(stage: str, frac: float, msg: str) -> None:
    pass


def _sample(s: dict) -> Sample:
    return Sample(s["i"], s["lon"], s["lat"], s["d"], s["bearing"])


def refit(sample: Sample, rec: dict) -> dict:
    """Recompute a fixed image's offsets relative to a (possibly new) sample."""
    dist, lateral, along = local_offsets(sample, np.array([rec["lon"]]), np.array([rec["lat"]]))
    herr = 0.0 if rec["is_pano"] else float(angle_diff(rec["compass_angle"], sample.bearing))
    return {
        **rec,
        "distance_m": round(float(dist[0]), 2),
        "lateral_m": round(float(lateral[0]), 2),
        "along_m": round(sample.distance_m + float(along[0]), 2),
        "heading_error": round(herr, 2),
        "score": rec.get("score", 0.0),
    }


def override_map(plan: dict) -> dict[int, dict]:
    """Overrides are keyed by distance along the route so they survive a change of spacing."""
    out: dict[int, dict] = {}
    n, s = plan["n"], plan["spacing_m"]
    for ov in plan.get("overrides", []):
        if n == 0:
            break
        i = int(np.clip(round(ov["d"] / s - 0.5), 0, n - 1))
        out[i] = ov
    return out


def sample_status(plan: dict, i: int, ovs: dict[int, dict] | None = None) -> tuple[str, bool]:
    ovs = ovs if ovs is not None else override_map(plan)
    ov = ovs.get(i)
    if ov and ov["action"] == "exclude":
        return "excluded", False
    if ov and ov["action"] in ("swap", "lock"):
        return "overridden", True
    return ("chosen" if plan["samples"][i]["chosen"] else "gap"), False


def compute_stats(plan: dict) -> dict:
    samples = plan["samples"]
    n = plan["n"]
    kept = sum(1 for s in samples if s["chosen"])
    gaps, run, longest = 0, 0, 0
    for s in samples:
        if s["chosen"]:
            run = 0
        else:
            if run == 0:
                gaps += 1
            run += 1
            longest = max(longest, run)
    duration = plan["settings"]["duration_s"]
    return {
        "distance_m": round(plan["length_m"], 1),
        "spacing_m": round(plan["spacing_m"], 2),
        "planned": n,
        "kept": kept,
        "gaps": gaps,
        "longest_gap_m": round(longest * plan["spacing_m"], 1),
        "source_fps": round(kept / duration, 2) if duration else 0,
        "low_coverage_warning": kept < 0.5 * n,
        "can_render": kept >= MIN_RENDER_FRAMES,
    }


def samples_geojson(plan: dict) -> dict:
    ovs = override_map(plan)
    feats = []
    for s in plan["samples"]:
        status, locked = sample_status(plan, s["i"], ovs)
        feats.append(
            {
                "type": "Feature",
                "id": s["i"],
                "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                "properties": {"i": s["i"], "status": status, "locked": locked, "image_id": s["chosen"]["id"] if s["chosen"] else None},
            }
        )
    return {"type": "FeatureCollection", "features": feats}


class Pipeline:
    def __init__(self, cfg: Config, cache: Cache, http: CachedHttp):
        self.cfg = cfg
        self.cache = cache
        self.http = http
        self._indexes: OrderedDict[str, ImageIndex] = OrderedDict()

    # ---- coverage ----

    async def coverage(self, route: Route, pr: ProjectedRoute, progress: Progress = _noop) -> ImageIndex:
        if route.route_id in self._indexes:
            self._indexes.move_to_end(route.route_id)
            return self._indexes[route.route_id]
        idx = await fetch_coverage(
            pr.densify(10.0),
            http=self.http,
            cache=self.cache,
            ttl_s=self.cfg.tile_ttl_days * 86400,
            progress=lambda f, m: progress("coverage", f, m),
        )
        self._indexes[route.route_id] = idx
        while len(self._indexes) > 4:
            self._indexes.popitem(last=False)
        return idx

    def forget_coverage(self) -> None:
        self._indexes.clear()

    # ---- plan ----

    async def plan(self, route: Route, settings: Settings, *, plan_id: str, overrides: list[dict] | None = None, progress: Progress = _noop) -> dict:
        settings.validated()
        w = self.cfg.selection
        pr = ProjectedRoute(route.line)
        spacing, n = spacing_for(pr.length, settings.min_spacing_m, settings.frame_budget)
        progress("sample", 1.0, f"{n} samples every {spacing:.0f} m")
        samples = make_samples(pr, spacing, n)
        index = await self.coverage(route, pr, progress)
        progress("score", 0.0, f"scoring {len(index)} images")
        cands = await asyncio.to_thread(score_all, samples, index, w=w, settings=settings)
        plan = {
            "plan_id": plan_id,
            "route_id": route.route_id,
            "settings": settings.to_dict(),
            "length_m": pr.length,
            "spacing_m": spacing,
            "n": n,
            "overrides": list(overrides or []),
            "samples": [
                {"i": s.i, "lon": s.lon, "lat": s.lat, "d": s.distance_m, "bearing": s.bearing, "candidates": c, "chosen": None}
                for s, c in zip(samples, cands)
            ],
            "meta": {},
        }
        await self._select(plan, progress)
        return plan

    def _fixed_and_excluded(self, plan: dict) -> tuple[dict[int, dict], set[int]]:
        fixed, excluded = {}, set()
        for i, ov in override_map(plan).items():
            if ov["action"] == "exclude":
                excluded.add(i)
            elif ov.get("image"):
                fixed[i] = refit(_sample(plan["samples"][i]), ov["image"])
        return fixed, excluded

    async def _select(self, plan: dict, progress: Progress = _noop) -> None:
        """Viterbi + enrich, dropping rejected images (on foot, fisheye, portrait) and re-choosing."""
        w = self.cfg.selection
        fixed, excluded = self._fixed_and_excluded(plan)
        fixed_ids = {f["id"] for f in fixed.values()}
        cand_lists = [s["candidates"] for s in plan["samples"]]
        meta: dict[str, dict] = {}
        for rnd in range(ENRICH_ROUNDS):
            progress("choose", rnd / ENRICH_ROUNDS, "choosing frames")
            chosen = choose(cand_lists, fixed=fixed, excluded=excluded, w=w)
            ids = [c["id"] for c in chosen if c]
            progress("enrich", 0.0, f"metadata for {len(set(ids))} images")
            meta.update(await fetch_metadata(self.http, self.cache, [i for i in ids if i not in meta]))
            bad = {i for i in ids if i not in meta or reject_reason(meta[i])}
            bad -= fixed_ids
            if not bad:
                break
            cand_lists = [[c for c in cl if c["id"] not in bad] for cl in cand_lists]
        else:
            chosen = [None if c and c["id"] in bad else c for c in chosen]
        for s, cl, c in zip(plan["samples"], cand_lists, chosen):
            s["candidates"] = cl
            s["chosen"] = c
        plan["meta"] = {c["id"]: meta[c["id"]] for c in chosen if c and c["id"] in meta}

    # ---- overrides ----

    async def override(self, plan: dict, i: int, action: str, image_id: str | None = None) -> dict:
        if not 0 <= i < plan["n"]:
            raise PlanError("sample index out of range")
        s = plan["samples"][i]
        ovs = override_map(plan)
        plan["overrides"] = [ov for j, ov in ovs.items() if j != i]
        if action == "swap":
            rec = next((c for c in s["candidates"] if c["id"] == image_id), None)
            if rec is None:
                raise PlanError("image is not a candidate for this sample")
            meta = (await fetch_metadata(self.http, self.cache, [rec["id"]])).get(rec["id"])
            if meta is None:
                raise PlanError("image metadata unavailable")
            if reason := reject_reason(meta):
                raise PlanError(f"image rejected: {reason}")
            plan["meta"][rec["id"]] = meta
            plan["overrides"].append({"d": s["d"], "action": "swap", "image": rec})
            s["chosen"] = rec
        elif action == "lock":
            if not s["chosen"]:
                raise PlanError("nothing to lock: sample is a gap")
            plan["overrides"].append({"d": s["d"], "action": "lock", "image": s["chosen"]})
        elif action == "exclude":
            plan["overrides"].append({"d": s["d"], "action": "exclude", "image": None})
            s["chosen"] = None
        elif action in ("unlock", "reset"):
            # unlock keeps the current image but lets re-planning change it; reset also re-picks now.
            if action == "reset" or not s["chosen"]:
                s["chosen"] = await self._best_local(plan, i)
        else:
            raise PlanError(f"unknown action {action!r}")
        return plan

    async def _best_local(self, plan: dict, i: int) -> dict | None:
        """Best candidate for one sample given its current neighbours (no global re-run)."""
        w = self.cfg.selection
        samples = plan["samples"]
        prev = next((samples[j]["chosen"] for j in range(i - 1, -1, -1) if samples[j]["chosen"]), None)
        nxt = next((samples[j]["chosen"] for j in range(i + 1, plan["n"]) if samples[j]["chosen"]), None)
        best, best_cost = None, float("inf")
        meta = await fetch_metadata(self.http, self.cache, [c["id"] for c in samples[i]["candidates"]])
        for c in samples[i]["candidates"]:
            if c["id"] not in meta or reject_reason(meta[c["id"]]):
                continue
            cost = c["score"] + (transition_cost(prev, c, w) if prev else 0) + (transition_cost(c, nxt, w) if nxt else 0)
            if cost < best_cost:
                best, best_cost = c, cost
        if best:
            plan["meta"][best["id"]] = meta[best["id"]]
        return best

    # ---- render ----

    async def render(self, plan: dict, job_dir: Path, out_mp4: Path, progress: Progress = _noop, cancelled: Callable[[], bool] = lambda: False) -> dict:
        settings = Settings.from_dict(plan["settings"])
        kept = [s for s in plan["samples"] if s["chosen"]]
        if len(kept) < MIN_RENDER_FRAMES:
            raise PlanError(f"only {len(kept)} frames kept; at least {MIN_RENDER_FRAMES} are needed")
        ids = [s["chosen"]["id"] for s in kept]

        progress("enrich", 0.0, "image metadata")
        meta = {**plan.get("meta", {})}
        missing = [i for i in ids if i not in meta]
        if missing:
            meta.update(await fetch_metadata(self.http, self.cache, missing))
        progress("enrich", 1.0, "image metadata")

        paths = await download_images(self.http, self.cache, ids, size=2048, progress=lambda f, m: progress("download", f, m))
        if cancelled():
            raise PlanError("cancelled")

        frames_dir = job_dir / "frames"
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir(parents=True)
        written = await asyncio.to_thread(self._render_frames, kept, meta, paths, settings, plan["length_m"], frames_dir, progress, cancelled)
        if len(written) < 2:
            raise PlanError("too few frames could be rendered")

        await asyncio.to_thread(
            assemble,
            frames_dir,
            out_mp4,
            n_frames=len(written),
            duration_s=settings.duration_s,
            output_fps=settings.output_fps,
            blend=settings.blend,
            progress=lambda f, m: progress("assemble", f, m),
            cancelled=cancelled,
        )
        write_manifest(job_dir / "manifest.json", written)
        write_credits(job_dir / "credits.txt", [meta.get(f["image_id"], {}).get("creator") for f in written])
        shutil.rmtree(frames_dir, ignore_errors=True)
        return {"frames": len(written), "mp4": str(out_mp4)}

    def _render_frames(self, kept, meta, paths, settings: Settings, total_m, frames_dir: Path, progress: Progress, cancelled) -> list[dict]:
        floor = self.cfg.selection.min_luminance

        def geom(s: dict) -> Image.Image | None:
            c = s["chosen"]
            p = paths.get(c["id"])
            if p is None:
                return None
            m = meta.get(c["id"], {})
            pano = is_pano_entity(m) if m else c["is_pano"]
            compass = m.get("computed_compass_angle") if m.get("computed_compass_angle") is not None else c["compass_angle"]
            try:
                with Image.open(p) as im:
                    im.load()
                    return base_frame(im, is_pano=pano, route_bearing=s["bearing"], compass_angle=compass, width=settings.width, height=settings.height)
            except OSError:
                return None

        matcher = HistogramMatcher() if settings.normalize_colour else None
        written: list[dict] = []
        with ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1)) as pool:
            for k, (s, img) in enumerate(zip(kept, pool.map(geom, kept))):
                if cancelled():
                    raise PlanError("cancelled")
                progress("render", (k + 1) / len(kept), f"frame {k + 1}/{len(kept)}")
                if img is None or (floor and mean_luminance(img) < floor):
                    continue
                if matcher:
                    img = matcher(img)
                img = overlay(img, distance_m=s["d"], total_m=total_m)
                img.save(frames_dir / f"{len(written):06d}.jpg", "JPEG", quality=92)
                c = s["chosen"]
                written.append(
                    {"sample_index": s["i"], "image_id": c["id"], "lat": c["lat"], "lon": c["lon"], "distance_m": round(s["d"], 1), "captured_at": c["captured_at"]}
                )
        return written
