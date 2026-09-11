"""What other boxes need of the Engine Host, declared as a protocol.

``EngineHost`` itself is large — around twenty public members covering the thread, the
adopted RunEngine, plan execution and the pause/resume/abort/stop/halt controls. Very few
callers need all of that. This module declares the seam one of them actually uses, so that
box can be built and tested against a shape rather than against the class.

WHY ONLY ONE PROTOCOL HERE. The rule this project follows is: add one when a second
implementation appears, or when a test needs a stand-in. The device box meets that bar —
``tests/test_devices.py`` replaces the entire engine box with a ten-line fake — and the other
three consumers of the engine host (the status reporter, the engine router and the sequencer)
do not, yet. When one of them grows a fake, it earns a protocol of the same shape: named for
what that caller needs, not for everything ``EngineHost`` can do.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EngineThreadHost(Protocol):
    """Two ways to get work onto the engine's threads, and no handle on the engine itself.

    The device box needs exactly this much: an ophyd device must be constructed on the thread
    that owns the engine, and an ophyd-async device must then be connected on that engine's
    event loop. Neither requires the RunEngine object, only somewhere to send the work.

    This is the shape ``ifc:engine-commands`` describes — "callers never touch the RunEngine
    directly". Until 2026-09-11 the protocol also exposed ``engine``, and the device box used
    it to reach the loop itself; :meth:`run_on_engine_loop` is what replaced that.
    """

    def call(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        """Run ``fn`` on the engine thread and return its result, raising what it raised."""
        ...

    def run_on_engine_loop(self, coro: Coroutine[Any, Any, Any], timeout: float | None = None) -> Any:
        """Drive ``coro`` on the RunEngine's event loop and return its result."""
        ...
