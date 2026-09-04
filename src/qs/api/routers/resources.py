"""Plans & devices, permissions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from qs.api.deps import Services, get_services, require_scope
from qs.api.describe import describe_devices, describe_plans
from qs.api.responses import ok

router = APIRouter(tags=["plans and devices"])


@router.get("/plans/allowed", dependencies=[Depends(require_scope("read:resources"))])
async def plans_allowed(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "",
        plans_allowed=describe_plans(services.registry.plans()),
        plans_allowed_uid=services.status.registry_uid,
    )


@router.get("/plans/existing", dependencies=[Depends(require_scope("read:resources"))])
async def plans_existing(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "",
        plans_existing=describe_plans(services.registry.plans()),
        plans_existing_uid=services.status.registry_uid,
    )


@router.get("/devices/allowed", dependencies=[Depends(require_scope("read:resources"))])
async def devices_allowed(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "",
        devices_allowed=describe_devices(services.registry.devices()),
        devices_allowed_uid=services.status.registry_uid,
    )


@router.get("/devices/existing", dependencies=[Depends(require_scope("read:resources"))])
async def devices_existing(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "",
        devices_existing=describe_devices(services.registry.devices()),
        devices_existing_uid=services.status.registry_uid,
    )


@router.get("/permissions/get", dependencies=[Depends(require_scope("read:resources"))])
async def permissions_get(services: Services = Depends(get_services)) -> dict[str, Any]:
    # All plans and devices are allowed to every group until a permissions capability exists.
    permissions = {
        "user_groups": {
            "root": {
                "allowed_plans": [None],
                "forbidden_plans": [None],
                "allowed_devices": [None],
                "forbidden_devices": [None],
            },
            "primary": {
                "allowed_plans": [None],
                "forbidden_plans": [None],
                "allowed_devices": [None],
                "forbidden_devices": [None],
            },
        }
    }
    return ok("", user_group_permissions=permissions)


@router.post("/permissions/set", dependencies=[Depends(require_scope("write:permissions"))])
async def permissions_set() -> dict[str, Any]:
    return ok("Permissions are accepted but not enforced: every plan and device is allowed in this version")


@router.post("/permissions/reload", dependencies=[Depends(require_scope("write:permissions"))])
async def permissions_reload() -> dict[str, Any]:
    return ok("Permissions are not enforced in this version; nothing to reload")
