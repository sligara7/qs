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

Trial-only patches in this directory (gaps found on 2026-09-04; the sim-side ones are fixed in hex-ob main as of the same day, so only the hextools shim remains):

| file | works around |
|---|---|
| `15-shims.py` | hextools main builds `StandardFlyable(logic)` and implements `prepare/kickoff/complete`; the pinned fork wants `logic.with_device()` and `on_prepare/on_kickoff/on_complete` |

Result (2026-09-04, after the sim fixes landed in hex-ob main): profile loads (5 devices,
5 plans, stock hextools/ophyd-async classes, no device shims), `count`/`mv` succeed, and
`tomo_flyscan` with the PandA alone runs to **success: 5 of 5 rows captured, run in Tiled
with 5 primary events**. Requirements on the sim side: motor `VMAX` <= 10 deg/s (the sim's
default now) and the encoder bridge run as `--offset 0 --rate-hz 2000 --max-step 2`
(hextools' time-based PCOMP uses STEP=2 counts, so the injected position may not jump by
more). With `kinetix1` included the detector kickoff still fails because ADSimDetector
ignores external triggering and has already taken its frames; that is the one remaining
simulator limit for the camera path.
