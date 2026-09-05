"""Logging an operator can read from journalctl (dec:open-logging-and-errors, options A, C, E, B)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qs.diagnostics import TracebackPolicy, configure_logging, summarize
from qs.engine import EngineHost, EventBus
from qs.errors import CATALOGUE, ErrorCode, render_markdown
from qs.persistence import InMemoryQueueRepository
from qs.queue import QueueItem, QueueService
from qs.registry import Registry
from qs.runtime.compose import build_application
from qs.runtime.config import load_config
from qs.sequencer import Sequencer
from qs.sources.ipython_profile import IPythonProfileSource

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "tests" / "profiles" / "minimal" / "startup"


def wait_for(predicate, timeout: float = 20.0) -> None:  # noqa: ANN001
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


def test_summarize_finds_the_root_cause_and_the_profile_frame(tmp_path: Path) -> None:
    profile = tmp_path / "startup"
    profile.mkdir()
    (profile / "00-engine.py").write_text("from bluesky.run_engine import RunEngine\nRE = RunEngine({})\n")
    (profile / "10-plans.py").write_text(
        "def helper():\n    raise TimeoutError('ca://XF:TEST{Det:1}HDF1:Capture')\n\n"
        "def broken_plan():\n    try:\n        helper()\n    except Exception as exc:\n"
        "        raise RuntimeError('wrapped by the plan') from exc\n    yield None\n"
    )
    ns = IPythonProfileSource(profile).load().namespace
    with pytest.raises(RuntimeError) as info:
        list(ns["broken_plan"]())
    summary = summarize(info.value)
    assert summary.root == "TimeoutError: ca://XF:TEST{Det:1}HDF1:Capture"
    assert summary.where == "10-plans.py:2 in helper"
    assert "Traceback" in summary.traceback


def test_traceback_policy_keeps_the_journal_to_one_line_per_fault() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(TracebackPolicy(keep_tracebacks=False))
    log = logging.getLogger("qs.test.policy")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        try:
            raise ValueError("bad PV name")
        except ValueError:
            log.exception("Run aborted")  # what bluesky does on every failed plan
    finally:
        log.removeHandler(handler)
    text = stream.getvalue()
    assert text.count("\n") == 1, text
    # the test module itself counts as "user code", so the frame is named too
    assert text.startswith("ERROR Run aborted — ValueError: bad PV name (at test_logging.py:")

    debug_stream = io.StringIO()
    configure_logging("DEBUG", stream=debug_stream)
    try:
        try:
            raise ValueError("bad PV name")
        except ValueError:
            logging.getLogger("qs.test.policy2").exception("Run aborted")
    finally:
        configure_logging("WARNING", stream=io.StringIO())  # leave the root quiet for other tests
    assert "Traceback (most recent call last)" in debug_stream.getvalue()


def test_failed_plan_logs_one_actionable_headline(caplog: pytest.LogCaptureFixture) -> None:
    events = EventBus()
    host = EngineHost(events=events)
    host.start()
    try:
        load = host.load_source(IPythonProfileSource(PROFILE))
        registry = Registry()
        registry.load_from(load)
        queue = QueueService(InMemoryQueueRepository(), registry)
        seq = Sequencer(host=host, queue=queue, registry=registry, events=events, poll_interval=0.05)
        seq.start_thread()
        queue.add(QueueItem(name="failing_plan"))
        with caplog.at_level(logging.INFO, logger="qs"):
            seq.queue_start()
            wait_for(lambda: len(queue.history()) == 1 and not seq.queue_running)
        seq.close()
    finally:
        host.shutdown()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "qs.sequencer.sequencer"]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    line = errors[0].getMessage()
    assert line.startswith("[QS-PLAN-FAIL] failing_plan ")
    assert "ValueError: this plan always fails" in line
    assert "(at 20-plans.py:21 in failing_plan)" in line
    assert line.endswith("; queue stopped, waiting for a human")
    assert errors[0].exc_info is None, "the traceback belongs at DEBUG, not in the headline"
    assert seq.last_error and seq.last_error.startswith("[QS-PLAN-FAIL]")
    infos = [r.getMessage() for r in caplog.records if r.name.startswith("qs.")]
    assert any(m.startswith("[queue] started") for m in infos)
    assert any(m.startswith("[engine] idle -> running") for m in infos)


def test_error_codes_reach_http_bodies_and_the_docs_match_the_catalogue(tmp_path: Path) -> None:
    config = load_config(
        env={"QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": "k"},
        overrides={
            "startup.startup_dir": str(PROFILE),
            "database.url": f"sqlite:///{tmp_path}/q.sqlite",
            "engine.capture_console": False,
        },
    )
    app = build_application(config, source=IPythonProfileSource(PROFILE))
    try:
        client = TestClient(app.app, headers={"Authorization": "ApiKey k"})
        body = client.post("/api/queue/start", json={}).json()
        assert (
            body["success"] is False and body["code"] == "QS-QUEUE-EMPTY" and body["msg"] == "Queue is empty."
        )
        body = client.post("/api/re/resume", json={}).json()
        assert body["success"] is False and body["code"] == "QS-ENGINE-BUSY"
        body = client.post("/api/lock", json={"lock_key": "k", "environment": True}).json()
        assert body["code"] == "QS-UNSUPPORTED"
        assert client.get("/api/status", headers={"Authorization": "ApiKey wrong"}).status_code == 401
    finally:
        app.close()

    assert (ROOT / "docs" / "errors.md").read_text() == render_markdown(), (
        "docs/errors.md is stale: run `python -m qs.errors > docs/errors.md`"
    )
    assert set(CATALOGUE) == set(ErrorCode), "every code needs a catalogue entry"
