"""The certificate exists for one reason: without a secure origin, browsers do
not expose WebCrypto, and a phone on the LAN cannot join at all. These tests
guard that path, because every browser test runs on localhost, where the
problem is invisible."""
import asyncio
import socket
import ssl

import pytest
from cryptography import x509

from toss import tls
from toss.client import Client
from toss.hub import Hub

pytestmark = pytest.mark.asyncio


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


async def test_certificate_covers_the_lan_address(tmp_path):
    cert_path, key_path = tls.ensure_cert(tmp_path, "192.168.1.5")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    names = san.get_values_for_type(x509.DNSName)
    assert "192.168.1.5" in ips, "the phone connects to the LAN address, not localhost"
    assert "127.0.0.1" in ips
    assert "localhost" in names
    assert key_path.exists()


async def test_certificate_is_reused_then_regenerated_when_the_address_moves(tmp_path):
    first, _ = tls.ensure_cert(tmp_path, "192.168.1.5")
    body = first.read_bytes()
    tls.ensure_cert(tmp_path, "192.168.1.5")
    assert first.read_bytes() == body, "a restart should not hand out a new certificate"
    tls.ensure_cert(tmp_path, "10.0.0.9")
    assert first.read_bytes() != body, "moving networks must produce a certificate that matches"


async def test_fingerprint_is_short_and_stable(tmp_path):
    fp = tls.fingerprint(tmp_path, "192.168.1.5")
    assert fp == tls.fingerprint(tmp_path, "192.168.1.5")
    assert len(fp.split(":")) == 6


async def test_hub_serves_over_tls_and_a_client_can_join(tmp_path):
    hub = Hub(code="amber-fox-tiger-reef")
    port = free_port()
    ctx = tls.context(tmp_path, "127.0.0.1")
    task = asyncio.create_task(hub.run(host="127.0.0.1", port=port, ssl_context=ctx))
    await asyncio.sleep(0.4)
    try:
        async with Client(f"https://127.0.0.1:{port}", hub.code, name="phone", insecure=True) as c:
            assert c.fingerprint == hub.fingerprint
            await c.send_text("over tls")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_a_client_that_refuses_the_self_signed_cert_fails_closed(tmp_path):
    """Sanity check that TLS is really on: a strict client must reject it."""
    hub = Hub(code="amber-fox-tiger-reef")
    port = free_port()
    ctx = tls.context(tmp_path, "127.0.0.1")
    task = asyncio.create_task(hub.run(host="127.0.0.1", port=port, ssl_context=ctx))
    await asyncio.sleep(0.4)
    try:
        with pytest.raises((ssl.SSLError, ssl.SSLCertVerificationError, OSError)):
            async with Client(f"https://127.0.0.1:{port}", hub.code, insecure=False):
                pass
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
