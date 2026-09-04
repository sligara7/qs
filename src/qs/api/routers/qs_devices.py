"""qs extension: device-definition CRUD under ``/api/qs/devices`` (``cap:device-http-api``).

Not part of bluesky-httpserver; namespaced under ``/api/qs/`` so the compatible surface
stays recognisable. Definitions are data (class, prefix, kwargs); instantiation runs on the
engine thread; profile-defined devices are read-only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from qs.api.deps import Services, get_services, require_scope
from qs.api.describe import describe_device
from qs.api.payload import read_payload
from qs.api.responses import fail, ok
from qs.devices.models import DeviceDefinition
from qs.devices.service import DeviceDefinitionError

router = APIRouter(tags=["qs devices"])


def _svc(services: Services):  # type: ignore[no-untyped-def]
    if services.devices is None:
        raise DeviceDefinitionError("Device definitions are not enabled (no database configured)")
    return services.devices


def _entry(services: Services, definition: DeviceDefinition) -> dict[str, Any]:
    d = definition.to_dict()
    entry = services.registry.devices().get(definition.name)
    d["instantiated"] = entry is not None and entry.origin == "definition"
    if d["instantiated"] and entry is not None:
        d["device"] = describe_device(definition.name, entry.device)
    return d


@router.get("/qs/devices", dependencies=[Depends(require_scope("read:resources"))])
async def list_devices(services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        svc = _svc(services)
    except DeviceDefinitionError as exc:
        return fail(str(exc), definitions=[], profile_devices=[])
    profile = sorted(name for name, e in services.registry.devices().items() if e.origin == "profile")
    return ok("", definitions=[_entry(services, d) for d in svc.list()], profile_devices=profile)


@router.post("/qs/devices", dependencies=[Depends(require_scope("write:queue"))])
async def create_device(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        svc = _svc(services)
        definition = svc.create(DeviceDefinition.from_dict(payload))
        if payload.get("instantiate", True) and definition.enabled:
            svc.instantiate(definition.name)
        return ok("", definition=_entry(services, definition))
    except (DeviceDefinitionError, KeyError, TypeError, ValueError) as exc:
        return fail(
            f"{type(exc).__name__}: {exc}" if not isinstance(exc, DeviceDefinitionError) else str(exc)
        )
    except Exception as exc:  # noqa: BLE001 - a broken device class must not 500
        return fail(f"Device could not be created: {type(exc).__name__}: {exc}")


@router.get("/qs/devices/{name}", dependencies=[Depends(require_scope("read:resources"))])
async def get_device(name: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return ok("", definition=_entry(services, _svc(services).get(name)))
    except DeviceDefinitionError as exc:
        return fail(str(exc))


@router.put("/qs/devices/{name}", dependencies=[Depends(require_scope("write:queue"))])
async def update_device(
    name: str, request: Request, services: Services = Depends(get_services)
) -> dict[str, Any]:
    payload = await read_payload(request)
    payload["name"] = name
    try:
        svc = _svc(services)
        was_live = svc.is_instantiated(name)
        if was_live:
            svc.remove_instance(name)
        definition = svc.update(DeviceDefinition.from_dict(payload))
        if payload.get("instantiate", was_live) and definition.enabled:
            svc.instantiate(name)
        return ok("", definition=_entry(services, definition))
    except (DeviceDefinitionError, KeyError, TypeError, ValueError) as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail(f"Device could not be updated: {type(exc).__name__}: {exc}")


@router.delete("/qs/devices/{name}", dependencies=[Depends(require_scope("write:queue"))])
async def delete_device(name: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        _svc(services).delete(name)
        return ok("")
    except DeviceDefinitionError as exc:
        return fail(str(exc))


@router.post("/qs/devices/{name}/instantiate", dependencies=[Depends(require_scope("write:queue"))])
async def instantiate_device(name: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        svc = _svc(services)
        svc.instantiate(name)
        return ok("", definition=_entry(services, svc.get(name)))
    except DeviceDefinitionError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail(f"Device could not be instantiated: {type(exc).__name__}: {exc}")


@router.post("/qs/devices/{name}/remove", dependencies=[Depends(require_scope("write:queue"))])
async def remove_device_instance(name: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        svc = _svc(services)
        svc.remove_instance(name)
        return ok("", definition=_entry(services, svc.get(name)))
    except DeviceDefinitionError as exc:
        return fail(str(exc))
