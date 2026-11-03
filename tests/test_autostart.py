import sys

import pytest

from toss import autostart


def test_install_writes_a_file_and_uninstall_removes_it(tmp_path, monkeypatch):
    target = tmp_path / ("toss.bat" if sys.platform == "win32" else "toss.desktop")
    monkeypatch.setattr(autostart, "target", lambda: target)

    assert not autostart.installed()
    path = autostart.install(7777)
    assert path.exists() and autostart.installed()
    assert "7777" in path.read_text()
    assert "toss" in path.read_text()

    assert autostart.uninstall() is True
    assert not autostart.installed()
    assert autostart.uninstall() is False, "removing twice is not an error"


def test_install_is_plain_text_you_can_read(tmp_path, monkeypatch):
    target = tmp_path / "entry"
    monkeypatch.setattr(autostart, "target", lambda: target)
    body = autostart.install(7777).read_text()
    assert "serve" in body and "--quiet" in body
    assert sys.executable.split("/")[-1].split("\\")[-1].replace(".exe", "") in body.replace(".exe", "") \
        or "python" in body.lower()
