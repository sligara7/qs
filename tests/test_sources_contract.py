"""One contract, every profile source (``ifc:profile-source``).

``sources`` is the box with the most implementations of one protocol — an NSLS-II style
profile directory, an APS BITS instrument package, a happi database — and until 2026-09-11
each was tested separately, so nothing said what they must have in common. That is the same
asymmetry ``tests/test_persistence.py`` closed for the repositories: the checks below run
against all three, and a source that honours the protocol's signature while producing
something the rest of the service cannot use now fails.

The existing per-source tests stay: this says what they share, not what makes each one
different. Each source loads once per module, because loading a BITS instrument is slow.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from bluesky.run_engine import RunEngine

from qs.registry import Registry
from qs.sources import LoadResult, ProfileSource

PROFILES = Path(__file__).parent / "profiles"


def _happi_database(tmp_path) -> str:
    happi = pytest.importorskip("happi")
    from happi.item import HappiItem

    path = tmp_path / "db.json"
    client = happi.Client(path=str(path))
    for name, cls in (("motor", "ophyd.sim.SynAxis"), ("det", "ophyd.sim.SynSignal")):
        client.create_item(
            HappiItem, name=name, device_class=cls, args=[], kwargs={"name": "{{name}}"}, active=True
        ).save()
    return str(path)


def _build(kind: str, tmp_path, mp: pytest.MonkeyPatch) -> ProfileSource:
    if kind == "ipython":
        from qs.sources.ipython_profile import IPythonProfileSource

        return IPythonProfileSource(PROFILES / "minimal" / "startup")
    if kind == "bits":
        pytest.importorskip("apsbits")
        from qs.sources.bits_instrument import BitsInstrumentSource

        mp.chdir(tmp_path)  # the demo instrument writes into the working directory
        mp.setenv("MPLBACKEND", "Agg")
        return BitsInstrumentSource("apsbits.demo_instrument.startup")
    from qs.sources.happi_database import HappiDatabaseSource

    return HappiDatabaseSource(_happi_database(tmp_path))


@pytest.fixture(scope="module", params=["ipython", "bits", "happi"])
def loaded(request: pytest.FixtureRequest, tmp_path_factory) -> Iterator[tuple[ProfileSource, LoadResult]]:
    tmp_path = tmp_path_factory.mktemp(request.param)
    with pytest.MonkeyPatch.context() as mp:
        source = _build(request.param, tmp_path, mp)
        yield source, source.load()


@pytest.fixture
def source(loaded: tuple[ProfileSource, LoadResult]) -> ProfileSource:
    return loaded[0]


@pytest.fixture
def result(loaded: tuple[ProfileSource, LoadResult]) -> LoadResult:
    return loaded[1]


# ---- the protocol itself ---------------------------------------------------------------


def test_every_source_satisfies_the_protocol(source: ProfileSource) -> None:
    assert isinstance(source, ProfileSource)


def test_the_description_is_something_an_operator_can_read(source: ProfileSource) -> None:
    """It goes in the log line and in the status document, so it cannot be blank."""
    assert isinstance(source.description, str)
    assert source.description.strip()


def test_the_description_does_not_change_between_reads(source: ProfileSource) -> None:
    assert source.description == source.description


def test_load_answers_with_a_load_result(result: LoadResult) -> None:
    assert isinstance(result, LoadResult)


# ---- what a load must contain ------------------------------------------------------------


def test_devices_and_plans_are_mappings_keyed_by_name(result: LoadResult) -> None:
    assert isinstance(result.devices, Mapping)
    assert isinstance(result.plans, Mapping)
    assert all(isinstance(name, str) and name for name in result.devices)
    assert all(isinstance(name, str) and name for name in result.plans)


def test_nothing_private_is_published(result: LoadResult) -> None:
    """A namespace is full of imports and helpers; only deliberate names reach the registry."""
    assert [n for n in result.devices if n.startswith("_")] == []
    assert [n for n in result.plans if n.startswith("_")] == []


def test_every_plan_is_callable(result: LoadResult) -> None:
    not_callable = [name for name, plan in result.plans.items() if not callable(plan)]
    assert not_callable == []


def test_the_engine_is_a_runengine_or_nothing(result: LoadResult) -> None:
    """A source may create one, and the host adopts it; otherwise the host makes its own."""
    assert result.engine is None or isinstance(result.engine, RunEngine)


def test_the_namespace_comes_back_for_the_service_to_inspect(result: LoadResult) -> None:
    assert isinstance(result.namespace, Mapping)


def test_the_result_says_where_it_came_from(source: ProfileSource, result: LoadResult) -> None:
    assert result.source_description
    assert result.source_description == source.description


# ---- the part that actually matters --------------------------------------------------------


def test_what_a_source_produces_is_usable_by_the_registry(result: LoadResult) -> None:
    """The contract is not the signature — it is that the rest of the service can use this.

    A source could honour every check above and still hand back something the registry
    refuses. This is the check that would catch it.
    """
    registry = Registry()
    registry.load_from(result)

    assert set(registry.plans()) == set(result.plans)
    assert set(registry.devices()) == set(result.devices)
    for name in result.devices:
        assert registry.get_device(name) is result.devices[name]
    for name in result.plans:
        assert registry.resolve(name) is not None


def test_a_source_yields_something_to_run(result: LoadResult) -> None:
    """Devices without plans is a usable beamline; neither is not."""
    assert result.plans, "a source with no plans leaves the queue unable to run anything"
