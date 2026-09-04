"""Plan and device descriptors in the shape finch reads from ``plans/allowed`` and
``devices/allowed`` (modelled on bluesky-queueserver's ``existing_plans_and_devices``).
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from qs.registry import DeviceEntry

_KIND_VALUES = {
    "POSITIONAL_ONLY": 0,
    "POSITIONAL_OR_KEYWORD": 1,
    "VAR_POSITIONAL": 2,
    "KEYWORD_ONLY": 3,
    "VAR_KEYWORD": 4,
}


def _annotation_text(annotation: Any) -> str | None:
    if annotation is inspect.Parameter.empty:
        return None
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", None) or repr(annotation)


def describe_plan(name: str, plan: Any) -> dict[str, Any]:
    target = plan
    try:
        target = inspect.unwrap(plan)
    except ValueError:  # pragma: no cover
        pass
    parameters: list[dict[str, Any]] = []
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        for p in signature.parameters.values():
            entry: dict[str, Any] = {
                "name": p.name,
                "kind": {"name": p.kind.name, "value": _KIND_VALUES[p.kind.name]},
            }
            if p.default is not inspect.Parameter.empty:
                entry["default"] = repr(p.default)
            annotation = _annotation_text(p.annotation)
            if annotation is not None:
                entry["annotation"] = {"type": annotation}
            parameters.append(entry)
    return {
        "name": name,
        "module": getattr(target, "__module__", "") or "",
        "description": inspect.getdoc(target) or "",
        "properties": {"is_generator": inspect.isgeneratorfunction(target)},
        "parameters": parameters,
    }


def _device_flags(obj: Any) -> dict[str, bool]:
    return {
        "is_readable": callable(getattr(obj, "read", None)) and callable(getattr(obj, "describe", None)),
        "is_movable": callable(getattr(obj, "set", None)),
        "is_flyable": callable(getattr(obj, "kickoff", None)) and callable(getattr(obj, "complete", None)),
    }


def _components(obj: Any, depth: int, max_depth: int) -> dict[str, Any]:
    if depth >= max_depth:
        return {}
    out: dict[str, Any] = {}
    # ophyd v1: component_names; ophyd-async: children()
    names: list[str] = list(getattr(obj, "component_names", ()) or ())
    children = getattr(obj, "children", None)
    if not names and callable(children):
        try:
            names = [n for n, _ in children()]
        except Exception:  # noqa: BLE001
            names = []
    for cname in names:
        try:
            child = getattr(obj, cname)
        except Exception:  # noqa: BLE001
            continue
        entry = describe_device(cname, child, depth=depth + 1, max_depth=max_depth)
        out[cname] = entry
    return out


def describe_device(name: str, obj: Any, *, depth: int = 0, max_depth: int = 2) -> dict[str, Any]:
    cls = type(obj)
    entry: dict[str, Any] = {
        **_device_flags(obj),
        "classname": cls.__name__,
        "module": cls.__module__,
    }
    components = _components(obj, depth, max_depth)
    if components:
        entry["components"] = components
    return entry


def describe_plans(plans: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: describe_plan(name, plan) for name, plan in sorted(plans.items())}


def describe_devices(devices: Mapping[str, DeviceEntry], *, max_depth: int = 2) -> dict[str, dict[str, Any]]:
    return {
        name: describe_device(name, entry.device, max_depth=max_depth)
        for name, entry in sorted(devices.items())
    }
