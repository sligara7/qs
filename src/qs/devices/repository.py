"""The DeviceDefinitionRepository protocol (interface ``ifc:device-definition-repository``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from qs.devices.models import DeviceDefinition


@runtime_checkable
class DeviceDefinitionRepository(Protocol):
    def list_definitions(self) -> Sequence[DeviceDefinition]: ...

    def get_definition(self, name: str) -> DeviceDefinition | None: ...

    def save_definition(self, definition: DeviceDefinition) -> None:
        """Insert or replace by name."""
        ...

    def delete_definition(self, name: str) -> bool:
        """Return whether something was deleted."""
        ...
