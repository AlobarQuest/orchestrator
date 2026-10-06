"""The App Brain source a page render uses: the admission one, with a short per-phase timeout.

Measured against a real socket that accepts the connection and never answers, so the wait is
the source's own timeout rather than a double's idea of one.
"""

import socket
import threading
import time
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from orchestrator.api.routes.common import (
    PAGE_APP_BRAIN_TIMEOUT_SECONDS,
    get_page_landing_source,
)
from orchestrator.config import Settings
from orchestrator.services.landing.estate_landing import SOURCE_UNREADABLE, EstateAnswer


@pytest.fixture
def silent_server() -> Iterator[str]:
    """A server that accepts connections and never writes a byte back."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            held.append(connection)

    thread = threading.Thread(target=accept)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        stop.set()
        thread.join(timeout=5)
        for connection in held:
            connection.close()
        listener.close()


def _settings(url: str) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://unused@127.0.0.1/unused",
        app_brain_url=url,
        app_brain_read_key=SecretStr("read-key"),
        app_brain_timeout_seconds=10.0,
    )


def test_a_page_gives_up_on_a_silent_app_brain_within_its_short_timeout(
    silent_server: str,
) -> None:
    source = get_page_landing_source(_settings(silent_server))
    started = time.monotonic()

    answer = source.landing_for("AlobarQuest/brain")

    assert answer == EstateAnswer(None, SOURCE_UNREADABLE)
    assert time.monotonic() - started < PAGE_APP_BRAIN_TIMEOUT_SECONDS * 3


def test_the_page_cap_never_lengthens_a_shorter_configured_timeout(silent_server: str) -> None:
    settings = _settings(silent_server).model_copy(update={"app_brain_timeout_seconds": 0.2})
    started = time.monotonic()

    get_page_landing_source(settings).landing_for("AlobarQuest/brain")

    assert time.monotonic() - started < 1
