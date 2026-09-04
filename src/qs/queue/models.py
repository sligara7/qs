"""Queue items in bluesky-httpserver's shape (``req:finch-client-compat``).

An item is ``{item_type, name, args, kwargs, meta, item_uid, user, user_group, result}``;
history entries are items with a ``result`` dict carrying ``exit_status``, ``run_uids``,
``time_start``, ``time_stop``, ``msg``, ``traceback``.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any


def new_item_uid() -> str:
    return str(uuid.uuid4())


class ItemState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    STOPPED = "stopped"
    HALTED = "halted"


@dataclass(frozen=True)
class QueueItem:
    name: str
    item_type: str = "plan"
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    item_uid: str = field(default_factory=new_item_uid)
    user: str = ""
    user_group: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_type": self.item_type,
            "name": self.name,
            "args": list(self.args),
            "kwargs": dict(self.kwargs),
            "meta": dict(self.meta),
            "item_uid": self.item_uid,
            "user": self.user,
            "user_group": self.user_group,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, user: str = "", user_group: str = "") -> QueueItem:
        return cls(
            name=str(data["name"]),
            item_type=str(data.get("item_type", "plan")),
            args=list(data.get("args") or []),
            kwargs=dict(data.get("kwargs") or {}),
            meta=dict(data.get("meta") or {}),
            item_uid=str(data.get("item_uid") or new_item_uid()),
            user=str(data.get("user") or user),
            user_group=str(data.get("user_group") or user_group),
        )

    def with_new_uid(self) -> QueueItem:
        return replace(self, item_uid=new_item_uid(), created_at=time.time())


@dataclass(frozen=True)
class HistoryEntry:
    """An item that has been executed, with its result."""

    item: QueueItem
    state: ItemState
    exit_status: str
    run_uids: tuple[str, ...] = ()
    time_start: float = 0.0
    time_stop: float = 0.0
    msg: str = ""
    traceback: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = self.item.to_dict()
        d["result"] = {
            "exit_status": self.exit_status,
            "run_uids": list(self.run_uids),
            "scan_ids": [],
            "time_start": self.time_start,
            "time_stop": self.time_stop,
            "msg": self.msg,
            "traceback": self.traceback,
        }
        return d
