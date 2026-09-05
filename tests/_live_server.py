"""Shared helper: run qs on the minimal profile under a real uvicorn server on a free port."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from qs.runtime.compose import Application, build_application
from qs.runtime.config import load_config
from qs.sources.ipython_profile import IPythonProfileSource

PROFILE = Path(__file__).parent / "profiles" / "minimal" / "startup"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def live_server(tmp: Path, api_key: str, **overrides: object) -> Iterator[tuple[Application, str]]:
    config = load_config(
        env={"QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": api_key},
        overrides={
            "database.url": f"sqlite:///{tmp / 'qs.sqlite'}",
            "engine.capture_console": False,
            **overrides,
        },
    )
    app = build_application(config, source=IPythonProfileSource(PROFILE))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="uvicorn-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        app.close()
