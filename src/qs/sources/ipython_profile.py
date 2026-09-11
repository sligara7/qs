"""NSLS-II style profile-collection source (``cap:load-ipython-profile``).

Executes the ``.py`` files of a startup directory in lexical order into one namespace, the
way IPython does for a profile, and collects the devices, plans and RunEngine they define.
This is the primary source (``con:nsls2-first``). The concepts mirror bluesky-queueserver's
``profile_ops.load_profile_collection`` (patched ``get_ipython``, ordered execution) without
depending on that package.

Runs on the engine thread so that an engine created by ``nslsii.configure_base`` or an
explicit ``RunEngine(...)`` is born there (``dec:exec-profile-in-process``,
``dec:adopt-source-runengine``).
"""

from __future__ import annotations

import builtins
import logging
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from qs.sources.namespace import load_result
from qs.sources.protocol import LoadResult

logger = logging.getLogger(__name__)

_GET_IPYTHON_IMPORT = re.compile(r"^[^#]*\bIPython\b[^#]*\bget_ipython\b")
_MAGIC_LINE = re.compile(r"^\s*[%!]")


class _NoOp:
    """Swallows any IPython API call a profile makes (magics, hooks, events)."""

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        logger.debug("Ignored IPython call with %r %r", args, kwargs)

    def __getattr__(self, name: str) -> _NoOp:
        return self


class FakeIPython:
    """The object our patched ``get_ipython()`` returns.

    Exposes ``user_ns`` (the profile namespace, which ``nslsii.configure_base`` writes into)
    and tolerates everything else as a no-op.
    """

    def __init__(self, user_ns: dict[str, Any]) -> None:
        self.user_ns = user_ns
        self.ns_table = {"user_global": user_ns, "user_local": user_ns}
        self.events = _NoOp()
        self.magics_manager = _NoOp()

    def run_line_magic(self, magic_name: str, line: str = "") -> None:
        logger.info("Ignored IPython line magic %%%s %s", magic_name, line)

    def run_cell_magic(self, magic_name: str, line: str, cell: str) -> None:
        logger.info("Ignored IPython cell magic %%%%%s", magic_name)

    def magic(self, arg_s: str) -> None:
        logger.info("Ignored IPython magic %s", arg_s)

    def register_magics(self, *args: Any) -> None:
        pass

    def register_magic_function(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __getattr__(self, name: str) -> _NoOp:
        return _NoOp()


def patch_profile_code(code: str) -> str:
    """Neutralise IPython-only syntax in profile code.

    * a line importing ``get_ipython`` from IPython gets ``get_ipython`` rebound to the
      patched one afterwards (the profile then talks to :class:`FakeIPython`);
    * lines starting with ``%`` or ``!`` (magics, shell escapes) are commented out.
    """
    out: list[str] = []
    for line in code.splitlines():
        if _MAGIC_LINE.match(line):
            logger.warning("Commented out IPython magic/shell line: %s", line.strip())
            out.append("# qs: skipped magic: " + line)
        elif _GET_IPYTHON_IMPORT.match(line):
            out.append(line.rstrip() + "; get_ipython = _qs_get_ipython_patch")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def startup_files(startup_dir: Path) -> list[Path]:
    """The files IPython would run for this startup directory, in order."""
    files = sorted(p for p in startup_dir.iterdir() if p.is_file() and p.suffix in {".py", ".ipy"})
    for p in files:
        if p.suffix == ".ipy":
            logger.warning("Skipping %s: .ipy startup files are not supported", p)
    return [p for p in files if p.suffix == ".py"]


class IPythonProfileSource:
    """Load an NSLS-II style profile-collection startup directory in-process."""

    def __init__(
        self,
        startup_dir: str | Path,
        *,
        extra_namespace: Mapping[str, Any] | None = None,
        add_to_sys_path: bool = True,
        exec_fn: Callable[[Any, dict[str, Any]], None] | None = None,
    ) -> None:
        self._startup_dir = Path(startup_dir).expanduser().resolve()
        self._extra_namespace = dict(extra_namespace or {})
        self._add_to_sys_path = add_to_sys_path
        self._exec = exec_fn or (lambda code, ns: exec(code, ns))  # noqa: S102 - by design

    @property
    def description(self) -> str:
        return f"IPython-style profile at {self._startup_dir}"

    @property
    def startup_dir(self) -> Path:
        return self._startup_dir

    def load(self) -> LoadResult:
        if not self._startup_dir.is_dir():
            raise FileNotFoundError(f"Startup directory not found: {self._startup_dir}")
        namespace: dict[str, Any] = {"__name__": "__main__", "__builtins__": builtins}
        ipython = FakeIPython(namespace)
        namespace["_qs_get_ipython_patch"] = lambda: ipython
        namespace["get_ipython"] = namespace["_qs_get_ipython_patch"]
        namespace.update(self._extra_namespace)

        if self._add_to_sys_path and str(self._startup_dir) not in sys.path:
            sys.path.insert(0, str(self._startup_dir))

        files = startup_files(self._startup_dir)
        if not files:
            raise FileNotFoundError(f"No .py startup files in {self._startup_dir}")
        for path in files:
            logger.info("Executing profile file %s", path.name)
            # dont_inherit: without it the profile inherits this module's
            # `from __future__ import annotations`, turning class annotations into strings that
            # ophyd-async's typed devices cannot resolve in the profile namespace.
            code = compile(patch_profile_code(path.read_text()), str(path), "exec", dont_inherit=True)
            namespace["__file__"] = str(path)
            try:
                self._exec(code, namespace)
            except Exception as exc:
                raise RuntimeError(f"Profile file {path} failed: {exc}") from exc

        return load_result(namespace, label=str(self._startup_dir), source_description=self.description)
