"""APS BITS instrument source (``cap:load-bits-instrument``).

A BITS instrument is a Python package whose ``startup`` module runs the explicit init
sequence (config, instrument/oregistry, RunEngine via ``init_RE``, devices from
``devices.yml`` via guarneri). Importing that module on the engine thread performs the
startup; the module namespace then holds ``RE``, the devices and the plans, and the same
extraction as for an IPython-style profile applies. The RunEngine BITS created is adopted
(``dec:adopt-source-runengine``).

First increment: import-based loading only; guarneri/oregistry device discovery beyond the
module namespace is not yet walked.
"""

from __future__ import annotations

import importlib
import logging

from qs.sources.namespace import iter_devices, load_result
from qs.sources.protocol import LoadResult

logger = logging.getLogger(__name__)


class BitsInstrumentSource:
    def __init__(self, startup_module: str) -> None:
        self._module_name = startup_module

    @property
    def description(self) -> str:
        return f"BITS instrument startup module {self._module_name}"

    def load(self) -> LoadResult:
        try:
            module = importlib.import_module(self._module_name)
        except Exception as exc:
            raise RuntimeError(f"BITS startup module {self._module_name!r} failed to import: {exc}") from exc
        namespace = dict(vars(module))
        # BITS keeps devices in an ophyd-registry ("oregistry") as well; include what it holds.
        oregistry = namespace.get("oregistry")
        devices = dict(iter_devices(namespace))
        if oregistry is not None:
            try:
                for dev in oregistry.all_devices:  # ophyd-registry API
                    devices.setdefault(getattr(dev, "name", None) or str(dev), dev)
            except Exception:  # noqa: BLE001 - registry shape varies between versions
                logger.debug("Could not enumerate oregistry devices", exc_info=True)
        return load_result(
            namespace,
            label=f"BITS instrument {self._module_name}",
            source_description=self.description,
            devices=devices,
        )
