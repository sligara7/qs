"""Run-engine and environment groups."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request

from qs.api.deps import Services, get_services, require_scope
from qs.api.payload import read_payload
from qs.api.responses import fail, ok
from qs.engine.host import EngineHostError, EngineState

router = APIRouter(tags=["run engine"])


@router.post("/re/pause", dependencies=[Depends(require_scope("write:execute"))])
async def re_pause(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    option = str(payload.get("option", "deferred"))
    if option not in {"deferred", "immediate"}:
        return fail(f"Invalid pause option {option!r}: use 'deferred' or 'immediate'")
    try:
        services.host.request_pause(defer=(option == "deferred"))
        return ok("")
    except Exception as exc:  # noqa: BLE001 - bluesky TransitionError etc.
        return fail(str(exc))


@router.post("/re/resume", dependencies=[Depends(require_scope("write:execute"))])
async def re_resume(services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        services.host.resume()
        return ok("")
    except EngineHostError as exc:
        return fail(str(exc))


@router.post("/re/abort", dependencies=[Depends(require_scope("write:execute"))])
async def re_abort(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        services.host.abort(reason=str(payload.get("reason", "aborted over HTTP")))
        return ok("")
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@router.post("/re/stop", dependencies=[Depends(require_scope("write:execute"))])
async def re_stop(services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        services.host.stop()
        return ok("")
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@router.post("/re/halt", dependencies=[Depends(require_scope("write:execute"))])
async def re_halt(services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        services.host.halt()
        return ok("")
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@router.get("/re/metadata", dependencies=[Depends(require_scope("read:status"))])
async def re_metadata(services: Services = Depends(get_services)) -> dict[str, Any]:
    engine = services.host.engine
    md = dict(engine.md) if engine is not None else {}
    return ok(
        "",
        metadata={
            k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in md.items()
        },
    )


def _open_runs(services: Services) -> list[dict[str, Any]]:
    """Open runs from the engine's run bundlers: start uid and scan_id when known."""
    engine = services.host.engine
    if engine is None:
        return []
    bundlers = getattr(engine, "_run_bundlers", {})  # noqa: SLF001
    out: list[dict[str, Any]] = []
    for bundler in bundlers.values():
        uid = getattr(bundler, "_run_start_uid", None)  # noqa: SLF001
        md = getattr(bundler, "_md", None) or getattr(bundler, "md", None) or {}  # noqa: SLF001
        scan_id = md.get("scan_id") if isinstance(md, dict) else None
        out.append({"uid": uid, "is_open": True, "scan_id": scan_id})
    return out


def _run_list(services: Services, option: str) -> list[dict[str, Any]]:
    running = services.host.state in (EngineState.RUNNING, EngineState.PAUSED)
    if option in ("active", "open"):
        return _open_runs(services) if running else []
    outcome = services.host.last_outcome
    if outcome is None:
        return []
    return [{"uid": u, "is_open": False, "scan_id": None} for u in outcome.run_uids]


@router.post("/re/runs", dependencies=[Depends(require_scope("read:status"))])
async def re_runs(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    option = str(payload.get("option", "active"))
    if option not in {"active", "open", "closed"}:
        return fail(f"Invalid option {option!r}")
    return ok(
        "", run_list=_run_list(services, option), run_list_uid=services.status.snapshot()["run_list_uid"]
    )


@router.get("/re/runs/active", dependencies=[Depends(require_scope("read:status"))])
async def re_runs_active(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "", run_list=_run_list(services, "active"), run_list_uid=services.status.snapshot()["run_list_uid"]
    )


@router.get("/re/runs/open", dependencies=[Depends(require_scope("read:status"))])
async def re_runs_open(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "", run_list=_run_list(services, "open"), run_list_uid=services.status.snapshot()["run_list_uid"]
    )


@router.get("/re/runs/closed", dependencies=[Depends(require_scope("read:status"))])
async def re_runs_closed(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "", run_list=_run_list(services, "closed"), run_list_uid=services.status.snapshot()["run_list_uid"]
    )


# ---- environment (tolerated: the profile is loaded at startup) ----


@router.post("/environment/open", dependencies=[Depends(require_scope("write:execute"))])
async def environment_open(services: Services = Depends(get_services)) -> dict[str, Any]:
    if services.host.state in (EngineState.NO_ENGINE, EngineState.STARTING):
        return fail("No profile is loaded; qs loads its profile at startup", task_uid=None)
    return ok(
        "The RE environment already exists (qs loads the profile at startup).", task_uid=str(uuid.uuid4())
    )


@router.post("/environment/close", dependencies=[Depends(require_scope("write:execute"))])
@router.post("/environment/destroy", dependencies=[Depends(require_scope("write:execute"))])
async def environment_close(services: Services = Depends(get_services)) -> dict[str, Any]:
    if services.host.state in (EngineState.RUNNING, EngineState.PAUSED):
        return fail("A plan is running; the environment cannot be closed")
    return fail(
        "Closing the RE environment is not supported: qs keeps the profile loaded for the life of the process"
    )


@router.post("/environment/update", dependencies=[Depends(require_scope("write:execute"))])
async def environment_update(services: Services = Depends(get_services)) -> dict[str, Any]:
    return fail("Reloading the profile at runtime is not supported yet; restart the service", task_uid=None)
