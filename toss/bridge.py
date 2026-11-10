"""The iPhone bridge: your laptop clipboard behind one tap on the back of your phone.

A web page cannot touch the phone's clipboard unless it is open and you tap
it, and iOS never lets anything run in the background. iOS Shortcuts can,
though, and Back Tap runs a Shortcut when you double-tap the back of the
phone, even from the lock screen... well, from any app. So:

  GET  /clip   returns whatever is on the laptop clipboard (text, image, file)
  POST /clip   puts the body on the laptop clipboard

Two four-action Shortcuts call these. Every request needs the bridge token,
which only your devices know. It runs on your wifi only.
"""
from __future__ import annotations

import hmac
import io
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import clipboard, config


def token() -> str:
    t = config.load().get("bridge_token")
    if not t:
        t = secrets.token_urlsafe(18)
        config.update(bridge_token=t)
    return t


def _to_png(data: bytes) -> bytes | None:
    if data.startswith(clipboard.PNG_SIGNATURE):
        return data
    try:
        from PIL import Image
        out = io.BytesIO()
        Image.open(io.BytesIO(data)).save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


def start(port: int, inbox: Path, on_event=None) -> int:
    secret = token()

    class Handler(BaseHTTPRequestHandler):
        server_version = "toss"

        def log_message(self, *a):
            pass

        def _authed(self) -> bool:
            q = parse_qs(urlparse(self.path).query)
            given = (q.get("t") or [""])[0] or self.headers.get("X-Toss-Token", "")
            if urlparse(self.path).path != "/clip" or not hmac.compare_digest(given, secret):
                self.send_response(404)
                self.end_headers()
                return False
            return True

        def _send(self, body: bytes, kind: str, name: str | None = None):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            if name:
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._authed():
                return
            item = clipboard.read()
            if not item:
                return self._send(b"", "text/plain; charset=utf-8")
            if item["kind"] == "text":
                self._send(item["text"].encode("utf-8"), "text/plain; charset=utf-8")
            elif item["kind"] == "image":
                self._send(item["png"], "image/png")
            else:
                p = Path(item["paths"][0])
                kind = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                self._send(p.read_bytes(), kind, p.name)
            if on_event:
                on_event("iPhone pulled your clipboard")

        def do_POST(self):
            if not self._authed():
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200 * 1024 * 1024:
                self.send_response(413)
                self.end_headers()
                return
            body = self.rfile.read(n)
            kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            q = parse_qs(urlparse(self.path).query)
            given_name = Path((q.get("name") or [""])[0] or self.headers.get("X-Toss-Name", "")).name
            what = "something"
            # The share sheet sends a link or a note as a "file" with a generic
            # type, so judge by the bytes: short valid UTF-8 is text.
            vague = kind in ("", "application/octet-stream", "application/x-www-form-urlencoded")
            if kind.startswith("text/") or (vague and len(body) < 1_000_000 and _is_text(body)):
                clipboard.write(body.decode("utf-8", "replace"))
                what = "text"
            elif kind.startswith("image/") and (png := _to_png(body)):
                clipboard.write_image(png)
                what = "an image"
            else:
                ext = mimetypes.guess_extension(kind) or ".bin"
                inbox.mkdir(parents=True, exist_ok=True)
                dest = inbox / (given_name or time.strftime(f"iphone-%Y%m%d-%H%M%S{ext}"))
                n = 2
                while dest.exists():
                    dest = dest.with_name(f"{Path(given_name or dest.name).stem} ({n}){dest.suffix}")
                    n += 1
                dest.write_bytes(body)
                clipboard.write_files([dest])
                what = dest.name
            self._send(b"ok\n", "text/plain")
            if on_event:
                on_event(f"iPhone sent {what}, ready to paste")

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port


def _is_text(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
