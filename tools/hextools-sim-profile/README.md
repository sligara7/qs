# hextools trial profile (development only)

A qs startup directory that builds the HEX tomography devices from **hextools** (the
package hex-profile-collection is moving to) with the PV prefixes served by
`hex-ob/hex-simulated-beamline`, and exposes hextools' `tomo_flyscan` and `take_radiograph`
plus bluesky's `count`, `mv`, `sleep`.

Environment (2026-09-04): hextools checkout at v0.2.3-128 and the ophyd-async fork its
`pixi.lock` pins (jwlodek/ophyd-async @5495b4d8), installed with qs in a throwaway venv:

    uv venv --python 3.12 .hextools-env
    uv pip install -p .hextools-env/bin/python \
        "ophyd-async[ca,pva] @ git+https://github.com/jwlodek/ophyd-async@5495b4d80acd7a98b69bdb7dab73f137a3dcc3ca"
    uv pip install -p .hextools-env/bin/python -e ../hextools "nslsii @ git+https://github.com/NSLS2/nslsii@main" \
        bluesky-tiled-plugins "tiled[client]" redis-json-dict rich pyyaml matplotlib fastapi uvicorn sqlalchemy pydantic ipython ophyd httpx
    uv pip install -p .hextools-env/bin/python --no-deps -e .
    source ../hex-ob/hex-simulated-beamline/scripts/env.sh
    QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY=hex .hextools-env/bin/python -m qs.runtime.main --config tools/qs-hextools-sim.example.yml

Sim adjustments needed for the flyscan (all outside qs): motor `VMAX`/`ERES` are 0 in the
sim (set VMAX to a few deg/s and ERES to 0.005 deg/count); run the encoder bridge with
`--offset 0 --rate-hz 1000 --max-step 1` so PCOMP sees a continuous position that matches
hextools' counts = deg / ERES convention.

Trial-only patches in this directory, each a gap found on 2026-09-04:

| file | works around |
|---|---|
| `05-sim-kinetix.py` | sim Kinetix IOC offers TriggerMode `Internal/External`, ophyd-async expects `Internal/Rising Edge/Exp. Gate` |
| `10-devices.py` (mock shutters) | sim blackhole serves shutter PVs with CA types hextools' `Shutter` rejects |
| `15-shims.py` | hextools main builds `StandardFlyable(logic)` and implements `prepare/kickoff/complete`; the pinned fork wants `logic.with_device()` and `on_prepare/on_kickoff/on_complete` |

Result: profile loads (5 devices, 5 plans), `count`/`mv` succeed, `tomo_flyscan` with the
PandA alone runs prepare → kickoff → motor fly → PCOMP fires → 1 row captured, then times
out because the sim's PandA PULSE block emits one pulse of the requested train (the sim's own
`slowmove_capture_test.py` shows the same 1-of-5). With `kinetix1` included the detector
kickoff fails because ADSimDetector ignores external triggering and has already taken its
frames. Both limits are in the simulator, not qs or hextools.
