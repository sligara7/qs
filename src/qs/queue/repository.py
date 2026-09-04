"""The QueueRepository protocol (interface ``ifc:queue-repository``).

Declared by the Queue, implemented by Persistence (SQLAlchemy) and by an in-memory stand-in
for tests. Every method is atomic with respect to the others; the Sequencer relies on
:meth:`QueueRepository.pop_front` to hand out each item exactly once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from qs.queue.models import HistoryEntry, QueueItem


@runtime_checkable
class QueueRepository(Protocol):
    """Ordered storage of queued items plus the executed-item history."""

    # ---- queue ----
    def list_items(self) -> Sequence[QueueItem]: ...

    def get_item(self, item_uid: str) -> QueueItem | None: ...

    def insert(self, item: QueueItem, index: int | None = None) -> None:
        """Insert at ``index`` (``None`` = back). Raises ``ValueError`` on a duplicate uid."""
        ...

    def replace(self, item: QueueItem) -> None:
        """Replace the item with the same uid in place. Raises ``KeyError`` if absent."""
        ...

    def remove(self, item_uid: str) -> QueueItem:
        """Remove and return. Raises ``KeyError`` if absent."""
        ...

    def move(self, item_uid: str, index: int) -> None:
        """Move an item to ``index``. Raises ``KeyError`` if absent."""
        ...

    def clear(self) -> int: ...

    def pop_front(self) -> QueueItem | None:
        """Atomically remove and return the first item, or ``None`` if the queue is empty."""
        ...

    # ---- history ----
    def append_history(self, entry: HistoryEntry) -> None: ...

    def list_history(self) -> Sequence[HistoryEntry]: ...

    def clear_history(self) -> int: ...
