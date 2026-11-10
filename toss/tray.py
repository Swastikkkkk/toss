"""The tray icon: toss without a terminal.

Click it for the pairing QR. Pause sync when you are copying something you do
not want on your phone. Notifications say when something arrives.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

from .hub import WEB_DIR


class Tray:
    def __init__(self, port: int, inbox: Path, state: dict):
        self.port = port
        self.inbox = inbox
        self.state = state          # shared with the serve loop: {"paused": bool, "peers": [...]}
        self.icon = None

    @staticmethod
    def available() -> bool:
        if sys.platform != "win32":
            return False
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return True

    def start(self) -> bool:
        if not self.available():
            return False
        import pystray
        from PIL import Image

        image = Image.open(WEB_DIR / "icon-192.png")
        M = pystray.MenuItem

        def status(_):
            peers = self.state.get("peers") or []
            if self.state.get("paused"):
                return "Sync paused"
            return f"Connected: {', '.join(peers)}" if peers else "Waiting for your phone"

        menu = pystray.Menu(
            M(status, None, enabled=False),
            # one click on the icon sends whatever you copied, like a drop target
            M("Send my clipboard to phone", self.send_clipboard, default=True),
            M("Pair a phone (show QR)", self.show_qr),
            M("Pause clipboard sync", self.toggle_pause, checked=lambda _: bool(self.state.get("paused"))),
            M("Open received files", self.open_inbox),
            pystray.Menu.SEPARATOR,
            M("Quit toss", self.quit),
        )
        self.icon = pystray.Icon("toss", image, "toss", menu)
        self.icon.run_detached()
        return True

    def send_clipboard(self, *_):
        import subprocess
        from .autostart import _pythonw
        subprocess.Popen([str(_pythonw()), "-m", "toss", "send", "--port", str(self.port)],
                         creationflags=0x08000000 if sys.platform == "win32" else 0)

    def show_qr(self, *_):
        webbrowser.open(f"http://127.0.0.1:{self.port}/pair")

    def toggle_pause(self, *_):
        self.state["paused"] = not self.state.get("paused")
        self.notify("Clipboard sync paused" if self.state["paused"] else "Clipboard sync is back on")

    def open_inbox(self, *_):
        self.inbox.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(self.inbox)  # type: ignore[attr-defined]

    def notify(self, text: str):
        if self.icon:
            try:
                self.icon.notify(text, "toss")
            except Exception:
                pass

    def refresh(self):
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def quit(self, *_):
        if self.icon:
            self.icon.stop()
        threading.Timer(0.2, lambda: os._exit(0)).start()
