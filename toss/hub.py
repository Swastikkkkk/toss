"""The hub: a small server on your laptop that devices join with a code.

It serves one page and relays sealed envelopes between the devices that joined.
It does not decrypt anything, does not write anything to disk, and forgets
everything when you stop it.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from .crypto import KEY_BYTES, SALT_BYTES, b64, derive_key, fingerprint, make_code, verify_proof

# ships inside the package, so a pip install carries the receiver page with it
WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_MESSAGE = 8 * 1024 * 1024          # one chunk, with room to spare
AUTH_TIMEOUT = 20                       # seconds to answer the challenge
FAILED_LIMIT = 6                        # wrong codes allowed per address
FAILED_WINDOW = 60                      # ...per this many seconds


def lan_ip() -> str:
    """The address other devices on the wifi should use. No packets are sent;
    connecting a UDP socket just asks the OS which interface it would use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@dataclass
class Peer:
    id: str
    name: str
    ws: object
    joined: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name}


class Hub:
    def __init__(self, code: str | None = None, salt: bytes | None = None):
        self.code = code or make_code()
        self.salt = salt or os.urandom(SALT_BYTES)
        self.key = derive_key(self.code, self.salt)
        self.fingerprint = fingerprint(self.key)
        self.peers: dict[str, Peer] = {}
        self.failures: dict[str, list[float]] = {}
        self.on_item = None  # optional callback for local listeners

    # ---- auth bookkeeping -------------------------------------------------

    def _blocked(self, addr: str) -> bool:
        now = time.time()
        hits = [t for t in self.failures.get(addr, []) if now - t < FAILED_WINDOW]
        self.failures[addr] = hits
        return len(hits) >= FAILED_LIMIT

    def _record_failure(self, addr: str) -> None:
        self.failures.setdefault(addr, []).append(time.time())

    # ---- http -------------------------------------------------------------

    def _http(self, connection, request):
        """Serve the receiver page. websockets hands us non-WS requests here."""
        # A WebSocket upgrade on any path belongs to the handshake, not to us.
        if "websocket" in request.headers.get("Upgrade", "").lower():
            return None
        path = request.path.split("?")[0].split("#")[0]
        routes = {
            "/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
            "/manifest.json": "manifest.json",
            "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
            "/apple-touch-icon.png": "apple-touch-icon.png",
        }
        if path == "/health":
            return self._response(connection, HTTPStatus.OK, b"ok\n", "text/plain")
        name = routes.get(path)
        if not name:
            return self._response(connection, HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
        file = WEB_DIR / name
        if not file.exists():
            return self._response(connection, HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
        kind = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript",
            ".json": "application/manifest+json",
            ".png": "image/png",
        }.get(Path(name).suffix, "application/octet-stream")
        return self._response(connection, HTTPStatus.OK, file.read_bytes(), kind)

    @staticmethod
    def _response(connection, status, body: bytes, content_type: str):
        headers = Headers({
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            # icons never change within a run; everything else must stay fresh
            "Cache-Control": "public, max-age=86400" if content_type == "image/png" else "no-store",
            "Referrer-Policy": "no-referrer",
            # the page only ever talks to the hub it came from
            "Content-Security-Policy":
                "default-src 'self'; connect-src 'self' ws: wss:; "
                "img-src 'self' data:; style-src 'self' 'unsafe-inline'",
        })
        return Response(status.value, status.phrase, headers, body)

    # ---- websocket --------------------------------------------------------

    async def handler(self, ws):
        addr = getattr(ws, "remote_address", ("?",))[0]
        if self._blocked(addr):
            await ws.close(4029, "too many attempts")
            return

        challenge = os.urandom(32)
        await ws.send(json.dumps({
            "t": "hello",
            "salt": b64(self.salt),
            "challenge": b64(challenge),
            "iterations": 200_000,
        }))

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=AUTH_TIMEOUT)
            msg = json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed):
            await ws.close(4000, "no auth")
            return

        if msg.get("t") != "auth" or not verify_proof(self.key, challenge, msg.get("proof", "")):
            self._record_failure(addr)
            await ws.close(4001, "wrong code")
            return

        peer = Peer(id=b64(os.urandom(6)), name=str(msg.get("name") or "a device")[:40], ws=ws)
        self.peers[peer.id] = peer
        await ws.send(json.dumps({
            "t": "ready",
            "you": peer.public(),
            "fingerprint": self.fingerprint,
            "peers": [p.public() for p in self.peers.values() if p.id != peer.id],
        }))
        await self._broadcast({"t": "joined", "peer": peer.public()}, skip=peer.id)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("t")
                if kind in ("item", "chunk"):
                    # sealed by the sender, relayed untouched
                    msg["from"] = peer.public()
                    await self._broadcast(msg, skip=peer.id)
                    if self.on_item and kind == "item":
                        self.on_item(msg)
                elif kind == "ping":
                    await ws.send(json.dumps({"t": "pong"}))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.peers.pop(peer.id, None)
            await self._broadcast({"t": "left", "peer": peer.public()})

    async def _broadcast(self, msg: dict, skip: str | None = None) -> None:
        raw = json.dumps(msg)
        dead = []
        for pid, p in list(self.peers.items()):
            if pid == skip:
                continue
            try:
                await p.ws.send(raw)
            except Exception:
                dead.append(pid)
        for pid in dead:
            self.peers.pop(pid, None)

    # ---- lifecycle --------------------------------------------------------

    async def run(self, host: str = "0.0.0.0", port: int = 7777):
        async with serve(
            self.handler, host, port,
            process_request=self._http,
            max_size=MAX_MESSAGE,
            ping_interval=20,
        ) as server:
            self.port = port
            await server.serve_forever()
