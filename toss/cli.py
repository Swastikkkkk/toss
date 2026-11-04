"""toss: hold a key, it lands on your other device."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
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
                  inbox=args.inbox, name=args.name, grab=not args.no_grab)

    hub = Hub(code=code, salt=salt)
    ip = args.ip or lan_ip()

    # Browsers only hand out WebCrypto on a secure origin, and a LAN address
    # over plain http is not one, so without TLS a phone cannot join at all.
    ssl_context = None
    scheme = "http"
    if not args.no_https:
        try:
            ssl_context = tls.context(config.CONFIG_DIR, ip)
            scheme = "https"
        except Exception as exc:
            print(f"  could not set up TLS ({exc}); falling back to http")
            print("  phones will not be able to join over http")

    url = f"{scheme}://{ip}:{args.port}"
    link = f"{url}/#code={hub.code}"

    returning = bool(saved.get("code")) and not args.new_code and not args.code

    print()
    print(f"  toss is up on {url}")
    print()
    print(f"  code         {hub.code}")
    print(f"  session      {hub.fingerprint}")
    print()
    if returning:
        print("  Devices you already paired rejoin on their own.")
        print("  New code whenever you want it: toss serve --new-code")
        print()
    if not args.no_qr and not args.quiet:
        art = qr(link)
        if art:
            print(art)
            print()
        else:
            print("  (pip install qrcode for a scannable QR here)")
            print()
    print(f"  Open {url} on your other device and enter the code,")
    print("  or scan the QR which carries the code with it.")
    if scheme == "https":
        print()
        print("  First visit on each device shows a certificate warning. That is")
        print("  expected: the certificate is self-signed and never leaves your")
        print("  machine. Tap through it once and the device remembers.")
        print(f"  certificate  {tls.fingerprint(config.CONFIG_DIR, ip)}")
    if not autostart.installed():
        print("  Tired of starting this? toss install")
    print()

    server = asyncio.create_task(hub.run(host=args.host, port=args.port, ssl_context=ssl_context))

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
    echo = {"seq": None, "text": None}

    if want_hotkey:
        what = "whatever is selected" if not args.no_grab else "your clipboard"
        print(f"  Hold {args.key} for {args.hold:.1f}s to send {what}.")
        holding_ctrl = args.key.endswith("ctrl")

        async def trigger():
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
                    await sender.send_file(p)
                    print(f"  sent {p.name} ({human(p.stat().st_size)})")
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
                if not item or item["kind"] != "text":
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
            async for item in sender.listen():
                who = item.get("from") or "a device"
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
                    print(f"  {who} sent a file, saved to {dest}")

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
    url = args.url or f"https://127.0.0.1:{args.port}"
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
    print(f"  session      {fingerprint(derive_key(code, salt))}")
    print(f"  address      http://{lan_ip()}:{data.get('port', DEFAULT_PORT)}")
    print(f"  hold         {data.get('key')} for {data.get('hold')}s")
    print(f"  grab         {'copies your selection first' if data.get('grab') else 'clipboard only'}")
    print(f"  inbox        {data.get('inbox')}")
    print(f"  on login     {'yes' if autostart.installed() else 'no'}")
    print(f"  settings     {config.CONFIG_FILE}")
    print()
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="toss", description="Hold a key, it lands on your other device.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the hub on this laptop")
    saved = config.load()
    s.add_argument("--port", type=int, default=saved.get("port", DEFAULT_PORT))
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--ip", help="address to print, if autodetection picks the wrong interface")
    s.add_argument("--code", help="set a specific code instead of the remembered one")
    s.add_argument("--new-code", action="store_true", help="rotate the pairing code")
    s.add_argument("--key", default=saved.get("key", "right-ctrl"), help="key to hold")
    s.add_argument("--hold", type=float, default=saved.get("hold", 0.4), help="seconds to hold")
    s.add_argument("--name", default=saved.get("name", "this laptop"))
    s.add_argument("--inbox", default=saved.get("inbox", "~/Downloads"),
                   help="where received files are saved")
    s.add_argument("--no-hotkey", action="store_true", help="hub only, no keyboard watching")
    s.add_argument("--no-sync", action="store_true",
                   help="do not push every copy on this machine to your other devices")
    s.add_argument("--no-grab", action="store_true",
                   help="send the clipboard as it is, instead of copying the selection first")
    s.add_argument("--no-qr", action="store_true")
    s.add_argument("--no-https", action="store_true",
                   help="serve plain http; phones cannot join, for local testing only")
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

    s = sub.add_parser("status", help="show the code, address and settings")
    s.set_defaults(fn=cmd_status)
    return p


def main(argv=None):
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
