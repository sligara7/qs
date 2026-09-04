"""qs: a Bluesky queue service.

Loads a beamline profile, keeps its plans and devices, and runs a SQL-backed queue of
plans one at a time through a single RunEngine, controlled only over HTTP.
"""

__version__ = "0.1.0"
