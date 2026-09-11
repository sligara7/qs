"""The engine event stream (interface ``ifc:engine-events``).

Events are published from the engine thread (and from the RunEngine's own loop thread for
state changes). Subscribers run synchronously on the publishing thread and are isolated:
an exception in a subscriber is logged and never propagates into the engine. That isolation
is what keeps an API-side fault from touching a running plan.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

Subscriber = Callable[["EngineEvent"], None]


class EventKind(enum.StrEnum):
    """Every kind of event the engine publishes — the single definition of the vocabulary.

    A ``StrEnum`` for the same reason :class:`qs.errors.ErrorCode` is one: members compare,
    hash and serialise exactly as their string value, so the wire format the monitoring
    websockets emit (``ifc:monitor-ws``) is unchanged and a subscriber may still match on a
    plain string. Adding a kind means adding it here, which is what makes
    :data:`STATUS_CHANGE_KINDS` below impossible to forget.
    """

    STATE = "state"
    """Engine host lifecycle state changed. Payload: ``state``, ``previous``."""
    RE_STATE = "re_state"
    """The RunEngine's own state string changed. Payload: ``state``, ``previous``."""
    SOURCE_LOADED = "source_loaded"
    """A profile source finished loading. Payload: ``description``, ``n_devices``,
    ``n_plans``, ``engine_adopted``."""
    PLAN_STARTED = "plan_started"
    """The engine began executing a plan. Payload: ``item_uid``."""
    PLAN_FINISHED = "plan_finished"
    """The engine finished executing a plan. Payload: ``item_uid``, ``outcome``."""
    DEVICE_PROGRESS = "device_progress"
    """The queueserver ``device_progress`` payload, from the RunEngine waiting hook."""
    QUEUE_STATE = "queue_state"
    """The sequencer's view of the queue changed. Payload: ``running``, ``stop_pending``,
    ``autostart``, and optionally ``reason``."""
    ITEM_STARTED = "item_started"
    """The sequencer took an item off the queue. Payload: ``item_uid``, ``name``."""
    ITEM_FINISHED = "item_finished"
    """The sequencer finished an item. Payload: ``item_uid``, ``name``, ``exit_status``."""
    CONSOLE_OUTPUT = "console_output"
    """One captured console message. Payload: ``time``, ``msg``."""


STATUS_CHANGE_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.STATE,
        EventKind.RE_STATE,
        EventKind.QUEUE_STATE,
        EventKind.PLAN_STARTED,
        EventKind.PLAN_FINISHED,
        EventKind.ITEM_STARTED,
        EventKind.ITEM_FINISHED,
    }
)
"""The kinds that mean "the status document changed, push a new one".

Defined here rather than at each websocket because it was previously written out twice in
``qs.api.routers.ws``, and a kind missing from one copy makes the UI stop updating silently
instead of failing.
"""


@dataclass(frozen=True)
class EngineEvent:
    """One thing the engine reported."""

    kind: EventKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    time: float = field(default_factory=time.time)


class EventBus:
    """Thread-safe fan-out of :class:`EngineEvent` to subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[int, Subscriber] = {}
        self._next_id = 0
        self._lock = threading.Lock()

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register ``callback``; returns a function that unsubscribes it."""
        with self._lock:
            token = self._next_id
            self._next_id += 1
            self._subscribers[token] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(token, None)

        return unsubscribe

    def publish(self, event: EngineEvent) -> None:
        """Deliver ``event`` to every subscriber, isolating each from the others."""
        with self._lock:
            subscribers = list(self._subscribers.values())
        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - isolation is the point
                logger.exception("Engine event subscriber %r raised on %s", callback, event.kind)

    def emit(self, kind: EventKind, **payload: Any) -> None:
        """Convenience: build and publish an event."""
        self.publish(EngineEvent(kind=kind, payload=payload))
