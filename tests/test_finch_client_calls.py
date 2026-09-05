"""ver:finch-client-calls as a pytest: replay finch's queue-server client calls against a live qs.

Runs tools/finch_client_check.mjs (the exact requests finch's src/api/qServer/requests.ts makes,
with the response shapes its types declare) under Node against qs on the minimal profile.
Skipped when Node is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._live_server import live_server

TOOL = Path(__file__).parent.parent / "tools" / "finch_client_check.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_finch_client_calls_behave_as_finch_expects(tmp_path: Path) -> None:
    with live_server(tmp_path, api_key="finch-test-key") as (_, url):
        env = {**os.environ, "QS_URL": url, "QS_API_KEY": "finch-test-key"}
        result = subprocess.run(["node", str(TOOL)], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout, result.stdout
