"""A self-signed certificate, so the receiver page gets a secure context.

Browsers only expose WebCrypto on a secure origin. http://192.168.1.5:7777 is
not one, so on a phone `crypto.subtle` is simply undefined and nothing can be
decrypted. Serving https fixes that. The certificate is self-signed, so the
first visit on each device shows a warning you tap through once.

The key never leaves your machine and is readable only by you.
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import stat
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 825  # what browsers accept as a maximum for a leaf certificate


def _names(ip: str) -> list[x509.GeneralName]:
    """Everything this certificate should be valid for."""
    out: list[x509.GeneralName] = [x509.DNSName("localhost")]
    seen = set()
    for candidate in (ip, "127.0.0.1", "::1"):
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(candidate)))
        except ValueError:
            pass
    try:
        host = socket.gethostname()
        if host:
            out.append(x509.DNSName(host))
            out.append(x509.DNSName(f"{host}.local"))
    except OSError:
        pass
    return out


def ensure_cert(directory: Path, ip: str) -> tuple[Path, Path]:
    """Return (cert, key), generating them if missing or if the address moved."""
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"

    if cert_path.exists() and key_path.exists():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covers_ip = any(
                str(value) == ip for value in san.get_values_for_type(x509.IPAddress)
            )
            not_after = cert.not_valid_after_utc
            fresh = not_after > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
            if covers_ip and fresh:
                return cert_path, key_path
        except Exception:
            pass  # unreadable or malformed, just make a new one

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "toss"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "toss, local only"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(_names(ip)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return cert_path, key_path


def context(directory: Path, ip: str) -> ssl.SSLContext:
    cert_path, key_path = ensure_cert(directory, ip)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def fingerprint(directory: Path, ip: str) -> str:
    """Shown on the laptop so you can confirm the certificate your phone saw."""
    cert_path, _ = ensure_cert(directory, ip)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in digest[:6])
