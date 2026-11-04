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
import struct
import subprocess
import sys
import time
import zlib

CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15
CF_DIBV5 = 17
GMEM_MOVEABLE = 0x0002
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Until this moment, an interrupt is treated as a stray copy rather than a
# request to quit. The hotkey keeps it open while the key is held, and
# grab_selection extends it while it presses Ctrl+Insert. cli.py reads it.
injecting_until = 0.0

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
    sig(u.MapVirtualKeyW, [w.UINT, w.UINT], w.UINT)
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


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_encode(width: int, height: int, pixels: bytes, channels: int) -> bytes:
    """Encode top-down, unpadded RGB (3) or RGBA (4) pixels as a PNG."""
    row = width * channels
    raw = b"".join(b"\x00" + pixels[y * row:(y + 1) * row] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return (PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(raw, 6)) + _png_chunk(b"IEND", b""))


def png_size(png: bytes):
    if png[:8] != PNG_SIGNATURE or len(png) < 24:
        return None
    return struct.unpack(">II", png[16:24])


def dib_to_png(dib: bytes):
    """A Windows device-independent bitmap (what screenshots and most image
    copies put on the clipboard) as a PNG. 24 and 32 bit only, which is what
    every modern app produces. Returns None for anything it cannot read."""
    if len(dib) < 40:
        return None
    (bi_size, width, height, _planes, bpp, compression,
     _img, _xp, _yp, clr_used, _ci) = struct.unpack_from("<IiiHHIIiiII", dib, 0)
    if width <= 0 or height == 0 or bpp not in (24, 32) or compression not in (0, 3):
        return None
    if compression == 3:  # BI_BITFIELDS: only the standard BGRA layout
        masks = struct.unpack_from("<III", dib, 40)
        if masks != (0x00FF0000, 0x0000FF00, 0x000000FF):
            return None
    h = abs(height)
    stride = ((width * bpp + 31) // 32) * 4
    offset = bi_size
    if compression == 3:
        masks = dib[40:52]
        if bi_size == 40:
            offset += 12  # a plain header is followed by its three colour masks
        if dib[offset:offset + 12] == masks:
            # Windows quirk, seen on a real Windows 11 machine. When it
            # synthesises CF_DIBV5 it keeps the masks in the header and repeats
            # them after it. When it then synthesises CF_DIB from that, it makes
            # the same mistake itself: the pixels start 12 bytes late and the
            # last 12 are cut off. Either way the masks are not image data.
            offset += 12
    offset += clr_used * 4
    need = offset + stride * h
    if len(dib) < need:
        if compression == 3 and need - len(dib) <= 12:
            dib = dib + b"\x00" * (need - len(dib))  # three pixels Windows already lost
        else:
            return None
    data = dib[offset:offset + stride * h]
    bytespp = bpp // 8
    row = width * bytespp
    order = range(h) if height < 0 else range(h - 1, -1, -1)  # bottom-up unless negative
    buf = b"".join(data[y * stride:y * stride + row] for y in order)

    out = bytearray(len(buf))
    if bpp == 32:
        out[0::4], out[1::4], out[2::4] = buf[2::4], buf[1::4], buf[0::4]
        alpha = buf[3::4]
        # plain 32-bit bitmaps leave alpha at zero, which would mean fully clear
        out[3::4] = alpha if alpha.strip(b"\x00") else b"\xff" * (len(buf) // 4)
        return png_encode(width, h, bytes(out), 4)
    out[0::3], out[1::3], out[2::3] = buf[2::3], buf[1::3], buf[0::3]
    return png_encode(width, h, bytes(out), 3)


def _global_bytes(handle):
    ctypes, _, k, _ = _win()
    if not handle:
        return None
    ptr = k.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, k.GlobalSize(handle))
    finally:
        k.GlobalUnlock(handle)


def _windows_image():
    """The clipboard image as PNG bytes, or None. Prefers a real PNG if an app
    put one there, and falls back to converting the bitmap."""
    _, u, _, _ = _win()
    png_fmt = u.RegisterClipboardFormatW("PNG")
    if png_fmt and u.IsClipboardFormatAvailable(png_fmt):
        data = _global_bytes(u.GetClipboardData(png_fmt))
        if data and data.startswith(PNG_SIGNATURE):
            return data
    for fmt in (CF_DIBV5, CF_DIB):  # Windows synthesises these from CF_BITMAP
        if u.IsClipboardFormatAvailable(fmt):
            data = _global_bytes(u.GetClipboardData(fmt))
            png = dib_to_png(data) if data else None
            if png:
                return png
    return None


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

        # Text wins when both exist: Excel and Word also put a picture of what
        # you copied there, and you wanted the text.
        png = _windows_image()
        if png:
            return {"kind": "image", "png": png, "sensitive": sensitive}
    return None


def _windows_write_png(png: bytes) -> bool:
    """Put a PNG on the clipboard under the registered PNG format, which is
    what browsers, Office, Teams, Discord and most chat apps paste from."""
    ctypes, u, k, _ = _win()
    fmt = u.RegisterClipboardFormatW("PNG")
    if not fmt:
        return False
    handle = k.GlobalAlloc(GMEM_MOVEABLE, len(png))
    if not handle:
        return False
    ptr = k.GlobalLock(handle)
    if not ptr:
        k.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, png, len(png))
    k.GlobalUnlock(handle)
    try:
        with _Open():
            u.EmptyClipboard()
            if not u.SetClipboardData(fmt, handle):
                k.GlobalFree(handle)
                return False
            return True
    except OSError:
        k.GlobalFree(handle)
        return False


def _windows_write_files(paths) -> bool:
    """Put files on the clipboard the way Explorer does, so Ctrl+V pastes them
    into a folder, an email, WhatsApp or anything else that takes files."""
    ctypes, u, k, _ = _win()
    paths = [str(p) for p in paths]
    # DROPFILES: offset to the list, a point, fNC, fWide; then NUL-separated
    # UTF-16 paths ending in a double NUL
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    body = ("\x00".join(paths) + "\x00\x00").encode("utf-16-le")
    data = header + body
    drop_effect_fmt = u.RegisterClipboardFormatW("Preferred DropEffect")

    def alloc(raw):
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not h:
            return None
        ptr = k.GlobalLock(h)
        if not ptr:
            k.GlobalFree(h)
            return None
        ctypes.memmove(ptr, raw, len(raw))
        k.GlobalUnlock(h)
        return h

    files_handle = alloc(data)
    effect_handle = alloc(struct.pack("<I", 1))  # DROPEFFECT_COPY, so pasting copies rather than moves
    if not files_handle:
        return False
    try:
        with _Open():
            u.EmptyClipboard()
            if not u.SetClipboardData(CF_HDROP, files_handle):
                k.GlobalFree(files_handle)
                if effect_handle:
                    k.GlobalFree(effect_handle)
                return False
            if effect_handle and drop_effect_fmt:
                if not u.SetClipboardData(drop_effect_fmt, effect_handle):
                    k.GlobalFree(effect_handle)
            return True
    except OSError:
        k.GlobalFree(files_handle)
        if effect_handle:
            k.GlobalFree(effect_handle)
        return False


def write_files(paths) -> bool:
    if sys.platform == "win32" and paths:
        try:
            return _windows_write_files(paths)
        except OSError:
            return False
    return False


def write_image(png: bytes) -> bool:
    if sys.platform == "win32" and png.startswith(PNG_SIGNATURE):
        try:
            return _windows_write_png(png)
        except OSError:
            return False
    return False


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
    """{'kind':'text','text':..}, {'kind':'files','paths':[..]},
    {'kind':'image','png':bytes} or None.
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
    """Copy whatever is highlighted in the focused window, so there is no copy
    step before sending.

    It presses Ctrl+Insert, not Ctrl+C. Both mean copy almost everywhere on
    Windows, but in a terminal Ctrl+C means "stop the program", so pressing it
    while toss's own terminal was focused made toss shut itself down.
    Ctrl+Insert copies in terminals too and never interrupts anything."""
    global injecting_until
    if sys.platform != "win32":
        return False
    try:
        _, u, _, _ = _win()
        VK_CONTROL, VK_INSERT, KEYUP, EXTENDED = 0x11, 0x2D, 0x0002, 0x0001
        scan = u.MapVirtualKeyW(VK_INSERT, 0)
        injecting_until = max(injecting_until, time.monotonic() + 0.8)
        before = sequence()
        if not ctrl_already_down:
            u.keybd_event(VK_CONTROL, u.MapVirtualKeyW(VK_CONTROL, 0), 0, 0)
        u.keybd_event(VK_INSERT, scan, EXTENDED, 0)
        u.keybd_event(VK_INSERT, scan, EXTENDED | KEYUP, 0)
        if not ctrl_already_down:
            u.keybd_event(VK_CONTROL, u.MapVirtualKeyW(VK_CONTROL, 0), KEYUP, 0)
        for _ in range(25):  # the focused app needs a beat to service the copy
            time.sleep(0.015)
            if sequence() != before:
                return True
        return False
    except Exception:
        return False
