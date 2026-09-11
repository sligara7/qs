"""The queue service (interface ``ifc:queue-service``): what the API and Sequencer call.

Validates items against the Registry, generates uids, and implements httpserver's item
operations: add (with ``pos``, ``before_uid``, ``after_uid``), update, move, remove, batch
forms, clear, get, and history. Queue start/stop/autostart state belongs to the Sequencer.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Sequence
from typing import Any

from qs.queue.models import HistoryEntry, QueueItem
from qs.queue.repository import QueueRepository
from qs.registry import Registry, RegistryError
from qs.sources import PlanFactory


class QueueError(ValueError):
    """A refused queue operation; ``msg`` is fit to return to a caller."""


class QueueService:
    def __init__(self, repository: QueueRepository, registry: Registry) -> None:
        self._repo = repository
        self._registry = registry
        self._lock = threading.RLock()
        self._plan_queue_uid = 0  # bumps on every change; reported to clients as plan_queue_uid

    # ---- observation ---------------------------------------------------------------

    @property
    def revision(self) -> int:
        return self._plan_queue_uid

    def items(self) -> Sequence[QueueItem]:
        return self._repo.list_items()

    def get(self, item_uid: str) -> QueueItem:
        item = self._repo.get_item(item_uid)
        if item is None:
            raise QueueError(f"Item {item_uid!r} is not in the queue")
        return item

    def history(self) -> Sequence[HistoryEntry]:
        return self._repo.list_history()

    def __len__(self) -> int:
        return len(self._repo.list_items())

    # ---- validation ----------------------------------------------------------------

    def validate(self, item: QueueItem) -> None:
        if item.item_type != "plan":
            raise QueueError(
                f"Unsupported item type {item.item_type!r}: this service runs Bluesky plans only"
            )
        try:
            plan = self._registry.get_plan(item.name)
        except RegistryError as exc:
            raise QueueError(str(exc)) from exc
        if not isinstance(item.args, list) or not isinstance(item.kwargs, dict):
            raise QueueError("Item 'args' must be a list and 'kwargs' a dict")
        self._check_parameters(plan, item)

    @staticmethod
    def _check_parameters(plan: PlanFactory, item: QueueItem) -> None:
        """Can these args and kwargs actually call this plan?

        Bound, not type-checked. A real profile is full of plans carrying no annotations —
        bluesky's own ``bp.scan`` among them — so binding is the check that degrades
        gracefully, and it still catches what actually reaches a queue: the wrong number of
        arguments, a misspelled keyword, a required parameter nobody supplied.

        Values are not inspected, deliberately. A device is named by a string here and becomes
        an object only when the Registry resolves it at run time, so this cannot and should not
        judge whether ``"motor"`` is a real device — the Registry does that, and its refusal is
        already a QueueError too.

        Without this the mistake was found at execution instead, where req:failure-stop-and-wait
        correctly stops the whole queue: a typo became a stopped beamline hours later.
        """
        try:
            signature = inspect.signature(plan)
        except (TypeError, ValueError):
            return  # not introspectable; execution is then the only place that can know
        try:
            signature.bind(*item.args, **item.kwargs)
        except TypeError as exc:
            raise QueueError(f"Item does not fit plan {item.name!r}: {exc}") from exc

    # ---- mutation ------------------------------------------------------------------

    def add(
        self,
        item: QueueItem,
        *,
        pos: int | str | None = None,
        before_uid: str | None = None,
        after_uid: str | None = None,
    ) -> tuple[QueueItem, int]:
        """Add ``item``; returns the stored item and its index."""
        self.validate(item)
        with self._lock:
            if self._repo.get_item(item.item_uid) is not None:
                item = item.with_new_uid()
            index = self._resolve_index(pos, before_uid, after_uid, inserting=True)
            self._repo.insert(item, index)
            self._bump()
            return item, self._index_of(item.item_uid)

    def add_batch(self, items: Sequence[QueueItem], **kwargs: Any) -> list[tuple[QueueItem, int]]:
        for item in items:
            self.validate(item)  # all-or-nothing validation, as httpserver does
        with self._lock:
            return [self.add(item, **kwargs) for item in items]

    def update(self, item: QueueItem, *, replace_uid: bool = False) -> QueueItem:
        self.validate(item)
        with self._lock:
            existing = self.get(item.item_uid)
            new_item = item.with_new_uid() if replace_uid else item
            if replace_uid:
                index = self._index_of(existing.item_uid)
                self._repo.remove(existing.item_uid)
                self._repo.insert(new_item, index)
            else:
                self._repo.replace(new_item)
            self._bump()
            return new_item

    def remove(self, item_uid: str) -> QueueItem:
        with self._lock:
            try:
                item = self._repo.remove(item_uid)
            except KeyError:
                raise QueueError(f"Item {item_uid!r} is not in the queue") from None
            self._bump()
            return item

    def remove_batch(self, uids: Sequence[str], *, ignore_missing: bool = True) -> list[QueueItem]:
        with self._lock:
            removed: list[QueueItem] = []
            for uid in uids:
                try:
                    removed.append(self.remove(uid))
                except QueueError:
                    if not ignore_missing:
                        raise
            return removed

    def move(
        self,
        item_uid: str,
        *,
        pos: int | str | None = None,
        before_uid: str | None = None,
        after_uid: str | None = None,
    ) -> int:
        with self._lock:
            item = self.get(item_uid)
            if before_uid == item_uid or after_uid == item_uid:
                raise QueueError("An item cannot be moved relative to itself")
            # Compute the target index as if the item were absent, then re-insert it there.
            self._repo.remove(item_uid)
            try:
                index = self._resolve_index(pos, before_uid, after_uid, inserting=True)
            except QueueError:
                self._repo.insert(item, self._index_of_or_back(item))
                raise
            self._repo.insert(item, index)
            self._bump()
            return self._index_of(item_uid)

    def clear(self) -> int:
        with self._lock:
            n = self._repo.clear()
            self._bump()
            return n

    def pop_front(self) -> QueueItem | None:
        with self._lock:
            item = self._repo.pop_front()
            if item is not None:
                self._bump()
            return item

    def push_front(self, item: QueueItem) -> None:
        with self._lock:
            self._repo.insert(item, 0)
            self._bump()

    def record_history(self, entry: HistoryEntry) -> None:
        with self._lock:
            self._repo.append_history(entry)
            self._bump()

    def clear_history(self) -> int:
        with self._lock:
            n = self._repo.clear_history()
            self._bump()
            return n

    # ---- helpers -------------------------------------------------------------------

    def _bump(self) -> None:
        self._plan_queue_uid += 1

    def _index_of(self, item_uid: str) -> int:
        for i, it in enumerate(self._repo.list_items()):
            if it.item_uid == item_uid:
                return i
        raise QueueError(f"Item {item_uid!r} is not in the queue")

    def _resolve_index(
        self,
        pos: int | str | None,
        before_uid: str | None,
        after_uid: str | None,
        *,
        inserting: bool,
    ) -> int:
        n = len(self._repo.list_items())
        if sum(x is not None for x in (pos, before_uid, after_uid)) > 1:
            raise QueueError("Specify at most one of 'pos', 'before_uid', 'after_uid'")
        if before_uid is not None:
            return self._index_of(before_uid)
        if after_uid is not None:
            return self._index_of(after_uid) + 1
        if pos is None or pos == "back":
            return n
        if pos == "front":
            return 0
        if isinstance(pos, int):
            if pos < 0:
                pos = n + pos + (1 if inserting else 0)
            return max(0, min(pos, n))
        raise QueueError(f"Invalid position {pos!r}: use 'front', 'back' or an integer")

    def _index_of_or_back(self, item: QueueItem) -> int:
        return len(self._repo.list_items())
