"""A name for this laptop that survives wifi changes.

The phone saves its pairing per address. A raw IP changes whenever the laptop
joins another network or the router hands out a new lease, and the phone would
lose track. So the hub also answers to toss-xxxx.local over mDNS (the same
thing AirDrop and printers use). The QR still carries the IP, which always
works for the first scan; the page then moves itself to the .local name if the
phone can resolve it, and from then on it reconnects no matter the IP.
"""
from __future__ import annotations

import asyncio
import hashlib
import socket

from .hub import lan_ip


def host_for(salt: bytes) -> str:
    return "toss-" + hashlib.sha256(b"toss-host" + salt).hexdigest()[:6]


async def advertise(salt: bytes, port: int, on_ip_change=None) -> None:
    """Keep toss-xxxx.local pointing at our current address. Never raises."""
    try:
        from zeroconf import IPVersion, ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf
    except ImportError:
        return
    host = host_for(salt)
    zc = None
    info = None
    ip = None
    try:
        while True:
            now = lan_ip()
            if now != ip and now != "127.0.0.1":
                if zc:
                    try:
                        await zc.async_unregister_all_services()
                        await zc.async_close()
                    except Exception:
                        pass
                zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
                info = ServiceInfo(
                    "_toss._tcp.local.", f"{host}._toss._tcp.local.",
                    addresses=[socket.inet_aton(now)], port=port,
                    properties={"path": "/"}, server=f"{host}.local.",
                )
                try:
                    await zc.async_register_service(info, allow_name_change=True)
                except Exception:
                    pass
                if ip and on_ip_change:
                    on_ip_change(now)
                ip = now
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        pass
    finally:
        if zc:
            try:
                await zc.async_unregister_all_services()
                await zc.async_close()
            except Exception:
                pass
