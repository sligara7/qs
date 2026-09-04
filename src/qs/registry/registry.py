"""In-memory catalogue of plans and devices, and item resolution.

Merges profile-defined devices (read-only through the API, ``req:device-crud-semantics``)
with devices instantiated from stored definitions, and resolves a queue item
(plan name + args/kwargs that reference devices by name) into a plan generator factory
(interface ``ifc:registry-lookup``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from typing import Any

from qs.sources.protocol import LoadResult, PlanFactory


class RegistryError(LookupError):
    """Unknown plan or device, or an operation the registry refuses."""


@dataclass(frozen=True)
class DeviceEntry:
    name: str
    device: Any
    origin: str  # "profile" | "definition"


class Registry:
    """Thread-safe name → object catalogue."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plans: dict[str, PlanFactory] = {}
        self._devices: dict[str, DeviceEntry] = {}

    # ---- loading -------------------------------------------------------------------

    def load_from(self, result: LoadResult) -> None:
        """Replace the profile-defined plans and devices with those in ``result``."""
        with self._lock:
            self._plans = dict(result.plans)
            kept = {k: v for k, v in self._devices.items() if v.origin != "profile"}
            self._devices = {name: DeviceEntry(name, dev, "profile") for name, dev in result.devices.items()}
            self._devices.update(kept)

    # ---- queries -------------------------------------------------------------------

    def plans(self) -> Mapping[str, PlanFactory]:
        with self._lock:
            return dict(self._plans)

    def devices(self) -> Mapping[str, DeviceEntry]:
        with self._lock:
            return dict(self._devices)

    def get_plan(self, name: str) -> PlanFactory:
        with self._lock:
            try:
                return self._plans[name]
            except KeyError:
                raise RegistryError(f"Unknown plan: {name!r}") from None

    def get_device(self, name: str) -> Any:
        """Look up a device by name; dotted names walk attributes (``motor.setpoint``)."""
        head, _, rest = name.partition(".")
        with self._lock:
            try:
                obj = self._devices[head].device
            except KeyError:
                raise RegistryError(f"Unknown device: {head!r}") from None
        for attr in filter(None, rest.split(".")):
            try:
                obj = getattr(obj, attr)
            except AttributeError:
                raise RegistryError(f"Device {head!r} has no component {attr!r}") from None
        return obj

    def has_device(self, name: str) -> bool:
        try:
            self.get_device(name)
        except RegistryError:
            return False
        return True

    # ---- definition-instantiated devices -------------------------------------------

    def add_device(self, name: str, device: Any) -> None:
        with self._lock:
            existing = self._devices.get(name)
            if existing is not None and existing.origin == "profile":
                raise RegistryError(f"Device {name!r} is defined by the profile and cannot be replaced")
            self._devices[name] = DeviceEntry(name, device, "definition")

    def remove_device(self, name: str) -> Any:
        with self._lock:
            existing = self._devices.get(name)
            if existing is None:
                raise RegistryError(f"Unknown device: {name!r}")
            if existing.origin == "profile":
                raise RegistryError(f"Device {name!r} is defined by the profile and cannot be removed")
            del self._devices[name]
            return existing.device

    # ---- resolution ----------------------------------------------------------------

    def resolve(
        self,
        plan_name: str,
        args: list[Any] | tuple[Any, ...] = (),
        kwargs: Mapping[str, Any] | None = None,
    ) -> Callable[[], Generator[Any, Any, Any]]:
        """Return a factory that, when called, creates the plan generator.

        Strings in ``args``/``kwargs`` (recursively through lists, tuples and dicts) that
        name a registered device, or a dotted component of one, are replaced by the device
        object, as bluesky-queueserver does for parameters that accept devices. All other
        values pass through unchanged.
        """
        plan = self.get_plan(plan_name)
        resolved_args = [self._substitute(a) for a in args]
        resolved_kwargs = {k: self._substitute(v) for k, v in (kwargs or {}).items()}

        def factory() -> Generator[Any, Any, Any]:
            return plan(*resolved_args, **resolved_kwargs)

        return factory

    def _substitute(self, value: Any) -> Any:
        if isinstance(value, str):
            if self.has_device(value):
                return self.get_device(value)
            return value
        if isinstance(value, list):
            return [self._substitute(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._substitute(v) for v in value)
        if isinstance(value, dict):
            return {k: self._substitute(v) for k, v in value.items()}
        return value
