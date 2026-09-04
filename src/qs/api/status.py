"""The ``/api/status`` document, in bluesky-queueserver's shape.

``manager_state`` follows queueserver's vocabulary: ``idle`` (nothing running),
``executing_queue`` (queue started and/or an item running), ``paused`` (the engine is paused),
``creating_environment`` / ``closing_environment`` never occur here because the profile is
loaded at startup. ``re_state`` is bluesky's own state string.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from qs import __version__
from qs.engine.host import EngineHost, EngineState
from qs.queue.service import QueueService
from qs.registry import Registry
from qs.sequencer import Sequencer


def _uid_of(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


class StatusReporter:
    def __init__(
        self, *, host: EngineHost, queue: QueueService, sequencer: Sequencer, registry: Registry
    ) -> None:
        self._host = host
        self._queue = queue
        self._sequencer = sequencer
        self._registry = registry
        self._registry_uid = _uid_of("registry", 0)
        self._registry_revision = 0

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
        history = self._queue.history()
        qsize = len(self._queue)
        return {
            "msg": f"qs v{__version__} (bluesky-queueserver compatible)",
            "items_in_queue": qsize,
            "items_in_history": len(history),
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
            "plan_history_uid": _uid_of("history", len(history)),
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
