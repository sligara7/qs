"""Trial profile: hextools devices and plans on the simulated HEX beamline.

This is the shape hex-profile-collection is heading towards (import hextools instead of
defining devices locally). It exists to exercise qs against a hextools-style profile and
the sim; it is NOT a beamline profile. Needs the sim's scripts/env.sh sourced.
"""

import os

from bluesky.run_engine import RunEngine
from bluesky_tiled_plugins import TiledWriter
from tiled.client import from_uri

RE = RunEngine(
    {
        "facility": "NSLS-II",
        "group": "HEX",
        "beamline_id": "27-ID-1",
        "data_session": "pass-sim",
        "cycle": "2026-3",
    }
)

_tiled_uri = os.environ.get("TILED_URI", "http://127.0.0.1:8000")
_tiled_key = os.environ.get("TILED_BLUESKY_WRITING_API_KEY_HEX", "secret")
tiled_writing_client = from_uri(f"{_tiled_uri}/api/v1/metadata/hex/raw", api_key=_tiled_key)
RE.subscribe(TiledWriter(tiled_writing_client))
