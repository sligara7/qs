"""Bridge from the thread-side :class:`EventBus` to asyncio consumers (websockets).

Engine events are published on the engine thread. Each websocket gets its own
``asyncio.Queue`` fed through ``loop.call_soon_threadsafe``; a slow consumer drops the
oldest messages rather than blocking the engine (reliability first).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from qs.engine.events import EngineEvent, EventBus


class EventBroadcaster:
    def __init__(self, events: EventBus, *, max_queue: int = 1000) -> None:
        self._events = events
        self._max_queue = max_queue

    @contextlib.asynccontextmanager
    async def subscribe(
        self, predicate: Callable[[EngineEvent], bool] | None = None
    ) -> AsyncIterator[asyncio.Queue[EngineEvent]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=self._max_queue)

        def deliver(event: EngineEvent) -> None:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

        def on_event(event: EngineEvent) -> None:
            if predicate is not None and not predicate(event):
                return
            with contextlib.suppress(RuntimeError):  # loop closed
                loop.call_soon_threadsafe(deliver, event)

        unsubscribe = self._events.subscribe(on_event)
        try:
            yield queue
        finally:
            unsubscribe()


def json_safe(value: Any) -> Any:
    """Make an event payload JSON-serialisable (numpy scalars, dataclasses, tuples)."""
    if hasattr(value, "item") and callable(value.item) and not isinstance(value, (str, bytes)):
        with contextlib.suppress(Exception):
            return value.item()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: json_safe(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
