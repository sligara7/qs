"""The engine event stream (interface ``ifc:engine-events``).

Events are published from the engine thread (and from the RunEngine's own loop thread for
state changes). Subscribers run synchronously on the publishing thread and are isolated:
an exception in a subscriber is logged and never propagates into the engine. That isolation
is what keeps an API-side fault from touching a running plan.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

Subscriber = Callable[["EngineEvent"], None]


@dataclass(frozen=True)
class EngineEvent:
    """One thing the engine reported.

    ``kind`` is one of: ``state`` (payload: ``state``, ``previous``), ``plan_started``
    (``item_uid``), ``plan_finished`` (``item_uid``, ``outcome``), ``device_progress``
    (the queueserver ``device_progress`` payload), ``device_instantiated`` /
    ``device_removed`` (``name``), ``source_loaded`` (``description``, ``n_devices``,
    ``n_plans``, ``engine_adopted``).
    """

    kind: str
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

    def emit(self, kind: str, **payload: Any) -> None:
        """Convenience: build and publish an event."""
        self.publish(EngineEvent(kind=kind, payload=payload))
