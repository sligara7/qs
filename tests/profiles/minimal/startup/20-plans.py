"""Plans: imported bluesky plans plus a profile-defined one."""

import threading

import bluesky.plan_stubs as bps
from bluesky.plans import count, scan  # noqa: F401

plan_threads = []


def slow_plan(mot, n=3, dwell=0.2):
    """Move ``mot`` n times with a sleep between moves; long enough to pause mid-way."""
    plan_threads.append(threading.get_ident())
    for i in range(n):
        yield from bps.mv(mot, i)
        yield from bps.sleep(dwell)


def failing_plan():
    yield from bps.sleep(0.01)
    raise ValueError("this plan always fails")


not_a_plan = 42
