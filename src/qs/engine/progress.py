"""Device-progress streaming from the RunEngine waiting hook.

Reproduces the mechanism of Jakub Wlodek's ``bluesky-queueserver`` branch
``stream-watcher-updates`` (``manager/plan_monitoring.WatcherStreamManager``): the RunEngine
calls its ``waiting_hook`` with the set of Status objects it is waiting on, and with ``None``
when the wait ends. For each status that supports ``watch()`` we subscribe a callback and
publish throttled ``device_progress`` events; ``{"completed": True}`` is published when the
wait ends. Any hook the profile already installed (e.g. a ``ProgressBarManager``) is chained,
never replaced. See ``dec:progress-via-waiting-hook``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from qs.engine.events import EventBus

logger = logging.getLogger(__name__)

WaitingHook = Callable[[Any], None]

#: The device_progress payload fields, in queueserver's order.
_FIELDS = (
    "name",
    "current",
    "initial",
    "target",
    "unit",
    "precision",
    "fraction",
    "time_elapsed",
    "time_remaining",
    "done",
)


class ProgressWatcher:
    """A ``RunEngine.waiting_hook``-compatible object that streams device progress."""

    def __init__(
        self,
        events: EventBus,
        *,
        min_update_period: float = 0.2,
        chained_hook: WaitingHook | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._events = events
        self._min_update_period = min_update_period
        self._chained_hook = chained_hook
        self._clock = clock
        self._lock = threading.Lock()
        self._watched: set[int] = set()
        self._last_sent: dict[int, float] = {}
        self._last_payload: dict[int, dict[str, Any]] = {}
        self._counter = 0

    @property
    def chained_hook(self) -> WaitingHook | None:
        return self._chained_hook

    @chained_hook.setter
    def chained_hook(self, hook: WaitingHook | None) -> None:
        self._chained_hook = hook or None

    def __call__(self, status_objs_or_none: Iterable[Any] | None) -> None:
        if self._chained_hook is not None:
            try:
                self._chained_hook(status_objs_or_none)
            except Exception:  # noqa: BLE001
                logger.exception("Chained waiting_hook raised; continuing")
        if status_objs_or_none is None:
            self._send_completed()
            with self._lock:
                self._watched.clear()
                self._last_sent.clear()
                self._last_payload.clear()
                self._counter = 0
            return
        for status in status_objs_or_none:
            key = id(status)
            with self._lock:
                if key in self._watched:
                    continue
                self._watched.add(key)
                self._counter += 1
                label = self._counter
            if not hasattr(status, "watch") or getattr(status, "done", False):
                continue
            try:
                status.watch(self._make_callback(status, label))
            except Exception:  # noqa: BLE001
                logger.debug("Status %r does not support watch()", status, exc_info=True)
                continue
            # Watch callbacks can fire before the status is marked done (ophyd.sim does), so
            # the final ``done`` update is driven by the status completion callback.
            add_callback = getattr(status, "add_callback", None)
            if callable(add_callback):
                try:
                    add_callback(self._make_completion(status, label))
                except Exception:  # noqa: BLE001
                    logger.debug("Status %r rejected a completion callback", status, exc_info=True)

    def _make_completion(self, status: Any, label: int) -> Callable[..., None]:
        key = id(status)

        def on_done(*_args: Any, **_kwargs: Any) -> None:
            with self._lock:
                last = dict(self._last_payload.get(key, {}))
                if last.get("done"):
                    return
            last.setdefault("name", f"status_{label}")
            last.update(fraction=1.0, done=True)
            for field in _FIELDS:
                last.setdefault(field, None)
            with self._lock:
                self._last_payload[key] = last
            self._events.emit("device_progress", **last)

        return on_done

    def _make_callback(self, status: Any, label: int) -> Callable[..., None]:
        key = id(status)

        def callback(
            *,
            name: str | None = None,
            current: Any = None,
            initial: Any = None,
            target: Any = None,
            unit: str | None = None,
            precision: int | None = None,
            fraction: float | None = None,
            time_elapsed: float | None = None,
            time_remaining: float | None = None,
            **_: Any,
        ) -> None:
            done = bool(getattr(status, "done", False)) or (fraction is not None and fraction >= 1.0)
            now = self._clock()
            payload = {
                "name": name if name is not None else f"status_{label}",
                "current": current,
                "initial": initial,
                "target": target,
                "unit": unit,
                "precision": precision,
                "fraction": fraction,
                "time_elapsed": time_elapsed,
                "time_remaining": time_remaining,
                "done": done,
            }
            with self._lock:
                if self._last_payload.get(key, {}).get("done"):
                    return  # completion already reported
                # Keep the richest values seen so a sparse final update still carries them.
                merged = {
                    **self._last_payload.get(key, {}),
                    **{k: v for k, v in payload.items() if v is not None},
                }
                merged["done"] = done
                self._last_payload[key] = merged
                last = self._last_sent.get(key)
                if not done and last is not None and (now - last) < self._min_update_period:
                    return
                self._last_sent[key] = now
            self._events.emit("device_progress", **{f: merged.get(f) for f in _FIELDS})

        return callback

    def _send_completed(self) -> None:
        self._events.emit("device_progress", completed=True)
