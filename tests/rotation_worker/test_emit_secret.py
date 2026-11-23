"""`emit-secret`: one secret's value to a pipe, for the host's `set-root-token --stdin`."""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from rotation_worker import cli
from rotation_worker.config import Config, ConfigError
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value, scrub
from tests.rotation_worker.stores import MemoryStore

PLACEHOLDER = "placeholder-value-1"
ADMIN = "33333333-3333-4333-8333-333333333333"


class Captured(io.StringIO):
    """Keeps what the verb writes, for the assertions, over a real descriptor whose kind the
    verb's guard reads."""

    def __init__(self, descriptor: int) -> None:
        super().__init__()
        self._descriptor = descriptor

    def fileno(self) -> int:
        return self._descriptor


@pytest.fixture
def pipe() -> Iterator[Captured]:
    read_end, write_end = os.pipe()
    yield Captured(write_end)
    os.close(read_end)
    os.close(write_end)


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def read(self, secret_id: str) -> Value:
        self.reads.append(secret_id)
        return super().read(secret_id)


class FailingStore(MemoryStore):
    """A store whose refusal carries the value, to show the verb never repeats a message."""

    def read(self, secret_id: str) -> Value:
        raise StoreError(f"BWS refused: {PLACEHOLDER}")


def _config(tmp_path: Path) -> Config:
    return Config(
        orchestrator_url="https://sds.example",
        credential_key_id="rotation-executor-1",
        organization_id="11111111-1111-4111-8111-111111111111",
        work_project_id="22222222-2222-4222-8222-222222222222",
        bearer_secret_id=None,
        root_token_file=tmp_path / "root-token",
        state_dir=tmp_path,
        source_revision="0123456789ab",
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> CountingStore:
    memory = CountingStore()
    monkeypatch.setattr(cli, "open_store", lambda config: memory)
    return memory


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[Config]:
    """Each config the verb opened a store with. It returns a working store, so a refusal whose
    guard is gone shows as an open here, not as an exception the verb's `except` turns into 2."""
    calls: list[Config] = []

    def record(config: Config) -> MemoryStore:
        calls.append(config)
        return MemoryStore()

    monkeypatch.setattr(cli, "open_store", record)
    return calls


def test_the_value_reaches_the_pipe_once_with_one_newline_through_the_entry_point(
    tmp_path: Path,
    store: CountingStore,
    pipe: Captured,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_id = store.put(ADMIN, "rotation-executor host token", Value(PLACEHOLDER))
    monkeypatch.setattr(cli, "load", lambda path: _config(tmp_path))
    monkeypatch.setattr(cli, "configure", lambda: None)
    monkeypatch.setattr(sys, "stdout", pipe)
    assert cli.main(["emit-secret", secret_id]) == 0
    assert pipe.getvalue() == PLACEHOLDER + "\n"
    assert store.reads == [secret_id]
    assert PLACEHOLDER not in capsys.readouterr().err


REFUSED_KIND = "emit-secret: writes only to a pipe or a socket, not a terminal or a file\n"


def test_a_socket_is_written_to(tmp_path: Path, store: CountingStore) -> None:
    secret_id = store.put(ADMIN, "rotation-executor host token", Value(PLACEHOLDER))
    near, far = socket.socketpair()
    with near, far:
        out = Captured(near.fileno())
        assert cli.emit_secret(_config(tmp_path), secret_id, out) == 0
    assert out.getvalue() == PLACEHOLDER + "\n"


def test_a_terminal_is_refused_before_the_store_is_opened(
    tmp_path: Path, opened: list[Config], capsys: pytest.CaptureFixture[str]
) -> None:
    controller, terminal_end = os.openpty()
    try:
        terminal = Captured(terminal_end)
        secret_id = "44444444-4444-4444-8444-444444444444"
        assert cli.emit_secret(_config(tmp_path), secret_id, terminal) == cli.EXIT_UNREADABLE
    finally:
        os.close(controller)
        os.close(terminal_end)
    assert terminal.getvalue() == ""
    assert opened == []
    assert capsys.readouterr().err == REFUSED_KIND


def test_a_regular_file_is_refused_before_the_store_is_opened(
    tmp_path: Path, opened: list[Config], capsys: pytest.CaptureFixture[str]
) -> None:
    secret_id = "44444444-4444-4444-8444-444444444444"
    with (tmp_path / "redirected").open("w") as file:
        assert cli.emit_secret(_config(tmp_path), secret_id, file) == cli.EXIT_UNREADABLE
    assert (tmp_path / "redirected").read_text() == ""
    assert opened == []
    assert capsys.readouterr().err == REFUSED_KIND


def test_a_stream_with_no_descriptor_is_refused_before_the_store_is_opened(
    tmp_path: Path, opened: list[Config], capsys: pytest.CaptureFixture[str]
) -> None:
    in_memory = io.StringIO()
    secret_id = "44444444-4444-4444-8444-444444444444"
    assert cli.emit_secret(_config(tmp_path), secret_id, in_memory) == cli.EXIT_UNREADABLE
    assert in_memory.getvalue() == ""
    assert opened == []
    assert capsys.readouterr().err == REFUSED_KIND


def test_a_store_failure_exits_2_and_carries_no_value(
    tmp_path: Path,
    pipe: Captured,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "open_store", lambda config: FailingStore())
    secret_id = "55555555-5555-4555-8555-555555555555"
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    captured = capsys.readouterr()
    assert pipe.getvalue() == ""
    assert PLACEHOLDER not in captured.out
    assert PLACEHOLDER not in captured.err
    assert captured.err == f"emit-secret: {secret_id} unreadable: StoreError\n"


def test_an_unknown_secret_exits_2_with_nothing_on_the_pipe(
    tmp_path: Path, store: CountingStore, pipe: Captured, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_id = "66666666-6666-4666-8666-666666666666"
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    assert pipe.getvalue() == ""
    assert store.reads == [secret_id]
    assert secret_id in capsys.readouterr().err


@pytest.mark.parametrize(
    "secret_id",
    [
        "",
        "not-a-uuid",
        "44444444-4444-4444-8444-44444444444",
        "44444444-4444-4444-8444-44444444444A",
    ],
)
def test_a_malformed_id_is_refused_before_the_store_is_opened(
    tmp_path: Path,
    opened: list[Config],
    pipe: Captured,
    capsys: pytest.CaptureFixture[str],
    secret_id: str,
) -> None:
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    assert pipe.getvalue() == ""
    assert opened == []
    assert capsys.readouterr().err == "emit-secret: the secret id must be a lowercase UUID\n"


def test_the_keychain_instance_is_refused(
    tmp_path: Path, opened: list[Config], pipe: Captured, capsys: pytest.CaptureFixture[str]
) -> None:
    keychain = replace(_config(tmp_path), store="keychain")
    secret_id = "44444444-4444-4444-8444-444444444444"
    assert cli.emit_secret(keychain, secret_id, pipe) == cli.EXIT_UNREADABLE
    assert pipe.getvalue() == ""
    assert opened == []
    assert capsys.readouterr().err == "emit-secret: only the BWS executor reads with the root\n"


def test_a_closed_pipe_exits_2_naming_only_the_class(
    tmp_path: Path, store: CountingStore, pipe: Captured, capsys: pytest.CaptureFixture[str]
) -> None:
    class Closed(Captured):
        def write(self, text: str) -> int:
            raise BrokenPipeError(text)

    secret_id = store.put(ADMIN, "rotation-executor host token", Value(PLACEHOLDER))
    closed = Closed(pipe.fileno())
    assert cli.emit_secret(_config(tmp_path), secret_id, closed) == cli.EXIT_UNREADABLE
    err = capsys.readouterr().err
    assert PLACEHOLDER not in err
    assert "BrokenPipeError" in err


def test_an_unforeseen_failure_exits_2_naming_only_the_class(
    tmp_path: Path,
    pipe: Captured,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Broken(MemoryStore):
        def read(self, secret_id: str) -> Value:
            raise KeyError(PLACEHOLDER)

    monkeypatch.setattr(cli, "open_store", lambda config: Broken())
    secret_id = "77777777-7777-4777-8777-777777777777"
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    assert capsys.readouterr().err == f"emit-secret: {secret_id} unreadable: KeyError\n"


def test_a_config_error_names_its_reason(
    tmp_path: Path,
    pipe: Captured,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unreadable(config: Config) -> MemoryStore:
        raise ConfigError("the root token file is empty")

    monkeypatch.setattr(cli, "open_store", unreadable)
    secret_id = "88888888-8888-4888-8888-888888888888"
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    # Scrubbed: an earlier test in the same process may have wrapped a value this text holds.
    assert capsys.readouterr().err == f"emit-secret: {scrub('the root token file is empty')}\n"


@pytest.mark.parametrize(
    "stored", ["placeholder value-1", "placeholder-\nvalue-1", "placeholder-\x07"]
)
def test_a_value_holding_whitespace_or_a_control_character_is_refused(
    tmp_path: Path,
    store: CountingStore,
    pipe: Captured,
    capsys: pytest.CaptureFixture[str],
    stored: str,
) -> None:
    secret_id = store.put(ADMIN, "rotation-executor host token", Value(stored))
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    assert pipe.getvalue() == ""
    assert stored not in capsys.readouterr().err


@pytest.mark.parametrize(
    "stored", ["", " placeholder-value-1", "placeholder-value-1 ", "placeholder-value-1\n"]
)
def test_an_empty_value_or_one_with_surrounding_whitespace_is_refused_as_unreadable(
    tmp_path: Path,
    pipe: Captured,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stored: str,
) -> None:
    """The store wraps what BWS holds as `BwsStore.read` does, so `Value` refuses it: nothing
    reaches the pipe, not even a bare newline, and the message names only the error's class."""

    class Raw(MemoryStore):
        def read(self, secret_id: str) -> Value:
            return Value(stored)

    monkeypatch.setattr(cli, "open_store", lambda config: Raw())
    secret_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    assert pipe.getvalue() == ""
    assert capsys.readouterr().err == f"emit-secret: {secret_id} unreadable: ValueError\n"


CLOSED_PIPE_RUN = """
import sys
from pathlib import Path
from rotation_worker import cli
from rotation_worker.config import Config
from rotation_worker.values import Value

class Store:
    def read(self, secret_id):
        return Value("placeholder-value-1" * 4096)

cli.open_store = lambda config: Store()
config = Config(
    orchestrator_url="https://sds.example",
    credential_key_id="rotation-executor-1",
    organization_id="11111111-1111-4111-8111-111111111111",
    work_project_id="22222222-2222-4222-8222-222222222222",
    bearer_secret_id=None,
    root_token_file=Path("root-token"),
    state_dir=Path("."),
    source_revision="0123456789ab",
)
sys.exit(cli.emit_secret(config, "99999999-9999-4999-8999-999999999999", sys.stdout))
"""


def test_a_real_closed_pipe_exits_2_with_one_message() -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        finished = subprocess.run(
            [sys.executable, "-c", CLOSED_PIPE_RUN],
            stdout=write_end,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        os.close(write_end)
    assert finished.returncode == cli.EXIT_UNREADABLE
    assert finished.stderr == (
        "emit-secret: 99999999-9999-4999-8999-999999999999 not written: BrokenPipeError\n"
    )
