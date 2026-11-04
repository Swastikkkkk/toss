"""Remembered settings, so pairing is a thing you do once.

Lives in ~/.toss/config.json. The pairing code is in here, which is the whole
point: the same code survives a reboot, so your phone rejoins without you
typing anything. Rotate it whenever you like with `toss serve --new-code`.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .crypto import SALT_BYTES, b64, make_code, unb64

# an empty TOSS_HOME would otherwise resolve to the working directory
CONFIG_DIR = Path(os.environ.get("TOSS_HOME") or "~/.toss").expanduser()
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "key": "right-ctrl",
    "hold": 0.4,
    "port": 7777,
    "inbox": "~/Downloads",
    "name": "this laptop",
    "grab": True,
    "sync": True,
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return data


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    try:
        # the pairing code is in here, so keep it to this user
        CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def pairing(new: bool = False) -> tuple[str, bytes]:
    """The remembered code and salt, creating them the first time.

    The salt is kept too, so the derived key is identical across restarts and a
    device that already joined does not have to redo the slow key stretch.
    """
    data = load()
    if new or not data.get("code") or not data.get("salt"):
        data["code"] = make_code()
        data["salt"] = b64(os.urandom(SALT_BYTES))
        save(data)
    return data["code"], unb64(data["salt"])


def update(**kwargs) -> dict:
    data = load()
    data.update({k: v for k, v in kwargs.items() if v is not None})
    save(data)
    return data
