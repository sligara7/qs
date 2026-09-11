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


def test_event_kinds_are_never_written_as_bare_strings() -> None:
    """Every engine event kind comes from ``EventKind`` (rule:code-style-and-commits, StrEnum).

    The kinds were 40 bare literals across 8 modules until 2026-09-11, and the websocket
    router held the "status changed" set twice. A kind missing from one copy makes the UI
    stop updating silently rather than fail, so the vocabulary has one definition and this
    test is what keeps it that way. Checked structurally, not by grepping for the strings:
    ``re_state`` is also a status-document key and ``device_progress`` a wire payload key,
    and neither of those is an event kind.
    """
    import ast

    from qs.engine.events import EventKind

    values = {k.value for k in EventKind}
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name == "events.py" and path.parent.name == "engine":
            continue  # the enum's own definition
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # bus.emit("queue_state", ...) — the kind must be an EventKind member
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "emit"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in values
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} emit({node.args[0].value!r})")
            # event.kind == "console_output" / event.kind in {...}
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == "kind"
            ):
                for comparator in node.comparators:
                    literals = (
                        [comparator]
                        if isinstance(comparator, ast.Constant)
                        else getattr(comparator, "elts", [])
                    )
                    for lit in literals:
                        if isinstance(lit, ast.Constant) and lit.value in values:
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} kind == {lit.value!r}")
    assert offenders == [], "use EventKind, not bare strings: " + "; ".join(offenders)
