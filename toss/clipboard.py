"""Reading and writing the system clipboard, without a hard dependency.

Windows goes through ctypes with every signature declared explicitly. That is
not decoration: ctypes assumes a function returns a 32-bit int unless told
otherwise, and on 64-bit Windows the clipboard hands back 64-bit pointers. An
undeclared call truncates them, which reads as an empty clipboard on a good day
and an access violation on a bad one. Everything else shells out to whatever
the desktop provides.
"""
from __future__ import annotations

import functools
import shutil
import subprocess
import sys
import time

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002

# Formats password managers set to say "do not sync or record this". Windows'
# own clipboard history and cloud clipboard honour them, so we do too.
_SKIP_IF_PRESENT = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
_SKIP_IF_ZERO = ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")


@functools.lru_cache(maxsize=1)
def _win():
    """Load the three DLLs once, with every signature we touch declared."""
    import ctypes
    from ctypes import wintypes as w

    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    s = ctypes.WinDLL("shell32", use_last_error=True)

    def sig(fn, args, res):
        fn.argtypes, fn.restype = args, res

    sig(u.OpenClipboard, [w.HWND], w.BOOL)
    sig(u.CloseClipboard, [], w.BOOL)
    sig(u.EmptyClipboard, [], w.BOOL)
    sig(u.IsClipboardFormatAvailable, [w.UINT], w.BOOL)
    sig(u.GetClipboardData, [w.UINT], w.HANDLE)
    sig(u.SetClipboardData, [w.UINT, w.HANDLE], w.HANDLE)
    sig(u.GetClipboardSequenceNumber, [], w.DWORD)
    sig(u.RegisterClipboardFormatW, [w.LPCWSTR], w.UINT)
    sig(u.keybd_event, [w.BYTE, w.BYTE, w.DWORD, ctypes.c_size_t], None)
    sig(u.GetAsyncKeyState, [ctypes.c_int], ctypes.c_short)
    sig(k.GlobalLock, [w.HGLOBAL], w.LPVOID)
    sig(k.GlobalUnlock, [w.HGLOBAL], w.BOOL)
    sig(k.GlobalAlloc, [w.UINT, ctypes.c_size_t], w.HGLOBAL)
    sig(k.GlobalFree, [w.HGLOBAL], w.HGLOBAL)
    sig(k.GlobalSize, [w.HGLOBAL], ctypes.c_size_t)
    sig(s.DragQueryFileW, [w.HANDLE, w.UINT, w.LPWSTR, w.UINT], w.UINT)
    return ctypes, u, k, s


class _Open:
    """OpenClipboard fails while another app holds it, which happens constantly
    in the instant after a copy. Retry briefly instead of reporting empty."""

    def __enter__(self):
        _, u, _, _ = _win()
        for _ in range(25):
            if u.OpenClipboard(None):
                return self
            time.sleep(0.02)
        raise OSError("the clipboard is busy in another app")

    def __exit__(self, *exc):
        _win()[1].CloseClipboard()


def _windows_dword_format(name: str):
    """Value of a DWORD-sized custom format, or None when it is absent."""
    ctypes, u, k, _ = _win()
    fmt = u.RegisterClipboardFormatW(name)
    if not fmt or not u.IsClipboardFormatAvailable(fmt):
        return None
    handle = u.GetClipboardData(fmt)
    if not handle:
        return None
    ptr = k.GlobalLock(handle)
    if not ptr:
        return None
    try:
        if k.GlobalSize(handle) >= 4:
            return ctypes.c_uint32.from_address(ptr).value
        return None
    finally:
        k.GlobalUnlock(handle)


def _windows_sensitive() -> bool:
    _, u, _, _ = _win()
    for name in _SKIP_IF_PRESENT:
        fmt = u.RegisterClipboardFormatW(name)
        if fmt and u.IsClipboardFormatAvailable(fmt):
            return True
    for name in _SKIP_IF_ZERO:
        if _windows_dword_format(name) == 0:
            return True
    return False


def _windows_read():
    ctypes, u, k, s = _win()
    with _Open():
        sensitive = _windows_sensitive()

        # A file copied in Explorer arrives as a drop list, not as text.
        if u.IsClipboardFormatAvailable(CF_HDROP):
            handle = u.GetClipboardData(CF_HDROP)
            if handle:
                count = s.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
                paths = []
                for i in range(count):
                    length = s.DragQueryFileW(handle, i, None, 0)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    s.DragQueryFileW(handle, i, buf, length + 1)
                    paths.append(buf.value)
                if paths:
                    return {"kind": "files", "paths": paths, "sensitive": sensitive}

        if u.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = u.GetClipboardData(CF_UNICODETEXT)
            if handle:
                ptr = k.GlobalLock(handle)
                if ptr:
                    try:
                        size = k.GlobalSize(handle)
                        text = ctypes.wstring_at(ptr, size // 2) if size else ctypes.wstring_at(ptr)
                    finally:
                        k.GlobalUnlock(handle)
                    text = text.split("\x00", 1)[0]
                    if text:
                        return {"kind": "text", "text": text, "sensitive": sensitive}
    return None


def _windows_write(text: str) -> bool:
    ctypes, u, k, _ = _win()
    data = (text + "\x00").encode("utf-16-le")
    handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = k.GlobalLock(handle)
    if not ptr:
        k.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, len(data))
    k.GlobalUnlock(handle)
    try:
        with _Open():
            u.EmptyClipboard()
            if not u.SetClipboardData(CF_UNICODETEXT, handle):
                k.GlobalFree(handle)  # we still own it if the system refused
                return False
            return True  # the system owns the memory now
    except OSError:
        k.GlobalFree(handle)
        return False


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
    """{'kind':'text','text':..} or {'kind':'files','paths':[..]} or None.
    Either may carry 'sensitive': True when a password manager asked us not to sync it."""
    if sys.platform == "win32":
        try:
            return _windows_read()
        except OSError:
            return None
    if sys.platform == "darwin":
        text = _run(["pbpaste"])
        return {"kind": "text", "text": text, "sensitive": False} if text else None
    for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"]):
        if shutil.which(cmd[0]):
            text = _run(cmd)
            if text:
                return {"kind": "text", "text": text, "sensitive": False}
    return None


def write(text: str) -> bool:
    if sys.platform == "win32":
        try:
            return _windows_write(text)
        except OSError:
            return False
    if sys.platform == "darwin":
        return bool(_run(["pbcopy"], text))
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
        if shutil.which(cmd[0]):
            return bool(_run(cmd, text))
    return False


def sequence() -> int | None:
    """A counter Windows bumps on every clipboard change. Cheap enough to poll."""
    if sys.platform != "win32":
        return None
    try:
        return int(_win()[1].GetClipboardSequenceNumber())
    except Exception:
        return None


def grab_selection(ctrl_already_down: bool = False) -> bool:
    """Press Ctrl+C in whatever window has focus, so you can send what is
    highlighted without copying it first. When the trigger key you are holding
    is already a Ctrl, only `c` is sent, because Ctrl is down as far as the OS
    is concerned."""
    if sys.platform != "win32":
        return False
    try:
        _, u, _, _ = _win()
        VK_CONTROL, VK_C, KEYUP = 0x11, 0x43, 0x0002
        before = sequence()
        if not ctrl_already_down:
            u.keybd_event(VK_CONTROL, 0, 0, 0)
        u.keybd_event(VK_C, 0, 0, 0)
        u.keybd_event(VK_C, 0, KEYUP, 0)
        if not ctrl_already_down:
            u.keybd_event(VK_CONTROL, 0, KEYUP, 0)
        for _ in range(25):  # the focused app needs a beat to service the copy
            time.sleep(0.015)
            if sequence() != before:
                return True
        return False
    except Exception:
        return False
