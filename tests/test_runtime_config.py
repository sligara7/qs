"""Configuration at the Runtime box's boundary (``cmp:runtime``, ``cap:qserver-env``).

``load_config`` is the whole reason an operator can move from bluesky-queueserver without
relearning how to configure anything, so the part worth pinning is the PRECEDENCE — file,
then environment, then command line — and the promise that the variables qs cannot honour
are reported rather than silently ignored (``dec:replaces-queueserver``).

``env`` is always passed explicitly: a test that read the real environment would pass or fail
depending on whose shell ran it.
"""

from __future__ import annotations

import json

import pytest

from qs.runtime import Config, load_config


def write_config(tmp_path, text: str) -> str:
    path = tmp_path / "qs.yml"
    path.write_text(text)
    return str(path)


def test_the_defaults_are_what_the_docs_say() -> None:
    config = load_config(env={})
    assert config.startup.kind == "ipython"
    assert config.database.url == "sqlite:///qs.sqlite"
    assert (config.http.host, config.http.port) == ("127.0.0.1", 60610)
    assert config.http.allow_anonymous is False
    assert config.engine.stream_device_progress is False
    assert config.config_path is None


# ---- the file -------------------------------------------------------------------------


def test_a_yaml_file_is_applied_and_recorded(tmp_path) -> None:
    path = write_config(
        tmp_path,
        """
        startup:
          kind: bits
          startup_module: my_instrument.startup
        http:
          port: 8080
          allow_origins: ["https://finch.example"]
        engine:
          stream_device_progress: true
        """,
    )
    config = load_config(config_path=path, env={})
    assert config.startup.kind == "bits"
    assert config.startup.startup_module == "my_instrument.startup"
    assert config.http.port == 8080
    assert config.http.allow_origins == ["https://finch.example"]
    assert config.engine.stream_device_progress is True
    assert config.config_path == path


def test_the_file_may_also_be_named_by_QSERVER_CONFIG(tmp_path) -> None:
    path = write_config(tmp_path, "http:\n  port: 9999\n")
    assert load_config(env={"QSERVER_CONFIG": path}).http.port == 9999


def test_a_config_file_that_is_not_there_is_reported(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(config_path=str(tmp_path / "absent.yml"), env={})


def test_an_unknown_key_is_refused_rather_than_ignored(tmp_path) -> None:
    """A typo in a config file must not read as a default."""
    path = write_config(tmp_path, "http:\n  prot: 8080\n")
    with pytest.raises(ValueError, match="Unknown config key http.prot"):
        load_config(config_path=path, env={})


def test_a_section_that_is_not_a_mapping_is_refused(tmp_path) -> None:
    path = write_config(tmp_path, "http: 8080\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(config_path=path, env={})


def test_an_empty_file_leaves_the_defaults_standing(tmp_path) -> None:
    assert load_config(config_path=write_config(tmp_path, ""), env={}).http.port == 60610


# ---- the environment -------------------------------------------------------------------


def test_the_queueserver_variables_an_operator_already_has_are_honoured() -> None:
    config = load_config(
        env={
            "QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": "secret-key-value",
            "QSERVER_HTTP_SERVER_ALLOW_ANONYMOUS_ACCESS": "yes",
            "QSERVER_HTTP_SERVER_ALLOW_ORIGINS": "https://a.example https://b.example",
            "QSERVER_HTTP_SERVER_RESPONSE_BYTESIZE_LIMIT": "1000",
            "QSERVER_PERMITTED_RE_METADATA_KEYS": "proposal:cycle",
        }
    )
    assert config.http.api_key == "secret-key-value"
    assert config.http.allow_anonymous is True
    assert config.http.allow_origins == ["https://a.example", "https://b.example"]
    assert config.http.response_bytesize_limit == 1000
    assert config.engine.permitted_re_metadata_keys == ["proposal", "cycle"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("no", False), ("", False)],
)
def test_a_boolean_variable_reads_the_spellings_operators_actually_type(value: str, expected: bool) -> None:
    config = load_config(env={"QS_STREAM_DEVICE_PROGRESS": value})
    assert config.engine.stream_device_progress is expected


def test_naming_a_startup_directory_or_module_also_picks_the_kind() -> None:
    assert load_config(env={"QS_STARTUP_DIR": "/srv/profile"}).startup.kind == "ipython"
    assert load_config(env={"QS_STARTUP_MODULE": "inst.startup"}).startup.kind == "bits"


def test_the_variables_qs_cannot_honour_are_reported_not_dropped() -> None:
    """HTTP only, and the queue is the lock — so these have nowhere to go. Say so."""
    config = load_config(
        env={
            "QSERVER_ZMQ_CONTROL_ADDRESS": "tcp://localhost:60615",
            "QSERVER_IPYTHON_KERNEL_IP": "auto",
            "QSERVER_EMERGENCY_LOCK_KEY_FOR_SERVER": "unused",
            "QSERVER_USE_IPYTHON_KERNEL": "1",
            "QS_HTTP_PORT": "1234",
        }
    )
    assert config.ignored_environment == [
        "QSERVER_EMERGENCY_LOCK_KEY_FOR_SERVER",
        "QSERVER_IPYTHON_KERNEL_IP",
        "QSERVER_USE_IPYTHON_KERNEL",
        "QSERVER_ZMQ_CONTROL_ADDRESS",
    ]
    assert config.http.port == 1234, "an unhonoured variable does not stop the rest being read"


# ---- precedence -------------------------------------------------------------------------


def test_environment_beats_file_and_the_command_line_beats_both(tmp_path) -> None:
    path = write_config(tmp_path, "http:\n  port: 1111\n  host: from-file\n")
    config = load_config(
        config_path=path,
        env={"QS_HTTP_PORT": "2222"},
        overrides={"http.port": 3333},
    )
    assert config.http.port == 3333
    assert config.http.host == "from-file", "an override touches only what it names"


def test_an_override_of_none_is_not_an_override(tmp_path) -> None:
    """Click and argparse hand through None for a flag nobody typed."""
    path = write_config(tmp_path, "http:\n  port: 1111\n")
    assert load_config(config_path=path, env={}, overrides={"http.port": None}).http.port == 1111


def test_an_override_that_names_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown override 'http.prot'"):
        load_config(env={}, overrides={"http.prot": 8080})


# ---- reporting the effective settings -----------------------------------------------------


def test_to_dict_redacts_the_secrets_by_default() -> None:
    config = load_config(env={"QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY": "0123456789abcdef"})
    config.tiled.api_key = "tiled-secret"

    redacted = config.to_dict()
    assert redacted["http"]["api_key"] == "01234567…"
    assert redacted["tiled"]["api_key"] == "…"

    assert config.to_dict(redact=False)["http"]["api_key"] == "0123456789abcdef"


def test_the_effective_settings_can_be_dumped_where_queueserver_dumped_them(tmp_path) -> None:
    target = tmp_path / "settings.json"
    load_config(env={"QSERVER_SETTINGS_SAVE_TO_FILE": str(target), "QS_HTTP_PORT": "4321"})
    assert json.loads(target.read_text())["http"]["port"] == 4321


def test_an_unwritable_dump_target_does_not_stop_the_service_starting(tmp_path) -> None:
    """Reporting settings is a convenience; failing to report them is not fatal."""
    config = load_config(env={"QSERVER_SETTINGS_SAVE_TO_FILE": str(tmp_path / "no" / "such" / "dir.json")})
    assert isinstance(config, Config)
