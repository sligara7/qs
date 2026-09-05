"""Acceptance tests against a running qs on the HEX profile + hex-ob/hex-simulated-beamline.

Skipped unless QS_HEX_SIM_URL points at that qs. Needs: QS_API_KEY (default "hex"), the sim's
scripts/env.sh sourced (EPICS_CA_ADDR_LIST etc.), docker for the fault drills, and HEX_SIM_DIR
(default ~/git_projects/hex-ob/hex-simulated-beamline) to re-seed Tiled after it restarts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.acceptance


class Sim:
    def __init__(self, url: str, api_key: str, sim_dir: Path) -> None:
        self.url = url.rstrip("/") + "/api"
        self.client = httpx.Client(
            base_url=self.url, headers={"Authorization": f"ApiKey {api_key}"}, timeout=10
        )
        self.sim_dir = sim_dir

    # -- qs over HTTP -------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return self.client.get("/status").json()

    def post(self, path: str, **body: Any) -> dict[str, Any]:
        return self.client.post(path, json=body).json()

    def add(self, name: str, args: list[Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        r = self.post(
            "/queue/item/add", item={"item_type": "plan", "name": name, "args": args or [], "kwargs": kwargs}
        )
        assert r["success"], r
        return r["item"]

    def start(self) -> None:
        r = self.post("/queue/start")
        assert r["success"], r

    def wait_idle(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = self.status()
            if s["manager_state"] == "idle" and s["re_state"] == "idle" and s["running_item_uid"] is None:
                return s
            time.sleep(0.5)
        raise TimeoutError(f"qs still busy after {timeout}s: {self.status()}")

    def wait_running(self, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status()["re_state"] == "running":
                return
            time.sleep(0.2)
        raise TimeoutError("plan never started running")

    def last_result(self) -> dict[str, Any]:
        items = self.client.get("/history/get").json()["items"]
        return items[-1]["result"] | {"name": items[-1]["name"]}

    def run_one(
        self, name: str, args: list[Any] | None = None, timeout: float = 60, **kwargs: Any
    ) -> dict[str, Any]:
        self.add(name, args, **kwargs)
        self.start()
        try:
            self.wait_idle(timeout)
        except TimeoutError:
            self.post("/re/abort")
            self.wait_idle(60)
        return self.last_result()

    # -- the simulator ------------------------------------------------------------------
    def docker(self, *args: str) -> None:
        subprocess.run(["docker", *args], check=True, capture_output=True, timeout=120)

    def reseed_tiled(self) -> None:
        for _ in range(30):
            try:
                if httpx.get("http://127.0.0.1:8000/api/v1/", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        subprocess.run(
            ["bash", str(self.sim_dir / "scripts" / "seed.sh")], check=True, capture_output=True, timeout=120
        )

    def reinit_kinetix(self) -> None:
        """The sim's Kinetix IOC loses its Kinetix personality (TriggerMode choices, ArrayCallbacks)
        whenever its container restarts; the sim's own init script restores it."""
        script = self.sim_dir / "iocs" / "kinetix" / "init_kinetix.py"
        python = self.sim_dir / ".toolenv" / "bin" / "python"
        cmd = [str(python if python.exists() else "python3"), str(script)]
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=60,
            env={**os.environ, "EPICS_CA_ADDR_LIST": "127.0.0.1:5085"},
        )

    def tiled_runs(self) -> list[dict[str, Any]]:
        r = httpx.get(
            "http://127.0.0.1:8000/api/v1/search/hex/raw",
            params={"limit": 200},
            headers={"Authorization": f"Apikey {os.environ.get('TILED_API_KEY', 'secret')}"},
            timeout=10,
        )
        return r.json()["data"]


@pytest.fixture(scope="session")
def sim() -> Sim:
    url = os.environ.get("QS_HEX_SIM_URL")
    if not url:
        pytest.skip("QS_HEX_SIM_URL not set: no simulated beamline to test against")
    s = Sim(
        url,
        os.environ.get("QS_API_KEY", "hex"),
        Path(os.environ.get("HEX_SIM_DIR", "~/git_projects/hex-ob/hex-simulated-beamline")).expanduser(),
    )
    try:
        s.status()
    except httpx.HTTPError as exc:
        pytest.skip(f"qs not reachable at {url}: {exc}")
    return s


@pytest.fixture
def clean(sim: Sim) -> Sim:
    sim.wait_idle(120)
    sim.post("/queue/clear")
    return sim


@pytest.fixture
def docker_available() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker not available for fault injection")
