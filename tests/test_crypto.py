import os

import pytest

from toss import crypto


def test_code_is_readable_and_random():
    codes = {crypto.make_code() for _ in range(200)}
    assert len(codes) > 190, "codes should not collide this easily"
    assert all(len(c.split("-")) == 4 for c in codes)
    assert all(w in crypto.WORDS for c in codes for w in c.split("-"))


def test_wordlist_has_no_duplicates():
    assert len(crypto.WORDS) == len(set(crypto.WORDS))
    assert len(crypto.WORDS) > 300, "needs enough entropy to be worth rate limiting"


@pytest.mark.parametrize("typed", [
    "amber fox tiger reef", "Amber-Fox-Tiger-Reef", "  amber,fox;tiger  reef ", "AMBER FOX TIGER REEF",
])
def test_people_retype_codes_badly(typed):
    assert crypto.normalize_code(typed) == "amber-fox-tiger-reef"


def test_same_code_and_salt_give_the_same_key():
    salt = os.urandom(16)
    assert crypto.derive_key("amber-fox", salt) == crypto.derive_key("AMBER FOX", salt)


def test_different_salt_gives_a_different_key():
    a = crypto.derive_key("amber-fox", os.urandom(16))
    b = crypto.derive_key("amber-fox", os.urandom(16))
    assert a != b, "a fresh session must not reuse an old session's key"


def test_fingerprint_is_short_stable_and_unambiguous():
    key = crypto.derive_key("amber-fox", b"x" * 16)
    fp = crypto.fingerprint(key)
    assert len(fp) == 6
    assert fp == crypto.fingerprint(key)
    assert not set(fp) & set("01ILO"), "no characters that get misread aloud"
    assert fp != crypto.fingerprint(crypto.derive_key("other-code", b"x" * 16))


def test_proof_round_trip_and_rejection():
    salt, challenge = os.urandom(16), os.urandom(32)
    key = crypto.derive_key("amber-fox-tiger-reef", salt)
    wrong = crypto.derive_key("amber-fox-tiger-reek", salt)
    assert crypto.verify_proof(key, challenge, crypto.proof(key, challenge))
    assert not crypto.verify_proof(key, challenge, crypto.proof(wrong, challenge))
    assert not crypto.verify_proof(key, os.urandom(32), crypto.proof(key, challenge))
    assert not crypto.verify_proof(key, challenge, "not base64 at all !!")


def test_seal_round_trip():
    key = crypto.derive_key("amber-fox", os.urandom(16))
    env = crypto.seal(key, {"kind": "text", "data": "hello ₹ \U0001f600"})
    assert "hello" not in env["ct"], "the payload must not be readable on the wire"
    assert crypto.open_sealed(key, env)["data"] == "hello ₹ \U0001f600"


def test_seal_handles_raw_bytes():
    key = crypto.derive_key("amber-fox", os.urandom(16))
    blob = os.urandom(5000)
    assert crypto.open_sealed(key, crypto.seal(key, blob), raw=True) == blob


def test_tampering_is_detected():
    key = crypto.derive_key("amber-fox", os.urandom(16))
    env = crypto.seal(key, {"data": "hello"})
    bad = bytearray(crypto.unb64(env["ct"]))
    bad[0] ^= 1
    with pytest.raises(Exception):
        crypto.open_sealed(key, {"iv": env["iv"], "ct": crypto.b64(bytes(bad))})


def test_wrong_key_cannot_open():
    salt = os.urandom(16)
    env = crypto.seal(crypto.derive_key("right-code", salt), {"data": "secret"})
    with pytest.raises(Exception):
        crypto.open_sealed(crypto.derive_key("wrong-code", salt), env)


def test_nonce_is_never_reused():
    key = crypto.derive_key("amber-fox", os.urandom(16))
    ivs = {crypto.seal(key, {"n": i})["iv"] for i in range(500)}
    assert len(ivs) == 500
