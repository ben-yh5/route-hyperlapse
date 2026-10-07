"""route -> plan -> override -> render on one short fixture route, no network."""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from . import fixture_data

SETTINGS = {"duration_s": 30, "min_spacing_m": 15, "width": 320, "height": 180, "output_fps": 30}


def sse_events(client, job_id):
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((ev, json.loads(line.split(":", 1)[1])))
    return events


def ffprobe_frames(path) -> tuple[int, str, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,r_frame_rate,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    return int(s["nb_read_frames"]), s["r_frame_rate"], s["width"], s["height"]


def plan_route(client):
    r = client.post("/api/route", json={"waypoints": fixture_data.build().waypoints})
    assert r.status_code == 200, r.text
    route = r.json()
    assert 3200 < route["distance_m"] < 3400
    return route


def test_full_flow(app, mocked, cfg):
    http = app.state.ctx.http
    with TestClient(app) as client:
        session_root = app.state.ctx.cache.root
        assert session_root.exists() and session_root.name.startswith("session-")

        cfgj = client.get("/api/config").json()
        assert cfgj["has_mapillary_token"] and cfgj["defaults"]["duration_s"] == 60 and cfgj["defaults"]["min_spacing_m"] == 20

        g = client.post("/api/geocode", json={"query": "47.6, -122.35"}).json()
        assert g["results"][0]["lat"] == pytest.approx(47.6)
        g2 = client.post("/api/geocode", json={"query": "fixture end"}).json()
        assert g2["results"][0]["name"].startswith("Fixture End")

        route = plan_route(client)
        r = client.post("/api/plan", json={"route_id": route["route_id"], "settings": SETTINGS})
        assert r.status_code == 200, r.text
        plan = r.json()
        pid = plan["plan_id"]
        stats = plan["stats"]
        n = stats["planned"]
        assert n in (219, 220) and stats["spacing_m"] == 15
        assert stats["gaps"] >= 1 and stats["longest_gap_m"] >= 200
        assert 150 <= stats["kept"] < stats["planned"] and stats["can_render"]
        assert len(plan["samples"]["features"]) == n

        frames = client.get(f"/api/plan/{pid}/frames", params={"offset": 0, "limit": 500}).json()
        assert frames["total"] == n
        fr = frames["frames"]
        chosen = [f for f in fr if f["image_id"]]
        # Acceptance: no chosen frame > 40 m from the route, < 5% facing > 35 deg off travel.
        from hyperlapse.core.select import local_offsets
        from hyperlapse.core.sampling import Sample, angle_diff
        import numpy as np
        imgs = fixture_data.build().images
        off = 0
        for f in chosen:
            s = Sample(f["i"], f["lon"], f["lat"], f["distance_m"], f["bearing"])
            d, lat_off, _ = local_offsets(s, np.array([f["image_lon"]]), np.array([f["image_lat"]]))
            assert d[0] <= 40
            assert imgs[f["image_id"]]["sequence_id"] != "seq-rev"  # wrong carriageway never wins
            if not f["is_pano"] and angle_diff(imgs[f["image_id"]]["compass_angle"], f["bearing"]) > 35:
                off += 1
        assert off / len(chosen) < 0.05
        # Rejected entities (on foot, portrait) are never chosen.
        assert not any(imgs[f["image_id"]]["on_foot"] for f in chosen)
        assert all(f["image_id"] != "3000001" for f in chosen)

        # Candidates + overrides.
        i = next(f["i"] for f in fr if f["image_id"] and f["i"] > 5)
        cands = client.get(f"/api/plan/{pid}/samples/{i}/candidates").json()["candidates"]
        assert 1 < len(cands) <= 5 and sum(c["chosen"] for c in cands) == 1
        alt = next(c for c in cands if not c["chosen"] and not imgs[c["image_id"]]["on_foot"])
        r = client.patch(f"/api/plan/{pid}/samples/{i}", json={"action": "swap", "image_id": alt["image_id"]})
        assert r.status_code == 200, r.text
        assert r.json()["frame"]["status"] == "overridden" and r.json()["frame"]["image_id"] == alt["image_id"]
        j = i + 3
        r = client.patch(f"/api/plan/{pid}/samples/{j}", json={"action": "exclude"})
        assert r.json()["frame"]["status"] == "excluded"
        kept_after = r.json()["stats"]["kept"]
        k = i + 6
        locked_img = fr[k]["image_id"]
        r = client.patch(f"/api/plan/{pid}/samples/{k}", json={"action": "lock"})
        assert r.json()["frame"]["locked"]

        # Overrides survive reload (GET) and a settings change (re-plan in place).
        again = client.get(f"/api/plan/{pid}").json()
        assert again["stats"]["kept"] == kept_after
        r = client.post("/api/plan", json={"route_id": route["route_id"], "settings": {**SETTINGS, "include_pano": False}, "plan_id": pid})
        assert r.json()["plan_id"] == pid
        fr2 = {f["i"]: f for f in client.get(f"/api/plan/{pid}/frames").json()["frames"]}
        assert fr2[i]["image_id"] == alt["image_id"] and fr2[i]["status"] == "overridden"
        assert fr2[j]["status"] == "excluded"
        assert fr2[k]["image_id"] == locked_img and fr2[k]["locked"]
        assert not any(f["is_pano"] for f in fr2.values() if f["image_id"] and f["status"] == "chosen")

        # Warm cache: re-plan makes zero tile or image requests.
        app.state.ctx.pipeline.forget_coverage()
        before = dict(http.requests)
        client.post("/api/plan", json={"route_id": route["route_id"], "settings": SETTINGS, "plan_id": pid})
        assert http.requests["tile"] == before.get("tile", 0)
        assert http.requests["image"] == before.get("image", 0)

        # Thumbnails through the image proxy.
        t = client.get(fr2[k]["thumb_url"])
        assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"

        # Render.
        job = client.post(f"/api/plan/{pid}/render").json()["job_id"]
        events = sse_events(client, job)
        kinds = [e for e, _ in events]
        assert kinds[-1] == "done", events[-3:]
        stages = {d["stage"] for e, d in events if e == "progress"}
        assert {"download", "render", "assemble"} <= stages
        assert client.get("/api/status").json()["undownloaded_render"]
        video = client.get(f"/api/jobs/{job}/video", params={"download": 1})
        assert video.status_code == 200 and "attachment" in video.headers["content-disposition"]
        assert not client.get("/api/status").json()["undownloaded_render"]
        mp4 = app.state.ctx.cache.dir("renders") / f"{job}.mp4"
        n, rate, w, h = ffprobe_frames(mp4)
        assert abs(n - 30 * 30) <= 1 and rate == "30/1" and (w, h) == (320, 180)
        manifest = client.get(f"/api/jobs/{job}/manifest.json").json()["frames"]
        assert len(manifest) >= 150 and {"sample_index", "image_id", "lat", "lon", "distance_m", "captured_at"} <= set(manifest[0])
        credits = client.get(f"/api/jobs/{job}/credits.txt").text
        assert "alice" in credits and "bob" in credits and "CC BY-SA 4.0" in credits

        # Warm re-render: zero tile or image requests.
        before = dict(http.requests)
        job2 = client.post(f"/api/plan/{pid}/render").json()["job_id"]
        assert sse_events(client, job2)[-1][0] == "done"
        assert http.requests["tile"] == before.get("tile", 0)
        assert http.requests["image"] == before.get("image", 0)

        # Clear cache drops size to near zero but keeps the plan; next plan re-fetches.
        size = client.get("/api/cache").json()
        assert size["total"] > 100_000
        cleared = client.post("/api/cache/clear").json()
        assert cleared["total"] - cleared["categories"]["plans"] == 0
        assert client.get(f"/api/plan/{pid}").status_code == 200
        before = dict(http.requests)
        r = client.post("/api/plan", json={"route_id": route["route_id"], "settings": SETTINGS, "plan_id": pid})
        assert r.status_code == 200 and http.requests["tile"] > before.get("tile", 0)

        assert client.post("/api/quit").json() == {"ok": True}
    # Lifespan shutdown ran the quit cleanup.
    assert not session_root.exists()


def test_gpx_route(app):
    pts = fixture_data.build().route_lonlat
    gpx = '<?xml version="1.0"?><gpx version="1.1" creator="t"><trk><trkseg>' + "".join(
        f'<trkpt lat="{la}" lon="{lo}"/>' for lo, la in pts[:10]
    ) + "</trkseg><trkseg>" + "".join(f'<trkpt lat="{la}" lon="{lo}"/>' for lo, la in pts[10:]) + "</trkseg></trk></gpx>"
    with TestClient(app) as client:
        r = client.post("/api/route/gpx", files={"file": ("t.gpx", gpx, "application/gpx+xml")})
        assert r.status_code == 200, r.text
        assert len(r.json()["geojson"]["geometry"]["coordinates"]) == len(pts)
        assert 3200 < r.json()["distance_m"] < 3400


def test_render_refused_below_150(app):
    with TestClient(app) as client:
        route = plan_route(client)
        plan = client.post("/api/plan", json={"route_id": route["route_id"], "settings": {**SETTINGS, "min_spacing_m": 30}}).json()
        assert plan["stats"]["kept"] < 150 and not plan["stats"]["can_render"]
        assert client.post(f"/api/plan/{plan['plan_id']}/render").status_code == 400


def test_startup_removes_leftover_sessions(cfg):
    from hyperlapse.core.cache import open_session

    cfg.cache_dir.mkdir(parents=True)
    stale = cfg.cache_dir / "session-123-dead"
    (stale / "images").mkdir(parents=True)
    root = open_session(cfg.cache_dir, True)
    assert not stale.exists() and root.name.startswith("session-")


def test_clear_on_quit_off_persists(cfg, mocked, tmp_path):
    from hyperlapse.core.http import CachedHttp
    from hyperlapse.server.app import create_app

    cfg.clear_cache_on_quit = False
    app = create_app(cfg, http=CachedHttp(mapillary_token="x"), web_dist=tmp_path / "none")
    with TestClient(app) as client:
        plan_route(client)
    assert any((cfg.cache_dir / "persistent" / "routes").iterdir())
