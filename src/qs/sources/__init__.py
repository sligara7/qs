"""Profile sources: where plans, devices and (optionally) a RunEngine come from.

Every source implements :class:`qs.sources.protocol.ProfileSource` and returns a
:class:`qs.sources.protocol.LoadResult`. Loading always happens on the engine thread.
"""

from qs.sources.protocol import LoadResult, ProfileSource

__all__ = ["LoadResult", "ProfileSource"]
