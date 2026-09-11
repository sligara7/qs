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

import asyncio
import enum
import logging
import os
import queue
import threading
import traceback
from collections.abc import Callable, Coroutine, Generator, Mapping
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, cast

# bluesky re-exports RunEngineInterrupted without an explicit __all__ entry.
from bluesky.run_engine import RunEngine, RunEngineInterrupted  # type: ignore[attr-defined]

from qs.diagnostics import summarize
from qs.engine.events import EventBus, EventKind
from qs.engine.progress import ProgressWatcher
from qs.sources import LoadResult, ProfileSource

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

    root_cause: str = ""  # innermost exception, e.g. "TimeoutError: ca://..."
    where: str = ""  # deepest frame in profile code, e.g. "85-fly-plans.py:133 in tomo_dark_flat"

    @property
    def succeeded(self) -> bool:
        return self.exit_status == "success"


def _plain(value: Any) -> Any:
    """Copy a metadata value into plain JSON types.

    A Redis-backed RE.md hands back its own mapping and sequence types (redis_json_dict's
    ObservableMapping), which JSON encoders refuse; status must never fail because of that.
    """
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _callback_name(cb: Any) -> str:
    """Human-readable name for a subscribed callback, unwrapping bluesky's bound-method proxy."""
    inst = getattr(cb, "inst", None)
    func = getattr(cb, "func", None)
    if inst is not None or func is not None:
        # bluesky's _BoundMethodProxy: a bound method (inst + func) or a plain callable (func
        # only). A callable instance such as a TiledWriter has no __name__, so use its class.
        target = inst() if callable(inst) and not isinstance(inst, type) else inst
        owner = type(target).__name__ if target is not None else ""
        fname = getattr(func, "__name__", "")
        if owner:
            return f"{owner}.{fname}".strip(".")
        if fname:
            return fname
        return type(func).__name__ if func is not None else type(cb).__name__
    if hasattr(cb, "__self__"):
        return f"{type(cb.__self__).__name__}.{cb.__name__}"
    name: str | None = getattr(cb, "__name__", None)
    if name and name != "<lambda>":
        return name
    return type(cb).__name__ if name is None else f"{type(cb).__name__}:<lambda>"


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

    # ================================================================================
    # cap:engine-isolation — the dedicated thread, and faults contained on it
    #
    # One thread owns the RunEngine for its whole life. Everything that touches the engine
    # directly is funnelled onto it through the command queue below, so a fault raised in the
    # HTTP, persistence or registry layers is reported over the API and never reaches a running
    # plan. See also _run_loop, which turns every exception into a Future's result.
    # ================================================================================

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

    # ================================================================================
    # Read-only views of the engine
    #
    # Two capabilities and one region the design attributes to neither.
    #   cap:experiment-visibility — EXPERIMENT_KEYS and experiment_metadata(), which read the
    #     sync-experiment keys a beamline writes into a shared RE.md. Read off the engine
    #     thread on purpose, so a running plan cannot block a status request.
    #   cap:adopt-engine — engine_adopted and load_result report the outcome of the adoption
    #     performed below in _load_source_on_thread.
    #   Unattributed: state, current_item_uid, last_outcome, last_error, re_state,
    #     engine_metadata(), open_runs() and subscribers() carry no REALIZES edge of their own;
    #     they feed cap:runengine-fault-api and the status document, which the design attributes
    #     to src/qs/api/status.py rather than here.
    # ================================================================================

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

    EXPERIMENT_KEYS = ("data_session", "cycle", "proposal", "username", "start_datetime")

    def experiment_metadata(self) -> dict[str, Any]:
        """The synced experiment as the profile's RE.md records it (read-only).

        At NSLS-II ``sync-experiment`` writes these keys into a Redis-backed RE.md shared by every
        process on the beamline; qs only reads them. Read directly (not on the engine thread) so a
        running plan cannot block a status request; a mapping error is reported, not raised.
        """
        engine = self._engine
        if engine is None:
            return {}
        try:
            md = engine.md
            return {key: _plain(md[key]) for key in self.EXPERIMENT_KEYS if md.get(key) is not None}
        except Exception as exc:  # noqa: BLE001 - RE.md may be a network-backed mapping
            return {"error": f"{type(exc).__name__}: {exc}"}

    def engine_metadata(self) -> dict[str, Any]:
        """The whole of the adopted engine's ``RE.md``, flattened for JSON.

        Values that are not JSON scalars become their ``str()``, one level deep — the shape
        ``/re/metadata`` has always returned. Compare :meth:`experiment_metadata`, which picks
        out the sync-experiment keys and copies them recursively.

        Reading ``RE.md`` can fail: at NSLS-II it is a Redis-backed mapping shared by every
        process on the beamline, so this is a network operation. A failure is reported in the
        payload rather than raised, for the same reason :meth:`experiment_metadata` does it —
        a beamline-wide dependency having a bad moment should degrade one field of one
        endpoint, not answer the operator with a blank server error.
        """
        engine = self._engine
        if engine is None:
            return {}
        try:
            md = dict(engine.md)
        except Exception as exc:  # noqa: BLE001 - RE.md may be a network-backed mapping
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {
            k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in md.items()
        }

    def open_runs(self) -> list[dict[str, Any]]:
        """Runs the engine currently has open: start uid and scan_id where known.

        Reads bluesky's run bundlers, which are private. That is why this lives here: the
        engine box is the one place allowed to know bluesky's internals, so a rename upstream
        breaks one method rather than an HTTP route.
        """
        engine = self._engine
        if engine is None:
            return []
        out: list[dict[str, Any]] = []
        for bundler in getattr(engine, "_run_bundlers", {}).values():  # noqa: SLF001
            uid = getattr(bundler, "_run_start_uid", None)  # noqa: SLF001
            md = getattr(bundler, "_md", None) or getattr(bundler, "md", None) or {}  # noqa: SLF001
            scan_id = md.get("scan_id") if isinstance(md, dict) else None
            out.append({"uid": uid, "is_open": True, "scan_id": scan_id})
        return out

    def subscribers(self) -> dict[str, list[str]]:
        """Names of the callbacks subscribed to the engine, per document type (read-only).

        The service never adds or removes subscribers (``dec:no-service-document-consumers``);
        this exists so an operator can confirm the profile's Tiled writer or BestEffortCallback
        is attached.
        """
        engine = self._engine
        if engine is None:
            return {}
        try:
            registry = engine.dispatcher.cb_registry.callbacks
        except AttributeError:  # pragma: no cover - bluesky internals moved
            return {}
        out: dict[str, list[str]] = {}
        for doc_type, callbacks in registry.items():
            names = sorted({_callback_name(cb) for cb in callbacks.values()})
            if names:
                out[getattr(doc_type, "value", str(doc_type))] = names
        return out

    @property
    def re_state(self) -> str | None:
        """bluesky's own state string, or ``None`` before an engine exists."""
        return None if self._engine is None else str(self._engine.state)

    # ================================================================================
    # ifc:engine-commands — the only way in
    #
    # submit/call put work on the engine thread; run_on_engine_loop puts it on the RunEngine's
    # event loop; load_source and run_plan are the two things the rest of the service asks for.
    # No caller holds the RunEngine itself, which is what the interface promises.
    # ================================================================================

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

    def run_on_engine_loop(self, coro: Coroutine[Any, Any, Any], timeout: float | None = None) -> Any:
        """Drive an awaitable on the RunEngine's own event loop, from any thread.

        An ophyd-async device must be connected on the loop the engine owns. Callers hand the
        coroutine here rather than reaching for the engine and its loop themselves, so that
        knowledge stays inside this box (``ifc:engine-commands``).
        """
        engine = self._require_engine()
        loop = getattr(engine, "loop", None)
        if loop is None or not loop.is_running():
            raise EngineHostError("The engine's event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def load_source(self, source: ProfileSource, timeout: float | None = None) -> LoadResult:
        """Load ``source`` on the engine thread and adopt (or create) the RunEngine."""
        return cast("LoadResult", self.call(lambda: self._load_source_on_thread(source), timeout))

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

    # ================================================================================
    # ifc:engine-commands, continued — interrupts that must not queue behind a plan
    #
    # bluesky makes request_pause/abort/stop/halt thread-safe, so these are called from the
    # caller's thread rather than the engine thread. That is the point: the engine thread is
    # busy running the plan you are trying to interrupt.
    # ================================================================================

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

    # ================================================================================
    # cap:adopt-engine and cap:qserver-env — what happens on the engine thread
    #
    #   cap:adopt-engine — _load_source_on_thread takes the RunEngine the profile created, or
    #     builds one when it made none, and _install_engine binds it: exactly one engine per
    #     service, with the profile's own subscriptions left alone
    #     (dec:no-service-document-consumers).
    #   cap:qserver-env — _load_source_on_thread sets _QSERVER_RE_WORKER_ACTIVE before the
    #     profile runs, so a profile asking is_re_worker_active() gets the same answer it would
    #     under the bluesky-queueserver worker.
    #   The rest — _run_plan_on_thread, _handle_pause, the directive handshake and the outcome
    #     builders — is plan execution, which the design attributes to cap:queue-execution on
    #     the sequencer rather than to this file.
    # ================================================================================

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
            EventKind.SOURCE_LOADED,
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
            logger.info("[engine] %s -> %s", old_state, new_state)
            self._events.emit(EventKind.RE_STATE, state=str(new_state), previous=str(old_state))

        # bluesky annotates state_hook/waiting_hook as None, so a real hook reads as a type error.
        engine.state_hook = state_hook  # type: ignore[assignment]
        if self._progress_enabled:
            watcher = ProgressWatcher(
                self._events,
                min_update_period=self._progress_min_update_period,
                chained_hook=engine.waiting_hook,
            )
            engine.waiting_hook = watcher  # type: ignore[assignment]
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
        self._events.emit(EventKind.PLAN_STARTED, item_uid=item_uid)
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
                root_cause=summarize(exc).root,
                where=summarize(exc).where,
                run_uids=self._collect_run_uids(),
            )
        finally:
            self._current_item_uid = None
        self._last_outcome = outcome
        if not outcome.succeeded:
            self._last_error = outcome.exception or outcome.reason or outcome.exit_status
        self._set_state(EngineState.IDLE)
        self._events.emit(EventKind.PLAN_FINISHED, item_uid=item_uid, outcome=outcome)
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
                    root_cause=summarize(exc).root,
                    where=summarize(exc).where,
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
        summary = summarize(exc) if isinstance(exc, BaseException) else None
        return PlanOutcome(
            item_uid=item_uid,
            exit_status=str(exit_status),
            run_uids=tuple(getattr(result, "run_start_uids", ()) or ()),
            reason=str(getattr(result, "reason", "") or ""),
            exception="" if exc is None else f"{type(exc).__name__}: {exc}",
            traceback=summary.traceback if summary else "",
            root_cause=summary.root if summary else "",
            where=summary.where if summary else "",
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
            self._events.emit(EventKind.STATE, state=state.value, previous=previous.value)
