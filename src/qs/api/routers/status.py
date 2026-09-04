"""Status group: ``/api/``, ``/api/ping``, ``/api/status``, ``/api/config/get``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from qs import __version__
from qs.api.deps import Services, get_services, require_scope
from qs.api.responses import ok

router = APIRouter(tags=["status"])


@router.get("/")
@router.get("/ping")
async def ping(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(f"qs v{__version__} (bluesky-queueserver compatible)")


@router.get("/status", dependencies=[Depends(require_scope("read:status"))])
async def status(services: Services = Depends(get_services)) -> dict[str, Any]:
    return services.status.snapshot()


@router.get("/config/get", dependencies=[Depends(require_scope("read:config"))])
async def config_get(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok("", config=services.config)
