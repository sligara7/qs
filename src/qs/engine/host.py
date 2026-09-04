"""The Engine Host: one thread, one RunEngine, a command channel and an event stream.

Threading model (accepted decisions ``dec:runengine-own-thread`` and
``dec:adopt-source-runengine``):

* A dedicated engine thread runs everything that touches the RunEngine directly: loading the
  profile source (so an engine the profile creates is born on this thread), ``RE(plan)``,
  ``RE.resume()`` and device instantiation.
* ``RE.request_pause``, ``RE.abort``, ``RE.stop`` and ``RE.halt`` are thread-safe in bluesky
  and are called directly from the caller's thread, so a control request never waits behind
  the plan that is blocking the engine thread.
* Nothing here imports FastAPI or SQLAlchemy. Faults in subscribers are isolated by the
  :class:`~qs.engine.events.EventBus`.
"""

from __future__ import annotations

import enum
import logging
import os
import queue
import threading
import traceback
from collections.abc import Callable, Generator
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from bluesky.run_engine import RunEngine, RunEngineInterrupted

from qs.engine.events import EventBus
from qs.engine.progress import ProgressWatcher
from qs.sources.protocol import LoadResult, ProfileSource

logger = logging.getLogger(__name__)

#: bluesky-queueserver's worker sets this so profiles' ``is_re_worker_active()`` is true.
#: qs sets it for the same reason (``cap:qserver-env``).
RE_WORKER_ACTIVE_ENV = "_QSERVER_RE_WORKER_ACTIVE"
RUNNING_IPYTHON_KERNEL_ENV = "_QSERVER_RUNNING_IPYTHON_KERNEL"


class EngineState(enum.StrEnum):
    """The host's view of the engine, one level above bluesky's own state machine."""

    STARTING = "starting"
    NO_ENGINE = "no_engine"  # thread up, no source loaded yet
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CLOSED = "closed"


@dataclass(frozen=True)
class PlanOutcome:
    """What happened to one plan submitted through :meth:`EngineHost.run_plan`."""

    item_uid: str
    exit_status: str  # "success" | "abort" | "fail" | "halt" | "stop"
    run_uids: tuple[str, ...] = ()
    reason: str = ""
    exception: str = ""
    traceback: str = ""
    plan_result: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.exit_status == "success"


class EngineHostError(RuntimeError):
    """Raised for host-level misuse (no engine, wrong state, host closed)."""


_Command = tuple[Callable[[], Any], Future[Any]]


class EngineHost:
    """See module docstring. Construct, :meth:`start`, :meth:`load_source`, then submit plans."""

    def __init__(
        self,
        *,
        events: EventBus,
        engine_factory: Callable[[], RunEngine] | None = None,
        progress_enabled: bool = False,
        progress_min_update_period: float = 0.2,
        thread_name: str = "qs-engine",
    ) -> None:
        self._events = events
        self._engine_factory = engine_factory or (lambda: RunEngine({}))
        self._progress_enabled = progress_enabled
        self._progress_min_update_period = progress_min_update_period
        self._thread_name = thread_name

        self._commands: queue.Queue[_Command | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._engine: RunEngine | None = None
        self._engine_adopted = False
        self._load_result: LoadResult | None = None
        self._progress: ProgressWatcher | None = None
        self._state = EngineState.STARTING
        self._state_lock = threading.Lock()

        # Directive used while a plan is paused: "resume" | "abort" | "stop" | "halt".
        self._directive: str | None = None
        self._directive_cv = threading.Condition()
        self._current_item_uid: str | None = None
        self._last_outcome: PlanOutcome | None = None
        self._last_error: str | None = None

    # ---- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise EngineHostError("EngineHost already started")
        self._thread = threading.Thread(target=self._run_loop, name=self._thread_name, daemon=True)
        self._thread.start()
        self._set_state(EngineState.NO_ENGINE)

    def shutdown(self, timeout: float | None = 10.0) -> None:
        """Stop accepting commands and join the thread. Does not abort a running plan."""
        if self._thread is None:
            return
        self._commands.put(None)
        self._thread.join(timeout)
        self._set_state(EngineState.CLOSED)

    @property
    def engine_thread(self) -> threading.Thread | None:
        return self._thread

    def on_engine_thread(self) -> bool:
        return threading.current_thread() is self._thread

    # ---- observation ---------------------------------------------------------------

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    @property
    def engine(self) -> RunEngine | None:
        return self._engine

    @property
    def engine_adopted(self) -> bool:
        return self._engine_adopted

    @property
    def load_result(self) -> LoadResult | None:
        return self._load_result

    @property
    def current_item_uid(self) -> str | None:
        return self._current_item_uid

    @property
    def last_outcome(self) -> PlanOutcome | None:
        return self._last_outcome

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def re_state(self) -> str | None:
        """bluesky's own state string, or ``None`` before an engine exists."""
        return None if self._engine is None else str(self._engine.state)

    # ---- command channel (ifc:engine-commands) -------------------------------------

    def submit(self, fn: Callable[[], Any]) -> Future[Any]:
        """Run ``fn`` on the engine thread; the future carries its result or exception."""
        if self._thread is None or self.state is EngineState.CLOSED:
            raise EngineHostError("EngineHost is not running")
        fut: Future[Any] = Future()
        self._commands.put((fn, fut))
        return fut

    def call(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        """Like :meth:`submit` but wait for the result."""
        return self.submit(fn).result(timeout)

    def load_source(self, source: ProfileSource, timeout: float | None = None) -> LoadResult:
        """Load ``source`` on the engine thread and adopt (or create) the RunEngine."""
        return self.call(lambda: self._load_source_on_thread(source), timeout)

    def run_plan(
        self,
        plan_factory: Callable[[], Generator[Any, Any, Any]],
        *,
        item_uid: str,
        metadata: dict[str, Any] | None = None,
    ) -> Future[PlanOutcome]:
        """Execute a plan on the engine thread. The future resolves when the plan is over.

        ``plan_factory`` is called on the engine thread so the generator is created there.
        """
        if self._engine is None:
            raise EngineHostError("No RunEngine: load a profile source first")
        if self.state is not EngineState.IDLE:
            raise EngineHostError(f"Engine is {self.state.value}; cannot start a plan")
        return self.submit(lambda: self._run_plan_on_thread(plan_factory, item_uid, metadata or {}))

    # ---- control (thread-safe; callable from any thread) ---------------------------

    def request_pause(self, *, defer: bool = False) -> None:
        engine = self._require_engine()
        engine.request_pause(defer=defer)

    def resume(self) -> None:
        """Resume a paused plan. Returns immediately; the plan's future resolves later."""
        self._require_engine()
        self._set_directive("resume")

    def abort(self, reason: str = "") -> None:
        self._interrupt("abort", reason)

    def stop(self) -> None:
        self._interrupt("stop", "")

    def halt(self) -> None:
        self._interrupt("halt", "")

    def _interrupt(self, kind: str, reason: str) -> None:
        engine = self._require_engine()
        if self.state is EngineState.PAUSED:
            # The engine thread is waiting on the directive; let it perform the interruption.
            self._abort_reason = reason
            self._set_directive(kind)
            return
        if self.state is EngineState.RUNNING:
            # bluesky's abort/stop/halt are thread-safe while running.
            if kind == "abort":
                engine.abort(reason=reason)
            elif kind == "stop":
                engine.stop()
            else:
                engine.halt()
            return
        raise EngineHostError(f"Engine is {self.state.value}; nothing to {kind}")

    # ---- engine-thread internals ---------------------------------------------------

    def _run_loop(self) -> None:
        while True:
            item = self._commands.get()
            if item is None:
                break
            fn, fut = item
            if fut.set_running_or_notify_cancel() is False:
                continue
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 - reported through the future
                fut.set_exception(exc)

    def _load_source_on_thread(self, source: ProfileSource) -> LoadResult:
        os.environ[RE_WORKER_ACTIVE_ENV] = "1"
        os.environ[RUNNING_IPYTHON_KERNEL_ENV] = "0"
        result = source.load()
        if result.engine is not None:
            engine, adopted = result.engine, True
        else:
            engine, adopted = self._engine_factory(), False
        self._install_engine(engine, adopted)
        self._load_result = result
        self._events.emit(
            "source_loaded",
            description=result.source_description or source.description,
            n_devices=len(result.devices),
            n_plans=len(result.plans),
            engine_adopted=adopted,
        )
        return result

    def _install_engine(self, engine: RunEngine, adopted: bool) -> None:
        self._engine = engine
        self._engine_adopted = adopted
        # Ask bluesky for a RunEngineResult instead of a bare uid tuple.
        engine._call_returns_result = True  # noqa: SLF001 - documented bluesky flag
        # bluesky's default context manager installs a SIGINT handler around every
        # RE(plan); that only works on the main thread. Interrupts reach this engine over
        # HTTP (request_pause/abort/...), so drop it and keep anything else the profile set.
        engine.context_managers = [
            cm for cm in engine.context_managers if getattr(cm, "__name__", "") != "SigintHandler"
        ]
        previous_state_hook = engine.state_hook

        def state_hook(new_state: Any, old_state: Any) -> None:
            if previous_state_hook is not None:
                try:
                    previous_state_hook(new_state, old_state)
                except Exception:  # noqa: BLE001
                    logger.exception("Profile state_hook raised; continuing")
            self._events.emit("re_state", state=str(new_state), previous=str(old_state))

        engine.state_hook = state_hook
        if self._progress_enabled:
            watcher = ProgressWatcher(
                self._events,
                min_update_period=self._progress_min_update_period,
                chained_hook=engine.waiting_hook,
            )
            engine.waiting_hook = watcher
            self._progress = watcher
        self._set_state(EngineState.IDLE)

    def _run_plan_on_thread(
        self,
        plan_factory: Callable[[], Generator[Any, Any, Any]],
        item_uid: str,
        metadata: dict[str, Any],
    ) -> PlanOutcome:
        engine = self._require_engine()
        self._current_item_uid = item_uid
        self._last_error = None
        self._abort_reason = ""
        self._set_state(EngineState.RUNNING)
        self._events.emit("plan_started", item_uid=item_uid)
        outcome: PlanOutcome
        try:
            plan = plan_factory()
            result = engine(plan, **metadata)
            outcome = self._outcome_from_result(item_uid, result)
        except RunEngineInterrupted:
            outcome = self._handle_pause(item_uid)
        except Exception as exc:  # noqa: BLE001 - a failed plan is an outcome, not a crash
            outcome = PlanOutcome(
                item_uid=item_uid,
                exit_status="fail",
                reason=str(exc),
                exception=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
                run_uids=self._collect_run_uids(),
            )
        finally:
            self._current_item_uid = None
        self._last_outcome = outcome
        if not outcome.succeeded:
            self._last_error = outcome.exception or outcome.reason or outcome.exit_status
        self._set_state(EngineState.IDLE)
        self._events.emit("plan_finished", item_uid=item_uid, outcome=outcome)
        return outcome

    def _handle_pause(self, item_uid: str) -> PlanOutcome:
        """Loop while the plan is paused, acting on resume/abort/stop/halt directives."""
        engine = self._require_engine()
        while True:
            if str(engine.state) != "paused":
                # Interrupted but not paused: the run ended (e.g. aborted while pausing).
                return self._outcome_from_engine(item_uid)
            self._set_state(EngineState.PAUSED)
            directive = self._wait_directive()
            self._set_state(EngineState.RUNNING)
            try:
                if directive == "resume":
                    result = engine.resume()
                elif directive == "abort":
                    result = engine.abort(reason=self._abort_reason)
                elif directive == "stop":
                    result = engine.stop()
                else:
                    result = engine.halt()
                return self._outcome_from_result(item_uid, result)
            except RunEngineInterrupted:
                continue
            except Exception as exc:  # noqa: BLE001
                return PlanOutcome(
                    item_uid=item_uid,
                    exit_status="fail",
                    reason=str(exc),
                    exception=f"{type(exc).__name__}: {exc}",
                    traceback=traceback.format_exc(),
                    run_uids=self._collect_run_uids(),
                )

    def _wait_directive(self) -> str:
        with self._directive_cv:
            while self._directive is None:
                self._directive_cv.wait()
            directive, self._directive = self._directive, None
            return directive

    def _set_directive(self, directive: str) -> None:
        if self.state is not EngineState.PAUSED:
            raise EngineHostError(f"Engine is {self.state.value}; can only {directive} when paused")
        with self._directive_cv:
            self._directive = directive
            self._directive_cv.notify_all()

    def _outcome_from_result(self, item_uid: str, result: Any) -> PlanOutcome:
        # bluesky returns a RunEngineResult when _call_returns_result is True.
        exit_status = getattr(result, "exit_status", None) or "success"
        exc = getattr(result, "exception", None)
        return PlanOutcome(
            item_uid=item_uid,
            exit_status=str(exit_status),
            run_uids=tuple(getattr(result, "run_start_uids", ()) or ()),
            reason=str(getattr(result, "reason", "") or ""),
            exception="" if exc is None else f"{type(exc).__name__}: {exc}",
            plan_result=getattr(result, "plan_result", None),
            extra={"interrupted": bool(getattr(result, "interrupted", False))},
        )

    def _outcome_from_engine(self, item_uid: str) -> PlanOutcome:
        engine = self._require_engine()
        exit_status = getattr(engine, "_exit_status", None) or "abort"  # noqa: SLF001
        return PlanOutcome(
            item_uid=item_uid,
            exit_status=str(exit_status),
            reason=str(getattr(engine, "_reason", "") or ""),  # noqa: SLF001
            run_uids=self._collect_run_uids(),
        )

    def _collect_run_uids(self) -> tuple[str, ...]:
        engine = self._engine
        if engine is None:
            return ()
        uids = getattr(engine, "_run_start_uids", None)  # noqa: SLF001
        return tuple(uids or ())

    def _require_engine(self) -> RunEngine:
        if self._engine is None:
            raise EngineHostError("No RunEngine: load a profile source first")
        return self._engine

    def _set_state(self, state: EngineState) -> None:
        with self._state_lock:
            previous, self._state = self._state, state
        if previous is not state:
            self._events.emit("state", state=state.value, previous=previous.value)
