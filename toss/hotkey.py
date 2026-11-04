"""Hold a key, send what is on your clipboard.

This is the only part that has to touch the OS at a low level, so it is kept
small and separate. On Windows it uses a raw key-state poll through ctypes,
which needs nothing installed and no admin rights. Elsewhere it falls back to
the `keyboard` package if it happens to be there, and otherwise tells you to
use `toss send` instead of pretending to work.
"""
from __future__ import annotations

import asyncio
import sys
import time

from . import clipboard

# How long after the key is released a stray Ctrl+C still counts as a copy.
QUIET_AFTER = 0.8

# Virtual key codes you might want to hold.
KEYS = {
    "right-ctrl": 0xA3,
    "left-ctrl": 0xA2,
    "right-alt": 0xA5,
    "right-shift": 0xA1,
    "capslock": 0x14,
    "scrolllock": 0x91,
    "f9": 0x78,
}

DEFAULT_KEY = "right-ctrl"
DEFAULT_HOLD = 0.4   # seconds, long enough not to fire on a normal keypress


def available() -> bool:
    return sys.platform == "win32"


async def watch(key: str, hold: float, on_trigger, poll: float = 0.03):
    """Call on_trigger() once each time `key` is held for `hold` seconds.

    It deliberately fires on the way down rather than on release, so the moment
    it sends is the moment you feel, and it will not fire again until you let go.
    """
    if not available():
        raise RuntimeError(
            "The hold-to-send hotkey needs Windows. On this machine use `toss send` instead."
        )
    import ctypes

    vk = KEYS.get(key)
    if vk is None:
        raise ValueError(f"unknown key {key!r}. Try one of: {', '.join(KEYS)}")

    user32 = ctypes.windll.user32
    down_since = None
    fired = False

    while True:
        # high bit set means the key is currently down
        is_down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        now = time.monotonic()
        if is_down:
            # While the hotkey is held, a Ctrl+C arriving is a copy, ours or
            # another app's that also reacts to a held Ctrl. It is never you
            # trying to quit, so toss must not die from it. A Ctrl+C at any
            # other time still stops toss.
            clipboard.injecting_until = max(clipboard.injecting_until, now + QUIET_AFTER)
            if down_since is None:
                down_since = now
            elif not fired and now - down_since >= hold:
                fired = True
                try:
                    result = on_trigger()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:  # a bad send must not kill the watcher
                    print(f"  send failed: {exc}")
        else:
            down_since = None
            fired = False
        await asyncio.sleep(poll)
