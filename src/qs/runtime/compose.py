"""The composition root: wires every part together (dependency injection happens here).

Order at startup (``cap:service-runtime``): console capture, engine thread, profile load on
the engine thread (adopting its RunEngine), database and repositories, registry, queue,
device definitions re-instantiated, sequencer thread, HTTP application.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI

from qs.api.app import create_app
from qs.api.auth import Authenticator, SingleKeyAuthenticator
from qs.api.console import ConsoleCapture
from qs.api.deps import Services
from qs.api.status import StatusReporter
from qs.api.streams import EventBroadcaster
from qs.devices.service import DeviceDefinitionService
from qs.engine.events import EventBus
from qs.engine.host import EngineHost
from qs.persistence import Database, SqlDeviceDefinitionRepository, SqlQueueRepository
from qs.queue.service import QueueService
from qs.registry import Registry
from qs.runtime.config import Config
from qs.sequencer import Sequencer
from qs.sources.protocol import ProfileSource

logger = logging.getLogger(__name__)


@dataclass
class Application:
    config: Config
    services: Services
    app: FastAPI
    database: Database

    def close(self) -> None:
        self.services.sequencer.close()
        self.services.host.shutdown()
        self.services.console.uninstall()
        self.database.dispose()


def make_source(config: Config) -> ProfileSource:
    kind = config.startup.kind
    if kind == "ipython":
        if not config.startup.startup_dir:
            raise ValueError("startup.startup_dir is required for an IPython-style profile")
        from qs.sources.ipython_profile import IPythonProfileSource

        return IPythonProfileSource(config.startup.startup_dir)
    if kind == "bits":
        if not config.startup.startup_module:
            raise ValueError("startup.startup_module is required for a BITS instrument")
        from qs.sources.bits_instrument import BitsInstrumentSource

        return BitsInstrumentSource(config.startup.startup_module)
    if kind == "happi":
        if not config.startup.happi_path:
            raise ValueError("startup.happi_path is required for a happi database")
        from qs.sources.happi_database import HappiDatabaseSource

        return HappiDatabaseSource(config.startup.happi_path)
    raise ValueError(f"Unknown startup.kind {kind!r}: use 'ipython', 'bits' or 'happi'")


def make_authenticator(config: Config) -> Authenticator:
    if config.http.allow_anonymous:
        return SingleKeyAuthenticator(None, allow_anonymous=True)
    if config.http.api_key:
        return SingleKeyAuthenticator(config.http.api_key)
    import secrets

    key = secrets.token_hex(32)
    logger.warning("No API key configured; generated single-user key: %s", key)
    return SingleKeyAuthenticator(key, generated=True)


def build_application(
    config: Config,
    *,
    source: ProfileSource | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> Application:
    events = EventBus()
    console = ConsoleCapture(events)
    if config.engine.capture_console:
        console.install()

    host = EngineHost(
        events=events,
        progress_enabled=config.engine.stream_device_progress,
        progress_min_update_period=config.engine.progress_min_update_period,
    )
    host.start()
    source = source or make_source(config)
    logger.info("Loading profile: %s", source.description)
    load_result = host.load_source(source)
    logger.info(
        "Profile loaded: %d devices, %d plans, engine %s",
        len(load_result.devices),
        len(load_result.plans),
        "adopted" if host.engine_adopted else "created",
    )

    registry = Registry()
    registry.load_from(load_result)
    for name in config.ignored_environment:
        logger.warning("[startup] ignoring %s: not applicable to qs (HTTP only; no 0MQ, kernel, lock)", name)
    if config.http.api_key:
        auth = "single-user API key"
    elif config.http.allow_anonymous:
        auth = "anonymous access"
    else:
        auth = "generated key"
    logger.info(
        "[startup] http://%s:%d, %s, database %s, progress stream %s, console capture %s",
        config.http.host,
        config.http.port,
        auth,
        _redact(config.database.url),
        "on" if config.engine.stream_device_progress else "off",
        "on" if config.engine.capture_console else "off",
    )

    database = Database(config.database.url)
    database.create_all()
    queue = QueueService(SqlQueueRepository(database), registry)

    sequencer = Sequencer(
        host=host,
        queue=queue,
        registry=registry,
        events=events,
        require_synced_experiment=config.engine.require_synced_experiment,
    )
    status = StatusReporter(host=host, queue=queue, sequencer=sequencer, registry=registry)

    devices = DeviceDefinitionService(
        repository=SqlDeviceDefinitionRepository(database),
        registry=registry,
        host=host,
        on_change=status.bump_registry,
    )
    failures = devices.instantiate_all_enabled()
    for name, error in failures.items():
        logger.error("Device definition %s could not be instantiated: %s", name, error)

    sequencer.start_thread()

    services = Services(
        host=host,
        registry=registry,
        queue=queue,
        sequencer=sequencer,
        events=events,
        authenticator=make_authenticator(config),
        status=status,
        console=console,
        broadcaster=EventBroadcaster(events),
        devices=devices,
        config=config.to_dict(),
        shutdown_callback=shutdown_callback,
    )
    app = create_app(services, allow_origins=config.http.allow_origins, smoke_page=config.http.smoke_page)
    return Application(config=config, services=services, app=app, database=database)


def _redact(url: str) -> str:
    """Hide credentials in a database URL for logs."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url
