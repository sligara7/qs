"""Browser test of qs's smoke page (dec:open-playwright-ui-tests, option A).

Drives the page a person would use to check a deployment: enter the API key, connect, add a
plan, start the queue, and watch status, history and device progress update over the
websockets. Skipped when Playwright or a Chromium-family browser is unavailable.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from tests._live_server import live_server  # noqa: E402

API_KEY = "smoke-test-key"
pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def browser() -> Iterator[playwright.Browser]:
    with playwright.sync_playwright() as p:
        b = None
        for launch_kwargs in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
            try:
                b = p.chromium.launch(headless=True, **launch_kwargs)
                break
            except Exception:  # noqa: BLE001 - try the next browser
                continue
        if b is None:
            pytest.skip("no Chromium-family browser available for Playwright")
        yield b
        b.close()


def test_smoke_page_runs_a_plan(browser: playwright.Browser, tmp_path: Path) -> None:
    with live_server(tmp_path, api_key=API_KEY, **{"engine.stream_device_progress": True}) as (_, url):
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/", wait_until="domcontentloaded")
        assert "qs smoke page" in page.title()

        page.fill("#key", API_KEY)
        page.click("text=Connect")
        page.wait_for_function("document.getElementById('conn').textContent.includes('connected')")
        page.wait_for_function("document.getElementById('plan').options.length > 0")

        # A scan moves the sim motor, so the info websocket carries device_progress messages.
        page.select_option("#plan", "scan")
        page.fill("#args", json.dumps([["det"], "motor", -1, 1, 5]))
        page.fill("#kwargs", "{}")
        page.click("text=Add")
        page.wait_for_function("document.getElementById('queue').textContent.includes('scan')")

        page.click("button:has-text('Start')")
        page.wait_for_function(
            "document.getElementById('history').textContent.includes('success')", timeout=60_000
        )
        assert "scan" in page.inner_text("#history")
        status = json.loads(page.inner_text("#status"))
        assert status["items_in_queue"] == 0 and status["re_state"] == "idle"
        assert page.inner_text("#progress") != "—", "device progress never arrived on the info websocket"
        assert page.inner_text("#log") == ""
        assert errors == [], errors
        page.close()
