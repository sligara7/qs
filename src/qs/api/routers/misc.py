"""Tasks, lock, functions/scripts, console output, admin: tolerated or refused per the
accepted support-level decision, always with a route so finch never sees a 404."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from qs.api.deps import Services, get_services, require_scope
from qs.api.payload import read_payload
from qs.api.responses import fail, ok, unsupported

router = APIRouter(tags=["misc"])

_LOCK_INFO = {
    "environment": False,
    "queue": False,
    "user": None,
    "note": None,
    "time": None,
    "time_str": "",
    "emergency_lock_key_is_set": False,
}


# ---- tasks: every operation in qs is synchronous, so any task is already complete ----


@router.get("/task/status", dependencies=[Depends(require_scope("read:status"))])
async def task_status(request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    task_uid = payload.get("task_uid")
    if not task_uid:
        return fail("Payload must contain 'task_uid'", task_uid=None, status=None)
    return ok("", task_uid=task_uid, status="completed")


@router.get("/task/result", dependencies=[Depends(require_scope("read:status"))])
async def task_result(request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    task_uid = payload.get("task_uid")
    if not task_uid:
        return fail("Payload must contain 'task_uid'", task_uid=None, status=None, result=None)
    return ok("", task_uid=task_uid, status="completed", result={"success": True, "msg": ""})


# ---- lock: the queue is the lock (con:queue-is-the-lock) ----


@router.post("/lock", dependencies=[Depends(require_scope("write:lock"))])
async def lock() -> dict[str, Any]:
    return fail(
        "Locking is not supported: exclusive control comes from the queue itself (every operation runs as a "
        "plan through one RunEngine)",
        lock_info=_LOCK_INFO,
        lock_info_uid="",
    )


@router.get("/lock/info", dependencies=[Depends(require_scope("read:lock"))])
async def lock_info() -> dict[str, Any]:
    return ok("", lock_info=_LOCK_INFO, lock_info_uid="")


@router.post("/unlock", dependencies=[Depends(require_scope("write:unlock"))])
async def unlock() -> dict[str, Any]:
    return ok("Nothing to unlock: this service has no lock", lock_info=_LOCK_INFO, lock_info_uid="")


# ---- functions and scripts: this service runs Bluesky plans only ----


@router.post("/function/execute", dependencies=[Depends(require_scope("write:execute"))])
async def function_execute() -> dict[str, Any]:
    return unsupported("Executing arbitrary functions", "this service runs Bluesky plans only")


@router.post("/script/upload", dependencies=[Depends(require_scope("write:scripts"))])
async def script_upload() -> dict[str, Any]:
    return unsupported("Uploading scripts", "this service runs Bluesky plans only")


# ---- console output ----


@router.get("/console_output", dependencies=[Depends(require_scope("read:console"))])
async def console_output(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    nlines = int(payload.get("nlines", 200))
    return ok("", text=services.console.text(nlines))


@router.get("/console_output/uid", dependencies=[Depends(require_scope("read:console"))])
async def console_output_uid(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok("", console_output_uid=services.console.uid)


@router.get("/console_output_update", dependencies=[Depends(require_scope("read:console"))])
async def console_output_update(
    request: Request, services: Services = Depends(get_services)
) -> dict[str, Any]:
    payload = await read_payload(request)
    last = payload.get("last_msg_uid")
    messages = services.console.messages()
    return ok(
        "",
        console_output_msgs=messages if last != services.console.uid else [],
        last_msg_uid=services.console.uid,
    )


@router.get("/stream_console_output", dependencies=[Depends(require_scope("read:console"))])
async def stream_console_output(services: Services = Depends(get_services)) -> Any:
    from fastapi.responses import StreamingResponse

    async def generate():  # type: ignore[no-untyped-def]
        async with services.broadcaster.subscribe(lambda e: e.kind == "console_output") as queue:
            while True:
                event = await queue.get()
                yield (event.payload.get("msg") or "").encode()

    return StreamingResponse(generate(), media_type="text/plain")


# ---- admin ----


@router.post("/manager/stop", dependencies=[Depends(require_scope("write:manager"))])
async def manager_stop(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    option = str(payload.get("option", "safe_on"))
    if option == "safe_on" and services.host.state.value in ("running", "paused"):
        return fail(
            "A plan is running; use option 'safe_off' to stop anyway (the running plan is left to finish)"
        )
    if services.shutdown_callback is not None:
        loop = asyncio.get_running_loop()
        loop.call_later(0.2, services.shutdown_callback)
    return ok("Service is shutting down")


@router.post("/kernel/interrupt", dependencies=[Depends(require_scope("write:execute"))])
async def kernel_interrupt() -> dict[str, Any]:
    return unsupported("Kernel interrupt", "there is no IPython kernel; the profile runs in-process")


@router.post("/test/manager/kill", dependencies=[Depends(require_scope("write:testing"))])
async def test_manager_kill() -> dict[str, Any]:
    return unsupported("test/manager/kill", "test routes are not available")


@router.get("/test/server/sleep", dependencies=[Depends(require_scope("read:testing"))])
async def test_server_sleep(request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    await asyncio.sleep(min(float(payload.get("time", 0) or 0), 5.0))
    return ok("")
