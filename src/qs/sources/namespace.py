"""What can be found in a loaded namespace, and how a :class:`LoadResult` is assembled.

Every profile source ends the same way: given a namespace some loading mechanism produced,
pick out the devices, the plans and the RunEngine, say in the log what was found, and return
a :class:`~qs.sources.protocol.LoadResult`. None of that is IPython behaviour — it is true of
a BITS startup module and a happi database too — but it lived in ``ipython_profile`` until
2026-09-11, so the other two sources imported it from a peer implementation and one of them
copied the assembly outright.

Internal to the ``sources`` package: the three implementations use these, nothing outside
does, and so none of it appears in ``qs.sources.__all__``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterator, Mapping
from typing import Any

from bluesky.run_engine import RunEngine

from qs.sources.protocol import LoadResult, PlanFactory

logger = logging.getLogger(__name__)


def iter_devices(namespace: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    """Top-level ophyd / ophyd-async devices and signals in ``namespace``."""
    types: list[type] = []
    try:
        from ophyd.ophydobj import OphydObject

        types.append(OphydObject)
    except ImportError:  # pragma: no cover
        pass
    try:
        from ophyd_async.core import Device as AsyncDevice

        types.append(AsyncDevice)
    except ImportError:  # pragma: no cover
        pass
    if not types:
        return
    type_tuple = tuple(types)
    for name, obj in namespace.items():
        if name.startswith("_"):
            continue
        if isinstance(obj, type_tuple):
            yield name, obj


def is_plan(obj: Any) -> bool:
    """Is ``obj`` something the RunEngine can be handed after calling it?"""
    if not callable(obj) or inspect.isclass(obj):
        return False
    try:
        from bluesky.utils import is_plan as bluesky_is_plan

        if bluesky_is_plan(obj):
            return True
    except ImportError:  # pragma: no cover
        pass
    target = obj
    try:
        target = inspect.unwrap(obj)
    except ValueError:  # pragma: no cover
        pass
    return inspect.isgeneratorfunction(target)


def iter_plans(namespace: Mapping[str, Any]) -> Iterator[tuple[str, PlanFactory]]:
    for name, obj in namespace.items():
        if name.startswith("_"):
            continue
        if is_plan(obj):
            yield name, obj


def find_engine(namespace: Mapping[str, Any]) -> RunEngine | None:
    """The RunEngine the profile created, if any: ``RE`` by convention, else any instance."""
    candidate = namespace.get("RE")
    if isinstance(candidate, RunEngine):
        return candidate
    engines = [v for v in namespace.values() if isinstance(v, RunEngine)]
    if len(engines) == 1:
        return engines[0]
    if len(engines) > 1:
        logger.warning("Profile defined %d RunEngines and none named RE; adopting none", len(engines))
    return None


def load_result(
    namespace: Mapping[str, Any],
    *,
    label: str,
    source_description: str,
    devices: Mapping[str, Any] | None = None,
) -> LoadResult:
    """Collect what ``namespace`` defined and report it, once, for every source.

    ``label`` names the thing that was loaded in the log line (a startup directory, a module).
    Pass ``devices`` when the source found them somewhere other than the namespace — BITS also
    keeps them in an ophyd-registry — and they are used as given instead of being re-derived.
    """
    found = dict(iter_devices(namespace)) if devices is None else dict(devices)
    plans = dict(iter_plans(namespace))
    engine = find_engine(namespace)
    logger.info(
        "Loaded %s: %d devices, %d plans, engine %s",
        label,
        len(found),
        len(plans),
        "adopted" if engine is not None else "not defined",
    )
    return LoadResult(
        devices=found,
        plans=plans,
        engine=engine,
        namespace=namespace,
        source_description=source_description,
    )
