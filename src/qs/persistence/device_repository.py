"""SQLAlchemy and in-memory implementations of :class:`DeviceDefinitionRepository`."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from sqlalchemy import select

from qs.devices.models import DeviceDefinition
from qs.persistence.database import Database, DeviceDefinitionRow


class SqlDeviceDefinitionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_definitions(self) -> Sequence[DeviceDefinition]:
        with self._db.session() as s:
            rows = s.scalars(select(DeviceDefinitionRow).order_by(DeviceDefinitionRow.name)).all()
            return [self._to_model(r) for r in rows]

    def get_definition(self, name: str) -> DeviceDefinition | None:
        with self._db.session() as s:
            row = s.get(DeviceDefinitionRow, name)
            return None if row is None else self._to_model(row)

    def save_definition(self, definition: DeviceDefinition) -> None:
        with self._db.session() as s:
            row = s.get(DeviceDefinitionRow, definition.name)
            if row is None:
                row = DeviceDefinitionRow(name=definition.name, created_at=definition.created_at)
                s.add(row)
            row.class_path = definition.class_path
            row.prefix = definition.prefix
            row.kwargs = dict(definition.kwargs)
            row.enabled = definition.enabled
            row.created_at = definition.created_at
            row.updated_at = definition.updated_at

    def delete_definition(self, name: str) -> bool:
        with self._db.session() as s:
            row = s.get(DeviceDefinitionRow, name)
            if row is None:
                return False
            s.delete(row)
            return True

    @staticmethod
    def _to_model(row: DeviceDefinitionRow) -> DeviceDefinition:
        return DeviceDefinition(
            name=row.name,
            class_path=row.class_path,
            prefix=row.prefix,
            kwargs=dict(row.kwargs or {}),
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class InMemoryDeviceDefinitionRepository:
    def __init__(self) -> None:
        self._items: dict[str, DeviceDefinition] = {}
        self._lock = threading.Lock()

    def list_definitions(self) -> Sequence[DeviceDefinition]:
        with self._lock:
            return sorted(self._items.values(), key=lambda d: d.name)

    def get_definition(self, name: str) -> DeviceDefinition | None:
        with self._lock:
            return self._items.get(name)

    def save_definition(self, definition: DeviceDefinition) -> None:
        with self._lock:
            self._items[definition.name] = definition

    def delete_definition(self, name: str) -> bool:
        with self._lock:
            return self._items.pop(name, None) is not None
