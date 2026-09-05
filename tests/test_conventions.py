"""Conventions the design records as enforced rules (rule:http-only-no-zmq, rule:code-style-and-commits)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "qs"


def test_no_zeromq_anywhere_in_qs() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        if re.search(r"^\s*(import|from)\s+(zmq|bluesky_queueserver|bluesky\.callbacks\.zmq)\b", text, re.M):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"HTTP only: these modules import 0MQ machinery: {offenders}"

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    runtime = [d.lower() for d in pyproject["project"]["dependencies"]]
    for dep in runtime:
        assert not dep.startswith(("pyzmq", "zmq", "bluesky-queueserver")), (
            f"runtime dependency {dep!r} brings 0MQ"
        )


def test_python_floor_and_no_future_annotations_in_profile_fixtures() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == ">=3.12"
    # Profile fixtures are executed like IPython startup files; a future import there would hide
    # the loader defect that tests/test_profile_source.py guards against.
    for path in (ROOT / "tests" / "profiles").rglob("*.py"):
        assert "from __future__ import annotations" not in path.read_text(), path
