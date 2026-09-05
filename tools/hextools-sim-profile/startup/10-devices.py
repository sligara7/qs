"""hextools / ophyd-async devices for the sim, with the sim's PV prefixes."""

import os
from pathlib import Path

from hextools.motors import RotationMotor
from hextools.photon_delivery_system import Shutter
from ophyd_async.core import StaticPathProvider, UUIDFilenameProvider, init_devices
from ophyd_async.epics.adcore import ADWriterFactory
from ophyd_async.epics.adkinetix import KinetixDetector
from ophyd_async.fastcs.panda import HDFPanda

# The sim's IOC containers mount this directory, so detector files land where the
# writer says they will.
_data_dir = Path(os.environ.get("HEX_SIM_DATA_DIR", "/tmp/hex-sim-data"))
_data_dir.mkdir(parents=True, exist_ok=True)
path_provider = StaticPathProvider(UUIDFilenameProvider(), _data_dir)

_ROT_MOTOR = os.environ.get("HEX_SIM_ROT_MOTOR", "XF:27IDF-OP:1{MC:5-Ax:4}Mtr")

with init_devices(timeout=5):
    fe_shutter = Shutter("XF:27IDA-PPS{Sh:FE}")
    photon_shutter = Shutter("XF:27IDA-PPS{L1-S1}")
    panda1 = HDFPanda("XF:27ID1-ES{PANDA:1}:", path_provider)
    kinetix1 = KinetixDetector("XF:27ID1-BI{Kinetix-Det:1}", ADWriterFactory.hdf(path_provider))
    tomo_rot_axis = RotationMotor(_ROT_MOTOR)
