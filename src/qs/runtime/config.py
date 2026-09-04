"""Configuration: a YAML file, bluesky-queueserver / httpserver environment variables, and
command-line overrides, in that order of precedence (``cap:qserver-env``).

Honoured environment variables (same meaning as in the predecessors):

* ``QSERVER_CONFIG`` — path to the YAML config file.
* ``QSERVER_PERMITTED_RE_METADATA_KEYS`` — colon-separated RE.md keys callers may set.
* ``QSERVER_SETTINGS_SAVE_TO_FILE`` — dump the effective settings to this path at startup.
* ``QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY``, ``QSERVER_HTTP_SERVER_ALLOW_ANONYMOUS_ACCESS``,
  ``QSERVER_HTTP_SERVER_ALLOW_ORIGINS``, ``QSERVER_HTTP_SERVER_RESPONSE_BYTESIZE_LIMIT``.

qs-specific variables: ``QS_STARTUP_DIR``, ``QS_STARTUP_MODULE``, ``QS_DATABASE_URL``,
``QS_HTTP_HOST``, ``QS_HTTP_PORT``, ``QS_STREAM_DEVICE_PROGRESS``.

Not applicable, by design: ``QSERVER_ZMQ_*`` (HTTP only), ``QSERVER_EMERGENCY_LOCK_KEY_FOR_SERVER``
(the queue is the lock), ``QSERVER_USE_IPYTHON_KERNEL`` and ``QSERVER_IPYTHON_KERNEL_*`` (no kernel).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

NOT_APPLICABLE_PREFIXES = ("QSERVER_ZMQ_", "QSERVER_IPYTHON_KERNEL_")
NOT_APPLICABLE_VARS = ("QSERVER_EMERGENCY_LOCK_KEY_FOR_SERVER", "QSERVER_USE_IPYTHON_KERNEL")


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


@dataclass
class StartupConfig:
    kind: str = "ipython"  # "ipython" | "bits" | "happi"
    startup_dir: str | None = None  # ipython
    startup_module: str | None = None  # bits
    happi_path: str | None = None  # happi (a path to a JSON database or a URI)
    device_max_depth: int = 2


@dataclass
class DatabaseConfig:
    url: str = "sqlite:///qs.sqlite"


@dataclass
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 60610
    api_key: str | None = None
    allow_anonymous: bool = False
    allow_origins: list[str] = field(default_factory=list)
    response_bytesize_limit: int = 300_000_000
    smoke_page: bool = True


@dataclass
class EngineConfig:
    stream_device_progress: bool = False
    progress_min_update_period: float = 0.2
    permitted_re_metadata_keys: list[str] = field(default_factory=lambda: ["/"])
    capture_console: bool = True


@dataclass
class TiledConfig:
    uri: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None


@dataclass
class Config:
    startup: StartupConfig = field(default_factory=StartupConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    tiled: TiledConfig = field(default_factory=TiledConfig)
    config_path: str | None = None
    ignored_environment: list[str] = field(default_factory=list)

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if redact:
            if d["http"].get("api_key"):
                d["http"]["api_key"] = d["http"]["api_key"][:8] + "…"
            if d["tiled"].get("api_key"):
                d["tiled"]["api_key"] = "…"
        return d


def _apply_file(config: Config, path: Path) -> None:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Config file {path} must contain a mapping")
    for section_name in ("startup", "database", "http", "engine", "tiled"):
        section = data.get(section_name) or {}
        if not isinstance(section, Mapping):
            raise ValueError(f"Config section {section_name!r} must be a mapping")
        target = getattr(config, section_name)
        for key, value in section.items():
            if not hasattr(target, key):
                raise ValueError(f"Unknown config key {section_name}.{key}")
            setattr(target, key, value)


def _apply_environment(config: Config, env: Mapping[str, str]) -> None:
    if env.get("QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY"):
        config.http.api_key = env["QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY"]
    if "QSERVER_HTTP_SERVER_ALLOW_ANONYMOUS_ACCESS" in env:
        config.http.allow_anonymous = _truthy(env["QSERVER_HTTP_SERVER_ALLOW_ANONYMOUS_ACCESS"])
    if env.get("QSERVER_HTTP_SERVER_ALLOW_ORIGINS"):
        config.http.allow_origins = env["QSERVER_HTTP_SERVER_ALLOW_ORIGINS"].split()
    if env.get("QSERVER_HTTP_SERVER_RESPONSE_BYTESIZE_LIMIT"):
        config.http.response_bytesize_limit = int(env["QSERVER_HTTP_SERVER_RESPONSE_BYTESIZE_LIMIT"])
    if env.get("QSERVER_PERMITTED_RE_METADATA_KEYS"):
        config.engine.permitted_re_metadata_keys = env["QSERVER_PERMITTED_RE_METADATA_KEYS"].split(":")
    if env.get("QS_STARTUP_DIR"):
        config.startup.kind = "ipython"
        config.startup.startup_dir = env["QS_STARTUP_DIR"]
    if env.get("QS_STARTUP_MODULE"):
        config.startup.kind = "bits"
        config.startup.startup_module = env["QS_STARTUP_MODULE"]
    if env.get("QS_DATABASE_URL"):
        config.database.url = env["QS_DATABASE_URL"]
    if env.get("QS_HTTP_HOST"):
        config.http.host = env["QS_HTTP_HOST"]
    if env.get("QS_HTTP_PORT"):
        config.http.port = int(env["QS_HTTP_PORT"])
    if "QS_STREAM_DEVICE_PROGRESS" in env:
        config.engine.stream_device_progress = _truthy(env["QS_STREAM_DEVICE_PROGRESS"])
    config.ignored_environment = sorted(
        k for k in env if k.startswith(NOT_APPLICABLE_PREFIXES) or k in NOT_APPLICABLE_VARS
    )


def load_config(
    *,
    config_path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Build the effective configuration. ``overrides`` are ``{"section.key": value}`` (CLI)."""
    env = os.environ if env is None else env
    config = Config()
    path = config_path or env.get("QSERVER_CONFIG")
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        _apply_file(config, p)
        config.config_path = str(p)
    _apply_environment(config, env)
    for dotted, value in (overrides or {}).items():
        if value is None:
            continue
        section_name, _, key = dotted.partition(".")
        target = getattr(config, section_name)
        if not hasattr(target, key):
            raise ValueError(f"Unknown override {dotted!r}")
        setattr(target, key, value)
    save_to = env.get("QSERVER_SETTINGS_SAVE_TO_FILE")
    if save_to:
        try:
            Path(save_to).write_text(json.dumps(config.to_dict(), indent=2))
        except OSError:
            pass
    return config
