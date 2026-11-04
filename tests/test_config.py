import json
import os
from pathlib import Path

import pytest

from toss import config


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


def test_empty_toss_home_does_not_land_in_the_working_directory(monkeypatch):
    import importlib
    monkeypatch.setenv("TOSS_HOME", "")
    reloaded = importlib.reload(config)
    assert reloaded.CONFIG_DIR == Path("~/.toss").expanduser()
    monkeypatch.delenv("TOSS_HOME")
    importlib.reload(config)


def test_first_run_creates_a_pairing():
    code, salt = config.pairing()
    assert len(code.split("-")) == 4
    assert len(salt) == 16


def test_pairing_survives_restarts():
    """The whole point: you type the code once, not once per boot."""
    first = config.pairing()
    assert config.pairing() == first
    assert config.pairing() == first


def test_rotating_changes_both_code_and_salt():
    code, salt = config.pairing()
    new_code, new_salt = config.pairing(new=True)
    assert new_code != code
    assert new_salt != salt
    assert config.pairing() == (new_code, new_salt)


def test_settings_round_trip():
    config.update(key="capslock", hold=0.25)
    data = config.load()
    assert data["key"] == "capslock"
    assert data["hold"] == 0.25
    assert data["inbox"] == config.DEFAULTS["inbox"], "untouched defaults stay"


def test_update_ignores_none_so_unset_flags_do_not_wipe_settings():
    config.update(key="f9")
    config.update(key=None, hold=0.9)
    assert config.load()["key"] == "f9"


def test_a_corrupt_file_falls_back_instead_of_crashing():
    config.CONFIG_FILE.write_text("{ not json at all")
    assert config.load()["key"] == config.DEFAULTS["key"]


def test_config_is_not_world_readable():
    config.pairing()
    mode = config.CONFIG_FILE.stat().st_mode & 0o777
    assert mode & 0o077 == 0, "the pairing code lives here"


def test_the_code_is_the_only_secret_stored():
    config.pairing()
    stored = json.loads(config.CONFIG_FILE.read_text())
    assert set(stored) <= set(config.DEFAULTS) | {"code", "salt"}


def test_status_prints_the_https_address(capsys, monkeypatch):
    """serve speaks https because phones need a secure origin for WebCrypto.
    status must print the same scheme, or people copy a dead link."""
    import asyncio
    from toss import cli
    monkeypatch.setattr(cli, "lan_ip", lambda: "192.168.1.5")
    asyncio.run(cli.cmd_status(None))
    out = capsys.readouterr().out
    assert "https://192.168.1.5:" in out
    assert "http://192.168.1.5" not in out
