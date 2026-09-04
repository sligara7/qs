"""In-memory :class:`QueueRepository` for tests and for running without a database."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from qs.queue.models import HistoryEntry, QueueItem


class InMemoryQueueRepository:
    def __init__(self) -> None:
        self._items: list[QueueItem] = []
        self._history: list[HistoryEntry] = []
        self._lock = threading.RLock()

    def list_items(self) -> Sequence[QueueItem]:
        with self._lock:
            return list(self._items)

    def get_item(self, item_uid: str) -> QueueItem | None:
        with self._lock:
            return next((i for i in self._items if i.item_uid == item_uid), None)

    def insert(self, item: QueueItem, index: int | None = None) -> None:
        with self._lock:
            if self.get_item(item.item_uid) is not None:
                raise ValueError(f"Duplicate item uid {item.item_uid!r}")
            if index is None:
                self._items.append(item)
            else:
                self._items.insert(max(0, min(index, len(self._items))), item)

    def replace(self, item: QueueItem) -> None:
        with self._lock:
            for i, existing in enumerate(self._items):
                if existing.item_uid == item.item_uid:
                    self._items[i] = item
                    return
            raise KeyError(item.item_uid)

    def remove(self, item_uid: str) -> QueueItem:
        with self._lock:
            for i, existing in enumerate(self._items):
                if existing.item_uid == item_uid:
                    return self._items.pop(i)
            raise KeyError(item_uid)

    def move(self, item_uid: str, index: int) -> None:
        with self._lock:
            item = self.remove(item_uid)
            self.insert(item, index)

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items.clear()
            return n

    def pop_front(self) -> QueueItem | None:
        with self._lock:
            return self._items.pop(0) if self._items else None

    def append_history(self, entry: HistoryEntry) -> None:
        with self._lock:
            self._history.append(entry)

    def list_history(self) -> Sequence[HistoryEntry]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> int:
        with self._lock:
            n = len(self._history)
            self._history.clear()
            return n
