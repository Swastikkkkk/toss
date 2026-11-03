import asyncio
import socket

import pytest
import pytest_asyncio

from toss.hub import Hub


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest_asyncio.fixture
async def hub():
    """A hub on a free port, torn down after the test."""
    h = Hub(code="amber-fox-tiger-reef")
    h.port = free_port()
    task = asyncio.create_task(h.run(host="127.0.0.1", port=h.port))
    for _ in range(60):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", h.port)
            w.close()
            break
        except OSError:
            await asyncio.sleep(0.05)
    h.url = f"http://127.0.0.1:{h.port}"
    yield h
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
