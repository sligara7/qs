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
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from qs.api.auth import AuthenticationError, Credential
from qs.api.deps import Services
from qs.api.streams import json_safe
from qs.engine import STATUS_CHANGE_KINDS, EngineEvent, EventKind

router = APIRouter(tags=["websockets"])

AUTH_MESSAGE_TIMEOUT = 10.0

_INFO_KINDS = STATUS_CHANGE_KINDS | {EventKind.DEVICE_PROGRESS}

EventQueue = asyncio.Queue[EngineEvent]
DrainTask = asyncio.Task[None]
Predicate = Callable[[EngineEvent], bool]
StreamBody = Callable[[WebSocket, Services, EventQueue, DrainTask], Awaitable[None]]


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


async def _serve(ws: WebSocket, predicate: Predicate, body: StreamBody) -> None:
    """Everything the three sockets do identically, in one place.

    Accept, authenticate (closing with finch's codes if that fails), start the drain task,
    subscribe with ``predicate``, hand the queue to ``body`` — and cancel the drain task
    however the socket ends. Each handler below supplies only the two things that differ,
    so a fourth socket cannot forget the cleanup.
    """
    services: Services = ws.app.state.services
    await ws.accept()
    if not await _authenticate(ws, services):
        return
    drain = asyncio.create_task(_drain(ws))
    try:
        async with services.broadcaster.subscribe(predicate) as queue:
            await body(ws, services, queue, drain)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        drain.cancel()


@router.websocket("/status/ws")
async def status_ws(ws: WebSocket) -> None:
    await _serve(ws, lambda e: e.kind in STATUS_CHANGE_KINDS, _status_body)


async def _status_body(ws: WebSocket, services: Services, queue: EventQueue, drain: DrainTask) -> None:
    """The status document, once per second and on every change."""
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


@router.websocket("/info/ws")
async def info_ws(ws: WebSocket) -> None:
    await _serve(ws, lambda e: e.kind in _INFO_KINDS, _info_body)


async def _info_body(ws: WebSocket, services: Services, queue: EventQueue, drain: DrainTask) -> None:
    """``{"time", "msg": {"status": ...}}``, plus device progress as it arrives."""
    last_status = 0.0
    while not drain.done():
        now = time.time()
        if now - last_status >= 1.0:
            await ws.send_text(
                json.dumps({"time": now, "msg": {"status": json_safe(services.status.snapshot())}})
            )
            last_status = now
        try:
            event = await asyncio.wait_for(queue.get(), timeout=max(0.05, 1.0 - (time.time() - last_status)))
        except TimeoutError:
            continue
        if event.kind == EventKind.DEVICE_PROGRESS:
            await ws.send_text(
                json.dumps({"time": event.time, "msg": {"device_progress": json_safe(dict(event.payload))}})
            )
        else:
            await ws.send_text(
                json.dumps({"time": time.time(), "msg": {"status": json_safe(services.status.snapshot())}})
            )
            last_status = time.time()


@router.websocket("/console_output/ws")
async def console_ws(ws: WebSocket) -> None:
    await _serve(ws, lambda e: e.kind == EventKind.CONSOLE_OUTPUT, _console_body)


async def _console_body(ws: WebSocket, _services: Services, queue: EventQueue, drain: DrainTask) -> None:
    """``{"time", "msg": <text>}`` per captured console message."""
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
