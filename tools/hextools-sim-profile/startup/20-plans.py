"""Plans: bluesky's count plus hextools' tomography plans, exactly as hextools ships them.

Queue items name devices as strings; qs's registry substitutes the objects, e.g.
  tomo_flyscan  args=[["kinetix1"], 0.1, "panda1", "tomo_rot_axis", "photon_shutter", 5]
                kwargs={"start_position": 0, "stop_position": 10, "use_shutter": false}
"""

from bluesky.plan_stubs import mv, sleep  # noqa: F401
from bluesky.plans import count  # noqa: F401
from hextools.tomography.flyscans import tomo_flyscan  # noqa: F401
from hextools.tomography.take_radiograph import take_radiograph  # noqa: F401
