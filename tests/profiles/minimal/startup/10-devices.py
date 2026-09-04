"""Simulated devices; a magic line below must be tolerated."""

%matplotlib inline
from ophyd.sim import det, motor, noisy_det  # noqa: E402, F401

motor.delay = 0.3  # seconds per move, so a move produces watchable progress
