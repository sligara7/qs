"""The ``/api/status`` document, in bluesky-queueserver's shape.

``manager_state`` follows queueserver's vocabulary: ``idle`` (nothing running),
``executing_queue`` (queue started and/or an item running), ``paused`` (the engine is paused),
``creating_environment`` / ``closing_environment`` never occur here because the profile is
loaded at startup. ``re_state`` is bluesky's own state string.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from qs import __version__
from qs.engine import EngineHost, EngineState
from qs.queue import QueueService
from qs.registry import Registry
from qs.sequencer import Sequencer

logger = logging.getLogger(__name__)


def _uid_of(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


class StatusReporter:
    def __init__(
        self,
        *,
        host: EngineHost,
        queue: QueueService,
        sequencer: Sequencer,
        registry: Registry,
        stall_after: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._host = host
        self._queue = queue
        self._sequencer = sequencer
        self._registry = registry
        self._registry_uid = _uid_of("registry", 0)
        self._registry_revision = 0
        self._last_counts = (0, 0)  # (items_in_queue, items_in_history) when the database last answered
        self._database_error: str | None = None
        self._stall_after = stall_after
        self._clock = clock
        self._last_progress_at = clock()
        self._last_collected = 0
        self._stall_announced = False

    def _progress_and_stall(self) -> dict[str, Any]:
        """How far the running plan has got, and whether it has stopped getting anywhere.

        qs NEVER aborts a plan on a timer (``dec:open-a-plan-that-never-returns``). An overnight
        series or a tomography flyscan is legitimately hours long, and beamtime killed by a
        default cannot be recovered. This only says so: an alarm that cannot act.

        The stall test is "the collected-event count has not moved for ``stall_after`` seconds
        while a plan is running". It is derived here rather than on a timer because the
        sequencer's thread is parked inside the running plan and has no tick to spare.

        ⚠️ THE LIMIT THAT MATTERS FOR LATER: this is computed when somebody asks for status, so
        it notices only while something is watching — which is true today (the user, 2026-09-11:
        "for now, somebody will watch it") because an open websocket rebuilds this about once a
        second. When qs is to run unwatched, this needs a tick of its own to sit on, and the
        notification channel that would consume it. Nothing here has to be undone for that.
        """
        collected = self._host.collected_events()
        total = int(collected["total"])
        now = self._clock()
        running = self._host.state is EngineState.RUNNING
        if total != self._last_collected or not running:
            self._last_collected = total
            self._last_progress_at = now
            self._stall_announced = False
        quiet_for = now - self._last_progress_at
        stalled = running and quiet_for >= self._stall_after
        if stalled and not self._stall_announced:
            self._stall_announced = True
            logger.warning(
                "[engine] no data collected for %.0f s while a plan is running; qs is not "
                "stopping it. Check the devices it is waiting on, or abort if it is wedged.",
                quiet_for,
            )
        return {
            "collected_events": total,
            "runs_in_progress": collected["runs"],
            "seconds_since_progress": round(quiet_for, 1) if running else None,
            "plan_stalled": stalled,
        }

    def bump_registry(self) -> None:
        self._registry_revision += 1
        self._registry_uid = _uid_of("registry", self._registry_revision)

    @property
    def registry_uid(self) -> str:
        return self._registry_uid

    def manager_state(self) -> str:
        host_state = self._host.state
        if host_state is EngineState.PAUSED:
            return "paused"
        if host_state is EngineState.RUNNING or self._sequencer.queue_running:
            return "executing_queue"
        return "idle"

    def running_item(self) -> dict[str, Any]:
        item = self._sequencer.running_item
        if item is None:
            return {}
        return item.to_dict()

    def snapshot(self) -> dict[str, Any]:
        host = self._host
        seq = self._sequencer
        env_exists = host.state not in (EngineState.STARTING, EngineState.NO_ENGINE, EngineState.CLOSED)
        running_item = seq.running_item
        try:
            qsize = len(self._queue)
            history_len = len(self._queue.history())
            self._last_counts = (qsize, history_len)
            self._database_error = None
        except Exception as exc:  # noqa: BLE001 - status must answer while the database is down
            qsize, history_len = self._last_counts
            self._database_error = f"{type(exc).__name__}: {exc}"
        pending = seq.pending_history
        return {
            "msg": f"qs v{__version__} (bluesky-queueserver compatible)",
            "items_in_queue": qsize,
            "items_in_history": history_len + len(pending),
            "running_item_uid": running_item.item_uid if running_item else None,
            "manager_state": self.manager_state(),
            "queue_stop_pending": seq.stop_pending,
            "queue_autostart_enabled": seq.autostart,
            "worker_environment_exists": env_exists,
            "worker_environment_state": self._env_state(),
            "worker_background_tasks": 0,
            "re_state": host.re_state,
            "ip_kernel_state": None,
            "ip_kernel_captured": None,
            "pause_pending": False,
            "run_list_uid": _uid_of("runs", host.last_outcome.run_uids if host.last_outcome else ()),
            "plan_queue_uid": _uid_of("queue", self._queue.revision),
            "plan_history_uid": _uid_of("history", history_len + len(pending)),
            "devices_existing_uid": self._registry_uid,
            "plans_existing_uid": self._registry_uid,
            "devices_allowed_uid": self._registry_uid,
            "plans_allowed_uid": self._registry_uid,
            "plan_queue_mode": {"loop": seq.loop_mode, "ignore_failures": False},
            "task_results_uid": _uid_of("tasks", 0),
            "lock_info_uid": _uid_of("lock", 0),
            "lock": {"environment": False, "queue": False},
            # qs additions (harmless to httpserver clients):
            "qs": {
                "engine_state": host.state.value,
                "engine_adopted": host.engine_adopted,
                "last_error": host.last_error or seq.last_error,
                "engine_subscribers": host.subscribers(),
                "experiment": host.experiment_metadata(),
                "database_ok": self._database_error is None and seq.database_error is None,
                "database_error": self._database_error or seq.database_error,
                "pending_history": len(pending),
                # Plan-level progress and the stall alarm. Inside the qs namespace, not beside
                # it: bluesky-queueserver has no equivalent, and one qs-owned key is easier to
                # keep honest than two.
                "progress": self._progress_and_stall(),
                "require_synced_experiment": seq.require_synced_experiment,
            },
        }

    def _env_state(self) -> str:
        state = self._host.state
        if state in (EngineState.STARTING, EngineState.NO_ENGINE):
            return "initializing"
        if state is EngineState.CLOSED:
            return "closed"
        if state is EngineState.RUNNING or state is EngineState.PAUSED:
            return "executing_plan"
        if self._host.last_error and not self._sequencer.queue_running:
            return "idle"
        return "idle"
