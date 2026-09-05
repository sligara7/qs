"""Turn exceptions into one line an operator can act on, and keep tracebacks out of the journal.

Decided 2026-09-04 (dec:open-logging-and-errors): every fault logs ONE headline naming the layer,
the item, the root cause and what qs did; the deepest frame in profile code is named beside the
exception; tracebacks appear only at DEBUG (and in the item's history entry over HTTP).
"""

from __future__ import annotations

import logging
import sys
import sysconfig
import traceback
from dataclasses import dataclass
from pathlib import Path

_QS_PACKAGE_DIR = str(Path(__file__).resolve().parent)
_LIBRARY_DIRS = tuple(
    p
    for p in {
        sysconfig.get_paths().get("purelib", ""),
        sysconfig.get_paths().get("platlib", ""),
        sysconfig.get_paths().get("stdlib", ""),
        _QS_PACKAGE_DIR,
    }
    if p
)


def root_cause(exc: BaseException) -> BaseException:
    """The innermost exception: follow bluesky FailedStatus into the status, then __cause__/__context__."""
    seen: set[int] = set()
    current = exc
    while id(current) not in seen:
        seen.add(id(current))
        status_exc = _status_exception(current)
        if status_exc is not None and status_exc is not current:
            current = status_exc
            continue
        nxt = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if nxt is None or nxt is current:
            break
        current = nxt
    return current


def _status_exception(exc: BaseException) -> BaseException | None:
    # bluesky.utils.FailedStatus(status): the status carries the real error.
    if type(exc).__name__ != "FailedStatus" or not exc.args:
        return None
    status = exc.args[0]
    getter = getattr(status, "exception", None)
    try:
        inner = getter() if callable(getter) else getter
    except Exception:  # noqa: BLE001
        return None
    return inner if isinstance(inner, BaseException) else None


def user_frame(exc: BaseException) -> str:
    """'file.py:LINE in func' for the deepest frame outside libraries and qs itself, else ''."""
    for tb_exc in _chain(exc):
        frames = traceback.extract_tb(tb_exc.__traceback__)
        for frame in reversed(frames):
            if not _is_library(frame.filename):
                return f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
    return ""


def _chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = root_cause(exc)
    # innermost first, then outwards
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = None
    outer = exc
    while outer is not None and id(outer) not in seen:
        seen.add(id(outer))
        chain.append(outer)
        outer = outer.__cause__ or (None if outer.__suppress_context__ else outer.__context__)
    return chain


def _is_library(filename: str) -> bool:
    if filename.startswith("<"):
        return True
    return any(filename.startswith(d) for d in _LIBRARY_DIRS) or "/site-packages/" in filename


@dataclass(frozen=True)
class ExceptionSummary:
    root: str  # "TimeoutError: ca://XF:...HDF1:Capture"
    where: str  # "85-fly-plans.py:133 in tomo_dark_flat" or ""
    traceback: str

    @property
    def headline(self) -> str:
        return f"{self.root} (at {self.where})" if self.where else self.root


def summarize(exc: BaseException) -> ExceptionSummary:
    root = root_cause(exc)
    root_text = f"{type(root).__name__}: {root}".strip().rstrip(":")
    return ExceptionSummary(
        root=root_text,
        where=user_frame(exc),
        traceback="".join(traceback.format_exception(exc)),
    )


class TracebackPolicy(logging.Filter):
    """Keep tracebacks out of INFO/WARNING/ERROR output: append the root cause instead.

    Installed on the root logger's handlers by ``configure_logging``. With ``keep_tracebacks``
    (DEBUG level) records pass unchanged. Applies to every library (bluesky logs 'Run aborted' with
    a 40-line stack on every failed plan; qs already logged the headline).
    """

    def __init__(self, keep_tracebacks: bool) -> None:
        super().__init__()
        self.keep_tracebacks = keep_tracebacks

    def filter(self, record: logging.LogRecord) -> bool:
        if self.keep_tracebacks or not record.exc_info:
            return True
        exc = record.exc_info[1]
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        if exc is not None:
            summary = summarize(exc)
            record.msg = f"{record.getMessage()} — {summary.headline}"
            record.args = ()
        return True


def configure_logging(level: str = "INFO", stream=None) -> None:  # noqa: ANN001
    """Journald-friendly logging: one line per record, tracebacks only at DEBUG."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        numeric = logging.INFO
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(TracebackPolicy(keep_tracebacks=numeric <= logging.DEBUG))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(numeric)
    # uvicorn's access log is noise next to the story; keep its errors.
    logging.getLogger("uvicorn.access").setLevel(max(numeric, logging.WARNING))
    logging.getLogger("httpx").setLevel(max(numeric, logging.WARNING))
