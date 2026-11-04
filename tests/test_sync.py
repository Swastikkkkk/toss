"""Clipboard sync: the protocol, and a guard against the bug that broke it.

The Windows clipboard itself cannot run here, so it was tested on a real
Windows 11 machine. What these tests pin down is the part that can regress
silently anywhere."""
import ast
import asyncio
import pathlib

import pytest

from toss.client import Client

SRC = pathlib.Path(__file__).resolve().parents[1] / "toss" / "clipboard.py"


async def test_a_synced_copy_arrives_marked_as_sync(hub):
    async with Client(hub.url, hub.code, name="laptop") as laptop, \
               Client(hub.url, hub.code, name="phone") as phone:
        got = asyncio.create_task(anext(phone.listen()))
        await asyncio.sleep(0.1)
        await laptop.send_text("copied on windows", sync=True)
        item = await asyncio.wait_for(got, 5)
    assert item["data"] == "copied on windows"
    assert item["sync"] is True, "the phone pins synced copies, so the flag must survive"


async def test_an_ordinary_send_is_not_marked_as_sync(hub):
    async with Client(hub.url, hub.code, name="laptop") as laptop, \
               Client(hub.url, hub.code, name="phone") as phone:
        got = asyncio.create_task(anext(phone.listen()))
        await asyncio.sleep(0.1)
        await laptop.send_text("held the hotkey")
        item = await asyncio.wait_for(got, 5)
    assert item["sync"] is False


async def test_sync_text_is_still_encrypted_on_the_wire(hub, monkeypatch):
    seen = []
    original = hub._broadcast

    async def spy(msg, skip=None):
        seen.append(msg)
        return await original(msg, skip=skip)

    monkeypatch.setattr(hub, "_broadcast", spy)
    async with Client(hub.url, hub.code, name="laptop") as laptop, \
               Client(hub.url, hub.code, name="phone") as phone:
        got = asyncio.create_task(anext(phone.listen()))
        await asyncio.sleep(0.1)
        await laptop.send_text("my otp is 482913", sync=True)
        await asyncio.wait_for(got, 5)
    relayed = [m for m in seen if m.get("t") == "item"]
    assert relayed and all("482913" not in str(m) for m in relayed)


def _declared_restypes():
    """Every `sig(<lib>.<Fn>, [...], <restype>)` call in clipboard.py."""
    tree = ast.parse(SRC.read_text())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "sig" and len(node.args) == 3:
            target = node.args[0]
            if isinstance(target, ast.Attribute):
                out[target.attr] = ast.unparse(node.args[2])
    return out


def test_pointer_returning_calls_declare_a_pointer_restype():
    """The bug that made the clipboard read as empty, or crash outright: on
    64-bit Windows, ctypes assumes an int return and truncates pointers. These
    functions return handles or pointers, so each must declare it."""
    declared = _declared_restypes()
    must_be_pointers = {"GetClipboardData", "SetClipboardData", "GlobalLock", "GlobalAlloc", "GlobalFree"}
    missing = must_be_pointers - declared.keys()
    assert not missing, f"no signature declared for {missing}"
    for name in must_be_pointers:
        assert declared[name] in {"w.HANDLE", "w.HGLOBAL", "w.LPVOID"}, \
            f"{name} returns a pointer but is declared {declared[name]}"


def test_password_manager_formats_are_honoured():
    src = SRC.read_text()
    for fmt in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                "CanUploadToCloudClipboard"):
        assert fmt in src, f"copies flagged {fmt} must not be synced"


async def test_a_synced_image_arrives_marked_and_intact(hub):
    from toss.clipboard import png_encode
    png = png_encode(64, 48, bytes([200, 30, 30, 255]) * 64 * 48, 4)
    async with Client(hub.url, hub.code, name="laptop") as laptop, \
               Client(hub.url, hub.code, name="phone") as phone:
        got = asyncio.create_task(anext(phone.listen()))
        await asyncio.sleep(0.1)
        await laptop.send_file(png, name="clipboard.png", mime="image/png", sync=True)
        item = await asyncio.wait_for(got, 10)
    assert item["kind"] == "file" and item["mime"] == "image/png"
    assert item["sync"] is True, "the phone pins synced images, so the flag must survive chunking"
    assert item["bytes"] == png


async def test_a_large_synced_image_survives_many_chunks(hub):
    import os
    blob = b"\x89PNG\r\n\x1a\n" + os.urandom(900_000)
    async with Client(hub.url, hub.code, name="laptop") as laptop, \
               Client(hub.url, hub.code, name="phone") as phone:
        got = asyncio.create_task(anext(phone.listen()))
        await asyncio.sleep(0.1)
        chunks = await laptop.send_file(blob, name="big.png", mime="image/png", sync=True)
        item = await asyncio.wait_for(got, 20)
    assert chunks > 3
    assert item["bytes"] == blob and item["sync"] is True
