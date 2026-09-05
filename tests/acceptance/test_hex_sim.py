"""ver:hex-sim-acceptance and ver:hex-sim-fault-injection as pytest.

Successful plans and failure modes kept on purpose (user, 2026-09-04). Each test says what it
proves about qs; failures caused by the profile or the sim are asserted as such.
"""

from __future__ import annotations

import time

import pytest

from tests.acceptance.conftest import Sim

pytestmark = pytest.mark.acceptance


def test_profile_loaded_and_engine_adopted(sim: Sim) -> None:
    s = sim.status()
    assert s["worker_environment_exists"] and s["qs"]["engine_adopted"] is True
    assert "TiledWriter" in s["qs"]["engine_subscribers"].get("start", []), (
        "profile's TiledWriter must be kept"
    )
    plans = sim.client.get("/plans/allowed").json()["plans_allowed"]
    assert {"count", "sleep_for_secs", "tomo_flyscan"} <= set(plans)


def test_software_plan_succeeds(clean: Sim) -> None:
    r = clean.run_one("sleep_for_secs", [1])
    assert r["exit_status"] == "success" and r["run_uids"] == []


def test_count_on_real_areadetector_ioc_lands_in_tiled(clean: Sim) -> None:
    before = {run["id"] for run in clean.tiled_runs()}
    r = clean.run_one("count", [["kinetix1"]], num=2)
    assert r["exit_status"] == "success" and len(r["run_uids"]) == 1
    assert r["run_uids"][0] in {run["id"] for run in clean.tiled_runs()} - before


def test_connect_tier_detector_fails_and_queue_waits(clean: Sim) -> None:
    # kinetix3 is the typed caproto sim: it connects but never produces frames.
    clean.add("count", [["kinetix3"]], num=1)
    clean.add("sleep_for_secs", [1])
    clean.start()
    clean.wait_idle(90)
    r = clean.last_result()
    assert r["name"] == "count" and r["exit_status"] == "fail" and "kinetix-det3" in r["msg"]
    assert clean.status()["items_in_queue"] == 1, "stop-and-wait: the next item must not run"
    clean.post("/queue/clear")


def test_abort_of_long_software_plan(clean: Sim) -> None:
    clean.add("sleep_for_secs", [120])
    clean.start()
    clean.wait_running()
    assert clean.post("/re/abort")["success"]
    clean.wait_idle(30)
    assert clean.last_result()["exit_status"] == "abort"


def test_tiled_down_fails_plan_and_recovers(clean: Sim, docker_available: None) -> None:
    clean.docker("stop", "hexsim-tiled")
    try:
        r = clean.run_one("count", [["kinetix1"]], timeout=90, num=1)
        assert r["exit_status"] == "fail" and "Connect" in r["msg"]
    finally:
        clean.docker("start", "hexsim-tiled")
        clean.reseed_tiled()
    assert clean.run_one("count", [["kinetix1"]], num=1)["exit_status"] == "success"


def test_redis_down_fails_plan_and_recovers(clean: Sim, docker_available: None) -> None:
    clean.docker("stop", "hexsim-redis")
    try:
        r = clean.run_one("count", [["kinetix1"]], timeout=90, num=1)
        assert r["exit_status"] == "fail" and "6380" in r["msg"]
    finally:
        clean.docker("start", "hexsim-redis")
        time.sleep(5)
    assert clean.run_one("count", [["kinetix1"]], num=1)["exit_status"] == "success"


def test_ioc_killed_mid_plan_api_stays_up_abort_works_and_recovers(
    clean: Sim, docker_available: None
) -> None:
    clean.add("count", [["kinetix1"]], num=60, delay=0.5)
    clean.start()
    clean.wait_running()
    time.sleep(2)
    clean.docker("stop", "hexsim-kinetix-ioc")
    try:
        time.sleep(5)
        t0 = time.monotonic()
        s = clean.status()
        assert time.monotonic() - t0 < 2, "status must answer while a plan hangs on dead hardware"
        assert s["re_state"] in ("running", "idle")
        assert clean.post("/re/abort")["success"] or clean.status()["re_state"] == "idle"
        clean.wait_idle(60)
        assert clean.last_result()["exit_status"] in ("abort", "fail")
    finally:
        clean.docker("start", "hexsim-kinetix-ioc")
        time.sleep(25)
        clean.reinit_kinetix()
    assert clean.run_one("count", [["kinetix1"]], num=2)["exit_status"] == "success", "no restart needed"
