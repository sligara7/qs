"""The three send-only websockets finch opens (``cap:monitor-websockets``).

* ``/api/status/ws`` — the status document once per second and on every change.
* ``/api/info/ws`` — ``{"time", "msg": {"status": ...}}`` and ``{"time", "msg": {"device_progress": ...}}``.
* ``/api/console_output/ws`` — ``{"time", "msg": <text>}``.

Authentication as finch's client sends it: ``?api_key=`` / ``?access_token=`` on the
handshake, ``Authorization: Apikey`` header, or a first ``{"type": "auth", "api_key": ...}``
message within 10 s. Bad credentials close with 4401 (api key) or 4001 (token).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from qs.api.auth import AuthenticationError, Credential
from qs.api.deps import Services
from qs.api.streams import json_safe

router = APIRouter(tags=["websockets"])

AUTH_MESSAGE_TIMEOUT = 10.0


async def _authenticate(ws: WebSocket, services: Services) -> bool:
    credential = Credential.from_headers_and_query(ws.headers, ws.query_params)
    if not credential.value:
        # finch's 'message' mode: wait for {"type": "auth", "api_key"|"access_token"} briefly.
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_MESSAGE_TIMEOUT)
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("type") == "auth":
                if data.get("api_key"):
                    credential = Credential("message", str(data["api_key"]))
                elif data.get("access_token"):
                    credential = Credential("bearer", str(data["access_token"]))
        except (TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
            pass
    try:
        services.authenticator.authenticate(credential)
        return True
    except AuthenticationError:
        await ws.close(code=4001 if credential.scheme == "bearer" else 4401)
        return False


async def _drain(ws: WebSocket) -> None:
    """Ignore client frames (finch sends none after auth) until the socket closes."""
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass


@router.websocket("/status/ws")
async def status_ws(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    await ws.accept()
    if not await _authenticate(ws, services):
        return
    drain = asyncio.create_task(_drain(ws))
    try:
        async with services.broadcaster.subscribe(
            lambda e: (
                e.kind
                in {
                    "state",
                    "queue_state",
                    "item_started",
                    "item_finished",
                    "plan_started",
                    "plan_finished",
                    "re_state",
                }
            )
        ) as queue:
            last_sent = 0.0
            while not drain.done():
                snapshot = services.status.snapshot()
                await ws.send_text(json.dumps(json_safe(snapshot)))
                last_sent = time.time()
                try:
                    await asyncio.wait_for(queue.get(), timeout=max(0.0, 1.0 - (time.time() - last_sent)))
                    while not queue.empty():
                        queue.get_nowait()
                except TimeoutError:
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        drain.cancel()


@router.websocket("/info/ws")
async def info_ws(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    await ws.accept()
    if not await _authenticate(ws, services):
        return
    drain = asyncio.create_task(_drain(ws))
    interesting = {
        "state",
        "queue_state",
        "item_started",
        "item_finished",
        "plan_started",
        "plan_finished",
        "re_state",
        "device_progress",
    }
    try:
        async with services.broadcaster.subscribe(lambda e: e.kind in interesting) as queue:
            last_status = 0.0
            while not drain.done():
                now = time.time()
                if now - last_status >= 1.0:
                    await ws.send_text(
                        json.dumps({"time": now, "msg": {"status": json_safe(services.status.snapshot())}})
                    )
                    last_status = now
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=max(0.05, 1.0 - (time.time() - last_status))
                    )
                except TimeoutError:
                    continue
                if event.kind == "device_progress":
                    await ws.send_text(
                        json.dumps(
                            {"time": event.time, "msg": {"device_progress": json_safe(dict(event.payload))}}
                        )
                    )
                else:
                    await ws.send_text(
                        json.dumps(
                            {"time": time.time(), "msg": {"status": json_safe(services.status.snapshot())}}
                        )
                    )
                    last_status = time.time()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        drain.cancel()


@router.websocket("/console_output/ws")
async def console_ws(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    await ws.accept()
    if not await _authenticate(ws, services):
        return
    drain = asyncio.create_task(_drain(ws))
    try:
        async with services.broadcaster.subscribe(lambda e: e.kind == "console_output") as queue:
            while not drain.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                payload: dict[str, Any] = {
                    "time": event.payload.get("time", event.time),
                    "msg": event.payload.get("msg", ""),
                }
                await ws.send_text(json.dumps(payload))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        drain.cancel()
