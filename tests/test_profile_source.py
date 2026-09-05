"""IPythonProfileSource executes startup files the way IPython would."""

from __future__ import annotations

import typing
from pathlib import Path

from qs.sources.ipython_profile import IPythonProfileSource


def test_profile_annotations_are_evaluated_not_deferred(tmp_path: Path) -> None:
    # ophyd-async devices declare signals as class annotations and resolve them with
    # typing.get_type_hints at construction time. If the loader's own
    # `from __future__ import annotations` leaked into the compiled profile, the alias `A` below
    # would be left as a string and be unresolvable from the profile namespace.
    startup = tmp_path / "startup"
    startup.mkdir()
    (startup / "00-engine.py").write_text("from bluesky.run_engine import RunEngine\nRE = RunEngine({})\n")
    (startup / "10-annotated.py").write_text(
        "from typing import Annotated as A\n"
        "class Dev:\n"
        "    speed: A[float, 'm/s']\n"
        "hints = __import__('typing').get_type_hints(Dev, include_extras=True)\n"
    )
    result = IPythonProfileSource(startup).load()
    assert result.namespace["hints"]["speed"] == typing.Annotated[float, "m/s"]
    assert result.namespace["Dev"].__annotations__["speed"] is not str
    assert not isinstance(result.namespace["Dev"].__annotations__["speed"], str)
