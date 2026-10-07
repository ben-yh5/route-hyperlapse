"""Candidate scoring and Viterbi selection for continuity."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

import numpy as np

from ..config import SelectionWeights
from .mapillary.tiles import EARTH_R, ImageIndex
from .sampling import Sample, angle_diff

YEAR_MS = 365.25 * 86400 * 1000
TIME_GAP_SCALE_MS = 90 * 86400 * 1000  # a season apart counts as a full time-gap penalty
SIDE_TOLERANCE_M = 4.0
SIDE_SCALE_M = 10.0
WRONG_DIRECTION_DEG = 120.0


def since_ms(captured_since: str | None) -> int | None:
    if not captured_since:
        return None
    d = date.fromisoformat(captured_since)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def local_offsets(sample: Sample, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Distance, signed lateral offset (right of travel positive) and along-track offset in metres."""
    dx = np.radians(lon - sample.lon) * EARTH_R * np.cos(np.radians(sample.lat))
    dy = np.radians(lat - sample.lat) * EARTH_R
    b = np.radians(sample.bearing)
    lateral = dx * np.cos(b) - dy * np.sin(b)
    along = dx * np.sin(b) + dy * np.cos(b)
    return np.hypot(dx, dy), lateral, along


def score_sample(
    sample: Sample,
    index: ImageIndex,
    *,
    w: SelectionWeights,
    include_pano: bool,
    since: int | None,
    drive_on_left: bool,
    banned: set[str] = frozenset(),
    now_ms: float | None = None,
) -> list[dict]:
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    for radius in (w.radius_m, w.retry_radius_m):
        j = index.query(sample.lon, sample.lat, radius)
        if len(j):
            cands = _score(sample, index, j, radius, w, include_pano, since, drive_on_left, banned, now_ms)
            if cands:
                return cands
    return []


def _score(sample, index, j, radius, w, include_pano, since, drive_on_left, banned, now_ms) -> list[dict]:
    dist, lateral, along = local_offsets(sample, index.lon[j], index.lat[j])
    pano = index.is_pano[j]
    herr = np.where(pano, 0.0, angle_diff(index.compass_angle[j], sample.bearing))
    keep = (dist <= radius) & (pano | (herr <= w.max_heading_error_deg))
    if not include_pano:
        keep &= ~pano
    if since is not None:
        keep &= index.captured_at[j] >= since
    if banned:
        keep &= ~np.isin(index.ids[j], list(banned))
    if not keep.any():
        return []
    j, dist, lateral, along, herr = j[keep], dist[keep], lateral[keep], along[keep], herr[keep]
    age_years = np.clip((now_ms - index.captured_at[j]) / YEAR_MS, 0, None)
    travel_side = -lateral if drive_on_left else lateral
    wrong_side = np.clip((-travel_side - SIDE_TOLERANCE_M) / SIDE_SCALE_M, 0, 1)
    motion = index.seq_motion[j]
    wrong_dir = np.where(np.isnan(motion), 0.0, (angle_diff(np.nan_to_num(motion), sample.bearing) > WRONG_DIRECTION_DEG).astype(float))
    score = (
        w.w_distance * dist / radius
        + w.w_heading * herr / w.max_heading_error_deg
        + w.w_quality * (1 - index.quality_score[j])
        + w.w_age * age_years / 10
        + w.w_wrong_side * wrong_side
        + w.w_wrong_direction * wrong_dir
    )
    order = np.argsort(score, kind="stable")[: max(w.pool_k, w.top_k)]
    out = []
    for k in order:
        rec = index.record(int(j[k]))
        rec.update(
            distance_m=round(float(dist[k]), 2),
            heading_error=round(float(herr[k]), 2),
            lateral_m=round(float(lateral[k]), 2),
            along_m=round(sample.distance_m + float(along[k]), 2),
            score=round(float(score[k]), 4),
        )
        out.append(rec)
    return out


def score_all(samples: list[Sample], index: ImageIndex, *, w: SelectionWeights, settings, banned: set[str] = frozenset()) -> list[list[dict]]:
    since = since_ms(settings.captured_since)
    now = time.time() * 1000
    return [
        score_sample(s, index, w=w, include_pano=settings.include_pano, since=since, drive_on_left=settings.drive_on_left, banned=banned, now_ms=now)
        for s in samples
    ]


def transition_cost(a: dict, b: dict, w: SelectionWeights) -> float:
    c = 0.0
    if a["id"] == b["id"]:
        c += w.t_duplicate
    if a["sequence_id"] != b["sequence_id"]:
        c += w.t_sequence_change
    if a["is_pano"] != b["is_pano"]:
        c += w.t_camera_change
    c += w.t_time_gap * min(1.0, abs(a["captured_at"] - b["captured_at"]) / TIME_GAP_SCALE_MS)
    if b["along_m"] < a["along_m"] - 1.0:
        c += w.t_backwards
    return c


def transition_matrix(prev: list[dict], cur: list[dict], w: SelectionWeights) -> np.ndarray:
    """transition_cost for every (prev, cur) pair, vectorised."""
    def col(states, key, dtype):
        return np.array([s[key] for s in states], dtype=dtype)
    a, b = {}, {}
    for key, dt in (("id", object), ("sequence_id", object), ("is_pano", bool), ("captured_at", np.float64), ("along_m", np.float64)):
        a[key], b[key] = col(prev, key, dt)[:, None], col(cur, key, dt)[None, :]
    return (
        w.t_duplicate * (a["id"] == b["id"])
        + w.t_sequence_change * (a["sequence_id"] != b["sequence_id"])
        + w.t_camera_change * (a["is_pano"] != b["is_pano"])
        + w.t_time_gap * np.minimum(1.0, np.abs(a["captured_at"] - b["captured_at"]) / TIME_GAP_SCALE_MS)
        + w.t_backwards * (b["along_m"] < a["along_m"] - 1.0)
    ).astype(float)


def viterbi(states: list[list[dict]], emissions: list[list[float]], w: SelectionWeights) -> list[int]:
    """Minimum-cost path through per-step candidate lists. Every step must have >= 1 state."""
    if not states:
        return []
    cost = np.asarray(emissions[0], dtype=float)
    back: list[np.ndarray] = []
    for t in range(1, len(states)):
        trans = transition_matrix(states[t - 1], states[t], w)
        total = cost[:, None] + trans
        arg = total.argmin(axis=0)
        back.append(arg)
        cost = total[arg, np.arange(len(states[t]))] + np.asarray(emissions[t], dtype=float)
    path = [int(cost.argmin())]
    for arg in reversed(back):
        path.append(int(arg[path[-1]]))
    return path[::-1]


def choose(
    candidates: list[list[dict]],
    *,
    fixed: dict[int, dict],
    excluded: set[int],
    w: SelectionWeights,
) -> list[dict | None]:
    """Pick one candidate per sample (None = gap). Fixed samples keep their image; excluded ones are removed."""
    n = len(candidates)
    active = [i for i in range(n) if i not in excluded and (i in fixed or candidates[i])]
    states = [[fixed[i]] if i in fixed else candidates[i] for i in active]
    emissions = [[0.0] if i in fixed else [c["score"] for c in candidates[i]] for i in active]
    path = viterbi(states, emissions, w)
    chosen: list[dict | None] = [None] * n
    for i, s, k in zip(active, states, path):
        chosen[i] = s[k]
    # The same image winning consecutive samples: keep the first.
    prev = None
    for i in range(n):
        c = chosen[i]
        if c is None:
            continue
        if prev is not None and prev["id"] == c["id"] and i not in fixed:
            chosen[i] = None
            continue
        prev = c
    return chosen
