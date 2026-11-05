import socket, urllib.request, urllib.error
from toss import bridge, clipboard


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_bridge_roundtrip(tmp_path, monkeypatch):
    box = {}
    monkeypatch.setattr(clipboard, "read", lambda: {"kind": "text", "text": "from laptop", "sensitive": False})
    monkeypatch.setattr(clipboard, "write", lambda t: box.setdefault("text", t) or True)
    monkeypatch.setattr(clipboard, "write_files", lambda p: box.setdefault("files", p) or True)
    port = _port()
    bridge.start(port, tmp_path)
    tok = bridge.token()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}/clip"
    try:
        op.open(base + "?t=wrong")
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404
    assert op.open(f"{base}?t={tok}").read() == b"from laptop"
    req = urllib.request.Request(f"{base}?t={tok}", data="from iphone".encode(), headers={"Content-Type": "text/plain"})
    op.open(req).read()
    assert box["text"] == "from iphone"
    req = urllib.request.Request(f"{base}?t={tok}", data=b"%PDF-1.4", headers={"Content-Type": "application/pdf"})
    op.open(req).read()
    assert box["files"][0].suffix == ".pdf"
