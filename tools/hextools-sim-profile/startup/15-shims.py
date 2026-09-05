"""Trial-only shim for hextools main vs the ophyd-async fork it pins.

hextools.tomography.flyscans builds its PandA flyer as StandardFlyable(logic); the pinned
ophyd-async (jwlodek fork @5495b4d8) wants logic.with_device(). One-line drift in hextools;
patched here so the flyscan can be exercised. Remove when hextools catches up.
"""

import hextools.tomography.flyscans as _flyscans


def _flyer_from_logic(logic, name: str = "single_axis_panda_flyer"):
    return logic.with_device(name)


_flyscans.StandardFlyable = _flyer_from_logic


# Second drift: hextools' SingleAxisFlyableLogic implements prepare/kickoff/complete, while
# the pinned fork's FlyableLogic drives on_prepare(value) -> ctx, on_kickoff(ctx) -> ctx and
# on_complete(ctx). The base hooks are no-ops here (not enforced as abstract), so without this
# adapter the PandA is never programmed and the scan captures nothing.
from hextools.flyers import SingleAxisFlyableLogic as _SAFL  # noqa: E402


async def _on_prepare(self, value):
    await self.prepare(value)
    return None


async def _on_kickoff(self, ctx):
    await self.kickoff()
    return ctx


async def _on_complete(self, ctx):
    await self.complete()


_SAFL.on_prepare = _on_prepare
_SAFL.on_kickoff = _on_kickoff
_SAFL.on_complete = _on_complete
