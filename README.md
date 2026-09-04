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

## Development

```
uv sync --extra dev
uv run pytest
```
