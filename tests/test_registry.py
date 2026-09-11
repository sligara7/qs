"""The Registry box at its own boundary (``cmp:registry``, ``ifc:registry-lookup``).

Two jobs, both tested here through what ``qs.registry`` publishes: hold the merged catalogue
of profile-defined and definition-instantiated devices with the right precedence rules
(``req:device-crud-semantics``), and resolve a queue item's plan name and arguments into a
callable that makes the plan generator.
"""

from __future__ import annotations

import pytest
from ophyd.sim import SynAxis

from qs.registry import DeviceEntry, Registry, RegistryError
from qs.sources import LoadResult


def a_plan(detectors, motor, start, stop, num):
    yield ("open_run", detectors, motor, start, stop, num)


def another_plan():
    yield ("open_run",)


@pytest.fixture
def registry() -> Registry:
    r = Registry()
    r.load_from(
        LoadResult(
            devices={"motor": SynAxis(name="motor"), "det": SynAxis(name="det")},
            plans={"a_plan": a_plan, "another_plan": another_plan},
            source_description="test profile",
        )
    )
    return r


# ---- loading -------------------------------------------------------------------------


def test_load_replaces_profile_entries_but_keeps_instantiated_ones(registry: Registry) -> None:
    registry.add_device("extra", SynAxis(name="extra"))
    registry.load_from(LoadResult(devices={"motor": SynAxis(name="motor")}, plans={"a_plan": a_plan}))

    devices = registry.devices()
    assert set(devices) == {"motor", "extra"}, "a reload drops the old profile devices, not ours"
    assert devices["motor"].origin == "profile"
    assert devices["extra"].origin == "definition"
    assert set(registry.plans()) == {"a_plan"}


def test_entries_report_where_they_came_from(registry: Registry) -> None:
    assert registry.devices()["motor"] == DeviceEntry("motor", registry.get_device("motor"), "profile")


def test_queries_hand_back_copies(registry: Registry) -> None:
    registry.devices()["motor"] = None
    registry.plans()["a_plan"] = None
    assert registry.devices()["motor"] is not None
    assert registry.plans()["a_plan"] is a_plan


# ---- lookup --------------------------------------------------------------------------


def test_get_plan_finds_a_plan_and_names_an_unknown_one(registry: Registry) -> None:
    assert registry.get_plan("a_plan") is a_plan
    with pytest.raises(RegistryError, match="Unknown plan: 'nope'"):
        registry.get_plan("nope")


def test_a_dotted_name_walks_into_a_device(registry: Registry) -> None:
    motor = registry.get_device("motor")
    assert registry.get_device("motor.setpoint") is motor.setpoint


def test_an_unknown_device_and_an_unknown_component_are_told_apart(registry: Registry) -> None:
    with pytest.raises(RegistryError, match="Unknown device: 'nope'"):
        registry.get_device("nope")
    with pytest.raises(RegistryError, match="has no component 'nonesuch'"):
        registry.get_device("motor.nonesuch")


def test_has_device_answers_for_plain_and_dotted_names(registry: Registry) -> None:
    assert registry.has_device("motor")
    assert registry.has_device("motor.setpoint")
    assert not registry.has_device("nope")
    assert not registry.has_device("motor.nonesuch")


# ---- devices from stored definitions --------------------------------------------------


def test_a_profile_device_cannot_be_replaced_or_removed(registry: Registry) -> None:
    with pytest.raises(RegistryError, match="cannot be replaced"):
        registry.add_device("motor", SynAxis(name="motor"))
    with pytest.raises(RegistryError, match="cannot be removed"):
        registry.remove_device("motor")


def test_an_instantiated_device_can_be_added_replaced_and_removed(registry: Registry) -> None:
    first = SynAxis(name="extra")
    registry.add_device("extra", first)
    assert registry.get_device("extra") is first

    second = SynAxis(name="extra")
    registry.add_device("extra", second)
    assert registry.get_device("extra") is second

    assert registry.remove_device("extra") is second
    assert not registry.has_device("extra")


def test_removing_an_unknown_device_is_refused(registry: Registry) -> None:
    with pytest.raises(RegistryError, match="Unknown device: 'ghost'"):
        registry.remove_device("ghost")


# ---- resolution ----------------------------------------------------------------------


def test_resolve_substitutes_device_names_and_leaves_everything_else(registry: Registry) -> None:
    factory = registry.resolve("a_plan", [["det"], "motor", -1, 1], {"num": 11})
    _, detectors, motor, start, stop, num = next(factory())
    assert detectors == [registry.get_device("det")]
    assert motor is registry.get_device("motor")
    assert (start, stop, num) == (-1, 1, 11)


def test_substitution_reaches_through_nested_containers(registry: Registry) -> None:
    factory = registry.resolve("a_plan", [{"inner": ("motor",)}, "motor", 0, 1], {"num": {"deep": ["det"]}})
    _, nested, _, _, _, num = next(factory())
    assert nested == {"inner": (registry.get_device("motor"),)}
    assert num == {"deep": [registry.get_device("det")]}


def test_a_string_that_names_no_device_passes_through(registry: Registry) -> None:
    factory = registry.resolve("a_plan", [[], "motor", "not-a-device", 1], {"num": "linear"})
    _, _, _, start, _, num = next(factory())
    assert start == "not-a-device"
    assert num == "linear"


def test_resolve_returns_a_factory_so_the_generator_is_made_at_run_time(registry: Registry) -> None:
    """The sequencer calls the factory when the item starts, not when it is queued."""
    factory = registry.resolve("another_plan")
    assert next(factory()) == ("open_run",)
    assert next(factory()) == ("open_run",), "a factory can be called more than once"


def test_resolve_refuses_an_unknown_plan_before_touching_the_arguments(registry: Registry) -> None:
    with pytest.raises(RegistryError, match="Unknown plan"):
        registry.resolve("nope", ["motor"])
