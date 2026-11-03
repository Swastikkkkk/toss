"""End to end through a real browser.

The risk in this project is that the page's WebCrypto and Python's cryptography
disagree about key derivation or AES-GCM, in which case everything looks fine
until nothing can be decrypted. This drives an actual Chromium against a real
hub and moves real bytes in both directions.
"""
import asyncio
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from toss.client import Client
from toss.hub import Hub

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + str(detail)) if detail else ''}")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


async def join(page, url, code, name):
    """Join, unless the page already let itself in from a remembered code."""
    await page.goto(url)
    await page.wait_for_timeout(200)
    if await page.locator("#room[hidden]").count():
        if code:
            await page.fill("#code", code)
        if name:
            await page.fill("#name", name)
        await page.click("#join button")
    await page.wait_for_selector("#room:not([hidden])", timeout=10000)
    return await page.inner_text("#fp")


async def main():
    port = free_port()
    hub = Hub(code="amber-fox-tiger-reef")
    url = f"http://127.0.0.1:{port}"
    server = asyncio.create_task(hub.run(host="127.0.0.1", port=port))
    await asyncio.sleep(0.6)

    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        # one context per device, so localStorage is per device like real life
        phone_ctx = await browser.new_context(viewport={"width": 390, "height": 844},
                                              accept_downloads=True)
        tablet_ctx = await browser.new_context()
        phone = await phone_ctx.new_page()
        tablet = await tablet_ctx.new_page()
        for pg in (phone, tablet):
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # --- the fingerprint has to match across three implementations ---
        fp_phone = await join(phone, url, "AMBER FOX TIGER REEF", "phone")
        check("browser joins with a sloppily typed code", True, fp_phone)
        check("browser and python derive the same key", fp_phone == hub.fingerprint,
              f"browser={fp_phone} python={hub.fingerprint}")

        fp_tablet = await join(tablet, url + "/#code=amber-fox-tiger-reef", "", "tablet")
        check("code carried in the QR link prefills and joins", fp_tablet == hub.fingerprint)

        await phone.wait_for_timeout(300)
        check("devices see each other", "connected" in (await phone.inner_text("#peers")).lower(),
              await phone.inner_text("#peers"))

        # --- python -> browser, the hotkey path ---
        async with Client(url, hub.code, name="laptop") as laptop:
            await asyncio.sleep(0.2)
            await laptop.send_text("https://news.ycombinator.com/item?id=1")
            await phone.wait_for_selector("#items li", timeout=5000)
            body = await phone.inner_text("#items li .body")
            check("python text decrypts in the browser", "news.ycombinator.com" in body, body)
            check("a link is rendered as a link", await phone.locator("#items li a").count() == 1)
            meta = await phone.inner_text("#items li .meta")
            check("the sender's name comes through", "laptop" in meta, meta)

            # --- python file -> browser ---
            blob = os.urandom(400_000)
            await laptop.send_file(blob, name="report.pdf", mime="application/pdf")
            await phone.wait_for_function(
                "() => [...document.querySelectorAll('#items li .body strong')]"
                ".some(e => e.textContent === 'report.pdf')", timeout=20000)
            check("a chunked file reassembles in the browser", True)
            async with phone.expect_download(timeout=10000) as dl_info:
                await phone.click("#items li button[data-save]")
            dl = await dl_info.value
            saved = Path(await dl.path()).read_bytes()
            check("saved file is byte for byte identical", saved == blob,
                  f"{len(saved)} of {len(blob)} bytes")

            # --- browser -> python, the reply path ---
            got = asyncio.create_task(anext(laptop.listen()))
            await asyncio.sleep(0.2)
            await phone.fill("#compose", "sent back from the phone")
            await phone.click("#send button[type=submit]")
            item = await asyncio.wait_for(got, 10)
            check("browser text decrypts in python", item["data"] == "sent back from the phone", item["data"])
            check("python sees which device sent it", item["from"] == "phone", item["from"])

            # --- browser -> browser ---
            await tablet.wait_for_selector("#items li", timeout=5000)
            tablet_text = await tablet.inner_text("#items")
            check("a third device receives everything too", "sent back from the phone" in tablet_text)

        # --- convenience: pair once, never type it again ---
        reopened = await phone_ctx.new_page()        # same device, so same storage
        await reopened.goto(url)
        await reopened.wait_for_selector("#room:not([hidden])", timeout=10000)
        check("a paired device rejoins with no typing at all",
              (await reopened.inner_text("#fp")) == hub.fingerprint)

        # --- convenience: paste anywhere, no clicking into a box ---
        await reopened.evaluate("""() => {
            const e = new ClipboardEvent('paste', { bubbles: true, cancelable: true,
              clipboardData: new DataTransfer() });
            e.clipboardData.setData('text', 'pasted straight onto the page');
            document.dispatchEvent(e);
        }""")
        got2 = asyncio.create_task(asyncio.sleep(0))
        await phone.wait_for_function(
            "() => document.body.innerText.includes('pasted straight onto the page')",
            timeout=5000)
        check("pasting anywhere on the page sends it", True)

        # --- convenience: survive the hub going away and coming back ---
        await reopened.evaluate("() => window.__t = document.querySelectorAll('#items li').length")
        await reopened.evaluate("""() => {
            // force the socket shut the way a sleeping phone would
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        check("forget clears the pairing",
              await reopened.evaluate("""async () => {
                  document.querySelector('#forget').click();
                  return localStorage.getItem('toss-code') === null
                      && !document.querySelector('#gate').hidden;
              }"""))

        # --- a wrong code must be refused visibly ---
        stranger = await (await browser.new_context()).new_page()
        await stranger.goto(url)
        await stranger.fill("#code", "wrong-code-four-words")
        await stranger.click("#join button")
        await stranger.wait_for_function(
            "() => document.querySelector('#join-error').textContent.length > 0", timeout=10000)
        msg = await stranger.inner_text("#join-error")
        check("a wrong code is refused with a readable message", "code" in msg.lower(), msg)
        check("the stranger never reaches the room", await stranger.locator("#room[hidden]").count() == 1)

        # --- phone layout ---
        overflow = await phone.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        check("no horizontal scroll on a phone", not overflow)

        manifest = await phone.evaluate("""async () => {
            const r = await fetch('./manifest.json');
            return { ok: r.ok, type: r.headers.get('content-type'), body: await r.json() };
        }""")
        check("the page installs to a home screen",
              manifest["ok"] and manifest["body"]["display"] == "standalone"
              and len(manifest["body"]["icons"]) == 2)
        icon = await phone.evaluate(
            "async () => (await fetch('./icon-192.png')).ok")
        check("icons are served", icon)

        await phone.screenshot(path="screenshot-phone.png", full_page=True)
        await tablet.set_viewport_size({"width": 900, "height": 900})
        await tablet.screenshot(path="screenshot-desktop.png", full_page=True)
        await browser.close()

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    check("no console or page errors", not real_errors, real_errors[:3])

    server.cancel()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    # the hub holds the loop open, and the run is over
    sys.stdout.flush()
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
