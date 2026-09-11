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

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EngineThreadHost(Protocol):
    """Somewhere to run work on the engine thread, and the RunEngine it is driving.

    The device box needs exactly this much: construction of an ophyd device has to happen on
    the thread that owns the engine (an ophyd-async device's ``connect()`` uses that thread's
    event loop), and reaching the loop means reaching the engine.

    Part of ``ifc:engine-commands``. Note that the interface's own wording says callers never
    touch the RunEngine directly, which ``engine`` here does not honour — see
    ``fact:engine-handle-escapes-the-command-channel``.
    """

    @property
    def engine(self) -> Any:
        """The RunEngine being driven, or ``None`` before a profile has been loaded."""
        ...

    def call(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        """Run ``fn`` on the engine thread and return its result, raising what it raised."""
        ...
