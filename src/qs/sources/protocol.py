"""The contract every profile source honours.

Design decision (accepted): a source yields devices, plans and *optionally* the RunEngine
it created. The Engine Host adopts that engine; it constructs one only when the source
supplied none. See ``dec:adopt-source-runengine`` in the design.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from bluesky.run_engine import RunEngine

PlanFactory = Callable[..., Any]
"""A callable that returns a plan generator when called with its arguments."""


@dataclass(frozen=True)
class LoadResult:
    """What a profile source produced.

    ``namespace`` is the full namespace the source built (for IPython-style profiles,
    everything the startup scripts defined); ``devices`` and ``plans`` are the subsets the
    registry exposes; ``engine`` is the RunEngine the source created, or ``None``.
    """

    devices: Mapping[str, Any]
    plans: Mapping[str, PlanFactory]
    engine: RunEngine | None = None
    namespace: Mapping[str, Any] = field(default_factory=dict)
    source_description: str = ""


@runtime_checkable
class ProfileSource(Protocol):
    """A place plans and devices come from.

    ``load`` is called exactly once per service start, on the engine thread, so that any
    RunEngine the source creates is born on the thread that will drive it.
    """

    @property
    def description(self) -> str:
        """Human-readable description of what will be loaded (path, module, URI)."""
        ...

    def load(self) -> LoadResult:
        """Load the profile and return what it defined. Raises on failure."""
        ...
