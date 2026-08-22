# SPDX-License-Identifier: GPL-2.0-only
"""Tests for TOML configuration loading and validation."""

import pytest

from weft.config import (
    DEFAULT_HTTP_PORT,
    DEFAULT_IMAGE,
    DEFAULT_JOB_TIMEOUT_S,
    ConfigError,
    load,
)


def write(tmp_path, text):
    """write - drop a config file next to an existing workspace directory."""
    (tmp_path / "ws").mkdir(exist_ok=True)
    cfg = tmp_path / "weft.toml"
    cfg.write_text(text.replace("@WS@", str(tmp_path / "ws")))
    return cfg


def fake_quartus(tmp_path, name):
    """fake_quartus - an install root that looks real enough to validate."""
    root = tmp_path / name
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "quartus_sh").write_text("")
    return root


def test_minimal_config_applies_defaults(tmp_path):
    cfg = load(write(tmp_path, '[workspace]\nroot = "@WS@"\n'))
    assert cfg.workspace == tmp_path / "ws"
    assert cfg.image == DEFAULT_IMAGE
    assert cfg.job_timeout_s == DEFAULT_JOB_TIMEOUT_S
    assert cfg.http.port == DEFAULT_HTTP_PORT
    assert cfg.http.token is None
    assert cfg.quartus == {}
    assert cfg.edition is None
    assert cfg.rag_model is None


def test_state_files_default_into_the_workspace(tmp_path):
    cfg = load(write(tmp_path, '[workspace]\nroot = "@WS@"\n'))
    assert cfg.jobs_db == tmp_path / "ws" / ".weft" / "jobs.sqlite"
    assert cfg.rag_db == tmp_path / "ws" / ".weft" / "rag.sqlite"


def test_missing_workspace_section(tmp_path):
    with pytest.raises(ConfigError, match="missing \\[workspace\\]"):
        load(write(tmp_path, '[container]\nimage = "x"\n'))


def test_workspace_must_exist(tmp_path):
    cfg = tmp_path / "weft.toml"
    cfg.write_text('[workspace]\nroot = "/nonexistent/weft"\n')
    with pytest.raises(ConfigError, match="not a directory"):
        load(cfg)


def test_unknown_key_is_rejected(tmp_path):
    """A silently ignored typo is worse than a startup failure."""
    with pytest.raises(ConfigError, match="unknown key"):
        load(write(tmp_path, '[workspace]\nroot = "@WS@"\n[container]\nimg = "x"\n'))


def test_malformed_toml(tmp_path):
    cfg = tmp_path / "weft.toml"
    cfg.write_text("[workspace\n")
    with pytest.raises(ConfigError, match="malformed"):
        load(cfg)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load(tmp_path / "absent.toml")


def test_single_quartus_edition_becomes_the_default(tmp_path):
    root = fake_quartus(tmp_path, "lite")
    cfg = load(write(tmp_path, f'[workspace]\nroot = "@WS@"\n[quartus.lite]\nroot = "{root}"\n'))
    assert cfg.edition == "lite"
    assert cfg.quartus["lite"].root == root


def test_two_editions_need_an_explicit_default(tmp_path):
    lite = fake_quartus(tmp_path, "lite")
    pro = fake_quartus(tmp_path, "pro")
    cfg = load(
        write(
            tmp_path,
            f'[workspace]\nroot = "@WS@"\n'
            f'[quartus.lite]\nroot = "{lite}"\n[quartus.pro]\nroot = "{pro}"\n',
        )
    )
    assert cfg.edition is None
    assert set(cfg.quartus) == {"lite", "pro"}


def test_flexlm_env_is_carried_through(tmp_path):
    pro = fake_quartus(tmp_path, "pro")
    cfg = load(
        write(
            tmp_path,
            f'[workspace]\nroot = "@WS@"\n[quartus.pro]\nroot = "{pro}"\n'
            f'env = {{ LM_LICENSE_FILE = "1800@lic" }}\n',
        )
    )
    assert cfg.quartus["pro"].env == {"LM_LICENSE_FILE": "1800@lic"}


def test_quartus_root_without_binaries_is_rejected(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ConfigError, match="quartus_sh"):
        load(write(tmp_path, f'[workspace]\nroot = "@WS@"\n[quartus.lite]\nroot = "{empty}"\n'))


def test_default_edition_must_be_configured(tmp_path):
    lite = fake_quartus(tmp_path, "lite")
    with pytest.raises(ConfigError, match="unconfigured edition"):
        load(
            write(
                tmp_path,
                f'[workspace]\nroot = "@WS@"\n[quartus]\nedition = "pro"\n'
                f'[quartus.lite]\nroot = "{lite}"\n',
            )
        )


def test_http_settings_are_read(tmp_path):
    cfg = load(
        write(
            tmp_path,
            '[workspace]\nroot = "@WS@"\n[http]\nhost = "0.0.0.0"\nport = 9001\ntoken = "s3cret"\n',
        )
    )
    assert (cfg.http.host, cfg.http.port, cfg.http.token) == ("0.0.0.0", 9001, "s3cret")
