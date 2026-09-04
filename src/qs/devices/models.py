"""A device definition: class, PV prefix, keyword arguments (``req:device-crud-semantics``)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class DeviceDefinition:
    name: str
    class_path: str  # e.g. "ophyd.sim.SynAxis" or "ophyd_async.epics.motor.Motor"
    prefix: str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "class_path": self.class_path,
            "prefix": self.prefix,
            "kwargs": dict(self.kwargs),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceDefinition:
        return cls(
            name=str(data["name"]),
            class_path=str(data["class_path"]),
            prefix=str(data.get("prefix") or ""),
            kwargs=dict(data.get("kwargs") or {}),
            enabled=bool(data.get("enabled", True)),
        )

    def touched(self) -> DeviceDefinition:
        return replace(self, updated_at=time.time())
