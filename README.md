# toss

Highlight something and hold a key. It lands on your other device.

No app to open, no contact to pick, no chat to send yourself. The thing AirDrop
does, on the machines AirDrop ignores.

```
  toss is up on http://192.168.1.5:7777

  code         scout-gopher-crater-nest
  session      WXUKGF

  [QR]

  Hold right-ctrl for 0.4s to send whatever is selected.
```

Open that address on your phone, type the four words, done. Now highlight
anything on the laptop and hold right-ctrl. It is on your phone before you let
go.

You do that once. The code is remembered on both sides, so from then on the
page rejoins by itself and `toss install` means the laptop side is already
running when you log in. After the first minute there is nothing to set up,
nothing to open and nothing to type.

## Why it works this way

The transfer was never the hard part. Snapdrop and PairDrop have done
browser-to-browser sharing for years and nobody uses them daily, because you
still have to go and open a tab first. The friction is the opening, not the
sending.

So the laptop side has no window at all. It is a key you hold. The other side is
just a page that is already open, and it does not need anything installed,
which is the only way this works on a phone your friend is holding.

## Install

```bash
pip install "toss[qr] @ git+https://github.com/Swastikkkkk/toss"
```

Python 3.10+. Two real dependencies: `websockets` and `cryptography`. The QR is
optional, and without it you just read the four words out loud.

On Windows, double click `start.bat`. To hack on it, clone and
`pip install -e ".[qr]"` instead.

## Use

**On the laptop:**

```bash
toss serve
```

It prints a code, a QR and the address to open. Then it waits.

**On the other device:** open that address in any browser and enter the code, or
scan the QR which carries the code in the link. That page stays open. It is the
whole receiving app.

**To send:** highlight something and hold `right-ctrl` for 0.4 seconds. It
copies the selection for you first, so there is no copy-then-send dance. Files
you copied in Explorer go too. If you would rather it only ever read what is
already on your clipboard, use `--no-grab`.

**To send back:** type in the box and press enter, or paste anywhere on the
page, or drop files on it. Text lands on your laptop clipboard ready to paste,
files land in your Downloads.

Prefer a different key or a different feel:

```bash
toss serve --key capslock --hold 0.25
toss serve --key f9 --inbox ~/Desktop/inbox
```

Available keys: `right-ctrl`, `left-ctrl`, `right-alt`, `right-shift`,
`capslock`, `scrolllock`, `f9`. Whatever you pass is remembered, so you only
pass it once.

## Set it up once and forget it

```bash
toss install      # start automatically when you log in, no console window
toss status       # the code, the address, and every setting
toss uninstall    # undo it
```

`toss install` writes one plain text file into your startup folder that you can
read and delete by hand. On Windows it runs through `pythonw`, so there is no
terminal sitting in your taskbar.

On the phone, open the page once and use your browser's **add to home screen**.
It has an icon and a manifest, so it opens full screen like an app and rejoins
the moment it is on screen.

The pairing survives reboots on both sides. If you ever want to cut a device
off, rotate the code:

```bash
toss serve --new-code
```

Devices holding the old code stop being able to join, and say so rather than
retrying forever.

No hotkey, or not on Windows:

```bash
toss send "https://example.com"
toss send ~/Documents/notes.pdf
toss send                        # sends your clipboard
```

It uses the remembered code, so there is nothing to pass.

## How the code keeps it private

The four words are the only secret, and they never cross the network.

Both sides stretch the code through PBKDF2-SHA256, 200k iterations, against a
salt stored alongside it, into one AES-256 key. Joining is a
challenge-response: the hub sends random bytes, you send back an HMAC, and a
device that cannot produce it is dropped. Every item is sealed with AES-GCM
before it leaves, so the hub relays ciphertext it has no way to open, and
anyone sniffing the wifi sees nothing. Six wrong codes from one address and that
address stops being answered, which is what makes a four-word code enough.

Both devices show a six-character **session** fingerprint derived from the key.
If they match, you are talking to each other and not to a neighbour who guessed
well. If they differ, something is wrong, and that is worth knowing.

The page is served under a strict Content-Security-Policy with no inline script
and no outside origins, so it can only ever talk to the hub it came from.

Nothing that passes through is written to disk, and nothing is kept in the page
beyond the tab you have open. The one thing that is stored is the pairing
itself: the code and its salt in `~/.toss/config.json`, kept readable only by
you, and the code in the browser's local storage on each paired device, which
the **forget** link clears. That is the trade for not retyping it every day,
and `--new-code` takes it back whenever you want.

## What will break

**Client isolation.** Many college, office and public wifi networks block
devices from talking to each other directly. On that kind of network the page
will not connect at all, and there is nothing this can do about it from inside
the network. Home wifi and a phone hotspot both work fine. Routing around it
needs a relay server somewhere on the internet, which is a different project
with different privacy trade-offs.

**The hotkey is Windows only right now.** It uses a raw key-state poll through
ctypes, so there is nothing to install and no admin rights needed, but it is
Windows-specific. Everywhere else the hub, the page and `toss send` all work,
you just do not get hold-to-send.

**The hub is the laptop.** If the laptop sleeps, the session ends. That is the
intended shape, not a limitation to fix.

## Tests

```bash
pytest -q              # 36 tests: crypto, auth, relay, chunking, config, autostart
python tests/e2e.py    # 21 checks through a real Chromium
```

The interesting one is the second. The risk in a project like this is that the
browser's WebCrypto and Python's `cryptography` quietly disagree about key
derivation or AES-GCM, and everything looks fine until nothing decrypts. So the
end to end run drives a real browser against a real hub and checks:

- the fingerprint derived in Chromium matches the one derived in Python
- text sent from Python decrypts in the browser, and back the other way
- a 400 KB file survives chunking, encryption, relay and reassembly **byte for
  byte**, verified by downloading it and comparing
- a wrong code is refused with a message a person can read, and that device
  never reaches the room
- the hub, watched from inside, only ever relays envelopes with no plaintext in
  them
- a device that paired earlier gets back in with no typing at all, from a fresh
  page load in the same browser storage
- pasting anywhere on the page sends it
- forgetting a device really clears the stored code
- the manifest and icons are served, so it installs to a home screen
- no console errors, and no horizontal scroll on a phone viewport

## The site

`site/` is the source of the landing page and `docs/` is the built copy that
gets served. Both are plain files with no build tooling: `site/build.py` only
wraps the page in a standalone `<head>` and copies the assets across.

```bash
python site/build.py                          # rebuild docs/
python -m http.server 8000 --directory docs   # read it locally
```

It is split the way you would split any front end: `css/tokens.css` holds every
colour and type value and nothing else is allowed a literal colour,
`css/base.css` is the reset and typography, `css/components.css` is the pieces.
The JavaScript is ES modules, one job each: `words.js` (the wordlist, generated
from `toss/crypto.py` so the two cannot drift), `codegen.js`, `keyhold.js`,
`motion.js`, `clipboard.js`, and `main.js` as the only entry point.

Type is the Apple system stack, so Apple devices render real SF Pro and SF Mono
natively. SF is not licensed for web embedding, so this is the only honest way
to use it, and it costs no download at all. Windows 11 falls back to Segoe UI
Variable.

GSAP drives the hero entrance, the scroll reveals and the motion path on the
hold-to-send demo. It degrades cleanly: the page is complete and readable with
the scripts blocked entirely, and all of it is skipped under
`prefers-reduced-motion`.

### Putting it online

Push this repo to GitHub, then **Settings > Pages**, branch `main`, folder
`/docs`. It appears at `https://<you>.github.io/toss/` within a minute. Netlify
and Vercel are the same: point them at the repo with `docs` as the publish
directory and no build command. Netlify Drop also works, just drag the `docs`
folder onto it.

### Checked on

Sixteen configurations, every one with no horizontal scroll, nothing
overflowing, no console errors, the hold demo delivering and the code generator
rolling:

| | |
| --- | --- |
| phones | iPhone SE at 320px, iPhone 13, iPhone 15 Pro Max, Pixel 7 |
| tablets | iPad Mini portrait, 1024px landscape |
| desktop | 1440px, 1920px |
| themes | light and dark on every one of them |
| motion | `prefers-reduced-motion` leaves the page complete and still |

## Layout

```
toss/crypto.py      pairing codes, key derivation, sealing, fingerprints
toss/hub.py         the server on your laptop: one page, and a relay
toss/client.py      joining and sending from Python
toss/clipboard.py   clipboard in and out, ctypes on Windows, no dependency
toss/hotkey.py      the hold-to-send watcher, deliberately ~60 lines
toss/config.py      the remembered pairing and settings
toss/autostart.py   start on login, one plain text file
toss/cli.py         toss serve / send / install / status
web/                the receiving page: one html, one js, a manifest, no build step
```

## License

MIT
