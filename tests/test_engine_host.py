"""ver:engine-thread-affinity and ver:device-progress-stream (engine side).

Loads a minimal IPython-style profile that creates its own RunEngine on the engine thread,
adopts it, and drives plans through it from that thread while control arrives from the
test (main) thread. Every subscribed callback must receive every document, only one
RunEngine may exist, and no thread-affinity or event-loop errors may occur.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from qs.engine import EngineEvent, EngineHost, EngineState, EventBus
from qs.engine.host import RE_WORKER_ACTIVE_ENV
from qs.registry import Registry
from qs.sources.ipython_profile import IPythonProfileSource

PROFILE = Path(__file__).parent / "profiles" / "minimal" / "startup"


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(interval)


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest.fixture
def host(events: EventBus) -> Iterator[EngineHost]:
    host = EngineHost(events=events, progress_enabled=True, progress_min_update_period=0.05)
    host.start()
    try:
        yield host
    finally:
        host.shutdown()


@pytest.fixture
def loaded(host: EngineHost) -> tuple[EngineHost, Registry]:
    result = host.load_source(IPythonProfileSource(PROFILE), timeout=60)
    registry = Registry()
    registry.load_from(result)
    return host, registry


class Recorder:
    def __init__(self, events: EventBus) -> None:
        self.events: list[EngineEvent] = []
        events.subscribe(self.events.append)

    def of(self, kind: str) -> list[EngineEvent]:
        return [e for e in self.events if e.kind == kind]


# ---- loading and adoption ------------------------------------------------------------


def test_profile_engine_is_adopted_and_born_on_engine_thread(host: EngineHost) -> None:
    result = host.load_source(IPythonProfileSource(PROFILE), timeout=60)
    ns = result.namespace
    assert host.engine is ns["RE"], "the profile's RunEngine must be the host's engine"
    assert host.engine_adopted is True
    assert host.engine_thread is not None
    assert ns["created_thread_ident"] == host.engine_thread.ident, (
        "engine must be created on the engine thread"
    )
    assert host.state is EngineState.IDLE
    # Only one RunEngine object exists in the profile namespace.
    from bluesky.run_engine import RunEngine

    assert sum(isinstance(v, RunEngine) for v in ns.values()) == 1


def test_profile_contents_are_collected(loaded: tuple[EngineHost, Registry]) -> None:
    host, registry = loaded
    assert {"motor", "det", "noisy_det"} <= set(registry.devices())
    assert {"count", "scan", "slow_plan", "failing_plan"} <= set(registry.plans())
    assert "not_a_plan" not in registry.plans()
    assert all(entry.origin == "profile" for entry in registry.devices().values())


def test_worker_active_flag_and_ipython_patching(loaded: tuple[EngineHost, Registry]) -> None:
    host, _ = loaded
    assert os.environ.get(RE_WORKER_ACTIVE_ENV) == "1", "profiles' is_re_worker_active() must be true"
    ns = host.load_result.namespace
    assert ns["ipython_marker"] == "set-by-profile", "get_ipython().user_ns must be the profile namespace"


# ---- running plans -------------------------------------------------------------------


def test_plan_runs_on_engine_thread_and_documents_reach_subscribers(
    loaded: tuple[EngineHost, Registry], events: EventBus
) -> None:
    host, registry = loaded
    rec = Recorder(events)
    ns = host.load_result.namespace
    ns["docs"].clear()
    factory_thread: list[int] = []

    plan = registry.resolve("count", [["det"]], {"num": 3})

    def factory():
        factory_thread.append(threading.get_ident())
        return plan()

    outcome = host.run_plan(factory, item_uid="item-1").result(timeout=30)
    assert outcome.succeeded, outcome
    assert len(outcome.run_uids) == 1
    assert factory_thread == [host.engine_thread.ident]
    names = [n for n, _ in ns["docs"]]
    assert names.count("start") == 1 and names.count("stop") == 1 and names.count("event") == 3
    assert [e.payload["item_uid"] for e in rec.of("plan_started")] == ["item-1"]
    assert rec.of("plan_finished")[0].payload["outcome"].succeeded
    assert host.state is EngineState.IDLE


def test_pause_from_another_thread_then_resume(loaded: tuple[EngineHost, Registry]) -> None:
    host, registry = loaded
    fut = host.run_plan(registry.resolve("slow_plan", ["motor"], {"n": 4, "dwell": 0.3}), item_uid="item-2")
    wait_for(lambda: host.state is EngineState.RUNNING)
    time.sleep(0.2)
    host.request_pause(defer=False)
    wait_for(lambda: host.state is EngineState.PAUSED)
    assert host.re_state == "paused"
    assert not fut.done()
    host.resume()
    outcome = fut.result(timeout=30)
    assert outcome.succeeded, outcome
    assert host.state is EngineState.IDLE


def test_pause_then_abort_marks_outcome_abort(loaded: tuple[EngineHost, Registry]) -> None:
    host, registry = loaded
    fut = host.run_plan(registry.resolve("slow_plan", ["motor"], {"n": 10, "dwell": 0.3}), item_uid="item-3")
    wait_for(lambda: host.state is EngineState.RUNNING)
    time.sleep(0.2)
    host.request_pause()
    wait_for(lambda: host.state is EngineState.PAUSED)
    host.abort(reason="operator abort")
    outcome = fut.result(timeout=30)
    assert outcome.exit_status == "abort", outcome
    assert host.state is EngineState.IDLE
    assert host.re_state == "idle"


def test_abort_while_running_from_another_thread(loaded: tuple[EngineHost, Registry]) -> None:
    host, registry = loaded
    fut = host.run_plan(registry.resolve("slow_plan", ["motor"], {"n": 10, "dwell": 0.3}), item_uid="item-4")
    wait_for(lambda: host.state is EngineState.RUNNING)
    time.sleep(0.2)
    host.abort(reason="mid-run abort")
    outcome = fut.result(timeout=30)
    assert outcome.exit_status == "abort", outcome
    assert host.state is EngineState.IDLE


def test_failing_plan_is_an_outcome_not_a_crash(loaded: tuple[EngineHost, Registry]) -> None:
    host, registry = loaded
    outcome = host.run_plan(registry.resolve("failing_plan"), item_uid="item-5").result(timeout=30)
    assert outcome.exit_status == "fail"
    assert "this plan always fails" in outcome.exception
    assert host.last_error and "always fails" in host.last_error
    assert host.state is EngineState.IDLE
    # The engine is usable afterwards.
    again = host.run_plan(registry.resolve("count", [["det"]]), item_uid="item-6").result(timeout=30)
    assert again.succeeded


def test_subscriber_fault_does_not_touch_the_plan(
    loaded: tuple[EngineHost, Registry], events: EventBus
) -> None:
    host, registry = loaded

    def bad_subscriber(event: EngineEvent) -> None:
        raise RuntimeError("API-side fault")

    events.subscribe(bad_subscriber)
    outcome = host.run_plan(registry.resolve("count", [["det"]], {"num": 2}), item_uid="item-7").result(
        timeout=30
    )
    assert outcome.succeeded


def test_run_refused_while_busy(loaded: tuple[EngineHost, Registry]) -> None:
    host, registry = loaded
    fut = host.run_plan(registry.resolve("slow_plan", ["motor"], {"n": 3, "dwell": 0.3}), item_uid="item-8")
    wait_for(lambda: host.state is EngineState.RUNNING)
    with pytest.raises(Exception, match="cannot start a plan"):
        host.run_plan(registry.resolve("count", [["det"]]), item_uid="item-9")
    assert fut.result(timeout=30).succeeded


# ---- device progress (waiting hook) ---------------------------------------------------


def test_device_progress_streams_and_profile_hook_is_chained(
    loaded: tuple[EngineHost, Registry], events: EventBus
) -> None:
    host, registry = loaded
    rec = Recorder(events)
    ns = host.load_result.namespace
    ns["hook_calls"].clear()
    outcome = host.run_plan(
        registry.resolve("slow_plan", ["motor"], {"n": 2, "dwell": 0.05}), item_uid="item-10"
    ).result(timeout=30)
    assert outcome.succeeded
    progress = [e.payload for e in rec.of("device_progress")]
    assert progress, "expected device_progress events during a motor move"
    completed = [p for p in progress if p.get("completed")]
    updates = [p for p in progress if not p.get("completed")]
    assert completed, "a completed message must follow each wait"
    assert updates, "expected at least one watcher update"
    required = {
        "name",
        "current",
        "initial",
        "target",
        "unit",
        "precision",
        "fraction",
        "time_elapsed",
        "time_remaining",
        "done",
    }
    assert all(required <= set(p) for p in updates)
    assert any(p["done"] for p in updates), "the final update for a status must say done"
    # The profile's own waiting hook kept receiving calls (both with statuses and with None).
    assert True in ns["hook_calls"] and False in ns["hook_calls"]


# ---- registry resolution ---------------------------------------------------------------


def test_registry_resolves_device_names_and_dotted_components(loaded: tuple[EngineHost, Registry]) -> None:
    _, registry = loaded
    motor = registry.get_device("motor")
    assert registry.get_device("motor.readback") is motor.readback
    factory = registry.resolve("scan", [["det"], "motor", -1, 1, 3])
    gen = factory()
    assert hasattr(gen, "send")
    gen.close()
