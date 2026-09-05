"""Kinetix detector variant for the sim.

The sim's Kinetix IOC is ADSimDetector with a Kinetix overlay: TriggerMode offers the generic
('Internal', 'External') choices, while ophyd-async's KinetixTriggerMode expects
('Internal', 'Rising Edge', 'Exp. Gate') and refuses to connect. This variant maps EDGE (and
GATE) to the sim's 'External'. Trial-only; a real Kinetix needs the stock KinetixDetector.
"""

from typing import Annotated as A

import ophyd_async.epics.adkinetix as _adkinetix
from ophyd_async.core import SignalRW, StrictEnum
from ophyd_async.epics.adcore import ADAcquireLogic, ADWriterFactory, AreaDetector
from ophyd_async.epics.core import PvSuffix


class SimKinetixTriggerMode(StrictEnum):
    INTERNAL = "Internal"
    EDGE = "External"
    GATE = "External"  # alias of EDGE; the sim has no gate mode


class SimKinetixDriverIO(_adkinetix.KinetixDriverIO):
    trigger_mode: A[SignalRW[SimKinetixTriggerMode], PvSuffix("TriggerMode")]


class SimKinetixDetector(_adkinetix.KinetixDetector):
    def __init__(
        self, prefix: str, *writer_factories: ADWriterFactory, driver_suffix: str = "cam1:", name: str = ""
    ):
        driver = SimKinetixDriverIO(prefix + driver_suffix)
        AreaDetector.__init__(
            self,
            driver,
            prefix,
            *writer_factories,
            acquire_logic=ADAcquireLogic(driver),
            trigger_logic=_adkinetix.KinetixTriggerLogic(driver, None),
            plugins={},
            config_sigs=(),
            name=name,
        )


# KinetixTriggerLogic looks the enum up in its module at call time.
_adkinetix.KinetixTriggerMode = SimKinetixTriggerMode
