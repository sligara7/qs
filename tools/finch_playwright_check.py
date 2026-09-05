#!/usr/bin/env python3
"""Time-boxed experiment (dec:open-playwright-ui-tests, option B): drive finch's dev app with Playwright.

Starts qs on the minimal test profile at http://localhost:60610 (finch's default, key "test"),
starts finch's Vite dev server, opens the components page, clicks into the Queue Server
component and records what the browser did: requests to qs, console errors, a screenshot.

    FINCH_DIR=~/git_projects/finch .venv/bin/python tools/finch_playwright_check.py [out_dir]

Exits 0 if the page reached the Queue Server component and issued requests to qs within the
time box; 1 otherwise. Kept as a tool, not a test: finch is another team's app.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FINCH = Path(os.environ.get("FINCH_DIR", "~/git_projects/finch")).expanduser()
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
TIME_BOX = float(os.environ.get("TIME_BOX", "90"))


def wait_http(url: str, timeout: float) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    config = OUT / "qs-finch.yml"
    config.write_text(
        f"startup:\n  kind: ipython\n  startup_dir: {ROOT / 'tests/profiles/minimal/startup'}\n"
        f"database:\n  url: sqlite:///{OUT / 'qs-finch.sqlite'}\n"
        "http:\n  host: 127.0.0.1\n  port: 60610\n  allow_origins: ['http://localhost:5173', 'http://127.0.0.1:5173']\n"
        "engine:\n  stream_device_progress: true\n  capture_console: false\n"
    )
    env = {**os.environ, "QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": "test"}
    qs = subprocess.Popen(
        [sys.executable, "-m", "qs.runtime.main", "--config", str(config)],
        env=env,
        stdout=(OUT / "qs.log").open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    finch = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        cwd=FINCH,
        stdout=(OUT / "finch.log").open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log: dict[str, list[str]] = {"qs_requests": [], "console": [], "steps": []}
    ok = False
    try:
        if not wait_http("http://127.0.0.1:60610/", 60):
            log["steps"].append("qs did not start")
            return 1
        if not wait_http("http://127.0.0.1:5173/", 90):
            log["steps"].append("finch dev server did not start")
            return 1
        t0 = time.monotonic()
        with sync_playwright() as p:
            browser = None
            for kw in ({}, {"channel": "chrome"}):
                try:
                    browser = p.chromium.launch(headless=True, **kw)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if browser is None:
                log["steps"].append("no browser")
                return 1
            page = browser.new_page(viewport={"width": 1500, "height": 1400})
            page.set_default_timeout(TIME_BOX * 1000 / 3)

            def on_response(r):  # noqa: ANN001
                if ":60610" in r.url:
                    log["qs_requests"].append(f"{r.request.method} {r.url.split(':60610')[1]} -> {r.status}")

            def on_console(m):  # noqa: ANN001
                if m.type in ("error", "warning"):
                    log["console"].append(f"{m.type}: {m.text[:200]}")

            page.on("response", on_response)
            page.on("console", on_console)
            page.on("pageerror", lambda e: log["console"].append(f"pageerror: {str(e)[:200]}"))
            page.goto("http://127.0.0.1:5173/components", wait_until="domcontentloaded")
            log["steps"].append(f"components page loaded at {time.monotonic() - t0:.1f}s")
            tab = page.get_by_text("Real Beamline Devices")
            if tab.count():
                tab.first.click()
                log["steps"].append("clicked 'Real Beamline Devices'")
            row = page.get_by_text("Queue Server", exact=False)
            if row.count():
                row.first.click()
                log["steps"].append(f"clicked Queue Server row at {time.monotonic() - t0:.1f}s")
            page.wait_for_timeout(5000)
            page.screenshot(path=str(OUT / "finch-qserver.png"))
            buttons = page.locator("button").count()
            log["steps"].append(f"screenshot at {time.monotonic() - t0:.1f}s; buttons={buttons}")
            ok = any("/api/" in r for r in log["qs_requests"])
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log["steps"].append(f"exception: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        for proc in (finch, qs):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        (OUT / "finch-playwright-log.json").write_text(json.dumps(log, indent=1))
        print(json.dumps(log, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
