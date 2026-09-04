"""happi device-database source (``cap:load-happi-devices``).

Connects to a happi database (a JSON file path or a URI accepted by ``happi.Client``) and
instantiates every active device it describes. happi supplies devices only: no plans and no
RunEngine come from this source, so the Engine Host creates an engine and the registry's
plans are limited to those supplied through ``extra_plans`` (by default bluesky's standard
plans, so the queue is usable).

First increment: synchronous ``client.load_device`` per entry; ophyd-async devices from happi
are not connected here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from qs.sources.ipython_profile import iter_plans
from qs.sources.protocol import LoadResult, PlanFactory

logger = logging.getLogger(__name__)


def _standard_plans() -> dict[str, PlanFactory]:
    import bluesky.plans as bp

    return dict(iter_plans(vars(bp)))


class HappiDatabaseSource:
    def __init__(self, path_or_uri: str, *, extra_plans: Mapping[str, PlanFactory] | None = None) -> None:
        self._path = path_or_uri
        self._extra_plans = dict(extra_plans or {})

    @property
    def description(self) -> str:
        return f"happi database {self._path}"

    def load(self) -> LoadResult:
        try:
            import happi
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("happi is not installed; install qs[happi]") from exc
        client = (
            happi.Client(path=self._path)
            if not self._path.startswith(("http://", "https://"))
            else happi.Client.from_config(self._path)
        )
        devices: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for entry in client.all_items:
            if not getattr(entry, "active", True):
                continue
            try:
                devices[entry.name] = client.load_device(name=entry.name)
            except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the load
                failures[entry.name] = f"{type(exc).__name__}: {exc}"
        for name, error in failures.items():
            logger.error("happi device %s could not be loaded: %s", name, error)
        plans = _standard_plans()
        plans.update(self._extra_plans)
        logger.info("Loaded %d devices from %s (%d failed)", len(devices), self._path, len(failures))
        return LoadResult(
            devices=devices,
            plans=plans,
            engine=None,
            namespace={"happi_client": client, "happi_failures": failures},
            source_description=self.description,
        )
