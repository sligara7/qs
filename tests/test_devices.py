"""The Device Definitions box at its own boundary (``cmp:device-definitions``).

Until 2026-09-11 no test imported ``qs.devices`` at all: the box was exercised only by eight
HTTP assertions in ``test_api.py``, from the far side of the API. These address what
``qs.devices`` publishes directly.

Note what the engine fixture below demonstrates. ``DeviceDefinitionService`` needs exactly
two things of an ``EngineHost`` — ``call()`` and ``engine`` — so the whole box can be tested
without starting a thread or a RunEngine. That is the concrete argument for an ``EngineHost``
protocol under ``dec:open-inside-the-box-discipline``; the fake here is doing a protocol's
job without one existing.
"""

from __future__ import annotations

from typing import Any

import pytest
from ophyd.sim import SynAxis

from qs.devices import DeviceDefinition, DeviceDefinitionError, DeviceDefinitionService
from qs.persistence import InMemoryDeviceDefinitionRepository
from qs.registry import Registry
from qs.sources import LoadResult


class FakeEngineHost:
    """All of ``EngineHost`` that the device service actually uses."""

    def __init__(self) -> None:
        self.engine: Any = None
        self.calls: list[float | None] = []

    def call(self, fn, timeout: float | None = None):
        self.calls.append(timeout)
        return fn()


class Unbuildable:
    def __init__(self, **kwargs: Any) -> None:
        raise RuntimeError("this device always fails to construct")


@pytest.fixture
def registry() -> Registry:
    r = Registry()
    r.load_from(LoadResult(devices={"profile_motor": SynAxis(name="profile_motor")}, plans={}))
    return r


@pytest.fixture
def changes() -> list[int]:
    return []


@pytest.fixture
def service(registry: Registry, changes: list[int]) -> DeviceDefinitionService:
    return DeviceDefinitionService(
        repository=InMemoryDeviceDefinitionRepository(),
        registry=registry,
        host=FakeEngineHost(),
        on_change=lambda: changes.append(1),
    )


def definition(name: str = "m1", **kwargs: Any) -> DeviceDefinition:
    kwargs.setdefault("class_path", "ophyd.sim.SynAxis")
    return DeviceDefinition(name=name, **kwargs)


# ---- the definition itself ------------------------------------------------------------


def test_a_definition_round_trips_through_a_dict() -> None:
    original = definition("m1", prefix="XF:31ID{Mtr:1}", kwargs={"delay": 0.01}, enabled=False)
    restored = DeviceDefinition.from_dict(original.to_dict())
    assert (restored.name, restored.class_path, restored.prefix) == (
        "m1",
        "ophyd.sim.SynAxis",
        "XF:31ID{Mtr:1}",
    )
    assert restored.kwargs == {"delay": 0.01}
    assert restored.enabled is False


def test_from_dict_fills_in_the_optional_parts() -> None:
    restored = DeviceDefinition.from_dict({"name": "m1", "class_path": "ophyd.sim.SynAxis"})
    assert (restored.prefix, restored.kwargs, restored.enabled) == ("", {}, True)


def test_touched_moves_updated_at_and_leaves_everything_else() -> None:
    original = definition("m1")
    later = original.touched()
    assert later.updated_at >= original.updated_at
    assert later.created_at == original.created_at
    assert later.name == original.name


# ---- CRUD -----------------------------------------------------------------------------


def test_create_then_get_and_list(service: DeviceDefinitionService, changes: list[int]) -> None:
    service.create(definition("m1"))
    assert service.get("m1").class_path == "ophyd.sim.SynAxis"
    assert [d.name for d in service.list()] == ["m1"]
    assert changes == [1], "a change notification fires so the API can refresh"


def test_getting_something_that_was_never_defined_is_refused(service: DeviceDefinitionService) -> None:
    with pytest.raises(DeviceDefinitionError, match="No device definition named 'ghost'"):
        service.get("ghost")


def test_a_second_definition_with_the_same_name_is_refused(service: DeviceDefinitionService) -> None:
    service.create(definition("m1"))
    with pytest.raises(DeviceDefinitionError, match="already exists"):
        service.create(definition("m1"))


@pytest.mark.parametrize("name", ["", "not an identifier", "1starts-with-a-digit", "has-hyphens"])
def test_a_name_that_is_not_a_python_identifier_is_refused(
    service: DeviceDefinitionService, name: str
) -> None:
    with pytest.raises(DeviceDefinitionError, match="valid Python identifier"):
        service.create(definition(name))


def test_a_profile_device_cannot_be_redefined(service: DeviceDefinitionService) -> None:
    """``req:device-crud-semantics``: what the profile defines is read-only."""
    with pytest.raises(DeviceDefinitionError, match="defined by the profile"):
        service.create(definition("profile_motor"))


@pytest.mark.parametrize(
    ("class_path", "message"),
    [
        ("NoDots", "must be 'package.module.Class'"),
        ("no.such.module.Thing", "Cannot import module"),
        ("ophyd.sim.NoSuchClass", "is not a class"),
    ],
)
def test_a_class_path_that_cannot_be_imported_fails_at_definition_time(
    service: DeviceDefinitionService, class_path: str, message: str
) -> None:
    """Validated on create, so the operator hears about it then and not at instantiation."""
    with pytest.raises(DeviceDefinitionError, match=message):
        service.create(definition("m1", class_path=class_path))


def test_update_keeps_created_at_and_moves_updated_at(service: DeviceDefinitionService) -> None:
    created = service.create(definition("m1"))
    updated = service.update(definition("m1", prefix="XF:31ID{Mtr:1}"))
    assert updated.prefix == "XF:31ID{Mtr:1}"
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at


def test_updating_something_undefined_is_refused(service: DeviceDefinitionService) -> None:
    with pytest.raises(DeviceDefinitionError, match="No device definition named"):
        service.update(definition("ghost"))


def test_delete_removes_the_definition(service: DeviceDefinitionService) -> None:
    service.create(definition("m1"))
    service.delete("m1")
    assert list(service.list()) == []
    with pytest.raises(DeviceDefinitionError):
        service.delete("m1")


# ---- live instances --------------------------------------------------------------------


def test_instantiate_builds_the_device_and_registers_it(
    service: DeviceDefinitionService, registry: Registry
) -> None:
    service.create(definition("m1", kwargs={"delay": 0.0}))
    device = service.instantiate("m1")

    assert isinstance(device, SynAxis)
    assert registry.get_device("m1") is device
    assert registry.devices()["m1"].origin == "definition"
    assert service.is_instantiated("m1")


def test_the_device_is_built_on_the_engine_thread(registry: Registry) -> None:
    """Construction goes through the host, never directly — ophyd-async needs that loop."""
    host = FakeEngineHost()
    service = DeviceDefinitionService(
        repository=InMemoryDeviceDefinitionRepository(), registry=registry, host=host
    )
    service.create(definition("m1"))
    service.instantiate("m1")
    assert len(host.calls) == 1


def test_a_disabled_definition_is_not_instantiated(service: DeviceDefinitionService) -> None:
    service.create(definition("m1", enabled=False))
    with pytest.raises(DeviceDefinitionError, match="is disabled"):
        service.instantiate("m1")


def test_remove_instance_leaves_the_definition_behind(
    service: DeviceDefinitionService, registry: Registry
) -> None:
    service.create(definition("m1"))
    service.instantiate("m1")
    service.remove_instance("m1")

    assert not registry.has_device("m1")
    assert not service.is_instantiated("m1")
    assert service.get("m1").name == "m1", "the definition outlives the instance"


def test_removing_an_instance_that_is_not_there_is_refused(service: DeviceDefinitionService) -> None:
    service.create(definition("m1"))
    with pytest.raises(DeviceDefinitionError, match="Unknown device"):
        service.remove_instance("m1")


def test_deleting_a_definition_takes_its_live_instance_with_it(
    service: DeviceDefinitionService, registry: Registry
) -> None:
    service.create(definition("m1"))
    service.instantiate("m1")
    service.delete("m1")
    assert not registry.has_device("m1")


def test_instantiate_all_enabled_reports_failures_without_stopping(
    service: DeviceDefinitionService, registry: Registry
) -> None:
    """One bad device must not stop the service starting."""
    service.create(definition("good"))
    service.create(definition("skipped", enabled=False))
    service.create(definition("bad", class_path=f"{__name__}.Unbuildable"))

    failures = service.instantiate_all_enabled()

    assert set(failures) == {"bad"}
    assert "always fails to construct" in failures["bad"]
    assert registry.has_device("good")
    assert not registry.has_device("skipped")
