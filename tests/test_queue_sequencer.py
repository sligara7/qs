"""Queue service, SQL persistence, and the Sequencer's start/stop/autostart and stop-and-wait."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from qs.engine import EngineHost, EngineState, EventBus
from qs.persistence import Database, InMemoryQueueRepository, SqlQueueRepository
from qs.queue import ItemState, QueueError, QueueItem, QueueService
from qs.registry import Registry
from qs.sequencer import Sequencer, SequencerError
from qs.sources.ipython_profile import IPythonProfileSource

PROFILE = Path(__file__).parent / "profiles" / "minimal" / "startup"


def wait_for(predicate, timeout: float = 15.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(interval)


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path: Path):
    if request.param == "memory":
        yield InMemoryQueueRepository()
    else:
        db = Database(f"sqlite:///{tmp_path / 'qs.sqlite'}")
        db.create_all()
        yield SqlQueueRepository(db)
        db.dispose()


@pytest.fixture
def registry_only() -> Registry:
    """A registry with plan names but no engine, for pure queue-service tests."""
    reg = Registry()
    from qs.sources.protocol import LoadResult

    reg.load_from(
        LoadResult(devices={}, plans={"count": lambda *a, **k: iter(()), "scan": lambda *a, **k: iter(())})
    )
    return reg


# ---- queue service over both repositories --------------------------------------------


def test_add_get_move_remove_and_positions(repository, registry_only: Registry) -> None:
    q = QueueService(repository, registry_only)
    a, ia = q.add(QueueItem(name="count"))
    b, ib = q.add(QueueItem(name="count"))
    c, ic = q.add(QueueItem(name="scan"), pos="front")
    assert (ia, ib, ic) == (0, 1, 0)
    assert [i.item_uid for i in q.items()] == [c.item_uid, a.item_uid, b.item_uid]
    d, idx = q.add(QueueItem(name="count"), after_uid=a.item_uid)
    assert idx == 2
    assert q.move(c.item_uid, pos="back") == 3
    assert q.move(b.item_uid, before_uid=a.item_uid) == 0
    assert [i.item_uid for i in q.items()] == [b.item_uid, a.item_uid, d.item_uid, c.item_uid]
    q.remove(a.item_uid)
    assert len(q) == 3
    with pytest.raises(QueueError):
        q.remove(a.item_uid)
    assert q.get(b.item_uid).name == "count"
    rev = q.revision
    assert q.clear() == 3 and q.revision > rev


def test_validation_refuses_unknown_plan_and_non_plan_items(repository, registry_only: Registry) -> None:
    q = QueueService(repository, registry_only)
    with pytest.raises(QueueError, match="Unknown plan"):
        q.add(QueueItem(name="nope"))
    with pytest.raises(QueueError, match="Bluesky plans only"):
        q.add(QueueItem(name="count", item_type="function"))
    assert len(q) == 0


def test_update_keeps_position_and_can_replace_uid(repository, registry_only: Registry) -> None:
    q = QueueService(repository, registry_only)
    a, _ = q.add(QueueItem(name="count"))
    b, _ = q.add(QueueItem(name="count"))
    updated = q.update(QueueItem(name="scan", item_uid=a.item_uid, kwargs={"num": 2}))
    assert updated.item_uid == a.item_uid and q.items()[0].name == "scan"
    replaced = q.update(QueueItem(name="scan", item_uid=a.item_uid), replace_uid=True)
    assert replaced.item_uid != a.item_uid and q.items()[0].item_uid == replaced.item_uid
    assert q.items()[1].item_uid == b.item_uid


def test_item_dict_round_trip() -> None:
    item = QueueItem.from_dict(
        {"name": "count", "args": [["det"]], "kwargs": {"num": 2}, "meta": {"purpose": "t"}},
        user="u",
        user_group="g",
    )
    d = item.to_dict()
    assert d["item_type"] == "plan" and d["user"] == "u" and d["user_group"] == "g" and d["item_uid"]
    assert QueueItem.from_dict(d).to_dict() == d


# ---- sequencer with the real engine ---------------------------------------------------


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[tuple[EngineHost, QueueService, Sequencer, EventBus]]:
    events = EventBus()
    host = EngineHost(events=events)
    host.start()
    result = host.load_source(IPythonProfileSource(PROFILE), timeout=60)
    registry = Registry()
    registry.load_from(result)
    db = Database(f"sqlite:///{tmp_path / 'qs.sqlite'}")
    db.create_all()
    queue = QueueService(SqlQueueRepository(db), registry)
    seq = Sequencer(host=host, queue=queue, registry=registry, events=events, poll_interval=0.05)
    seq.start_thread()
    try:
        yield host, queue, seq, events
    finally:
        seq.close()
        host.shutdown()
        db.dispose()


def test_items_do_not_run_until_queue_started_then_run_in_order(stack) -> None:
    host, queue, seq, _ = stack
    a, _ = queue.add(QueueItem(name="count", args=[["det"]], kwargs={"num": 1}))
    b, _ = queue.add(QueueItem(name="count", args=[["det"]], kwargs={"num": 2}))
    time.sleep(0.3)
    assert len(queue) == 2 and not seq.queue_running and host.state is EngineState.IDLE
    seq.queue_start()
    wait_for(lambda: len(queue.history()) == 2)
    hist = queue.history()
    assert [h.item.item_uid for h in hist] == [a.item_uid, b.item_uid]
    assert all(h.state is ItemState.COMPLETED and h.exit_status == "success" for h in hist)
    assert all(len(h.run_uids) == 1 and h.time_stop >= h.time_start for h in hist)
    assert len(queue) == 0
    assert seq.queue_running, "an empty started queue stays started, as in queueserver"


def test_failure_stops_the_queue_and_waits_for_a_human(stack) -> None:
    host, queue, seq, events = stack
    bad, _ = queue.add(QueueItem(name="failing_plan"))
    good, _ = queue.add(QueueItem(name="count", args=[["det"]]))
    seq.queue_start()
    wait_for(lambda: len(queue.history()) == 1)
    wait_for(lambda: not seq.queue_running)
    entry = queue.history()[0]
    assert entry.item.item_uid == bad.item_uid and entry.state is ItemState.FAILED
    assert "always fails" in entry.msg and entry.traceback
    assert seq.last_error and "always fails" in seq.last_error
    time.sleep(0.4)
    assert len(queue) == 1 and queue.items()[0].item_uid == good.item_uid, "the next item must not run"
    assert host.state is EngineState.IDLE
    # A human starts the queue again; the remaining item runs.
    seq.queue_start()
    wait_for(lambda: len(queue.history()) == 2)
    assert queue.history()[1].state is ItemState.COMPLETED


def test_stop_finishes_current_item_then_stops_and_cancel_withdraws(stack) -> None:
    host, queue, seq, _ = stack
    queue.add(QueueItem(name="slow_plan", args=["motor"], kwargs={"n": 3, "dwell": 0.3}))
    queue.add(QueueItem(name="count", args=[["det"]]))
    seq.queue_start()
    wait_for(lambda: seq.running_item is not None)
    seq.queue_stop()
    assert seq.stop_pending
    seq.queue_stop_cancel()
    assert not seq.stop_pending
    seq.queue_stop()
    wait_for(lambda: len(queue.history()) == 1)
    wait_for(lambda: not seq.queue_running)
    assert queue.history()[0].state is ItemState.COMPLETED
    time.sleep(0.3)
    assert len(queue) == 1, "the second item waits for the next start"
    with pytest.raises(SequencerError):
        seq.queue_stop()


def test_abort_of_running_item_stops_queue(stack) -> None:
    host, queue, seq, _ = stack
    queue.add(QueueItem(name="slow_plan", args=["motor"], kwargs={"n": 10, "dwell": 0.3}))
    queue.add(QueueItem(name="count", args=[["det"]]))
    seq.queue_start()
    wait_for(lambda: host.state is EngineState.RUNNING)
    time.sleep(0.2)
    host.abort("operator")
    wait_for(lambda: len(queue.history()) == 1)
    wait_for(lambda: not seq.queue_running)
    assert queue.history()[0].state is ItemState.ABORTED
    assert len(queue) == 1


def test_autostart_runs_items_as_they_arrive(stack) -> None:
    host, queue, seq, _ = stack
    seq.set_autostart(True)
    queue.add(QueueItem(name="count", args=[["det"]]))
    wait_for(lambda: len(queue.history()) == 1)
    queue.add(QueueItem(name="count", args=[["det"]]))
    wait_for(lambda: len(queue.history()) == 2)
    assert all(h.state is ItemState.COMPLETED for h in queue.history())


def test_execute_now_runs_item_at_front(stack) -> None:
    host, queue, seq, _ = stack
    waiting, _ = queue.add(QueueItem(name="count", args=[["det"]]))
    seq.execute_now(QueueItem(name="count", args=[["det"]], kwargs={"num": 2}))
    wait_for(lambda: len(queue.history()) >= 1)
    assert queue.history()[0].item.kwargs == {"num": 2}
    wait_for(lambda: len(queue.history()) == 2)
    assert queue.history()[1].item.item_uid == waiting.item_uid


def test_queue_persists_across_service_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'persist.sqlite'}"
    reg = Registry()
    from qs.sources.protocol import LoadResult

    reg.load_from(LoadResult(devices={}, plans={"count": lambda *a, **k: iter(())}))
    db = Database(url)
    db.create_all()
    q = QueueService(SqlQueueRepository(db), reg)
    a, _ = q.add(QueueItem(name="count", kwargs={"num": 5}))
    db.dispose()

    db2 = Database(url)
    db2.create_all()
    q2 = QueueService(SqlQueueRepository(db2), reg)
    assert [i.item_uid for i in q2.items()] == [a.item_uid]
    assert q2.items()[0].kwargs == {"num": 5}
    db2.dispose()
