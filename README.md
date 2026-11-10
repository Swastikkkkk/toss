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
toss
```

It starts toss in the background (no window, and again every time you log in),
prints a QR and hands your terminal back. Close it. **Scan the QR with your
phone's camera. That is the whole setup.** `toss stop` turns it off; `toss serve`
runs it in the foreground if you want to watch the log.
The page opens, connects by itself and stays connected. No code to type, no
name to pick, no certificate warning. Add it to your home screen and it opens
like an app and reconnects on its own after that.

No camera? Open the address it prints and type the four words.

**Right-click, Send to phone.** On Windows every file and folder gets a
**Send to phone** entry in Explorer (on Windows 11 it is under *Show more
options*, and also in *Send to*). Folders go as a zip. Clicking the tray icon
sends whatever you copied. Nothing to open, it starts toss if it was not
running. `toss send a.pdf b.jpg somefolder` does the same from a terminal.

**The phone page opens instantly.** It goes straight to the connected screen
with no pairing step, the first scan skips the slow key stretching (the QR
carries the stretched key, which is no more secret than the code beside it,
and a `#fragment` never leaves the phone), and finding the stable
`toss-xxxx.local` name happens in the background instead of before connecting.

**iPhone, fully automatic.** A web page can't write your clipboard or run in
the background, so the phone page offers one Shortcut, once (it lives under the
**···** button after that). It makes toss a share option, like AirDrop: share a
photo, a link or a file from any app, tap **toss**, and it is on the laptop's
clipboard. Put the same Shortcut on Back Tap and double-tapping the back of
your iPhone pulls whatever you copied on the laptop. No page open, no Copy
button.
They talk to a small token-protected endpoint on port 7778 on your wifi.

**It stays linked.** On Windows toss lives in the tray: click the icon for the
pairing QR, pause sync, or open received files, and you get a notification when
something arrives. The laptop also answers to a fixed `toss-xxxx.local` name
over mDNS, and the phone page moves itself onto that name after the first scan,
so it reconnects on its own even when the laptop's IP changes. Anything you copy
while the phone is asleep or the page is closed waits on the laptop (encrypted,
in memory, up to 12 hours) and arrives the moment you open toss on the phone.

The page is plain http on your wifi. Browsers only hand WebCrypto to https
pages, so the page carries its own copy of the audited
[noble](https://github.com/paulmillr/noble-ciphers) crypto (AES-256-GCM,
PBKDF2, HMAC) and the hub still only ever sees ciphertext. `toss --https`
serves a self-signed certificate instead if you want the browser's own
clipboard APIs; phones then show a warning once.

**To send:** highlight something and hold `right-ctrl` for 0.4 seconds. It
copies the selection for you first, so there is no copy-then-send dance. Files
and images you copied go too. If you would rather it only ever read what is
already on your clipboard, use `--no-grab`.

It copies by pressing **Ctrl+Insert**, not Ctrl+C. Both mean copy almost
everywhere on Windows, but in a terminal Ctrl+C means stop the program, so with
toss's own terminal focused it used to shut itself down. And while the hotkey
is held, toss ignores any Ctrl+C that arrives, because at that moment it is a
copy, never you quitting: other apps that react to a held Ctrl, or an old copy
of toss still running, send one too. Ctrl+C at any other time stops toss.

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

## Clipboard sync

On by default, and it carries text, images and files.

| you copy on the laptop | it arrives on the phone as |
| --- | --- |
| text | pinned at the top, ready to paste |
| an image, a screenshot, "Copy image" in a browser | the picture, pinned, ready to paste |
| a file in Explorer, a PDF, a Word doc, anything | the file, with Save or share |

Going the other way, whatever you send from the phone lands on the laptop's
clipboard: text pastes as text, an image pastes as an image, and a file pastes
as a file into a folder, an email or a chat. Files are also saved to Downloads.

How a copy lands on the phone depends on the browser, and this part is the
platform's rule, not toss's:

| on the phone | text and images | files |
| --- | --- | --- |
| Android, Chrome | copied to the phone's clipboard automatically | one tap to save or share |
| iPhone, Safari | one tap on **Tap to copy** | one tap to save or share |

Safari refuses any clipboard write that is not inside a tap, for every website,
and no phone clipboard can hold a PDF, so documents go through the share sheet:
Save to Files, AirDrop, WhatsApp. **Send my clipboard to the laptop** reads the
phone's clipboard on a tap, text or image, and iPhone adds its own Paste prompt.

Copies from password managers are never synced. 1Password, Bitwarden, KeePass
and the rest mark their copies with `ExcludeClipboardContentFromMonitorProcessing`
and related flags, the same signal Windows clipboard history honours. Nothing
echoes back, the same copy is only sent once, and copied files over 50 MB are
skipped by sync (hold the hotkey to send one deliberately).

`toss serve --no-sync` turns it off and leaves only the hotkey.

### How images are read

Windows hands copied images over as a device-independent bitmap, sometimes
alongside a PNG. toss uses the PNG when an app provides one and otherwise
converts the bitmap itself, in plain Python with no imaging library.

Windows 11 has a quirk here, found on a real machine: when it generates the
V5 bitmap format it stores the colour masks inside the header and then repeats
them after it, and when it generates the older format from that, it makes the
same mistake itself and shifts every pixel. Reading from the wrong offset moves
the picture sideways. `test_image.py` recreates both layouts byte for byte.

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
pytest -q              # 65 tests: crypto, auth, relay, chunking, TLS, sync, images, config
python tests/e2e.py    # 21 checks through a real Chromium
```

The Windows clipboard, the hotkey and sync were tested on a real Windows 11
machine, since none of that runs in CI:

- text, unicode, 200 reads back to back, a password-manager copy being skipped
- images copied by an app, converted to PNG and checked pixel by pixel by
  Windows' own decoder, for both bitmap formats at 24 and 32 bit
- a browser-style PNG copy passed through untouched, and text winning when an
  app also puts a picture of it there
- through the real hub: an image, a PDF and text reaching a phone
  client, a Word file and an image from the phone landing on the Windows
  clipboard ready to paste, with nothing echoing back
- a keyboard hook confirming toss presses Ctrl+Insert and never C
- a held right-ctrl firing exactly once while a quick tap does nothing, and a
  real console Ctrl+C mid-hold being ignored while one at any other time stops
  toss within milliseconds

`test_sync.py` also guards the bug that made the clipboard read as empty: on
64-bit Windows, ctypes assumes every function returns a 32-bit int unless told
otherwise, and the clipboard returns 64-bit pointers. Undeclared, they get
truncated, which reads as an empty clipboard or crashes outright. The test
fails if any pointer-returning call loses its declared type.

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
