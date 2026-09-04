"""Console output capture for ``/api/console_output*`` and the console websocket.

Tees ``sys.stdout`` and ``sys.stderr`` (the RunEngine prints "Pausing...", plans print
progress, profiles print on load) into a bounded ring of timestamped messages, each with
a uid, and publishes each message as a ``console_output`` event on the EventBus so the
websocket can stream it. Uses the same message shape as queueserver's console stream:
``{"time": <ts>, "msg": <text>}``.
"""

from __future__ import annotations

import contextlib
import io
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any, TextIO

from qs.engine.events import EventBus


class _Tee(io.TextIOBase):
    def __init__(self, original: TextIO, sink: ConsoleCapture) -> None:
        self._original = original
        self._sink = sink

    def write(self, text: str) -> int:  # type: ignore[override]
        try:
            self._original.write(text)
        except Exception:  # noqa: BLE001
            pass
        self._sink.feed(text)
        return len(text)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self._original.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._original, "encoding", "utf-8")


class ConsoleCapture:
    def __init__(self, events: EventBus, *, max_messages: int = 2000) -> None:
        self._events = events
        self._messages: deque[dict[str, Any]] = deque(maxlen=max_messages)
        self._lock = threading.Lock()
        self._uid = str(uuid.uuid4())
        self._pending = ""
        self._installed = False
        self._saved: tuple[TextIO, TextIO] | None = None

    # ---- installation ----

    def install(self) -> None:
        if self._installed:
            return
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout = _Tee(sys.stdout, self)  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, self)  # type: ignore[assignment]
        self._installed = True

    def uninstall(self) -> None:
        if self._installed and self._saved is not None:
            sys.stdout, sys.stderr = self._saved
        self._installed = False

    # ---- feed ----

    def feed(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._pending += text
            lines: list[str] = []
            while "\n" in self._pending:
                line, self._pending = self._pending.split("\n", 1)
                lines.append(line + "\n")
        for line in lines:
            self._append(line)

    def _append(self, text: str) -> None:
        message = {"time": time.time(), "msg": text}
        with self._lock:
            self._messages.append(message)
            self._uid = str(uuid.uuid4())
        self._events.emit("console_output", **message)

    # ---- read ----

    @property
    def uid(self) -> str:
        return self._uid

    def text(self, nlines: int = 200) -> str:
        with self._lock:
            msgs = list(self._messages)[-nlines:]
        return "".join(m["msg"] for m in msgs)

    def messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._messages)
