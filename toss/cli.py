"""toss: hold a key, it lands on your other device."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import signal
import sys
import time
from pathlib import Path

from . import autostart, clipboard, config, tls
from .client import Client
from .crypto import make_code, normalize_code
from .hub import Hub, lan_ip

DEFAULT_PORT = 7777


def qr(text: str) -> str | None:
    """A QR in the terminal, if the optional dependency is installed."""
    try:
        import qrcode
    except ImportError:
        return None
    code = qrcode.QRCode(border=1)
    code.add_data(text)
    code.make(fit=True)
    matrix = code.get_matrix()
    # two rows per line using half blocks, so it stays square in a terminal
    out = []
    for y in range(0, len(matrix), 2):
        line = ""
        for x in range(len(matrix[y])):
            top = matrix[y][x]
            bottom = matrix[y + 1][x] if y + 1 < len(matrix) else False
            line += "█" if top and bottom else "▀" if top else "▄" if bottom else " "
        out.append(line)
    return "\n".join(out)


def debug(message: str) -> None:
    if os.environ.get("TOSS_DEBUG"):
        print(f"  debug: {message}", flush=True)


SYNC_FILE_LIMIT = 50 * 1024 * 1024  # bigger than this needs a deliberate hotkey press


def guess_mime(path: Path) -> str:
    import mimetypes
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def image_name() -> str:
    return time.strftime("clipboard-%Y%m%d-%H%M%S.png")


def describe_image(png: bytes) -> str:
    size = clipboard.png_size(png)
    dims = f"{size[0]}x{size[1]} " if size else ""
    return f"an image ({dims}{human(len(png))})"


def preview(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else "text"
    return line if len(line) <= 60 else line[:57] + "..."


def human(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n/1024:.0f} KB" if n < 1048576 else f"{n/1048576:.1f} MB"


async def cmd_serve(args):
    saved = config.load()
    if args.code:
        code, salt = normalize_code(args.code), None
        config.update(code=code)
    else:
        code, salt = config.pairing(new=args.new_code)
    config.update(key=args.key, hold=args.hold, port=args.port,
                  inbox=args.inbox, name=args.name, grab=not args.no_grab, sync=not args.no_sync)

    hub = Hub(code=code, salt=salt)
    ip = args.ip or lan_ip()

    # Plain http by default: the phone page carries its own audited crypto,
    # so there is no certificate warning to tap through. --https is opt-in.
    ssl_context = None
    scheme = "http"
    if args.https:
        try:
            ssl_context = tls.context(config.CONFIG_DIR, ip)
            scheme = "https"
        except Exception as exc:
            print(f"  could not set up TLS ({exc}); using http")
    config.update(scheme=scheme)

    url = f"{scheme}://{ip}:{args.port}"
    link = f"{url}/#code={hub.code}"

    returning = bool(saved.get("code")) and not args.new_code and not args.code

    print()
    if not args.no_qr and not args.quiet:
        art = qr(link)
        if art:
            print(art)
            print()
        else:
            print("  (pip install qrcode for a scannable QR here)")
            print()
    print("  Scan with your phone's camera. It connects by itself.")
    if returning:
        print("  Phones you paired before reconnect on their own.")
    print(f"  No camera? Open {url} and type  {hub.code}")
    print()

    pidfile = config.CONFIG_DIR / "toss.pid"
    try:
        pidfile.write_text(str(os.getpid()))
    except OSError:
        pass
    server = asyncio.create_task(hub.run(host=args.host, port=args.port, ssl_context=ssl_context))

    # Belt and braces for the copy shortcut: if an interrupt arrives in the
    # instant we are pressing keys for you, it is ours, not yours, so ignore it.
    # A real Ctrl+C at any other time still stops toss as normal.
    previous = signal.getsignal(signal.SIGINT)

    def guarded(signum, frame):
        now = time.monotonic()
        debug(f"interrupt at {now:.3f}, copy window open until {clipboard.injecting_until:.3f}")
        if now < clipboard.injecting_until:
            print("  ignored a Ctrl+C that arrived while the hotkey was held")
            return
        if callable(previous):
            previous(signum, frame)
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, guarded)

    from . import hotkey
    want_hotkey = not args.no_hotkey and hotkey.available()
    want_sync = not args.no_sync
    sender = None

    if want_hotkey or want_sync:
        await asyncio.sleep(0.4)
        sender = Client(f"{scheme}://127.0.0.1:{args.port}", hub.code, name=args.name,
                        insecure=scheme == "https")
        await sender.connect()

    # What we last pushed or received, so a phone's text landing on this
    # clipboard is not immediately bounced back to the phone.
    echo = {"seq": None, "text": None, "image": None, "files": None}

    if want_hotkey:
        what = "whatever is selected" if not args.no_grab else "your clipboard"
        print(f"  Hold {args.key} for {args.hold:.1f}s to send {what}.")
        holding_ctrl = args.key.endswith("ctrl")

        async def trigger():
            debug(f"hotkey fired at {time.monotonic():.3f}")
            if not args.no_grab:
                await asyncio.to_thread(clipboard.grab_selection, holding_ctrl)
            item = await asyncio.to_thread(clipboard.read)
            if not item:
                print("  nothing selected and the clipboard is empty")
                return
            if item["kind"] == "files":
                for path in item["paths"]:
                    p = Path(path)
                    if not p.is_file():
                        continue
                    await sender.send_file(p, mime=guess_mime(p))
                    print(f"  sent {p.name} ({human(p.stat().st_size)})")
            elif item["kind"] == "image":
                png = item["png"]
                echo["image"] = hashlib.sha256(png).digest()
                echo["seq"] = clipboard.sequence()
                await sender.send_file(png, name=image_name(), mime="image/png")
                print(f"  sent {describe_image(png)}")
            else:
                text = item["text"]
                echo["text"] = text
                echo["seq"] = clipboard.sequence()
                await sender.send_text(text)
                print(f"  sent {preview(text)}")

        asyncio.create_task(hotkey.watch(args.key, args.hold, trigger))
    elif not args.no_hotkey:
        print("  Hold-to-send needs Windows. Clipboard sync still works.")

    if want_sync:
        print("  Clipboard sync is on: anything you copy here appears on your phone.")
        print("  Copies from password managers are skipped. --no-sync turns it off.")

        async def watch_clipboard():
            last_seq = clipboard.sequence()
            last_text = None
            if last_seq is None:  # not Windows: compare contents instead
                first = await asyncio.to_thread(clipboard.read)
                last_text = first["text"] if first and first["kind"] == "text" else None
            while True:
                await asyncio.sleep(0.35)
                seq = clipboard.sequence()
                if seq is not None:
                    if seq == last_seq:
                        continue
                    last_seq = seq
                    if seq == echo["seq"]:
                        continue  # our own write, from the phone
                item = await asyncio.to_thread(clipboard.read)
                if not item:
                    continue
                if item["kind"] == "image":
                    if item.get("sensitive"):
                        print("  skipped an image marked private by another app")
                        continue
                    png = item["png"]
                    digest = hashlib.sha256(png).digest()
                    if digest == echo["image"]:
                        continue
                    if len(png) > 25 * 1024 * 1024:
                        print("  skipped a very large image; use the hotkey to send it")
                        continue
                    echo["image"] = digest
                    try:
                        await sender.send_file(png, name=image_name(), mime="image/png", sync=True)
                        print(f"  synced {describe_image(png)}")
                    except Exception as exc:
                        print(f"  sync failed: {exc}")
                    continue
                if item["kind"] == "files":
                    files = [Path(x) for x in item["paths"] if Path(x).is_file()]
                    if not files:
                        continue  # a folder, or something that is no longer there
                    signature = tuple((str(f), f.stat().st_size, f.stat().st_mtime_ns) for f in files)
                    if signature == echo["files"]:
                        continue
                    echo["files"] = signature
                    for f in files:
                        size = f.stat().st_size
                        if size > SYNC_FILE_LIMIT:
                            print(f"  skipped {f.name} ({human(size)}); over {human(SYNC_FILE_LIMIT)}, "
                                  "hold the hotkey to send it anyway")
                            continue
                        try:
                            await sender.send_file(f, mime=guess_mime(f), sync=True)
                            print(f"  synced {f.name} ({human(size)})")
                        except Exception as exc:
                            print(f"  sync failed for {f.name}: {exc}")
                    continue
                if item["kind"] != "text":
                    continue
                text = item["text"]
                if seq is None:
                    if text == last_text:
                        continue
                    last_text = text
                if item.get("sensitive"):
                    print("  skipped a copy marked private by a password manager")
                    continue
                if text == echo["text"] or not text.strip():
                    continue
                if len(text) > 200_000:
                    print("  skipped a very large copy; use the hotkey to send it as a file")
                    continue
                echo["text"] = text
                try:
                    await sender.send_text(text, sync=True)
                    print(f"  synced {preview(text)}")
                except Exception as exc:
                    print(f"  sync failed: {exc}")

        asyncio.create_task(watch_clipboard())

    if sender:
        async def receive():
            async for item in sender.listen(peers=True):
                who = item.get("from") or "a device"
                if item["kind"] == "peer":
                    print(f"  {who} {'connected' if item['event'] == 'joined' else 'disconnected'}", flush=True)
                    continue
                if item["kind"] == "text":
                    text = item["data"]
                    echo["text"] = text
                    ok = await asyncio.to_thread(clipboard.write, text)
                    echo["seq"] = clipboard.sequence()
                    tail = " (on your clipboard, ready to paste)" if ok else ""
                    print(f"  {who} sent {preview(text)}{tail}")
                else:
                    target = Path(args.inbox).expanduser()
                    target.mkdir(parents=True, exist_ok=True)
                    dest = unique(target / item["name"])
                    dest.write_bytes(item["bytes"])
                    # A copied image becomes a pasteable image; anything else is
                    # put on the clipboard as a file, so Ctrl+V pastes it
                    # straight into a folder, an email or a chat.
                    if item.get("sync") and item["bytes"].startswith(clipboard.PNG_SIGNATURE):
                        echo["image"] = hashlib.sha256(item["bytes"]).digest()
                        ok = await asyncio.to_thread(clipboard.write_image, item["bytes"])
                        what = describe_image(item["bytes"])
                    else:
                        st = dest.stat()
                        echo["files"] = ((str(dest), st.st_size, st.st_mtime_ns),)
                        ok = await asyncio.to_thread(clipboard.write_files, [dest])
                        what = f"{dest.name} ({human(st.st_size)})"
                    echo["seq"] = clipboard.sequence()
                    tail = ", on your clipboard to paste" if ok else ""
                    print(f"  {who} sent {what}{tail}. Saved to {dest}")

        asyncio.create_task(receive())
    print()

    try:
        await server
    except asyncio.CancelledError:
        pass


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while (path.parent / f"{stem} ({n}){suffix}").exists():
        n += 1
    return path.parent / f"{stem} ({n}){suffix}"


async def cmd_send(args):
    if not args.code:
        print("toss: no pairing code yet. Run `toss serve` once first.", file=sys.stderr)
        return 1
    url = args.url or f"{config.load().get('scheme', 'http')}://127.0.0.1:{args.port}"
    code = normalize_code(args.code)
    async with Client(url, code, name=args.name, insecure=url.startswith("https")) as c:
        if args.what:
            target = Path(args.what)
            if target.is_file():
                n = await c.send_file(target)
                print(f"sent {target.name} in {n} chunk{'s' if n != 1 else ''}")
            else:
                await c.send_text(args.what)
                print("sent")
        else:
            item = clipboard.read()
            if not item:
                print("nothing on the clipboard", file=sys.stderr)
                return 1
            if item["kind"] == "files":
                for p in item["paths"]:
                    await c.send_file(Path(p))
                    print(f"sent {Path(p).name}")
            else:
                await c.send_text(item["text"])
                print("sent clipboard text")
        await asyncio.sleep(0.3)
    return 0


def _running(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _spawn_background(port: int) -> None:
    """Start the hub with no window, detached from this terminal."""
    import subprocess
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(config.CONFIG_DIR / "toss.log", "ab")
    exe = Path(sys.executable)
    kw = {}
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        exe = w if w.exists() else exe
        kw["creationflags"] = 0x00000008 | 0x08000000 | 0x00000200  # detached, no window, new group
    else:
        kw["start_new_session"] = True
    subprocess.Popen([str(exe), "-m", "toss", "serve", "--port", str(port), "--quiet"],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **kw)


async def cmd_start(args):
    """Bare `toss`: make sure it runs in the background, show the QR, get out of the way."""
    saved = config.load()
    port = args.port or saved.get("port", DEFAULT_PORT)
    code, _ = config.pairing()
    if not _running(port):
        _spawn_background(port)
        for _ in range(50):
            if _running(port):
                break
            time.sleep(0.1)
        else:
            print(f"  toss did not start. Details: {config.CONFIG_DIR / 'toss.log'}")
            return 1
    if not autostart.installed():
        try:
            autostart.install(port)
        except Exception:
            pass
    scheme = config.load().get("scheme", "http")
    url = f"{scheme}://{lan_ip()}:{port}"
    print()
    art = qr(f"{url}/#code={code}")
    if art:
        print(art)
        print()
    print("  Scan with your phone's camera. It connects by itself, once, for good.")
    print(f"  No camera? Open {url} and type  {code}")
    print()
    print("  toss is running in the background and starts when you log in.")
    print("  You can close this window. Copy anything and it is on your phone.")
    print("  toss stop  turns it off.")
    print()
    return 0


async def cmd_stop(args):
    pidfile = config.CONFIG_DIR / "toss.pid"
    try:
        pid = int(pidfile.read_text())
    except (OSError, ValueError):
        print("  toss is not running\n")
        return 0
    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        print("  stopped. It still starts on login; toss uninstall stops that.\n")
    except OSError:
        print("  toss is not running\n")
    pidfile.unlink(missing_ok=True)
    return 0


async def cmd_install(args):
    saved = config.load()
    path = autostart.install(args.port or saved.get("port", DEFAULT_PORT))
    code, _ = config.pairing()
    print(f"\n  toss will start when you log in.")
    print(f"  {path}")
    print(f"\n  code  {code}")
    print("  Undo any time with: toss uninstall\n")
    return 0


async def cmd_uninstall(args):
    print("  removed from startup\n" if autostart.uninstall() else "  it was not set to start on login\n")
    return 0


async def cmd_status(args):
    data = config.load()
    code, salt = config.pairing()
    from .crypto import derive_key, fingerprint
    print()
    print(f"  code         {code}")
    print(f"  address      {data.get('scheme', 'http')}://{lan_ip()}:{data.get('port', DEFAULT_PORT)}")
    print(f"  hold         {data.get('key')} for {data.get('hold')}s")
    print(f"  grab         {'copies your selection first' if data.get('grab') else 'clipboard only'}")
    print(f"  sync         {'on, every copy goes to your phone' if data.get('sync', True) else 'off'}")
    print(f"  inbox        {data.get('inbox')}")
    print(f"  on login     {'yes' if autostart.installed() else 'no'}")
    print(f"  settings     {config.CONFIG_FILE}")
    print()
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="toss", description="Hold a key, it lands on your other device.")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the hub on this laptop")
    saved = config.load()
    s.add_argument("--port", type=int, default=saved.get("port", DEFAULT_PORT))
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--ip", help="address to print, if autodetection picks the wrong interface")
    s.add_argument("--code", help="set a specific code instead of the remembered one")
    s.add_argument("--new-code", action="store_true", help="rotate the pairing code")
    s.add_argument("--key", default=saved.get("key", "right-ctrl"), help="key to hold")
    s.add_argument("--hold", type=float, default=saved.get("hold", 0.4), help="seconds to hold")
    s.add_argument("--name", default=saved.get("name", "your laptop"))
    s.add_argument("--inbox", default=saved.get("inbox", "~/Downloads"),
                   help="where received files are saved")
    s.add_argument("--no-hotkey", action="store_true", help="hub only, no keyboard watching")
    s.add_argument("--no-sync", action="store_true",
                   help="do not push every copy on this machine to your other devices")
    s.add_argument("--no-grab", action="store_true",
                   help="send the clipboard as it is, instead of copying the selection first")
    s.add_argument("--no-qr", action="store_true")
    s.add_argument("--https", action="store_true",
                   help="serve https with a self-signed certificate (phones see a warning once)")
    s.add_argument("--quiet", action="store_true", help="skip the QR, for running on login")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("send", help="send to a running hub")
    s.add_argument("what", nargs="?", help="a file path or some text; omit to send the clipboard")
    s.add_argument("--code", default=config.load().get("code"),
                   help="defaults to the remembered pairing code")
    s.add_argument("--url", help="hub url, default localhost")
    s.add_argument("--port", type=int, default=config.load().get("port", DEFAULT_PORT))
    s.add_argument("--name", default="this laptop")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("install", help="start toss automatically when you log in")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("uninstall", help="stop starting on login")
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("start", help="run in the background and show the QR (what bare `toss` does)")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("stop", help="stop the background toss")
    s.set_defaults(fn=cmd_stop)

    s = sub.add_parser("status", help="show the code, address and settings")
    s.set_defaults(fn=cmd_status)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv.insert(0, "start")   # bare `toss`: background + QR
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.fn(args)) or 0
    except KeyboardInterrupt:
        print("\n  stopped. nothing was kept.")
        return 0
    except PermissionError as exc:
        print(f"toss: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as exc:
        print(f"toss: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
