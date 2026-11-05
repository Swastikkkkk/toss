"""Start toss when you log in, so it is simply always there.

Windows gets a shortcut in the Startup folder that runs pythonw, which means no
console window. Linux gets a .desktop autostart entry. macOS gets a LaunchAgent.
Everything written here is a plain text file you can delete by hand.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _windows_startup() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"


def target() -> Path:
    if sys.platform == "win32":
        return _windows_startup() / "toss.bat"
    if sys.platform == "darwin":
        return Path("~/Library/LaunchAgents/com.toss.hub.plist").expanduser()
    return Path("~/.config/autostart/toss.desktop").expanduser()


def installed() -> bool:
    return target().exists()


def install(port: int) -> Path:
    path = target()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # pythonw has no console, so this runs silently in the background
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        runner = pythonw if pythonw.exists() else exe
        path.write_text(
            "@echo off\r\n"
            f'start "" "{runner}" -m toss serve --port {port} --quiet --tray\r\n',
            encoding="utf-8",
        )
    elif sys.platform == "darwin":
        path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.toss.hub</string>
  <key>ProgramArguments</key>
  <array><string>{sys.executable}</string><string>-m</string><string>toss</string>
    <string>serve</string><string>--port</string><string>{port}</string><string>--quiet</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
""", encoding="utf-8")
    else:
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=toss\n"
            f"Exec={sys.executable} -m toss serve --port {port} --quiet\n"
            "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
            encoding="utf-8",
        )
    return path


def uninstall() -> bool:
    path = target()
    if path.exists():
        path.unlink()
        return True
    return False
