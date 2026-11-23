"""`emit-secret`: one secret's value to a pipe, for the host's `set-root-token --stdin`."""

from __future__ import annotations

import io
import sys
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


class Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


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


def _never_opened(config: Config) -> MemoryStore:
    raise AssertionError("the store was opened")


def test_the_value_reaches_the_pipe_once_with_one_newline_through_the_entry_point(
    tmp_path: Path,
    store: CountingStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_id = store.put(ADMIN, "rotation-executor host token", Value(PLACEHOLDER))
    monkeypatch.setattr(cli, "load", lambda path: _config(tmp_path))
    monkeypatch.setattr(cli, "configure", lambda: None)
    pipe = Pipe()
    monkeypatch.setattr(sys, "stdout", pipe)
    assert cli.main(["emit-secret", secret_id]) == 0
    assert pipe.getvalue() == PLACEHOLDER + "\n"
    assert store.reads == [secret_id]
    assert PLACEHOLDER not in capsys.readouterr().err


def test_a_terminal_is_refused_before_the_store_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "open_store", _never_opened)
    terminal = Terminal()
    secret_id = "44444444-4444-4444-8444-444444444444"
    assert cli.emit_secret(_config(tmp_path), secret_id, terminal) == cli.EXIT_UNREADABLE
    assert terminal.getvalue() == ""
    assert "pipe" in capsys.readouterr().err


def test_a_store_failure_exits_2_and_carries_no_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "open_store", lambda config: FailingStore())
    pipe = Pipe()
    secret_id = "55555555-5555-4555-8555-555555555555"
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    captured = capsys.readouterr()
    assert pipe.getvalue() == ""
    assert PLACEHOLDER not in captured.out
    assert PLACEHOLDER not in captured.err
    assert captured.err == f"emit-secret: {secret_id} unreadable: StoreError\n"


def test_an_unknown_secret_exits_2_with_nothing_on_the_pipe(
    tmp_path: Path, store: CountingStore, capsys: pytest.CaptureFixture[str]
) -> None:
    pipe = Pipe()
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret_id: str
) -> None:
    monkeypatch.setattr(cli, "open_store", _never_opened)
    pipe = Pipe()
    assert cli.emit_secret(_config(tmp_path), secret_id, pipe) == cli.EXIT_UNREADABLE
    assert pipe.getvalue() == ""


def test_the_keychain_instance_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "open_store", _never_opened)
    keychain = replace(_config(tmp_path), store="keychain")
    secret_id = "44444444-4444-4444-8444-444444444444"
    assert cli.emit_secret(keychain, secret_id, Pipe()) == cli.EXIT_UNREADABLE


def test_a_closed_pipe_exits_2_naming_only_the_class(
    tmp_path: Path, store: CountingStore, capsys: pytest.CaptureFixture[str]
) -> None:
    class Closed(Pipe):
        def write(self, text: str) -> int:
            raise BrokenPipeError(text)

    secret_id = store.put(ADMIN, "rotation-executor host token", Value(PLACEHOLDER))
    assert cli.emit_secret(_config(tmp_path), secret_id, Closed()) == cli.EXIT_UNREADABLE
    err = capsys.readouterr().err
    assert PLACEHOLDER not in err
    assert "BrokenPipeError" in err


def test_an_unforeseen_failure_exits_2_naming_only_the_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Broken(MemoryStore):
        def read(self, secret_id: str) -> Value:
            raise KeyError(PLACEHOLDER)

    monkeypatch.setattr(cli, "open_store", lambda config: Broken())
    secret_id = "77777777-7777-4777-8777-777777777777"
    assert cli.emit_secret(_config(tmp_path), secret_id, Pipe()) == cli.EXIT_UNREADABLE
    assert capsys.readouterr().err == f"emit-secret: {secret_id} unreadable: KeyError\n"


def test_a_config_error_names_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreadable(config: Config) -> MemoryStore:
        raise ConfigError("the root token file is empty")

    monkeypatch.setattr(cli, "open_store", unreadable)
    secret_id = "88888888-8888-4888-8888-888888888888"
    assert cli.emit_secret(_config(tmp_path), secret_id, Pipe()) == cli.EXIT_UNREADABLE
    # Scrubbed: an earlier test in the same process may have wrapped a value this text holds.
    assert capsys.readouterr().err == f"emit-secret: {scrub('the root token file is empty')}\n"
