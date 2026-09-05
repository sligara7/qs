"""qs error codes: a stable, searchable catalogue with a remedy for each.

Every fault qs reports carries one of these codes in its log headline, in ``qs.last_error`` and
in HTTP failure bodies (``code``). ``docs/errors.md`` is generated from this table
(``python -m qs.errors > docs/errors.md``) so the documentation and the code cannot drift; a
test checks they match. Decided 2026-09-04 (dec:open-logging-and-errors).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ErrorCode(enum.StrEnum):
    PROFILE_LOAD = "QS-PROFILE-LOAD"
    CONFIG = "QS-CONFIG"
    PLAN_FAIL = "QS-PLAN-FAIL"
    PLAN_ABORT = "QS-PLAN-ABORT"
    PLAN_HALT = "QS-PLAN-HALT"
    PLAN_STOP = "QS-PLAN-STOP"
    ITEM_INVALID = "QS-ITEM-INVALID"
    QUEUE_EMPTY = "QS-QUEUE-EMPTY"
    QUEUE_REFUSED = "QS-QUEUE-REFUSED"
    EXPERIMENT_UNSYNCED = "QS-EXPERIMENT-UNSYNCED"
    DB_UNAVAILABLE = "QS-DB-UNAVAILABLE"
    ENGINE_FAULT = "QS-ENGINE-FAULT"
    ENGINE_BUSY = "QS-ENGINE-BUSY"
    AUTH = "QS-AUTH"
    UNSUPPORTED = "QS-UNSUPPORTED"
    INTERNAL = "QS-INTERNAL"


@dataclass(frozen=True)
class ErrorInfo:
    code: ErrorCode
    title: str
    meaning: str
    remedy: str


CATALOGUE: dict[ErrorCode, ErrorInfo] = {
    info.code: info
    for info in (
        ErrorInfo(
            ErrorCode.PROFILE_LOAD,
            "The profile-collection did not load",
            "A startup file raised while qs executed it, or a device failed to connect during the "
            "load. The headline names the file, line and function in the profile and the innermost "
            "exception. qs exits: it never serves a half-loaded profile.",
            "Fix the profile (or the IOC it needs) and restart the service. `qs --list-plans` "
            "reproduces the load without serving.",
        ),
        ErrorInfo(
            ErrorCode.CONFIG,
            "Configuration is invalid",
            "The YAML file, an environment variable or a command-line flag could not be applied.",
            "Correct the setting named in the message; see the README Configuration table.",
        ),
        ErrorInfo(
            ErrorCode.PLAN_FAIL,
            "A plan raised an exception",
            "The RunEngine finished the item with exit status `fail`. The headline gives the root "
            "cause (innermost exception, e.g. the PV that timed out) and, when the fault is in "
            "profile code, the profile file, line and function. The queue stopped afterwards "
            "(stop-and-wait).",
            "Read the root cause: a PV timeout points at an IOC or network problem; an "
            "AttributeError/TypeError in a profile file is a profile bug. Fix, then `qsctl queue "
            "start`. The full traceback is in the item's history entry and in the log at DEBUG.",
        ),
        ErrorInfo(
            ErrorCode.PLAN_ABORT,
            "A plan was aborted",
            "Someone (or a suspender) aborted the running plan; its cleanup ran and the item is in "
            "history with exit status `abort`. The queue stopped.",
            "Nothing to fix unless the abort was unexpected; `qsctl queue start` to continue.",
        ),
        ErrorInfo(
            ErrorCode.PLAN_HALT,
            "A plan was halted",
            "The running plan was halted: it ended immediately WITHOUT running its cleanup. Devices "
            "may be left staged or in motion.",
            "Check device state (shutters, motors, detectors) before continuing.",
        ),
        ErrorInfo(
            ErrorCode.PLAN_STOP,
            "A plan was stopped early",
            "The running plan was stopped gracefully; data collected so far is kept.",
            "Nothing to fix; `qsctl queue start` to continue.",
        ),
        ErrorInfo(
            ErrorCode.ITEM_INVALID,
            "A queue item cannot be run",
            "The item names a plan that is not in the profile, a device that does not exist, or "
            "arguments the plan does not accept.",
            "Check `qsctl plans` / `qsctl devices` and the item's args; edit or remove the item.",
        ),
        ErrorInfo(
            ErrorCode.QUEUE_EMPTY,
            "The queue is empty",
            "`queue/start` was called with nothing to run (bluesky-queueserver's wording is kept "
            "for client compatibility).",
            "Add items first.",
        ),
        ErrorInfo(
            ErrorCode.QUEUE_REFUSED,
            "A queue operation was refused",
            "The queue is in a state where the operation makes no sense (e.g. stop while not "
            "running, start with no profile loaded).",
            "Check `qsctl status`.",
        ),
        ErrorInfo(
            ErrorCode.EXPERIMENT_UNSYNCED,
            "No experiment is synced",
            "engine.require_synced_experiment is on and the profile's RE.md has no `data_session`, "
            "so data would be written without a proposal.",
            "Run `sync-experiment` (nslsii) for the proposal, then start the queue again.",
        ),
        ErrorInfo(
            ErrorCode.DB_UNAVAILABLE,
            "The queue database is unreachable",
            "A history entry could not be written (or the queue could not be read). The finished "
            "item's outcome is held in memory (`qs.pending_history`) and the queue stopped; the "
            "running plan was never affected.",
            "Restore the database. `qsctl queue start` writes the held entries first and refuses "
            "until that succeeds.",
        ),
        ErrorInfo(
            ErrorCode.ENGINE_FAULT,
            "The engine host hit an internal fault",
            "Something outside the plan (the engine thread, an event consumer, a bookkeeping step) "
            "raised. The plan's outcome is still recorded; the queue stopped.",
            "Report it with the log line; the traceback is at DEBUG. Start the queue again.",
        ),
        ErrorInfo(
            ErrorCode.ENGINE_BUSY,
            "The engine is busy or idle in a way that refuses the command",
            "pause/resume/abort/stop/halt was sent in a state that cannot accept it (e.g. resume "
            "while nothing is paused).",
            "Check `qsctl status` for the engine state.",
        ),
        ErrorInfo(
            ErrorCode.AUTH,
            "Authentication failed",
            "The request carried no API key, an unknown key, or a key without the needed scope.",
            "Send `Authorization: ApiKey <key>` with the configured QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY.",
        ),
        ErrorInfo(
            ErrorCode.UNSUPPORTED,
            "The route is not supported by qs",
            "bluesky-httpserver operations that qs deliberately does not implement (locks, "
            "function/script execution, IPython kernel, test routes) answer success=false.",
            "Use the queue: everything qs does happens through plans.",
        ),
        ErrorInfo(
            ErrorCode.INTERNAL,
            "Unhandled internal error",
            "An HTTP handler raised an exception qs did not anticipate. The running plan is unaffected.",
            "Report it with the log line (journalctl shows the traceback at DEBUG).",
        ),
    )
}


def render_markdown() -> str:
    lines = [
        "# qs error codes",
        "",
        "Generated from `src/qs/errors.py` (`python -m qs.errors > docs/errors.md`). Each code appears",
        "in the log headline, in `qs.last_error` and in the `code` field of HTTP failure bodies.",
        "",
    ]
    for info in CATALOGUE.values():
        lines += [f"## {info.code}: {info.title}", "", info.meaning, "", f"**Remedy:** {info.remedy}", ""]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(render_markdown(), end="")
