"""Reading and writing the system clipboard, without a hard dependency.

Windows is handled through ctypes so there is nothing to install. Everything
else shells out to whatever the desktop provides. If none of it works the
caller gets None and can say so instead of crashing.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

CF_UNICODETEXT = 13
CF_HDROP = 15


def _windows_read():
    import ctypes
    from ctypes import wintypes

    user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
    if not user32.OpenClipboard(0):
        return None
    try:
        # A file copied in Explorer arrives as a drop list, not as text.
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            handle = user32.GetClipboardData(CF_HDROP)
            if handle:
                count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
                paths = []
                for i in range(count):
                    length = shell32.DragQueryFileW(handle, i, None, 0)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(handle, i, buf, length + 1)
                    paths.append(buf.value)
                if paths:
                    return {"kind": "files", "paths": paths}
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                ptr = kernel32.GlobalLock(handle)
                try:
                    text = ctypes.c_wchar_p(ptr).value
                finally:
                    kernel32.GlobalUnlock(handle)
                if text:
                    return {"kind": "text", "text": text}
        return None
    finally:
        user32.CloseClipboard()


def _run(cmd, text=None):
    try:
        if text is None:
            out = subprocess.run(cmd, capture_output=True, timeout=5)
            return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None
        subprocess.run(cmd, input=text.encode("utf-8"), timeout=5, check=True)
        return True
    except Exception:
        return None


def read():
    """-> {'kind':'text','text':...} or {'kind':'files','paths':[...]} or None."""
    if sys.platform == "win32":
        try:
            return _windows_read()
        except Exception:
            return None
    if sys.platform == "darwin":
        text = _run(["pbpaste"])
        return {"kind": "text", "text": text} if text else None
    for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"]):
        if shutil.which(cmd[0]):
            text = _run(cmd)
            if text:
                return {"kind": "text", "text": text}
    return None


def write(text: str) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            data = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(data)
            handle = kernel32.GlobalAlloc(0x2000, size)  # GMEM_MOVEABLE
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, data, size)
            kernel32.GlobalUnlock(handle)
            if not user32.OpenClipboard(0):
                return False
            try:
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_UNICODETEXT, handle)
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            return False
    if sys.platform == "darwin":
        return bool(_run(["pbcopy"], text))
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
        if shutil.which(cmd[0]):
            return bool(_run(cmd, text))
    return False


def sequence() -> int | None:
    """A counter Windows bumps on every clipboard change. Lets us tell whether
    a copy we asked for actually produced anything new."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def grab_selection(ctrl_already_down: bool = False) -> bool:
    """Press Ctrl+C in whatever window has focus, so you can send what is
    highlighted without copying it first.

    If the trigger key you are holding is already a Ctrl, only `c` is sent,
    because Ctrl is down in the OS's eyes.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        VK_CONTROL, VK_C, KEYUP = 0x11, 0x43, 0x0002
        before = sequence()
        if not ctrl_already_down:
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_C, 0, 0, 0)
        user32.keybd_event(VK_C, 0, KEYUP, 0)
        if not ctrl_already_down:
            user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
        # the focused app needs a beat to service the copy
        import time as _time
        for _ in range(20):
            _time.sleep(0.015)
            if sequence() != before:
                return True
        return False
    except Exception:
        return False
