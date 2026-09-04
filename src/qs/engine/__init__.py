"""The Engine Host: the reliability core.

Owns the dedicated engine thread and the single RunEngine (adopted from the profile source
or created). Everything else in the service talks to it through
:class:`qs.engine.host.EngineHost` (the command side) and :class:`qs.engine.events.EventBus`
(the event side). This package imports neither FastAPI nor SQLAlchemy, by design.
"""

from qs.engine.events import EngineEvent, EventBus
from qs.engine.host import EngineHost, EngineState, PlanOutcome

__all__ = ["EngineEvent", "EngineHost", "EngineState", "EventBus", "PlanOutcome"]
