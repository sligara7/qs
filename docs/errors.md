# qs error codes

Generated from `src/qs/errors.py` (`python -m qs.errors > docs/errors.md`). Each code appears
in the log headline, in `qs.last_error` and in the `code` field of HTTP failure bodies.

## QS-PROFILE-LOAD: The profile-collection did not load

A startup file raised while qs executed it, or a device failed to connect during the load. The headline names the file, line and function in the profile and the innermost exception. qs exits: it never serves a half-loaded profile.

**Remedy:** Fix the profile (or the IOC it needs) and restart the service. `qs --list-plans` reproduces the load without serving.

## QS-CONFIG: Configuration is invalid

The YAML file, an environment variable or a command-line flag could not be applied.

**Remedy:** Correct the setting named in the message; see the README Configuration table.

## QS-PLAN-FAIL: A plan raised an exception

The RunEngine finished the item with exit status `fail`. The headline gives the root cause (innermost exception, e.g. the PV that timed out) and, when the fault is in profile code, the profile file, line and function. The queue stopped afterwards (stop-and-wait).

**Remedy:** Read the root cause: a PV timeout points at an IOC or network problem; an AttributeError/TypeError in a profile file is a profile bug. Fix, then `qsctl queue start`. The full traceback is in the item's history entry and in the log at DEBUG.

## QS-PLAN-ABORT: A plan was aborted

Someone (or a suspender) aborted the running plan; its cleanup ran and the item is in history with exit status `abort`. The queue stopped.

**Remedy:** Nothing to fix unless the abort was unexpected; `qsctl queue start` to continue.

## QS-PLAN-HALT: A plan was halted

The running plan was halted: it ended immediately WITHOUT running its cleanup. Devices may be left staged or in motion.

**Remedy:** Check device state (shutters, motors, detectors) before continuing.

## QS-PLAN-STOP: A plan was stopped early

The running plan was stopped gracefully; data collected so far is kept.

**Remedy:** Nothing to fix; `qsctl queue start` to continue.

## QS-ITEM-INVALID: A queue item cannot be run

The item names a plan that is not in the profile, a device that does not exist, or arguments the plan does not accept.

**Remedy:** Check `qsctl plans` / `qsctl devices` and the item's args; edit or remove the item.

## QS-QUEUE-EMPTY: The queue is empty

`queue/start` was called with nothing to run (bluesky-queueserver's wording is kept for client compatibility).

**Remedy:** Add items first.

## QS-QUEUE-REFUSED: A queue operation was refused

The queue is in a state where the operation makes no sense (e.g. stop while not running, start with no profile loaded).

**Remedy:** Check `qsctl status`.

## QS-EXPERIMENT-UNSYNCED: No experiment is synced

engine.require_synced_experiment is on and the profile's RE.md has no `data_session`, so data would be written without a proposal.

**Remedy:** Run `sync-experiment` (nslsii) for the proposal, then start the queue again.

## QS-DB-UNAVAILABLE: The queue database is unreachable

A history entry could not be written (or the queue could not be read). The finished item's outcome is held in memory (`qs.pending_history`) and the queue stopped; the running plan was never affected.

**Remedy:** Restore the database. `qsctl queue start` writes the held entries first and refuses until that succeeds.

## QS-ENGINE-FAULT: The engine host hit an internal fault

Something outside the plan (the engine thread, an event consumer, a bookkeeping step) raised. The plan's outcome is still recorded; the queue stopped.

**Remedy:** Report it with the log line; the traceback is at DEBUG. Start the queue again.

## QS-ENGINE-BUSY: The engine is busy or idle in a way that refuses the command

pause/resume/abort/stop/halt was sent in a state that cannot accept it (e.g. resume while nothing is paused).

**Remedy:** Check `qsctl status` for the engine state.

## QS-AUTH: Authentication failed

The request carried no API key, an unknown key, or a key without the needed scope.

**Remedy:** Send `Authorization: ApiKey <key>` with the configured QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY.

## QS-UNSUPPORTED: The route is not supported by qs

bluesky-httpserver operations that qs deliberately does not implement (locks, function/script execution, IPython kernel, test routes) answer success=false.

**Remedy:** Use the queue: everything qs does happens through plans.

## QS-INTERNAL: Unhandled internal error

An HTTP handler raised an exception qs did not anticipate. The running plan is unaffected.

**Remedy:** Report it with the log line (journalctl shows the traceback at DEBUG).
