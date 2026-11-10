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
        try:
            install_send_menu()
        except Exception:
            pass
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
    remove_send_menu()
    path = target()
    if path.exists():
        path.unlink()
        return True
    return False


# ---- "Send to phone" in Explorer -------------------------------------------
# The AirDrop part on the laptop: right-click any file or folder, Send to
# phone, done. Per-user registry keys and one shortcut in the Send To folder,
# so no admin prompt, and `toss uninstall` takes all of it away again.

MENU_KEYS = (r"Software\Classes\*\shell\toss", r"Software\Classes\Directory\shell\toss")


def _pythonw() -> Path:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def _sendto_link() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/SendTo/Phone (toss).lnk"


def _icon() -> str:
    """An .ico next to the config, made from the page icon. Explorer cannot use a png."""
    from . import config
    ico = config.CONFIG_DIR / "toss.ico"
    if not ico.exists():
        try:
            from PIL import Image
            config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            Image.open(Path(__file__).parent / "web" / "icon-192.png").save(
                ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        except Exception:
            return str(_pythonw())
    return str(ico)


def install_send_menu() -> bool:
    if sys.platform != "win32":
        return False
    import subprocess
    import winreg
    py, icon = _pythonw(), _icon()
    command = f'"{py}" -m toss send "%1"'
    for key in MENU_KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, "Send to phone")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # select 50 photos and it still shows up (the default caps it at 15)
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command)
    # Send To hands every selected file to one process, so a batch goes in one go.
    link = _sendto_link()
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TOSS_LNK);"
        "$s.TargetPath=$env:TOSS_PY;$s.Arguments='-m toss send';"
        "$s.IconLocation=$env:TOSS_ICO;$s.Description='Send to your phone with toss';$s.Save()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       env={**os.environ, "TOSS_LNK": str(link), "TOSS_PY": str(py), "TOSS_ICO": icon},
                       capture_output=True, timeout=20, creationflags=0x08000000)
    except Exception:
        pass
    return True


def remove_send_menu() -> None:
    if sys.platform != "win32":
        return
    import winreg
    for key in MENU_KEYS:
        for sub in (key + r"\command", key):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
    try:
        _sendto_link().unlink()
    except OSError:
        pass
