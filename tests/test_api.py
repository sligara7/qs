"""ver:http-api-contract, ver:finch-openapi-compat, ver:device-progress-stream (websocket leg).

Drives the FastAPI app with a real RunEngine on the engine thread, simulated devices, and a
SQLite queue. No IOCs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qs.runtime.compose import Application, build_application
from qs.runtime.config import load_config
from qs.sources.ipython_profile import IPythonProfileSource

PROFILE = Path(__file__).parent / "profiles" / "minimal" / "startup"
FINCH_OPS = json.loads((Path(__file__).parent / "data" / "finch_httpserver_operations.json").read_text())
API_KEY = "test-key-0123456789"


def wait_for(predicate, timeout: float = 15.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(interval)


@pytest.fixture(scope="module")
def application(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Application]:
    tmp = tmp_path_factory.mktemp("api")
    config = load_config(
        env={"QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": API_KEY, "QS_STREAM_DEVICE_PROGRESS": "1"},
        overrides={"database.url": f"sqlite:///{tmp / 'qs.sqlite'}", "engine.capture_console": True},
    )
    app = build_application(config, source=IPythonProfileSource(PROFILE))
    try:
        yield app
    finally:
        app.close()


@pytest.fixture
def client(application: Application) -> Iterator[TestClient]:
    with TestClient(application.app, headers={"Authorization": f"Apikey {API_KEY}"}) as c:
        # Start every test from an empty, stopped queue and an idle engine.
        c.post("/api/queue/clear")
        c.post("/api/history/clear")
        yield c


def status(client: TestClient) -> dict:
    return client.get("/api/status").json()


# ---- authentication ----------------------------------------------------------------


def test_auth_header_query_and_refusal(application: Application) -> None:
    with TestClient(application.app) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", headers={"Authorization": "Apikey wrong"}).status_code == 401
        assert c.get("/api/status", headers={"Authorization": f"Apikey {API_KEY}"}).status_code == 200
        assert c.get("/api/status", headers={"Authorization": f"ApiKey {API_KEY}"}).status_code == 200
        assert c.get("/api/status", headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200
        assert c.get(f"/api/status?api_key={API_KEY}").status_code == 200
        assert c.get("/api/ping").status_code == 200, "ping needs no credential"
        who = c.get("/api/auth/whoami", headers={"Authorization": f"Apikey {API_KEY}"}).json()
        assert who["uuid"] == "single_user" and who["api_keys"][0]["first_eight"] == API_KEY[:8]
        scopes = c.get("/api/auth/scopes", headers={"Authorization": f"Apikey {API_KEY}"}).json()["scopes"]
        assert "write:queue" in scopes


# ---- contract coverage -----------------------------------------------------------------


def test_every_finch_operation_exists(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    served = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops if m.upper() != "HEAD"}
    missing = [
        (o["method"], o["path"]) for o in FINCH_OPS["operations"] if (o["method"], o["path"]) not in served
    ]
    assert not missing, f"routes finch's client calls but qs does not serve: {missing}"


def test_status_document_has_finch_fields(client: TestClient) -> None:
    s = status(client)
    for key in (
        "msg", "items_in_queue", "items_in_history", "running_item_uid", "manager_state",
        "queue_stop_pending", "queue_autostart_enabled", "worker_environment_exists",
        "worker_environment_state", "worker_background_tasks", "re_state", "ip_kernel_state",
        "ip_kernel_captured", "pause_pending", "run_list_uid", "plan_queue_uid", "plan_history_uid",
        "devices_existing_uid", "plans_existing_uid", "devices_allowed_uid", "plans_allowed_uid",
        "plan_queue_mode", "task_results_uid", "lock_info_uid", "lock",
    ):  # fmt: skip
        assert key in s, key
    assert s["manager_state"] == "idle" and s["re_state"] == "idle" and s["worker_environment_exists"] is True
    assert s["plan_queue_mode"] == {"loop": False, "ignore_failures": False}
    assert s["lock"] == {"environment": False, "queue": False}
    # The profile's own subscribers are visible, and the service added none (dec:no-service-document-consumers).
    subs = s["qs"]["engine_subscribers"]
    assert "start" in subs and subs["start"], subs
    assert all(not n.startswith("qs.") for names in subs.values() for n in names)


def test_plans_and_devices_allowed(client: TestClient) -> None:
    plans = client.get("/api/plans/allowed").json()
    assert plans["success"] and "count" in plans["plans_allowed"] and "slow_plan" in plans["plans_allowed"]
    slow = plans["plans_allowed"]["slow_plan"]
    assert slow["properties"]["is_generator"] is True
    assert [p["name"] for p in slow["parameters"]] == ["mot", "n", "dwell"]
    assert slow["parameters"][1]["default"] == "3"
    devices = client.get("/api/devices/allowed").json()
    motor = devices["devices_allowed"]["motor"]
    assert motor["is_movable"] and motor["is_readable"] and motor["classname"] == "SynAxis"
    assert "readback" in motor["components"]
    assert client.get("/api/plans/existing").json()["plans_existing"].keys() == plans["plans_allowed"].keys()
    assert client.get("/api/permissions/get").json()["success"]


# ---- queue operations --------------------------------------------------------------------


def test_queue_add_get_move_remove(client: TestClient) -> None:
    r = client.post(
        "/api/queue/item/add", json={"item": {"item_type": "plan", "name": "count", "args": [["det"]]}}
    ).json()
    assert r["success"] and r["qsize"] == 1 and r["item"]["user"] == "single_user" and r["item"]["item_uid"]
    a = r["item"]["item_uid"]
    r = client.post(
        "/api/queue/item/add", json={"item": {"name": "count", "args": [["det"]]}, "pos": "front"}
    ).json()
    b = r["item"]["item_uid"]
    q = client.get("/api/queue/get").json()
    assert [i["item_uid"] for i in q["items"]] == [b, a] and q["running_item"] == {}
    assert client.request("GET", "/api/queue/item/get", json={"uid": a}).json()["item"]["item_uid"] == a
    # finch main fetches single items by path (not in httpserver's spec); honoured as an alias.
    assert client.get(f"/api/queue/item/{a}").json()["item"]["item_uid"] == a
    assert client.get("/api/queue/item/no-such-uid").json()["success"] is False
    r = client.post("/api/queue/item/move", json={"uid": b, "pos_dest": "back"}).json()
    assert r["success"] and [i["item_uid"] for i in client.get("/api/queue/get").json()["items"]] == [a, b]
    r = client.post(
        "/api/queue/item/update",
        json={"item": {"item_uid": a, "name": "count", "args": [["det"]], "kwargs": {"num": 7}}},
    ).json()
    assert r["success"] and r["item"]["kwargs"] == {"num": 7}
    r = client.post("/api/queue/item/remove", json={"uid": b}).json()
    assert r["success"] and r["qsize"] == 1 and r["item"]["item_uid"] == b
    bad = client.post("/api/queue/item/add", json={"item": {"name": "no_such_plan"}}).json()
    assert bad["success"] is False and "Unknown plan" in bad["msg"]
    batch = client.post(
        "/api/queue/item/add/batch", json={"items": [{"name": "count", "args": [["det"]]}, {"name": "count"}]}
    ).json()
    assert batch["success"] and batch["qsize"] == 3
    uids = [i["item_uid"] for i in batch["items"]]
    r = client.post("/api/queue/item/remove/batch", json={"uids": uids}).json()
    assert r["success"] and r["qsize"] == 1
    assert client.post("/api/queue/clear").json()["success"] and status(client)["items_in_queue"] == 0


def test_items_run_only_after_start_and_stop_on_failure(client: TestClient) -> None:
    client.post(
        "/api/queue/item/add", json={"item": {"name": "count", "args": [["det"]], "kwargs": {"num": 2}}}
    )
    client.post("/api/queue/item/add", json={"item": {"name": "failing_plan"}})
    client.post("/api/queue/item/add", json={"item": {"name": "count", "args": [["det"]]}})
    time.sleep(0.3)
    assert status(client)["items_in_history"] == 0, "nothing runs before queue/start"
    assert client.post("/api/queue/start").json()["success"]
    wait_for(lambda: status(client)["items_in_history"] == 2)
    wait_for(lambda: status(client)["manager_state"] == "idle")
    hist = client.get("/api/history/get").json()["items"]
    assert hist[0]["result"]["exit_status"] == "success" and len(hist[0]["result"]["run_uids"]) == 1
    assert hist[1]["result"]["exit_status"] == "fail" and "always fails" in hist[1]["result"]["msg"]
    s = status(client)
    assert s["items_in_queue"] == 1 and s["qs"]["last_error"]
    assert client.post("/api/queue/stop").json()["success"] is False, "queue is already stopped"
    assert client.post("/api/queue/start").json()["success"]
    wait_for(lambda: status(client)["items_in_history"] == 3)


def test_pause_resume_abort_over_http(client: TestClient) -> None:
    # scan opens a run (slow_plan does not), so /api/re/runs has something to report while paused.
    client.post("/api/queue/item/add", json={"item": {"name": "scan", "args": [["det"], "motor", -1, 1, 40]}})
    client.post("/api/queue/start")
    wait_for(lambda: status(client)["re_state"] == "running")
    time.sleep(0.5)
    assert client.post("/api/re/pause", json={"option": "immediate"}).json()["success"]
    wait_for(lambda: status(client)["manager_state"] == "paused")
    assert status(client)["re_state"] == "paused"
    runs = client.post("/api/re/runs", json={"option": "active"}).json()
    assert runs["success"] and len(runs["run_list"]) == 1 and runs["run_list"][0]["is_open"]
    assert client.post("/api/re/resume").json()["success"]
    wait_for(lambda: status(client)["re_state"] == "running")
    assert client.post("/api/re/abort", json={"reason": "test"}).json()["success"]
    wait_for(lambda: status(client)["items_in_history"] == 1)
    assert client.get("/api/history/get").json()["items"][0]["result"]["exit_status"] == "abort"
    wait_for(lambda: status(client)["manager_state"] == "idle")
    assert client.post("/api/re/resume").json()["success"] is False, "nothing to resume"


def test_execute_now_and_autostart(client: TestClient) -> None:
    r = client.post("/api/queue/item/execute", json={"item": {"name": "count", "args": [["det"]]}}).json()
    assert r["success"]
    wait_for(lambda: status(client)["items_in_history"] == 1)
    assert client.post("/api/queue/autostart", json={"enable": True}).json()["success"]
    client.post("/api/queue/item/add", json={"item": {"name": "count", "args": [["det"]]}})
    wait_for(lambda: status(client)["items_in_history"] == 2)
    assert status(client)["queue_autostart_enabled"] is True
    client.post("/api/queue/autostart", json={"enable": False})
    client.post("/api/queue/stop")


# ---- tolerated and unsupported groups ------------------------------------------------------


def test_tolerated_and_unsupported_routes_answer_honestly(client: TestClient) -> None:
    env = client.post("/api/environment/open").json()
    assert env["success"] and "task_uid" in env
    assert client.post("/api/environment/close").json()["success"] is False
    lock = client.post("/api/lock", json={"lock_key": "k", "environment": True}).json()
    assert lock["success"] is False and lock["lock_info"]["queue"] is False
    assert client.get("/api/lock/info").json()["success"]
    fn = client.post("/api/function/execute", json={"item": {"name": "f"}}).json()
    assert fn["success"] is False and "plans only" in fn["msg"]
    assert client.post("/api/script/upload", json={"script": "x=1"}).json()["success"] is False
    task = client.request("GET", "/api/task/status", json={"task_uid": "abc"}).json()
    assert task["success"] and task["status"] == "completed"
    assert client.post("/api/kernel/interrupt").json()["success"] is False
    assert (
        client.post("/api/queue/mode/set", json={"mode": {"ignore_failures": True}}).json()["success"]
        is False
    )
    assert client.post("/api/queue/mode/set", json={"mode": {"loop": False}}).json()["success"]
    assert client.get("/api/config/get").json()["config"]["http"]["port"] == 60610


# ---- console -------------------------------------------------------------------------------


def test_console_output_captures_prints(client: TestClient, application: Application) -> None:
    # pytest replaces sys.stdout per test, so exercise the capture's own entry point here;
    # the stdout tee itself is covered by test_console_tee_wraps_stdout.
    application.services.console.feed("smoke-console-marker\n")
    wait_for(lambda: "smoke-console-marker" in client.get("/api/console_output").json()["text"])
    uid = client.get("/api/console_output/uid").json()["console_output_uid"]
    upd = client.request("GET", "/api/console_output_update", json={"last_msg_uid": "x"}).json()
    assert upd["last_msg_uid"] == uid and any(
        "smoke-console-marker" in m["msg"] for m in upd["console_output_msgs"]
    )


# ---- websockets ----------------------------------------------------------------------------


def test_status_websocket_streams_status(client: TestClient) -> None:
    with client.websocket_connect(f"/api/status/ws?api_key={API_KEY}") as ws:
        first = ws.receive_json()
        assert first["manager_state"] in {"idle", "executing_queue"} and "plan_queue_uid" in first


def test_websocket_auth_modes(application: Application) -> None:
    from starlette.websockets import WebSocketDisconnect

    with TestClient(application.app) as c:
        with c.websocket_connect("/api/status/ws") as ws:  # 'message' mode
            ws.send_text(json.dumps({"type": "auth", "api_key": API_KEY}))
            assert "manager_state" in ws.receive_json()
        with (
            pytest.raises(WebSocketDisconnect) as info,
            c.websocket_connect("/api/status/ws?api_key=wrong") as ws,
        ):
            ws.receive_json()
        assert info.value.code == 4401


def test_info_websocket_streams_device_progress(client: TestClient) -> None:
    with client.websocket_connect(f"/api/info/ws?api_key={API_KEY}") as ws:
        first = ws.receive_json()
        assert "status" in first["msg"] and "time" in first
        client.post(
            "/api/queue/item/execute",
            json={"item": {"name": "slow_plan", "args": ["motor"], "kwargs": {"n": 2, "dwell": 0.05}}},
        )
        progress: list[dict] = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            m = ws.receive_json()
            if "device_progress" in m["msg"]:
                progress.append(m["msg"]["device_progress"])
                if m["msg"]["device_progress"].get("completed") and any(
                    not p.get("completed") for p in progress
                ):
                    break
        updates = [p for p in progress if not p.get("completed")]
        assert updates and any(p.get("done") for p in updates), progress
        assert {"name", "current", "target", "fraction", "time_elapsed", "time_remaining", "done"} <= set(
            updates[0]
        )
        assert any(p.get("completed") for p in progress)
    wait_for(lambda: status(client)["items_in_history"] == 1)


def test_console_websocket(client: TestClient, application: Application) -> None:
    with client.websocket_connect(f"/api/console_output/ws?api_key={API_KEY}") as ws:
        application.services.console.feed("ws-console-marker\n")
        for _ in range(20):
            m = ws.receive_json()
            if "ws-console-marker" in m["msg"]:
                break
        else:
            raise AssertionError("console line not streamed")


def test_console_tee_wraps_stdout() -> None:
    import io
    import sys

    from qs.api.console import ConsoleCapture
    from qs.engine.events import EventBus

    fake = io.StringIO()
    saved = sys.stdout
    sys.stdout = fake
    try:
        capture = ConsoleCapture(EventBus())
        capture.install()
        print("tee-marker")
        capture.uninstall()
    finally:
        sys.stdout = saved
    assert "tee-marker" in fake.getvalue(), "original stream still receives output"
    assert "tee-marker" in capture.text()


# ---- device definitions (qs extension) ------------------------------------------------------


def test_device_definition_crud_and_use_in_plan(client: TestClient) -> None:
    r = client.post(
        "/api/qs/devices",
        json={"name": "m2", "class_path": "ophyd.sim.SynAxis", "kwargs": {"delay": 0}, "instantiate": True},
    ).json()
    assert r["success"], r
    assert r["definition"]["instantiated"] and r["definition"]["device"]["classname"] == "SynAxis"
    assert "m2" in client.get("/api/devices/allowed").json()["devices_allowed"]
    listed = client.get("/api/qs/devices").json()
    assert [d["name"] for d in listed["definitions"]] == ["m2"] and "motor" in listed["profile_devices"]
    # Use it in a plan through the queue.
    r = client.post(
        "/api/queue/item/execute",
        json={"item": {"name": "slow_plan", "args": ["m2"], "kwargs": {"n": 1, "dwell": 0.01}}},
    ).json()
    assert r["success"]
    wait_for(lambda: status(client)["items_in_history"] == 1)
    assert client.get("/api/history/get").json()["items"][0]["result"]["exit_status"] == "success"
    # Profile devices are read-only; bad classes are refused honestly.
    assert (
        client.post("/api/qs/devices", json={"name": "motor", "class_path": "ophyd.sim.SynAxis"}).json()[
            "success"
        ]
        is False
    )
    assert (
        client.post("/api/qs/devices", json={"name": "x", "class_path": "nope.Nope"}).json()["success"]
        is False
    )
    r = client.put(
        "/api/qs/devices/m2", json={"class_path": "ophyd.sim.SynAxis", "kwargs": {"delay": 0.01}}
    ).json()
    assert r["success"] and r["definition"]["kwargs"] == {"delay": 0.01} and r["definition"]["instantiated"]
    assert client.post("/api/qs/devices/m2/remove").json()["definition"]["instantiated"] is False
    assert "m2" not in client.get("/api/devices/allowed").json()["devices_allowed"]
    assert client.delete("/api/qs/devices/m2").json()["success"]
    assert client.get("/api/qs/devices/m2").json()["success"] is False
