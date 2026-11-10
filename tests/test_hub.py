import asyncio
import os

import pytest

from toss.client import Client
from toss.crypto import make_code

pytestmark = pytest.mark.asyncio


async def test_right_code_joins(hub):
    async with Client(hub.url, hub.code, name="phone") as c:
        assert c.fingerprint == hub.fingerprint


async def test_wrong_code_is_refused(hub):
    with pytest.raises(PermissionError):
        async with Client(hub.url, "wrong-code-four-words"):
            pass


async def test_code_typed_with_spaces_still_joins(hub):
    async with Client(hub.url, hub.code.replace("-", " ").upper()) as c:
        assert c.fingerprint == hub.fingerprint


async def test_brute_force_gets_cut_off(hub):
    refused = 0
    blocked = 0
    for _ in range(10):
        try:
            async with Client(hub.url, make_code()):
                pass
        except PermissionError:
            refused += 1
        except Exception:
            blocked += 1
    assert refused >= 1, "wrong codes are rejected"
    assert blocked >= 1, "repeated wrong codes stop being answered at all"


async def test_text_reaches_the_other_device(hub):
    async with Client(hub.url, hub.code, name="laptop") as a, \
               Client(hub.url, hub.code, name="phone") as b:
        received = asyncio.create_task(anext(b.listen()))
        await asyncio.sleep(0.1)
        await a.send_text("https://example.com/thing")
        item = await asyncio.wait_for(received, 5)
    assert item["kind"] == "text"
    assert item["data"] == "https://example.com/thing"
    assert item["from"] == "laptop"


async def test_sender_does_not_get_its_own_item_back(hub):
    async with Client(hub.url, hub.code, name="laptop") as a:
        await a.send_text("hello")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(a.listen()), 0.6)


async def test_a_big_file_arrives_byte_for_byte(hub):
    blob = os.urandom(700_000)
    async with Client(hub.url, hub.code, name="laptop") as a, \
               Client(hub.url, hub.code, name="phone") as b:
        received = asyncio.create_task(anext(b.listen()))
        await asyncio.sleep(0.1)
        chunks = await a.send_file(blob, name="holiday.zip", mime="application/zip")
        item = await asyncio.wait_for(received, 20)
    assert chunks > 1, "a file this size must be chunked"
    assert item["kind"] == "file"
    assert item["name"] == "holiday.zip"
    assert item["bytes"] == blob


async def test_three_devices_all_get_it(hub):
    async with Client(hub.url, hub.code, name="laptop") as a, \
               Client(hub.url, hub.code, name="phone") as b, \
               Client(hub.url, hub.code, name="tablet") as c:
        waits = [asyncio.create_task(anext(x.listen())) for x in (b, c)]
        await asyncio.sleep(0.1)
        await a.send_text("to everyone")
        got = await asyncio.wait_for(asyncio.gather(*waits), 5)
    assert [g["data"] for g in got] == ["to everyone", "to everyone"]


async def test_peers_are_announced(hub):
    async with Client(hub.url, hub.code, name="laptop") as a:
        assert a.peers == []
        async with Client(hub.url, hub.code, name="phone") as b:
            assert [p["name"] for p in b.peers] == ["laptop"]


async def test_hub_never_sees_plaintext(hub, monkeypatch):
    """The relay should move sealed envelopes it cannot read."""
    seen = []
    original = hub._broadcast

    async def spy(msg, skip=None):
        seen.append(msg)
        return await original(msg, skip=skip)

    monkeypatch.setattr(hub, "_broadcast", spy)
    async with Client(hub.url, hub.code, name="laptop") as a, \
               Client(hub.url, hub.code, name="phone") as b:
        received = asyncio.create_task(anext(b.listen()))
        await asyncio.sleep(0.1)
        await a.send_text("my bank password is hunter2")
        await asyncio.wait_for(received, 5)
    relayed = [m for m in seen if m.get("t") == "item"]
    assert relayed, "the item went through the relay"
    assert all("hunter2" not in str(m) for m in relayed)
    assert all(set(m) >= {"iv", "ct"} for m in relayed)


async def test_the_page_is_served(hub):
    import urllib.request
    def get(path):
        with urllib.request.urlopen(hub.url + path) as r:
            return r.status, r.read()
    status, body = await asyncio.to_thread(get, "/")
    assert status == 200
    assert b"<title>toss</title>" in body
    status, body = await asyncio.to_thread(get, "/app.js")
    assert status == 200 and b"deriveKey" in body


async def test_sends_from_the_laptop_itself_are_marked_local(hub):
    """`toss send` and the right-click menu run on the laptop. The laptop's own
    listener must see them as local, or it would paste them back onto its own
    clipboard and drop a second copy into Downloads."""
    async with Client(hub.url, hub.code, name="your laptop") as listener, \
               Client(hub.url, hub.code, name="your laptop", quiet=True) as sender:
        got = asyncio.create_task(anext(listener.listen()))
        await asyncio.sleep(0.1)
        await sender.send_text("hello")
        item = await asyncio.wait_for(got, 5)
    assert item["local"] is True


async def test_one_shot_senders_do_not_announce_themselves(hub):
    """A right-click send must not pop "your laptop joined" on the phone."""
    async with Client(hub.url, hub.code, name="phone") as phone:
        events = []

        async def watch():
            async for ev in phone.listen(peers=True):
                events.append(ev)

        task = asyncio.create_task(watch())
        async with Client(hub.url, hub.code, name="your laptop", quiet=True) as s:
            await s.send_text("x")
        await asyncio.sleep(0.3)
        task.cancel()
    assert [e for e in events if e["kind"] == "peer"] == []
    assert any(e.get("data") == "x" for e in events)


def test_qr_fragment_carries_the_same_key_the_hub_uses():
    """The phone skips PBKDF2 on the first scan by taking the key from the QR,
    so that key must be exactly the one the code derives."""
    import base64
    from urllib.parse import parse_qs
    from toss.crypto import derive_key
    from toss.hub import Hub

    h = Hub(code="amber-fox-tiger-reef")
    q = parse_qs(h.pair_fragment())
    k = q["k"][0]
    raw = base64.urlsafe_b64decode(k + "=" * (-len(k) % 4))
    assert raw == derive_key("amber-fox-tiger-reef", h.salt)
    assert q["code"][0] == "amber-fox-tiger-reef"
    assert q["h"][0].endswith(".local")
