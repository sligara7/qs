# qs

A Bluesky queue service and the replacement for `bluesky-queueserver` at NSLS-II.

It loads a beamline profile (NSLS-II IPython-style profile-collection, APS BITS
instrument, or a happi device database), keeps the plans and ophyd / ophyd-async devices
the profile defines, and runs a SQL-backed queue of plans **one at a time** through a
**single RunEngine** that lives on its own thread. Everything is controlled over HTTP
using the `bluesky-httpserver` API shape, so `finch` and `blop` work unchanged.

Design principles, from the design record in `.reflow2/`:

- **Reliability first.** Nothing the engine needs to run a plan passes through the HTTP
  layer or the database. A fault there never touches the running plan.
- **The queue is the lock.** All hardware control happens inside plans on the RunEngine.
- **HTTP only.** No ZMQ. TLS is terminated at a reverse proxy in production.
- **Adopt the profile's engine.** If the profile creates a RunEngine (as `nslsii.configure_base`
  does), the service uses that engine, keeping every callback the profile subscribed.
- Typed `Protocol` seams, composition over inheritance, dependency injection at the
  composition root.

## Layout

```
src/qs/
  engine/     Engine Host: the engine thread, command channel, event stream, progress watcher
  sources/    ProfileSource protocol and the IPython / BITS / happi implementations
  registry/   In-memory catalogue of plans and devices
  ...         (queue, sequencer, persistence, api, runtime follow)
tests/
```

## Configuration

Everything can be set three ways; later entries win:

1. a YAML file named by `--config` or `QSERVER_CONFIG` (sections `startup`, `database`,
   `http`, `engine`, `tiled`; see `tools/qs-hex-sim.example.yml`),
2. environment variables (`QSERVER_*` names carried over from bluesky-queueserver /
   bluesky-httpserver where the concern survives, `QS_*` for the rest),
3. flags on the `qs` command (`qs --help`).

| setting | YAML | environment | flag |
|---|---|---|---|
| HTTP port (default 60610, finch's default) | `http.port` | `QS_HTTP_PORT` | `--port` |
| bind address (default 127.0.0.1; TLS at a reverse proxy) | `http.host` | `QS_HTTP_HOST` | `--host` |
| single-user API key | `http.api_key` | `QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY` | `--api-key` |
| CORS origins (finch dev server) | `http.allow_origins` | `QSERVER_HTTP_SERVER_ALLOW_ORIGINS` | |
| profile startup directory | `startup.startup_dir` | `QS_STARTUP_DIR` | `--startup-dir` |
| queue database (any SQLAlchemy URL) | `database.url` | `QS_DATABASE_URL` | `--database-url` |
| stream device progress on the info websocket | `engine.stream_device_progress` | `QS_STREAM_DEVICE_PROGRESS` | |
| refuse to run until an experiment is synced | `engine.require_synced_experiment` | `QS_REQUIRE_SYNCED_EXPERIMENT` | |

The profile runs in-process and inherits the service's environment unchanged, so beamline
variables it needs (`BEAMLINE_ACRONYM` / `ENDSTATION_ACRONYM` for `nslsii`'s path providers,
Redis, Tiled and Kafka settings) are set on the service, exactly as for an IPython session.

### Deployment

qs needs the profile's own environment, so it runs inside it: for a pixi-managed profile,
`pixi run -e <env> qs --config /etc/qs/qs.yml`. `docs/deploy/qs.service` is a systemd unit
that does exactly that, with the API key and beamline variables in an environment file and
the port set there. Python 3.12 or newer. Logs go to the journal: `journalctl -fu qs.service`.

Before serving, `qs --config ... --list-plans` loads the profile the way the service would and
prints its devices and plans, which is the quickest check that a deployment will start.

### Driving qs from a terminal

`qsctl` is the operator command line (`qserver` cannot talk to qs: it speaks ZeroMQ). It reads
`QS_URL` (default http://localhost:60610) and `QS_API_KEY`, or `--url` / `--api-key`.

```
qsctl status                    qsctl watch
qsctl plans   |  qsctl devices  |  qsctl experiment
qsctl queue add count '[["det"]]' --kwargs '{"num": 3}' [--pos front]
qsctl queue get | start | stop | stop-cancel | autostart on|off | clear
qsctl queue remove <uid> | move <uid> front|back|<index>
qsctl re pause [--immediate] | resume | abort | stop | halt
qsctl history [--clear]
```

Every command prints a short summary (`--json` for the server's reply) and exits 1 when qs
refuses or cannot be reached.

### The synced experiment

At NSLS-II `sync-experiment` (from `nslsii`) writes the proposal into the Redis-backed
`RE.md` the profile shares with every process on the beamline. qs does not run it; it shows
the result read-only in `/api/status` under `qs.experiment` (`data_session`, `cycle`,
`proposal`, `username`, `start_datetime`) and, with `engine.require_synced_experiment`,
refuses `queue/start` and autostart until `data_session` is set.

### Reading the logs

`journalctl -fu qs.service` is meant to tell the story without the code. Every fault is one
line: a code from `docs/errors.md`, the layer in brackets, the item, the root cause and what qs
did, for example

```
ERROR qs.sequencer.sequencer: [QS-PLAN-FAIL] tomo_dark_flat 80930add fail after 6.1 s: AttributeError: 'HEXKinetixDetector' object has no attribute '_writer' (at 85-fly-plans.py:133 in tomo_dark_flat); queue stopped, waiting for a human
ERROR qs.sequencer.sequencer: [QS-PLAN-FAIL] count 43dbda96 fail after 23.9 s: TimeoutError: ca://XF:27ID1-BI{Kinetix-Det:1}HDF1:Capture; queue stopped, waiting for a human
```

The root cause is the innermost exception (through bluesky's `FailedStatus` into the device
status), and `at file:line in function` names the deepest frame inside the profile. State
changes get one INFO line each (`[queue] started: 3 item(s) to run`, `[engine] idle -> running`,
`[queue] empty, idle`). Tracebacks, from qs and from every library, are kept out of INFO and
above and shown only with `--log-level DEBUG`; the full traceback of a failed item is always in
its history entry over HTTP. The same code appears in `qs.last_error` and in the `code` field of
HTTP failure bodies; `docs/errors.md` (generated from `src/qs/errors.py`) explains each code and
its remedy.

### When the database goes away

A plan that is running is never touched. If the queue database is unreachable when an item
finishes, its outcome is held in memory, the queue stops, and status shows
`qs.database_ok: false` with the error and the number of held entries. The next `queue/start`
writes the held entries first and refuses to run until that succeeds. `/api/status` keeps
answering throughout, with the last known counts.

## Testing

```
uv run pytest                       # unit + integration; UI and acceptance tests skip themselves
uv run playwright install chromium  # once, for tests/ui (or an installed Chrome is used)
```

- `tests/` — engine host, queue, sequencer, HTTP API, bluesky-queueserver-api client, finch
  call replay (`tools/finch_client_check.mjs`, needs Node), the Playwright smoke-page test,
  `qsctl` and `--list-plans`, the BITS demo instrument and a happi database (dev extra), and
  the recorded conventions (no ZeroMQ, Python floor).
- `tests/acceptance/` — successful plans and failure drills against the simulated HEX
  beamline (`hex-ob/hex-simulated-beamline`, referenced, never vendored). Run with
  `QS_HEX_SIM_URL=http://127.0.0.1:60610 QS_API_KEY=... uv run pytest -m acceptance` once
  qs serves the HEX profile (`tools/qs-hex-sim.example.yml`) and the sim's `scripts/env.sh`
  is sourced. `tools/hex_sim_fault_tests.sh` is the shell form of the same drills.
- `.github/workflows/ci.yml` runs lint and the suite per pull request;
  `sim-acceptance.yml` runs the acceptance layer nightly / on demand (advisory).
- `tools/finch_playwright_check.py` drives finch's dev app against qs with Playwright
  (`docs/finch-queue-server-on-qs.png` is its output); `tools/hextools-sim-profile/` is the
  hextools trial profile.

## Development

```
uv sync --extra dev
uv run pytest
```
