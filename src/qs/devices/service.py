"""Device definition CRUD and live instantiation.

Definitions are data; instantiation runs on the engine thread so an ophyd-async device's
``connect()`` uses the engine's event loop. Profile-defined devices are never touched
(``req:device-crud-semantics``), and no operation here moves hardware.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Callable, Sequence
from typing import Any

from qs.devices.models import DeviceDefinition
from qs.devices.repository import DeviceDefinitionRepository
from qs.engine.host import EngineHost
from qs.registry import Registry, RegistryError

logger = logging.getLogger(__name__)


class DeviceDefinitionError(ValueError):
    pass


def import_class(class_path: str) -> type:
    module_name, _, class_name = class_path.rpartition(".")
    if not module_name or not class_name:
        raise DeviceDefinitionError(f"class_path must be 'package.module.Class', got {class_path!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DeviceDefinitionError(f"Cannot import module {module_name!r}: {exc}") from exc
    cls = getattr(module, class_name, None)
    if not inspect.isclass(cls):
        raise DeviceDefinitionError(f"{class_path!r} is not a class")
    return cls


class DeviceDefinitionService:
    def __init__(
        self,
        *,
        repository: DeviceDefinitionRepository,
        registry: Registry,
        host: EngineHost,
        on_change: Callable[[], None] | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self._repo = repository
        self._registry = registry
        self._host = host
        self._on_change = on_change or (lambda: None)
        self._connect_timeout = connect_timeout

    # ---- definitions (data) ----

    def list(self) -> Sequence[DeviceDefinition]:
        return self._repo.list_definitions()

    def get(self, name: str) -> DeviceDefinition:
        d = self._repo.get_definition(name)
        if d is None:
            raise DeviceDefinitionError(f"No device definition named {name!r}")
        return d

    def create(self, definition: DeviceDefinition) -> DeviceDefinition:
        self._validate(definition)
        if self._repo.get_definition(definition.name) is not None:
            raise DeviceDefinitionError(f"A definition named {definition.name!r} already exists")
        self._repo.save_definition(definition)
        self._on_change()
        return definition

    def update(self, definition: DeviceDefinition) -> DeviceDefinition:
        self._validate(definition)
        existing = self.get(definition.name)
        updated = DeviceDefinition(
            name=definition.name,
            class_path=definition.class_path,
            prefix=definition.prefix,
            kwargs=definition.kwargs,
            enabled=definition.enabled,
            created_at=existing.created_at,
        ).touched()
        self._repo.save_definition(updated)
        self._on_change()
        return updated

    def delete(self, name: str) -> None:
        self.get(name)
        if self.is_instantiated(name):
            self.remove_instance(name)
        self._repo.delete_definition(name)
        self._on_change()

    def _validate(self, definition: DeviceDefinition) -> None:
        if not definition.name or not definition.name.isidentifier():
            raise DeviceDefinitionError("Device name must be a valid Python identifier")
        entry = self._registry.devices().get(definition.name)
        if entry is not None and entry.origin == "profile":
            raise DeviceDefinitionError(
                f"{definition.name!r} is defined by the profile and cannot be redefined"
            )
        import_class(definition.class_path)  # fail early with a clear message

    # ---- live instances (engine thread) ----

    def is_instantiated(self, name: str) -> bool:
        entry = self._registry.devices().get(name)
        return entry is not None and entry.origin == "definition"

    def instantiate(self, name: str, *, timeout: float | None = None) -> Any:
        definition = self.get(name)
        if not definition.enabled:
            raise DeviceDefinitionError(f"Definition {name!r} is disabled")
        device = self._host.call(lambda: self._construct_on_engine_thread(definition), timeout=timeout)
        try:
            self._registry.add_device(name, device)
        except RegistryError as exc:
            raise DeviceDefinitionError(str(exc)) from exc
        self._on_change()
        return device

    def remove_instance(self, name: str) -> None:
        try:
            self._registry.remove_device(name)
        except RegistryError as exc:
            raise DeviceDefinitionError(str(exc)) from exc
        self._on_change()

    def instantiate_all_enabled(self) -> dict[str, str]:
        """At startup: instantiate every enabled definition; returns name → error for failures."""
        failures: dict[str, str] = {}
        for definition in self.list():
            if not definition.enabled:
                continue
            try:
                self.instantiate(definition.name)
            except Exception as exc:  # noqa: BLE001 - one bad device must not stop startup
                logger.exception("Could not instantiate device definition %s", definition.name)
                failures[definition.name] = f"{type(exc).__name__}: {exc}"
        return failures

    def _construct_on_engine_thread(self, definition: DeviceDefinition) -> Any:
        cls = import_class(definition.class_path)
        kwargs = dict(definition.kwargs)
        kwargs.setdefault("name", definition.name)
        device = cls(definition.prefix, **kwargs) if definition.prefix else cls(**kwargs)
        connect = getattr(device, "connect", None)
        if callable(connect) and inspect.iscoroutinefunction(connect):
            engine = self._host.engine
            loop = getattr(engine, "loop", None)
            if loop is None or not loop.is_running():
                raise DeviceDefinitionError(
                    "Cannot connect an ophyd-async device: the engine loop is not running"
                )
            fut = asyncio.run_coroutine_threadsafe(connect(), loop)
            fut.result(timeout=self._connect_timeout)
        return device
