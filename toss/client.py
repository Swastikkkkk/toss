"""Talking to a hub from Python: used by the hotkey daemon and `toss send`."""
from __future__ import annotations

import asyncio
import json
import os
import ssl

import websockets

from .crypto import b64, derive_key, fingerprint, open_sealed, proof, seal, unb64

CHUNK = 192 * 1024


class Client:
    def __init__(self, url: str, code: str, name: str = "laptop", insecure: bool = False,
                 quiet: bool = False):
        self.url = url.rstrip("/")
        self.code = code
        self.name = name
        # the hub's certificate is self-signed by design; the pairing code,
        # not the certificate, is what proves who we are talking to
        self.insecure = insecure
        self.ws = None
        self.key = None
        self.fingerprint = None
        self.on_item = None
        self.quiet = quiet

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def connect(self, timeout: float = 10.0):
        ws_url = self.url.replace("http://", "ws://").replace("https://", "wss://")
        kwargs = {"max_size": 16 * 1024 * 1024}
        if ws_url.startswith("wss://") and self.insecure:
            relaxed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            relaxed.check_hostname = False
            relaxed.verify_mode = ssl.CERT_NONE
            kwargs["ssl"] = relaxed
        self.ws = await asyncio.wait_for(websockets.connect(ws_url, **kwargs), timeout)
        hello = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
        if hello.get("t") != "hello":
            raise ConnectionError("that does not look like a toss hub")
        self.key = derive_key(self.code, unb64(hello["salt"]))
        self.fingerprint = fingerprint(self.key)
        await self.ws.send(json.dumps({
            "t": "auth", "name": self.name, "proof": proof(self.key, unb64(hello["challenge"])),
            **({"quiet": True} if self.quiet else {}),
        }))
        try:
            ready = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
        except websockets.ConnectionClosed as exc:
            if exc.rcvd and exc.rcvd.code == 4001:
                raise PermissionError("wrong pairing code") from None
            raise
        if ready.get("t") != "ready":
            raise ConnectionError("hub refused the join")
        self.peers = ready.get("peers", [])
        return self

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def send_text(self, text: str, sync: bool = False):
        # sync marks an automatic clipboard copy, so the phone can pin it
        body = {"kind": "text", "data": text, "sync": sync}
        await self.ws.send(json.dumps({"t": "item", **seal(self.key, body)}))

    async def send_file(self, path, name: str | None = None, mime: str = "application/octet-stream",
                        sync: bool = False):
        data = path if isinstance(path, (bytes, bytearray)) else open(path, "rb").read()
        name = name or (getattr(path, "name", None) or str(path).split("/")[-1].split("\\")[-1])
        ident = b64(os.urandom(9))
        total = max(1, -(-len(data) // CHUNK))
        for i in range(total):
            part = bytes(data[i * CHUNK:(i + 1) * CHUNK])
            await self.ws.send(json.dumps({
                "t": "chunk", "id": ident, "i": i, "n": total,
                "name": name, "mime": mime, "sync": sync, **seal(self.key, part),
            }))
        return total

    async def listen(self, peers=False):
        """Yield items other devices send. Files arrive reassembled."""
        pending: dict[str, dict] = {}
        async for raw in self.ws:
            msg = json.loads(raw)
            kind = msg.get("t")
            if kind in ("joined", "left") and peers:
                peer = msg.get("peer") or {}
                yield {"kind": "peer", "event": kind, "from": peer.get("name"), "local": bool(peer.get("local"))}
            elif kind == "item":
                payload = open_sealed(self.key, msg)
                payload["from"] = (msg.get("from") or {}).get("name")
                payload["local"] = bool((msg.get("from") or {}).get("local"))
                yield payload
            elif kind == "chunk":
                entry = pending.setdefault(msg["id"], {"parts": {}, "n": msg["n"]})
                entry["parts"][msg["i"]] = open_sealed(self.key, msg, raw=True)
                if len(entry["parts"]) == entry["n"]:
                    pending.pop(msg["id"])
                    body = b"".join(entry["parts"][i] for i in range(entry["n"]))
                    yield {
                        "kind": "file", "name": msg.get("name", "file"),
                        "mime": msg.get("mime"), "bytes": body,
                        "sync": bool(msg.get("sync")),
                        "from": (msg.get("from") or {}).get("name"),
                        "local": bool((msg.get("from") or {}).get("local")),
                    }
