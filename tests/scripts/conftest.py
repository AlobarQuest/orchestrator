"""A stand-in for the lane secrets broker: the real `scripts/secrets_broker.serve` with a fake
fetcher, so a lane-side client is tested against the broker's own framing and never against BWS."""

from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

import pytest

from scripts import secrets_broker

LANE = "example"
Fetcher = Callable[[str], str | None]


class Broker(Protocol):
    """The `broker` fixture's starter."""

    def __call__(self, values: dict[str, str | None], fetcher: Fetcher | None = None) -> Path: ...


@pytest.fixture
def broker() -> Iterator[Broker]:
    """`broker(values)` serves one lane whose allowlist is `values`' keys, and returns its socket.
    The fetcher answers `values[uuid]` (`None` is a failed `bws` read) unless `fetcher` is given.
    Stopped afterwards."""
    # Not `tmp_path`: on macOS its socket paths pass the 104-byte limit on a Unix socket.
    root = Path(tempfile.mkdtemp(prefix="broker-", dir="/tmp"))
    stop = threading.Event()
    threads: list[threading.Thread] = []

    def start(values: dict[str, str | None], fetcher: Fetcher | None = None) -> Path:
        assert not threads, "one broker per test"
        ready = threading.Event()
        thread = threading.Thread(
            target=secrets_broker.serve,
            args=({LANE: frozenset(values)}, root, fetcher or values.get),
            kwargs={"stop": stop, "ready": ready},
        )
        thread.start()
        threads.append(thread)
        deadline = time.monotonic() + 5
        while not ready.wait(0.02):
            assert thread.is_alive(), "the broker stopped before its socket listened"
            assert time.monotonic() < deadline, "the broker never listened on its socket"
        return root / LANE / secrets_broker.SOCKET_NAME

    try:
        yield start
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        shutil.rmtree(root)


@pytest.fixture
def dead_socket() -> Iterator[Path]:
    """A socket file nothing listens on, as a mount whose broker is down: `[ -S ]` is true and a
    connect is refused."""
    root = Path(tempfile.mkdtemp(prefix="dead-", dir="/tmp"))
    path = root / "broker.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
        bound.bind(str(path))
    try:
        yield path
    finally:
        shutil.rmtree(root)
