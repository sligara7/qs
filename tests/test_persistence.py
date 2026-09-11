"""The Persistence box at its own boundary (``cmp:persistence``).

Persistence exists to implement two protocols the domain boxes declare —
``QueueRepository`` (``ifc:queue-repository``) and ``DeviceDefinitionRepository``
(``ifc:device-definition-repository``) — so the useful test is a CONTRACT test: run one
suite against every implementation and require them to agree. A difference between the SQL
and in-memory repositories is a defect whichever one is wrong, and until 2026-09-11 nothing
would have caught it: the in-memory one is what most tests run against and the SQL one is
what production runs against.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text

from qs.devices import DeviceDefinition, DeviceDefinitionRepository
from qs.persistence import (
    Database,
    InMemoryDeviceDefinitionRepository,
    InMemoryQueueRepository,
    SqlDeviceDefinitionRepository,
    SqlQueueRepository,
)
from qs.queue import HistoryEntry, ItemState, QueueItem, QueueRepository


@pytest.fixture
def database(tmp_path) -> Iterator[Database]:
    db = Database(f"sqlite:///{tmp_path / 'qs.sqlite'}")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture(params=["sql", "memory"])
def queue_repo(request: pytest.FixtureRequest, database: Database) -> QueueRepository:
    return SqlQueueRepository(database) if request.param == "sql" else InMemoryQueueRepository()


@pytest.fixture(params=["sql", "memory"])
def device_repo(request: pytest.FixtureRequest, database: Database) -> DeviceDefinitionRepository:
    if request.param == "sql":
        return SqlDeviceDefinitionRepository(database)
    return InMemoryDeviceDefinitionRepository()


def item(name: str = "count", **kwargs) -> QueueItem:
    return QueueItem(name=name, **kwargs)


# ---- QueueRepository contract ------------------------------------------------------


def test_both_queue_repositories_satisfy_the_protocol(queue_repo: QueueRepository) -> None:
    assert isinstance(queue_repo, QueueRepository)


def test_insert_appends_by_default_and_honours_an_index(queue_repo: QueueRepository) -> None:
    first, second, third = item("first"), item("second"), item("third")
    queue_repo.insert(first)
    queue_repo.insert(second)
    queue_repo.insert(third, 1)
    assert [i.name for i in queue_repo.list_items()] == ["first", "third", "second"]


def test_insert_past_the_end_lands_at_the_back(queue_repo: QueueRepository) -> None:
    queue_repo.insert(item("only"))
    queue_repo.insert(item("late"), 99)
    assert [i.name for i in queue_repo.list_items()] == ["only", "late"]


def test_a_duplicate_uid_is_refused(queue_repo: QueueRepository) -> None:
    first = item("first")
    queue_repo.insert(first)
    with pytest.raises(ValueError):
        queue_repo.insert(first)


def test_get_item_finds_by_uid_and_answers_none_when_absent(queue_repo: QueueRepository) -> None:
    one = item("one")
    queue_repo.insert(one)
    assert queue_repo.get_item(one.item_uid) is not None
    assert queue_repo.get_item("no-such-uid") is None


def test_replace_keeps_position_and_refuses_an_unknown_uid(queue_repo: QueueRepository) -> None:
    first, second = item("first"), item("second")
    queue_repo.insert(first)
    queue_repo.insert(second)
    queue_repo.replace(QueueItem(name="renamed", item_uid=first.item_uid))
    assert [i.name for i in queue_repo.list_items()] == ["renamed", "second"]
    with pytest.raises(KeyError):
        queue_repo.replace(QueueItem(name="ghost", item_uid="no-such-uid"))


def test_remove_returns_the_item_and_closes_the_gap(queue_repo: QueueRepository) -> None:
    first, second, third = item("first"), item("second"), item("third")
    for i in (first, second, third):
        queue_repo.insert(i)
    assert queue_repo.remove(second.item_uid).name == "second"
    assert [i.name for i in queue_repo.list_items()] == ["first", "third"]
    with pytest.raises(KeyError):
        queue_repo.remove("no-such-uid")


def test_move_reorders(queue_repo: QueueRepository) -> None:
    first, second, third = item("first"), item("second"), item("third")
    for i in (first, second, third):
        queue_repo.insert(i)
    queue_repo.move(third.item_uid, 0)
    assert [i.name for i in queue_repo.list_items()] == ["third", "first", "second"]


def test_pop_front_hands_out_the_head_once_then_none(queue_repo: QueueRepository) -> None:
    queue_repo.insert(item("first"))
    queue_repo.insert(item("second"))
    assert queue_repo.pop_front().name == "first"
    assert queue_repo.pop_front().name == "second"
    assert queue_repo.pop_front() is None


def test_clear_reports_how_many_it_removed(queue_repo: QueueRepository) -> None:
    for name in ("a", "b", "c"):
        queue_repo.insert(item(name))
    assert queue_repo.clear() == 3
    assert list(queue_repo.list_items()) == []
    assert queue_repo.clear() == 0


def test_history_appends_in_order_and_clears(queue_repo: QueueRepository) -> None:
    done = item("done")
    queue_repo.append_history(
        HistoryEntry(item=done, state=ItemState.COMPLETED, exit_status="success", run_uids=("abc",))
    )
    queue_repo.append_history(
        HistoryEntry(item=item("failed"), state=ItemState.FAILED, exit_status="fail", msg="boom")
    )
    history = list(queue_repo.list_history())
    assert [h.item.name for h in history] == ["done", "failed"]
    assert history[0].run_uids == ("abc",)
    assert history[1].msg == "boom"
    assert queue_repo.clear_history() == 2
    assert list(queue_repo.list_history()) == []


def test_an_items_payload_survives_a_round_trip(queue_repo: QueueRepository) -> None:
    """Args, kwargs and meta are JSON columns in SQL and plain objects in memory."""
    rich = QueueItem(
        name="scan",
        args=[["motor"], -1, 1, 11],
        kwargs={"md": {"purpose": "alignment"}, "per_step": None},
        meta={"note": "π"},
        user="operator",
        user_group="primary",
    )
    queue_repo.insert(rich)
    stored = queue_repo.get_item(rich.item_uid)
    assert stored.args == [["motor"], -1, 1, 11]
    assert stored.kwargs == {"md": {"purpose": "alignment"}, "per_step": None}
    assert stored.meta == {"note": "π"}
    assert (stored.user, stored.user_group) == ("operator", "primary")


# ---- DeviceDefinitionRepository contract --------------------------------------------


def definition(name: str = "motor", **kwargs) -> DeviceDefinition:
    kwargs.setdefault("class_path", "ophyd.sim.SynAxis")
    return DeviceDefinition(name=name, **kwargs)


def test_both_device_repositories_satisfy_the_protocol(device_repo: DeviceDefinitionRepository) -> None:
    assert isinstance(device_repo, DeviceDefinitionRepository)


def test_save_inserts_then_replaces_by_name(device_repo: DeviceDefinitionRepository) -> None:
    device_repo.save_definition(definition("m1"))
    device_repo.save_definition(definition("m1", prefix="XF:31ID-OP{Mir}"))
    assert len(device_repo.list_definitions()) == 1
    assert device_repo.get_definition("m1").prefix == "XF:31ID-OP{Mir}"


def test_get_answers_none_for_an_unknown_name(device_repo: DeviceDefinitionRepository) -> None:
    assert device_repo.get_definition("nobody") is None


def test_list_is_ordered_by_name(device_repo: DeviceDefinitionRepository) -> None:
    for name in ("zulu", "alpha", "mike"):
        device_repo.save_definition(definition(name))
    assert [d.name for d in device_repo.list_definitions()] == ["alpha", "mike", "zulu"]


def test_delete_reports_whether_anything_went(device_repo: DeviceDefinitionRepository) -> None:
    device_repo.save_definition(definition("m1"))
    assert device_repo.delete_definition("m1") is True
    assert device_repo.delete_definition("m1") is False


def test_kwargs_and_flags_survive_a_round_trip(device_repo: DeviceDefinitionRepository) -> None:
    device_repo.save_definition(definition("m1", kwargs={"delay": 0.01, "labels": ["motors"]}, enabled=False))
    stored = device_repo.get_definition("m1")
    assert stored.kwargs == {"delay": 0.01, "labels": ["motors"]}
    assert stored.enabled is False


# ---- Database itself -----------------------------------------------------------------


def test_a_session_commits_on_success_and_rolls_back_on_error(database: Database) -> None:
    repo = SqlQueueRepository(database)
    repo.insert(item("kept"))
    assert [i.name for i in repo.list_items()] == ["kept"]

    insert = text(
        "INSERT INTO queue_items (item_uid, position, item_type, name, args, kwargs, meta,"
        " user, user_group, created_at)"
        " VALUES ('rolled-back', 99, 'plan', 'discarded', '[]', '{}', '{}', '', '', 0.0)"
    )
    with pytest.raises(RuntimeError), database.session() as session:
        session.execute(insert)
        raise RuntimeError("the operator's plan failed mid-transaction")

    assert [i.name for i in repo.list_items()] == ["kept"]


def test_the_url_is_readable_back_and_sqlite_gets_its_pragmas(database: Database) -> None:
    assert database.url.startswith("sqlite:///")
    with database.session() as session:
        assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
