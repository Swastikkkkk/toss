"""Pairing codes and payload encryption.

The pairing code is the only secret. Both sides stretch it into the same key,
so anything crossing the wifi is unreadable to everyone else on it, and the hub
relays ciphertext it never opens.

The browser side does the matching work with WebCrypto, so the parameters here
are deliberately ones WebCrypto also supports: PBKDF2-SHA256 and AES-256-GCM.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ITERATIONS = 200_000
KEY_BYTES = 32
IV_BYTES = 12
SALT_BYTES = 16

# A short, unambiguous wordlist. No near-homophones, nothing longer than six
# letters, so a code survives being read out loud across a room.
WORDS = """
amber anchor apple arrow aspen atlas bacon badge bagel banjo basil beach beacon
beetle bingo birch bison blade bloom board bolt bonsai boost brick bridge bronze
brush bubble cable cactus camel candle canoe canvas carbon cargo carpet cedar
chalk charm cherry chess chrome cider cinder circus citrus clamp cliff clover
cobalt cocoa comet copper coral cosmic cotton coyote crane crater crayon creek
crisp crown crystal cue curry cyan daisy dancer dapper dawn delta denim desert
diamond diesel dingo dinner dock dolphin domino donut dragon drift drum dune
eagle east ember emerald engine escape ethos fable falcon fancy feather fennel
fern fiber fiddle finch flame flare flint float flora flute foam forest fossil
fox frost fuzzy gadget galaxy garden garlic gecko ginger glacier glide globe
glow gold gopher granite grape gravel green grid grotto guava gumbo gusto
hammer harbor harvest hazel heron hickory hollow honey hornet hostel hunter
indigo ink iris island ivory jade jaguar jasmine jelly jewel jigsaw jolly
jungle juniper kayak kernel kettle keypad kiosk kite koala lagoon lantern lapis
lasso latte laurel lava ledge lemon lentil lilac linen lion lobby locket lotus
lumber lunar lychee magnet mango maple marble marina marsh meadow melon mesa
meteor mint mirror mocha mojave monsoon moss motel mural museum nacho nebula
nectar needle neon nest nickel noble nomad noodle north nugget oasis ocean
olive onyx opal orbit orchid otter oxide oyster pagoda palace panda papaya
pastel pebble pecan pelican pepper petal pewter phoenix piano picnic pigment
pilot pine pistol pivot pixel plasma plaza plum pocket pollen pond poppy portal
potato prairie prism puffin pumpkin purple quartz quiver rabbit radar rafter
rain ranch rapid raven reef relay ribbon ridge rifle ripple river robin rocket
rodeo roster rubber ruby rust saffron sage salmon sandal sapphire satin savanna
scarlet scout sepia shadow shale shell sierra silk silver siren sketch slate
sloth smoke socket solar sonar sorbet spark spiral spruce stable stamp statue
steam stone storm straw studio sugar summit sunset surf swift sycamore syrup
tabby talon tandem tango tapir teal tempo tender thistle thunder tiger timber
tinder toffee tomato topaz torch totem toucan tower trail trellis tribe trophy
tulip tundra turbo turtle tusk ultra umber urban vanilla velvet venue verdant
vessel vinyl violet viper vista vivid vortex walnut wander wasabi waffle walrus
wave weaver whale wheat willow window winter wisp wolf wombat woven yarn
yellow yodel yonder zebra zenith zephyr zigzag zinc zodiac
""".split()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text)


def make_code(words: int = 4) -> str:
    """A pairing code you can read out loud. Four words out of this list is
    about 32 bits, which is plenty once wrong guesses are rate limited."""
    return "-".join(secrets.choice(WORDS) for _ in range(words))


def normalize_code(code: str) -> str:
    """People retype codes with spaces, capitals and stray punctuation."""
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in str(code))
    return "-".join(cleaned.split())


def derive_key(code: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", normalize_code(code).encode("utf-8"), salt, PBKDF2_ITERATIONS, KEY_BYTES
    )


def fingerprint(key: bytes) -> str:
    """Six characters both devices can show, so you can see at a glance that
    you joined the right session and not a neighbour's."""
    digest = hashlib.sha256(b"toss-fingerprint" + key).digest()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alikes
    return "".join(alphabet[b % len(alphabet)] for b in digest[:6])


def proof(key: bytes, challenge: bytes) -> str:
    """Prove you know the code without ever sending it."""
    return b64(hmac.new(key, challenge, hashlib.sha256).digest())


def verify_proof(key: bytes, challenge: bytes, given: str) -> bool:
    try:
        return hmac.compare_digest(unb64(given), hmac.new(key, challenge, hashlib.sha256).digest())
    except Exception:
        return False


def seal(key: bytes, payload: dict | bytes, aad: bytes = b"") -> dict:
    """Encrypt a payload. Returns the two fields that go on the wire."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    iv = os.urandom(IV_BYTES)
    ct = AESGCM(key).encrypt(iv, raw, aad or None)
    return {"iv": b64(iv), "ct": b64(ct)}


def open_sealed(key: bytes, envelope: dict, aad: bytes = b"", raw: bool = False):
    """Decrypt. Raises if the ciphertext was touched or the key is wrong."""
    plain = AESGCM(key).decrypt(unb64(envelope["iv"]), unb64(envelope["ct"]), aad or None)
    return plain if raw else json.loads(plain.decode("utf-8"))
