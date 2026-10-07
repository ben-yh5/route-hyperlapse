"""Playwright smoke test against the fixture backend: set waypoints, plan, swap a frame, render.

Needs `web/dist` built and `uv run playwright install chromium`.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests import fixture_data

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (ROOT / "web" / "dist" / "index.html").exists(), reason="frontend not built")
playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    cache = tmp_path / "cache"
    proc = subprocess.Popen([sys.executable, "-m", "tests.fixture_server", "--port", str(port), "--cache", str(cache)], cwd=ROOT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/api/config", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("fixture server did not start")
    yield url, cache
    proc.terminate()
    proc.wait(10)


def _set_waypoint(page, testid: str, lat: float, lon: float) -> None:
    box = page.get_by_test_id(testid)
    box.fill(f"{lat},{lon}")
    option = page.get_by_test_id(f"{testid}-option-0")
    option.wait_for(timeout=5000)
    option.click()


def test_smoke(server):
    url, cache = server
    wp = fixture_data.build().waypoints
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)

        _set_waypoint(page, "start-input", wp[0]["lat"], wp[0]["lon"])
        _set_waypoint(page, "end-input", wp[1]["lat"], wp[1]["lon"])

        # Short, small render so the smoke test stays quick.
        page.get_by_test_id("plan-button").wait_for()
        page.get_by_test_id("duration-input").fill("30")
        page.get_by_test_id("plan-button").click()
        chosen = page.locator("[data-testid^=filmstrip-item-][data-status=chosen]")
        chosen.nth(3).wait_for(timeout=30_000)
        # The strip shows at most 50 equally spaced frames.
        assert page.locator("[data-testid^=filmstrip-item-]").count() <= 50
        stats = page.get_by_test_id("stats").inner_text()
        assert "Kept" in stats or "kept" in stats

        item = chosen.nth(3).get_attribute("data-testid")
        page.get_by_test_id(item).click()
        page.get_by_test_id("inspector").wait_for()
        page.get_by_test_id("candidate-1").click()
        page.get_by_test_id("swap-button").click()
        page.locator(f"[data-testid={item}][data-status=overridden]").wait_for(timeout=10_000)

        page.get_by_test_id("render-button").click()
        page.get_by_test_id("render-progress").wait_for(timeout=10_000)
        page.get_by_test_id("render-video").wait_for(timeout=120_000)
        href = page.get_by_test_id("download-link").get_attribute("href")
        assert href and "/video" in href
        assert not errors, errors
        browser.close()
