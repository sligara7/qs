"""The queue loop.

While the queue is started (or autostart is enabled), take the head of the queue, resolve it
through the Registry, submit it to the Engine Host, wait for the outcome, and record it in
history. Owns queue start/stop/autostart state (``req:queue-start-stop-semantics``) and the
stop-and-wait failure policy (``req:failure-stop-and-wait``): on abort or failure the item is
recorded as such, the queue stops, and nothing further runs until a human starts it again.

Runs on its own thread. A fault here (a database error, a bad item) stops the queue and is
reported; it never reaches the engine thread, and a plan already running finishes.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any

from qs.engine.events import EventBus
from qs.engine.host import EngineHost, EngineHostError, EngineState, PlanOutcome
from qs.queue.models import HistoryEntry, ItemState, QueueItem
from qs.queue.service import QueueError, QueueService
from qs.registry import Registry, RegistryError

logger = logging.getLogger(__name__)

_EXIT_TO_STATE = {
    "success": ItemState.COMPLETED,
    "abort": ItemState.ABORTED,
    "stop": ItemState.STOPPED,
    "halt": ItemState.HALTED,
    "fail": ItemState.FAILED,
}


class SequencerError(RuntimeError):
    pass


class Sequencer:
    def __init__(
        self,
        *,
        host: EngineHost,
        queue: QueueService,
        registry: Registry,
        events: EventBus,
        poll_interval: float = 0.1,
    ) -> None:
        self._host = host
        self._queue = queue
        self._registry = registry
        self._events = events
        self._poll_interval = poll_interval

        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._running = False  # queue started
        self._stop_pending = False
        self._autostart = False
        self._loop_mode = False
        self._closing = False
        self._thread: threading.Thread | None = None
        self._running_item: QueueItem | None = None
        self._last_error: str | None = None

    # ---- lifecycle ---------------------------------------------------------------

    def start_thread(self) -> None:
        if self._thread is not None:
            raise SequencerError("Sequencer thread already started")
        self._thread = threading.Thread(target=self._loop, name="qs-sequencer", daemon=True)
        self._thread.start()

    def close(self, timeout: float | None = 10.0) -> None:
        self._closing = True
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # ---- observation -------------------------------------------------------------

    @property
    def queue_running(self) -> bool:
        return self._running

    @property
    def stop_pending(self) -> bool:
        return self._stop_pending

    @property
    def autostart(self) -> bool:
        return self._autostart

    @property
    def loop_mode(self) -> bool:
        return self._loop_mode

    @property
    def running_item(self) -> QueueItem | None:
        return self._running_item

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ---- control (httpserver semantics) -------------------------------------------

    def queue_start(self) -> None:
        with self._lock:
            if self._host.state is EngineState.NO_ENGINE:
                raise SequencerError("No profile loaded; the queue cannot start")
            self._running = True
            self._stop_pending = False
        self._events.emit("queue_state", running=True, stop_pending=False, autostart=self._autostart)
        self._wakeup.set()

    def queue_stop(self) -> None:
        """Finish the current item, then stop."""
        with self._lock:
            if not self._running:
                raise SequencerError("The queue is not running")
            self._stop_pending = True
        self._events.emit("queue_state", running=True, stop_pending=True, autostart=self._autostart)

    def queue_stop_cancel(self) -> None:
        with self._lock:
            self._stop_pending = False
        self._events.emit("queue_state", running=self._running, stop_pending=False, autostart=self._autostart)

    def set_autostart(self, enable: bool) -> None:
        with self._lock:
            self._autostart = bool(enable)
        self._events.emit(
            "queue_state", running=self._running, stop_pending=self._stop_pending, autostart=enable
        )
        self._wakeup.set()

    def set_loop_mode(self, enable: bool) -> None:
        with self._lock:
            self._loop_mode = bool(enable)

    def execute_now(self, item: QueueItem) -> None:
        """httpserver ``queue/item/execute``: put the item at the front and start the queue."""
        self._queue.push_front(item)
        self.queue_start()

    # ---- the loop ----------------------------------------------------------------

    def _loop(self) -> None:
        while not self._closing:
            if not self._should_run():
                self._wakeup.wait(self._poll_interval)
                self._wakeup.clear()
                continue
            item = self._queue.pop_front()
            if item is None:
                self._wakeup.wait(self._poll_interval)
                self._wakeup.clear()
                continue
            self._run_one(item)

    def _should_run(self) -> bool:
        with self._lock:
            if self._closing:
                return False
            if self._running and self._stop_pending:
                self._running = False
                self._stop_pending = False
                self._events.emit("queue_state", running=False, stop_pending=False, autostart=self._autostart)
                return False
            if self._autostart and not self._running and len(self._queue) > 0:
                self._running = True
                self._events.emit("queue_state", running=True, stop_pending=False, autostart=True)
            return self._running and self._host.state is EngineState.IDLE

    def _run_one(self, item: QueueItem) -> None:
        self._running_item = item
        time_start = time.time()
        self._events.emit("item_started", item_uid=item.item_uid, name=item.name)
        try:
            factory = self._registry.resolve(item.name, item.args, item.kwargs)
            md = dict(item.meta) if item.meta else {}
            outcome = self._host.run_plan(factory, item_uid=item.item_uid, metadata=md).result()
        except (RegistryError, QueueError, EngineHostError) as exc:
            outcome = PlanOutcome(
                item_uid=item.item_uid,
                exit_status="fail",
                reason=str(exc),
                exception=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            logger.exception("Sequencer fault while running %s", item.item_uid)
            outcome = PlanOutcome(
                item_uid=item.item_uid,
                exit_status="fail",
                reason=str(exc),
                exception=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
        except BaseException:
            self._running_item = None
            raise
        # Keep the item reported as running until its history row and the queue state are
        # written, so a client polling status never sees "idle, nothing running" before the
        # outcome is visible.
        self._record(item, outcome, time_start)
        if outcome.succeeded:
            if self._loop_mode:
                self._safe(lambda: self._queue.add(item.with_new_uid()))
        else:
            # Stop-and-wait: the queue stops itself; a human starts it again.
            with self._lock:
                self._running = False
                self._stop_pending = False
            self._last_error = outcome.exception or outcome.reason or outcome.exit_status
            self._events.emit(
                "queue_state",
                running=False,
                stop_pending=False,
                autostart=self._autostart,
                reason=self._last_error,
            )
        self._running_item = None

    def _record(self, item: QueueItem, outcome: PlanOutcome, time_start: float) -> None:
        entry = HistoryEntry(
            item=item,
            state=_EXIT_TO_STATE.get(outcome.exit_status, ItemState.FAILED),
            exit_status=outcome.exit_status,
            run_uids=outcome.run_uids,
            time_start=time_start,
            time_stop=time.time(),
            msg=outcome.exception or outcome.reason,
            traceback=outcome.traceback,
        )
        self._safe(lambda: self._queue.record_history(entry))
        self._events.emit(
            "item_finished", item_uid=item.item_uid, name=item.name, exit_status=outcome.exit_status
        )

    def _safe(self, fn: Any) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("Sequencer bookkeeping failed; queue stops")
            with self._lock:
                self._running = False
            self._last_error = traceback.format_exc()
