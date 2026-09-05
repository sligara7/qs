"""Entry point: ``qs`` (console script) or ``python -m qs.runtime.main``."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from qs import __version__
from qs.diagnostics import configure_logging, summarize
from qs.errors import ErrorCode
from qs.runtime.config import load_config

logger = logging.getLogger("qs.runtime")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qs", description="Bluesky queue service (bluesky-queueserver replacement, HTTP only)."
    )
    parser.add_argument("--config", help="YAML config file (or QSERVER_CONFIG)")
    parser.add_argument("--startup-dir", help="IPython-style profile-collection startup directory")
    parser.add_argument(
        "--startup-module", help="BITS instrument startup module (e.g. my_instrument.startup)"
    )
    parser.add_argument("--happi-path", help="happi database path or URI")
    parser.add_argument("--database-url", help="SQLAlchemy URL for the queue database")
    parser.add_argument("--host", help="HTTP bind address (default 127.0.0.1; put TLS at a reverse proxy)")
    parser.add_argument("--port", type=int, help="HTTP port (default 60610)")
    parser.add_argument("--api-key", help="single-user API key (or QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY)")
    parser.add_argument(
        "--allow-anonymous", action="store_true", help="disable authentication (trusted network)"
    )
    parser.add_argument(
        "--stream-device-progress", action="store_true", help="stream device progress on /api/info/ws"
    )
    parser.add_argument("--no-console-capture", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--list-plans",
        action="store_true",
        help="load the profile, print its plans and devices, and exit without serving",
    )
    parser.add_argument("--version", action="version", version=f"qs {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    overrides: dict[str, Any] = {
        "startup.startup_dir": args.startup_dir,
        "startup.startup_module": args.startup_module,
        "startup.happi_path": args.happi_path,
        "database.url": args.database_url,
        "http.host": args.host,
        "http.port": args.port,
        "http.api_key": args.api_key,
        "http.allow_anonymous": True if args.allow_anonymous else None,
        "engine.stream_device_progress": True if args.stream_device_progress else None,
        "engine.capture_console": False if args.no_console_capture else None,
    }
    if args.startup_module:
        overrides["startup.kind"] = "bits"
    elif args.happi_path:
        overrides["startup.kind"] = "happi"
    config = load_config(config_path=args.config, overrides=overrides)

    if args.list_plans:
        return list_plans(config)

    import uvicorn

    from qs.runtime.compose import build_application

    server_holder: dict[str, Any] = {}

    def request_shutdown() -> None:
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True

    try:
        application = build_application(config, shutdown_callback=request_shutdown)
    except Exception as exc:  # noqa: BLE001 - a profile that does not load is fatal on purpose
        _log_load_failure(exc)
        return 1
    uv_config = uvicorn.Config(
        application.app,
        host=config.http.host,
        port=config.http.port,
        log_level=args.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    server = uvicorn.Server(uv_config)
    server_holder["server"] = server
    try:
        server.run()
    finally:
        application.close()
    return 0


def _log_load_failure(exc: BaseException) -> None:
    summary = summarize(exc)
    logger.error(
        "[%s] the profile did not load: %s. Fix the profile (or the IOC it needs) and restart; "
        "`qs --list-plans` reproduces the load without serving.",
        ErrorCode.PROFILE_LOAD,
        summary.headline,
    )
    logger.debug("Traceback:\n%s", summary.traceback)


def list_plans(config: Any, out: Any = None) -> int:
    """Load the profile the way the service would and print what it found (no server)."""
    from qs.engine import EngineHost, EventBus
    from qs.registry import Registry
    from qs.runtime.compose import make_source

    out = out or sys.stdout
    host = EngineHost(events=EventBus())
    host.start()
    try:
        try:
            result = host.load_source(make_source(config))
        except Exception as exc:  # noqa: BLE001
            _log_load_failure(exc)
            return 1
        registry = Registry()
        registry.load_from(result)
        print(f"# {result.source_description}", file=out)
        print(f"# engine: {'adopted from profile' if host.engine_adopted else 'created by qs'}", file=out)
        print(f"\n{len(registry.devices())} devices", file=out)
        for name, entry in sorted(registry.devices().items()):
            print(f"  {name:28s} {type(entry.device).__name__}", file=out)
        print(f"\n{len(registry.plans())} plans", file=out)
        for name, factory in sorted(registry.plans().items()):
            print(f"  {name:28s} {_signature(factory)}", file=out)
    finally:
        host.shutdown()
    return 0


def _signature(plan: Any) -> str:
    import inspect

    try:
        return str(inspect.signature(plan))
    except (TypeError, ValueError):
        return "(...)"


if __name__ == "__main__":
    sys.exit(main())
