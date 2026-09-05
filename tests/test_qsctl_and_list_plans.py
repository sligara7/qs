"""``qsctl`` (operator command line over HTTP) and ``qs --list-plans`` (dec:open-port-config-and-cli)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from qs.cli.qsctl import main as qsctl
from qs.runtime.config import load_config
from qs.runtime.main import list_plans
from tests._live_server import PROFILE, live_server

API_KEY = "qsctl-test-key"


def run(url: str, *argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = qsctl(["--url", url, "--api-key", API_KEY, *argv])
    return code, capsys.readouterr().out


def test_qsctl_drives_the_queue(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with live_server(tmp_path, api_key=API_KEY) as (_, url):
        code, out = run(url, "status", capsys=capsys)
        assert code == 0 and "manager: idle" in out
        code, out = run(url, "plans", capsys=capsys)
        assert code == 0 and "count" in out.split()
        code, out = run(url, "devices", capsys=capsys)
        assert code == 0 and "motor" in out and "movable" in out

        code, out = run(url, "queue", "add", "count", '[["det"]]', "--kwargs", '{"num": 2}', capsys=capsys)
        assert code == 0 and out.startswith("ok")
        code, out = run(url, "queue", "get", capsys=capsys)
        assert code == 0 and 'count(["det"], num=2)' in out
        code, out = run(url, "queue", "start", capsys=capsys)
        assert code == 0
        import time

        for _ in range(200):
            code, out = run(url, "status", capsys=capsys)
            if "history: 1" in out and "engine: idle" in out:
                break
            time.sleep(0.1)
        code, out = run(url, "history", capsys=capsys)
        assert code == 0 and out.startswith("success  count")

        # Failures come back as exit code 1 with the server's message, not a traceback.
        code, out = run(url, "queue", "start", capsys=capsys)
        assert code == 1 and "Queue is empty" in out
        code, out = run(url, "--json", "experiment", capsys=capsys)
        assert code == 0 and out.strip().startswith("{")

    code = qsctl(["--url", "http://127.0.0.1:1", "status"])  # nothing listens there
    assert code == 1


def test_list_plans_prints_the_profile_without_serving(tmp_path: Path) -> None:
    config = load_config(
        overrides={"startup.startup_dir": str(PROFILE), "database.url": f"sqlite:///{tmp_path}/x.sqlite"}
    )
    out = io.StringIO()
    assert list_plans(config, out=out) == 0
    text = out.getvalue()
    assert "engine: adopted from profile" in text
    assert "motor" in text and "SynAxis" in text
    assert "count" in text and "scan" in text
