"""Persistence: SQLAlchemy models and repositories (``cmp:persistence``).

The operator chooses the database engine; this package only needs a SQLAlchemy URL.
"""

from qs.persistence.database import Database
from qs.persistence.device_repository import InMemoryDeviceDefinitionRepository, SqlDeviceDefinitionRepository
from qs.persistence.memory import InMemoryQueueRepository
from qs.persistence.queue_repository import SqlQueueRepository

__all__ = [
    "Database",
    "InMemoryDeviceDefinitionRepository",
    "InMemoryQueueRepository",
    "SqlDeviceDefinitionRepository",
    "SqlQueueRepository",
]
