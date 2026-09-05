"""The two non-NSLS-II profile sources: an APS BITS instrument package and a happi database.

Both were written first and tested later (user decision 2026-09-04: test them). They need the
optional packages from the dev extra and are skipped without them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qs.engine import EngineHost, EventBus
from qs.registry import Registry


def test_bits_demo_instrument_loads_devices_plans_and_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("apsbits")
    from qs.sources.bits_instrument import BitsInstrumentSource

    monkeypatch.chdir(tmp_path)  # the demo instrument writes its RE.md dict and a SPEC file to cwd
    monkeypatch.setenv("MPLBACKEND", "Agg")
    result = BitsInstrumentSource("apsbits.demo_instrument.startup").load()
    assert {"sim_det", "sim_motor"} <= set(result.devices)
    assert "lineup2" in result.plans  # apstools plan re-exported by the instrument
    assert result.engine is not None, "BITS init_RE creates the RunEngine; qs must adopt it"


def test_happi_database_loads_devices_and_runs_a_plan(tmp_path: Path) -> None:
    happi = pytest.importorskip("happi")
    from happi.item import HappiItem

    from qs.sources.happi_database import HappiDatabaseSource

    db = tmp_path / "db.json"
    client = happi.Client(path=str(db))
    for name, cls in (("motor", "ophyd.sim.SynAxis"), ("det", "ophyd.sim.SynSignal")):
        client.create_item(
            HappiItem, name=name, device_class=cls, args=[], kwargs={"name": "{{name}}"}, active=True
        ).save()
    client.create_item(
        HappiItem, name="broken", device_class="ophyd.sim.NoSuchDevice", args=[], kwargs={}, active=True
    ).save()

    events = EventBus()
    host = EngineHost(events=events)
    host.start()
    try:
        result = host.load_source(HappiDatabaseSource(str(db)))
        assert set(result.devices) == {"motor", "det"}
        assert "broken" in result.namespace["happi_failures"], "one bad entry must not stop the load"
        assert result.engine is None and host.engine is not None, "no engine in a happi db: qs creates one"
        registry = Registry()
        registry.load_from(result)
        assert {"count", "scan"} <= set(registry.plans())
        factory = registry.resolve("count", [["det"]], {"num": 2})
        outcome = host.run_plan(factory, item_uid="happi-1", metadata={}).result(timeout=60)
        assert outcome.succeeded and len(outcome.run_uids) == 1
    finally:
        host.shutdown()
