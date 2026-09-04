"""ver:queueserver-api-client: bluesky-queueserver-api's HTTP client against a live qs.

This is the Python client blop (and most other agents) use to talk to a queue server. qs is
served by a real uvicorn server on a free port so the client's own HTTP code, auth header,
status polling and success/failure handling are exercised unchanged.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from bluesky_queueserver_api import BPlan
from bluesky_queueserver_api.comm_base import RequestFailedError
from bluesky_queueserver_api.http import REManagerAPI

from qs.runtime.compose import Application, build_application
from qs.runtime.config import load_config
from qs.sources.ipython_profile import IPythonProfileSource

PROFILE = Path(__file__).parent / "profiles" / "minimal" / "startup"
API_KEY = "client-test-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Application, str]]:
    tmp = tmp_path_factory.mktemp("client")
    config = load_config(
        env={"QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": API_KEY},
        overrides={"database.url": f"sqlite:///{tmp / 'qs.sqlite'}", "engine.capture_console": False},
    )
    app = build_application(config, source=IPythonProfileSource(PROFILE))
    port = _free_port()
    uv = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, name="uvicorn-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not uv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        uv.should_exit = True
        thread.join(10)
        app.close()


@pytest.fixture
def rm(server: tuple[Application, str]) -> Iterator[REManagerAPI]:
    _, uri = server
    client = REManagerAPI(http_server_uri=uri, timeout=10)
    client.set_authorization_key(api_key=API_KEY)
    client.queue_clear()
    client.history_clear()
    try:
        yield client
    finally:
        client.close()


def test_auth_is_required_and_the_apikey_header_works(server: tuple[Application, str]) -> None:
    _, uri = server
    anonymous = REManagerAPI(http_server_uri=uri, timeout=10)
    with pytest.raises(Exception):  # noqa: B017 - the client wraps a 401 in its own exception type
        anonymous.status()
    anonymous.close()
    client = REManagerAPI(http_server_uri=uri, timeout=10)
    client.set_authorization_key(api_key=API_KEY)
    status = client.status()
    assert status["manager_state"] == "idle" and status["worker_environment_exists"] is True
    assert status["re_state"] == "idle"
    client.close()


def test_environment_is_open_and_open_is_tolerated(rm: REManagerAPI) -> None:
    # blop's check_environment only needs worker_environment_exists; environment_open is a no-op.
    assert rm.status()["worker_environment_exists"] is True
    response = rm.environment_open()
    assert response["success"] is True
    rm.wait_for_idle(timeout=10)
    # Closing the environment is refused honestly; the client raises on success=false.
    with pytest.raises(RequestFailedError):
        rm.environment_close()


def test_plans_and_devices_allowed(rm: REManagerAPI) -> None:
    plans = rm.plans_allowed()["plans_allowed"]
    assert {"count", "scan", "slow_plan"} <= set(plans)
    assert plans["count"]["properties"]["is_generator"] is True
    devices = rm.devices_allowed()["devices_allowed"]
    assert devices["motor"]["is_movable"] and devices["det"]["is_readable"]
    assert rm.plans_existing()["plans_existing"].keys() == plans.keys()


def test_add_start_wait_for_idle_and_history(rm: REManagerAPI) -> None:
    a = rm.item_add(BPlan("count", ["det"], num=2))
    assert a["success"] and a["qsize"] == 1 and a["item"]["item_type"] == "plan"
    b = rm.item_add(BPlan("count", ["det"], num=1), pos="front")
    assert b["qsize"] == 2
    queue = rm.queue_get()
    assert [i["item_uid"] for i in queue["items"]] == [b["item"]["item_uid"], a["item"]["item_uid"]]
    assert rm.item_get(uid=a["item"]["item_uid"])["item"]["kwargs"] == {"num": 2}

    assert rm.status()["manager_state"] == "idle"
    rm.queue_start()
    rm.wait_for_idle(timeout=60)  # queueserver semantics: idle once the queue runs empty
    history = rm.history_get()["items"]
    assert [h["item_uid"] for h in history] == [b["item"]["item_uid"], a["item"]["item_uid"]]
    assert all(h["result"]["exit_status"] == "success" and len(h["result"]["run_uids"]) == 1 for h in history)
    assert rm.status()["items_in_queue"] == 0
    with pytest.raises(RequestFailedError, match="Queue is empty"):
        rm.queue_start()


def test_autostart_runs_items_as_blop_submits_them(rm: REManagerAPI) -> None:
    # blop's runner: queue_autostart(True), then item_add per suggestion, then listen for stops.
    rm.queue_autostart(True)
    assert rm.status()["queue_autostart_enabled"] is True
    rm.item_add(BPlan("count", ["det"], num=1))
    rm.wait_for_idle(timeout=60)
    rm.item_add(BPlan("count", ["det"], num=1))
    rm.wait_for_idle(timeout=60)
    assert len(rm.history_get()["items"]) == 2
    rm.queue_autostart(False)


def test_failure_stops_queue_and_client_sees_it(rm: REManagerAPI) -> None:
    rm.item_add(BPlan("failing_plan"))
    good = rm.item_add(BPlan("count", ["det"]))
    rm.queue_start()
    rm.wait_for_idle(timeout=60)
    history = rm.history_get()["items"]
    assert len(history) == 1 and history[0]["result"]["exit_status"] == "fail"
    assert "always fails" in history[0]["result"]["msg"]
    assert rm.queue_get()["items"][0]["item_uid"] == good["item"]["item_uid"]
    rm.queue_start()
    rm.wait_for_idle(timeout=60)
    assert rm.history_get()["items"][1]["result"]["exit_status"] == "success"


def test_execute_pause_resume_abort(rm: REManagerAPI) -> None:
    rm.item_execute(BPlan("scan", ["det"], "motor", -1, 1, 60))
    rm.wait_for_idle_or_running(timeout=20)
    deadline = time.monotonic() + 20
    while rm.status(reload=True)["re_state"] != "running" and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(0.5)
    runs = rm.re_runs(option="active")["run_list"]
    assert len(runs) == 1 and runs[0]["is_open"] is True and runs[0]["uid"]
    rm.re_pause(option="deferred")
    rm.wait_for_idle_or_paused(timeout=30)
    assert rm.status(reload=True)["manager_state"] == "paused"
    rm.re_resume()
    rm.wait_for_idle_or_running(timeout=20)
    rm.re_abort()
    rm.wait_for_idle(timeout=30)
    history = rm.history_get()["items"]
    assert history[-1]["result"]["exit_status"] == "abort"
    with pytest.raises(RequestFailedError):
        rm.re_resume()  # nothing to resume


def test_move_remove_clear_and_unsupported_groups(rm: REManagerAPI) -> None:
    a = rm.item_add(BPlan("count", ["det"]))["item"]["item_uid"]
    b = rm.item_add(BPlan("count", ["det"]))["item"]["item_uid"]
    rm.item_move(uid=a, pos_dest="back")
    assert [i["item_uid"] for i in rm.queue_get()["items"]] == [b, a]
    removed = rm.item_remove(uid=b)
    assert removed["item"]["item_uid"] == b and removed["qsize"] == 1
    rm.queue_clear()
    assert rm.status()["items_in_queue"] == 0
    assert rm.permissions_get()["success"]
    assert rm.lock_info()["lock_info"]["queue"] is False
    with pytest.raises(RequestFailedError):
        rm.lock(lock_key="k", environment=True)
    with pytest.raises(RequestFailedError):
        rm.function_execute(BPlan("count", ["det"]))
    assert rm.config_get()["config"]["http"]["port"] == 60610
