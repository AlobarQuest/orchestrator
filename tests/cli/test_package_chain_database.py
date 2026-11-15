"""The approval read against the factory-events database chain (ADR-0059 amendment 7, FE-3).

With `FACTORY_EVENTS_READ_PASSWORD` set, `package_sources` verifies the chain and reads its
`package.approved` events in one snapshot, through `orchestrator.factory_chain_read`, and refuses
on anything else. With only the append password set it refuses, and a set retired whole-DSN
variable refuses by name (ADR-0055 amendment 26). With no read password the read refuses by name:
the database is the chain of record, and no events file is read (FE-5). No test here reaches a
database: the walk against a real one is in `test_package_chain_database_integration.py`, run by
`scripts/check-integration.sh`.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import orchestrator.factory_chain_read as factory_chain_read
import orchestrator.package_sources as package_sources
from orchestrator.package_sources import PackageSourceError, VerifiedApproval

SECRET = "pw-must-not-leak-7f3a"
SETTINGS = {
    "FACTORY_EVENTS_DB_HOST": "127.0.0.1",
    "FACTORY_EVENTS_DB_PORT": "1",
    "FACTORY_EVENTS_DB_NAME": "factory_events",
}
RETIRED = ("FACTORY_EVENTS_APPEND_DSN", "FACTORY_EVENTS_READ_DSN", "FACTORY_EVENTS_IMPORT_DSN")
CHAIN_VARS = (
    "FACTORY_EVENTS_READ_PASSWORD",
    "FACTORY_EVENTS_APPEND_PASSWORD",
    *SETTINGS,
    *RETIRED,
)

_EVENT = {
    "action": "package.approved",
    "event_id": "evt-1",
    "timestamp": "2026-07-05T00:02:00Z",
    "source": {"ref": "pkg-1"},
    "evidence": [{"approved_hash": "h1", "revision": 1, "approver": "devon", "commit": "c1"}],
}
_APPROVAL = {"approver": "devon", "commit": "c1", "event_id": "evt-1"}


@pytest.fixture(autouse=True)
def _no_chain_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in CHAIN_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(package_sources, "_is_recognised_approver", lambda approver: True)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: True)
    monkeypatch.setattr(package_sources, "_security_standards_dir", lambda: tmp_path / "sec")


@pytest.fixture(autouse=True)
def _no_password_in_any_log_record(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Every test here: no log record, at any level, carries the placeholder password."""
    caplog.set_level(logging.DEBUG)
    yield
    # caplog.text is per phase, so at teardown read the call phase's records.
    assert not any(SECRET in r.getMessage() for r in caplog.get_records("call"))


def _streams(capfd: pytest.CaptureFixture[str]) -> str:
    captured = capfd.readouterr()
    return captured.out + captured.err


def _read_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The read role's password and the three settings, as a holder's environment sets them."""
    monkeypatch.setenv("FACTORY_EVENTS_READ_PASSWORD", SECRET)
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)


def _verify() -> VerifiedApproval:
    return package_sources._verify_current_approval(Path("."), "pkg-1", "h1", 1, _APPROVAL)


def _child(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int, stdout: str, stderr: str = ""
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "factory_events" not in argv, "the file chain's `factory_events verify` was run"
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(package_sources.subprocess, "run", run)
    return calls


def _record(outcome: str, **fields: object) -> str:
    return json.dumps({"outcome": outcome, **fields}) + "\n"


# ---- Read password set: the database answers, and only it --------------------------------------


def test_a_verified_walk_holding_the_approval_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    _read_chain(monkeypatch)
    calls = _child(monkeypatch, returncode=0, stdout=_record("verified", package_approved=[_EVENT]))

    assert _verify() == VerifiedApproval("devon", "2026-07-05T00:02:00Z", "evt-1", "c1")
    (call,) = calls
    assert call["argv"] == [sys.executable, "-m", "orchestrator.factory_chain_read"]
    assert call["env"]["FACTORY_EVENTS_READ_PASSWORD"] == SECRET  # by environment, never argv
    assert all(call["env"][name] == value for name, value in SETTINGS.items())
    assert str(Path(call["env"]["PYTHONPATH"].split(":")[0])).endswith("sec/src")
    assert call["capture_output"] is True


def test_a_verified_walk_without_the_approval_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _read_chain(monkeypatch)
    _child(monkeypatch, returncode=0, stdout=_record("verified", package_approved=[]))

    with pytest.raises(PackageSourceError, match="no matching package.approved event"):
        _verify()


@pytest.mark.parametrize(
    ("returncode", "stdout", "cause"),
    [
        (2, _record("broken", seq=7), "database chain did not verify past seq 7$"),
        (2, _record("failure", error_type="OperationalError"), r"\(OperationalError\)$"),
        (2, _record("failure", error_type="not an identifier"), "could not be read$"),
        (2, _record("verified", package_approved=[_EVENT]), "could not be read$"),
        (0, _record("broken", seq=1), "did not verify"),
        (0, _record("verified", package_approved="nope"), "gave no answer$"),
        (0, json.dumps([1]) + "\n", "gave no answer$"),
    ],
    ids=[
        "broken",
        "failure",
        "unnamed-failure",
        "exit-disagrees",
        "outcome-disagrees",
        "events-not-a-list",
        "record-not-a-mapping",
    ],
)
def test_anything_but_a_verified_walk_refuses(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    returncode: int,
    stdout: str,
    cause: str,
) -> None:
    _read_chain(monkeypatch)
    _child(monkeypatch, returncode=returncode, stdout=stdout, stderr=f"driver said {SECRET}")

    with pytest.raises(PackageSourceError, match=cause) as info:
        _verify()
    assert SECRET not in str(info.value)  # the child's stderr is never relayed
    assert SECRET not in _streams(capfd)


@pytest.mark.parametrize("stdout", ["", "not json\n"], ids=["empty", "not-json"])
def test_an_unreadable_answer_refuses_with_its_cause_chained(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    _read_chain(monkeypatch)
    _child(monkeypatch, returncode=0, stdout=stdout)

    with pytest.raises(PackageSourceError, match="gave no answer$") as info:
        _verify()
    assert isinstance(info.value.__cause__, IndexError | json.JSONDecodeError)


@pytest.mark.parametrize(
    "error",
    [OSError("no interpreter"), subprocess.TimeoutExpired(cmd="x", timeout=30)],
    ids=["os-error", "timeout"],
)
def test_a_read_that_cannot_run_refuses_with_its_cause_chained(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _read_chain(monkeypatch)

    def run(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(package_sources.subprocess, "run", run)

    with pytest.raises(PackageSourceError, match=type(error).__name__) as info:
        _verify()
    assert info.value.__cause__ is error


def test_a_missing_security_standards_checkout_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _read_chain(monkeypatch)
    monkeypatch.setattr(package_sources, "_security_standards_dir", lambda: None)
    _child(monkeypatch, returncode=0, stdout=_record("verified", package_approved=[_EVENT]))

    with pytest.raises(PackageSourceError, match="security-standards checkout is unavailable"):
        _verify()


_CHAIN_STUB = """\
import os

READ_ROLE = "factory_events_read"


class _Params:
    def __init__(self, role):
        self._kwargs = {
            "host": os.environ["FACTORY_EVENTS_DB_HOST"],
            "port": os.environ["FACTORY_EVENTS_DB_PORT"],
            "dbname": os.environ["FACTORY_EVENTS_DB_NAME"],
            "user": role,
            "password": os.environ["FACTORY_EVENTS_READ_PASSWORD"],
        }

    def as_kwargs(self):
        return dict(self._kwargs)


def connection_params(role):
    return _Params(role)
"""


def test_an_unreachable_database_refuses_through_the_real_child_without_the_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The real subprocess and the real driver, against a closed port: no database is reached.

    FE-1's `store` is stood in by a module that is never called, since the connect fails first,
    and its `chain` by one that builds the read role's parameters from the same variables.
    """
    store = tmp_path / "sec" / "src" / "factory_events"
    store.mkdir(parents=True)
    (store / "__init__.py").write_text("", encoding="utf-8")
    (store / "store.py").write_text("def walk_records(records):\n    raise SystemExit(9)\n")
    (store / "chain.py").write_text(_CHAIN_STUB, encoding="utf-8")
    _read_chain(monkeypatch)

    with pytest.raises(
        PackageSourceError, match=r"could not be read \(OperationalError\)$"
    ) as info:
        _verify()
    captured = capfd.readouterr()
    assert captured.out == ""  # nothing reaches the stdout emit-intake-payload reserves
    assert SECRET not in captured.err
    assert SECRET not in str(info.value)


# ---- Append password only: the append role can't read the chain --------------------------------


def test_the_append_password_alone_refuses_without_reading_the_chain(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_APPEND_PASSWORD", SECRET)
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    calls = _child(monkeypatch, returncode=0, stdout="")

    with pytest.raises(PackageSourceError, match="the append role can't read the chain$") as info:
        _verify()
    assert SECRET not in str(info.value)
    assert SECRET not in _streams(capfd)
    assert calls == []


# ---- A retired whole-DSN variable: refused by name, whatever else is set -----------------------


@pytest.mark.parametrize("retired", RETIRED)
@pytest.mark.parametrize("password", [None, "FACTORY_EVENTS_READ_PASSWORD"], ids=["unset", "read"])
def test_a_set_retired_variable_refuses_by_name_without_reading_either_chain(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    retired: str,
    password: str | None,
) -> None:
    if password:
        _read_chain(monkeypatch)
    monkeypatch.setenv(retired, f"postgresql://factory_events_read:{SECRET}@127.0.0.1:1/x")
    calls = _child(monkeypatch, returncode=0, stdout="")

    with pytest.raises(PackageSourceError, match=f"{retired} is retired") as info:
        _verify()
    assert SECRET not in str(info.value)
    assert SECRET not in _streams(capfd)
    assert calls == []


# ---- No read password: refused by name, and neither chain is read ------------------------------

_UNSET_REFUSAL = (
    "^approval verification failed: FACTORY_EVENTS_READ_PASSWORD is unset: "
    "the factory-events database is the chain$"
)


def test_with_no_read_password_the_read_refuses_by_name_and_reads_neither_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _child(monkeypatch, returncode=0, stdout="")

    with pytest.raises(PackageSourceError, match=_UNSET_REFUSAL):
        _verify()
    assert calls == []


@pytest.mark.parametrize("name", ["FACTORY_EVENTS_READ_PASSWORD", *RETIRED])
def test_an_empty_chain_variable_is_unset(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "")
    calls = _child(monkeypatch, returncode=0, stdout="")

    with pytest.raises(PackageSourceError, match=_UNSET_REFUSAL):
        _verify()
    assert calls == []


# ---- The child: one snapshot, one record, never the password ---------------------------------


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]], seen: dict[str, Any]) -> None:
        self._rows = rows
        self._seen = seen
        self.read_only = False
        self.isolation_level: object = None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @contextmanager
    def transaction(self) -> Generator[None]:
        self._seen["read_only"] = self.read_only
        self._seen["isolation_level"] = self.isolation_level
        yield

    @contextmanager
    def cursor(self, name: str) -> Generator[_FakeCursor]:
        yield _FakeCursor(self._rows, self._seen)


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]], seen: dict[str, Any]) -> None:
        self._rows = rows
        self._seen = seen

    def execute(self, query: str) -> None:
        self._seen["query"] = query

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self._rows)


def _fake_driver(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: list[tuple[Any, ...]] | None = None,
    walk_errors: list[str] | None = None,
    connect_error: Exception | None = None,
    chain_refusal: Exception | None = None,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class _Psycopg:
        class IsolationLevel:
            REPEATABLE_READ = "repeatable-read"

        @staticmethod
        def connect(**kwargs: object) -> _FakeConnection:
            seen["connect"] = kwargs
            if connect_error is not None:
                raise connect_error
            return _FakeConnection(rows or [], seen)

    class _Store:
        @staticmethod
        def walk_records(records: Iterator[dict[str, Any]]) -> tuple[list[str], dict | None, int]:
            walked = list(records)
            return list(walk_errors or []), (walked[-1] if walked else None), len(walked)

    class _Params:
        def __init__(self, role: str) -> None:
            self._role = role

        def __repr__(self) -> str:
            return f"_Params(user={self._role!r})"  # as FE-1's: the password stays out

        def as_kwargs(self) -> dict[str, str]:
            return {"user": self._role, "password": os.environ["FACTORY_EVENTS_READ_PASSWORD"]}

    class _Chain:
        READ_ROLE = "factory_events_read"
        APPEND_ROLE = "factory_events_append"

        @staticmethod
        def connection_params(role: str) -> _Params:
            seen["role"] = role
            if chain_refusal is not None:
                raise chain_refusal
            return _Params(role)

    modules = {"psycopg": _Psycopg, "factory_events.store": _Store, "factory_events.chain": _Chain}
    monkeypatch.setattr(factory_chain_read, "import_module", modules.__getitem__)
    return seen


def _row(seq: int, event: dict[str, Any]) -> tuple[Any, ...]:
    return (seq, "p" * 64, f"{seq:064x}", event.get("event_id"), json.dumps(event))


def _main(capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str]:
    code = factory_chain_read.main()
    captured = capsys.readouterr()
    out = captured.out
    assert SECRET not in out + captured.err
    lines = out.strip().splitlines()
    assert len(lines) == 1  # one record, the whole of stdout
    return code, json.loads(lines[0]), out


def test_the_child_walks_in_one_read_only_repeatable_read_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    other = {**_EVENT, "action": "package.transitioned", "event_id": "evt-0"}
    seen = _fake_driver(monkeypatch, rows=[_row(1, other), _row(2, _EVENT)])

    code, record, out = _main(capsys)

    assert code == factory_chain_read.VERIFIED
    assert record["outcome"] == "verified"
    assert record["package_approved"] == [_EVENT]
    assert (record["seq"], record["events"], record["head"]) == (2, 2, f"{2:064x}"[:12])
    assert seen["read_only"] is True and seen["isolation_level"] == "repeatable-read"
    assert seen["query"] == factory_chain_read.WALK
    assert seen["role"] == "factory_events_read"  # strictly the read role
    assert seen["connect"]["password"] == SECRET and SECRET not in out
    assert seen["connect"]["connect_timeout"] == factory_chain_read.CONNECT_TIMEOUT_SECONDS


def test_the_child_reports_a_broken_walk_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    _fake_driver(monkeypatch, rows=[_row(1, _EVENT)], walk_errors=["seq 1: hash mismatch"])

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert record["outcome"] == "broken"
    assert "package_approved" not in record  # a match in a broken chain counts for nothing


def test_the_child_reports_a_driver_error_by_type_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _read_chain(monkeypatch)
    _fake_driver(monkeypatch, connect_error=ConnectionError(f"cannot connect with {SECRET}"))

    code, record, out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert record == {
        "logger": "orchestrator.factory_chain_read",
        "op": "read-package-approvals",
        "outcome": "failure",
        "error_type": "ConnectionError",
    }
    assert SECRET not in out


class ChainError(RuntimeError):
    """Stands in for `factory_events.db.ChainError`, reported by its type name."""


def test_a_chain_refusal_is_unavailable_before_any_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing setting or a set retired variable: FE-1's `chain` refuses by name, and the
    record carries only the type, so nothing the refusal says reaches stdout."""
    seen = _fake_driver(
        monkeypatch, chain_refusal=ChainError(f"FACTORY_EVENTS_DB_HOST is not set {SECRET}")
    )

    code, record, out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert record["outcome"] == "failure" and record["error_type"] == "ChainError"
    assert seen["role"] == "factory_events_read"
    assert "connect" not in seen
    assert SECRET not in out


# ---- Through the entry point the carrier runs: its stdout is the payload, only ---------------

_FIXTURE = "tests/fixtures/intent-packages/ws32-approved-software"
_FIXTURE_EVENT = {
    "action": "package.approved",
    "event_id": "22222222-2222-2222-2222-222222222222",
    "timestamp": "2026-07-05T00:02:00Z",
    "source": {"ref": "ws32-approved-software"},
    "evidence": [
        {
            "approved_hash": "bfcf35c540a540efcac4eb4095b9dbf33529e39361a03a21d43b64c96dd054b2",
            "revision": 1,
            "approver": "devon",
            "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
    ],
}


def _emit_intake_payload() -> Any:
    from typer.testing import CliRunner

    from orchestrator.cli import app

    args = [
        "emit-intake-payload",
        _FIXTURE,
        "--source-repository",
        "AlobarQuest/intent-packages",
        "--idempotency-key",
        "k-1",
        "--json",
    ]
    return CliRunner().invoke(app, args)


def test_emit_intake_payload_under_the_read_password_prints_only_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _read_chain(monkeypatch)
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    record = _record("verified", seq=9, head="abc", package_approved=[_FIXTURE_EVENT])
    _child(monkeypatch, returncode=0, stdout=record)

    result = _emit_intake_payload()

    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # the whole of stdout, as the carrier parses it
    assert payload["package_id"] == "ws32-approved-software"
    assert SECRET not in result.stdout + result.stderr


def test_emit_intake_payload_under_the_read_password_refuses_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _read_chain(monkeypatch)
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    _child(
        monkeypatch,
        returncode=2,
        stdout=_record("failure", error_type="OperationalError"),
        stderr=f"driver said {SECRET}",
    )

    result = _emit_intake_payload()

    assert result.exit_code != 0
    assert "could not be read (OperationalError)" in result.stdout + result.stderr
    assert SECRET not in result.stdout + result.stderr
    assert '"outcome"' not in result.stdout  # the child's record is never relayed


def test_emit_intake_payload_with_no_chain_variable_refuses_and_opens_no_events_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FE-3's deferred refusal: with no read password, intake refuses by name.

    The scratch home holds a well-formed events file with the matching approval, so the base's
    file path would emit the payload.
    """
    home = tmp_path / "factory-home"
    home.mkdir()
    events_file = home / "events.jsonl"
    events_file.write_text(json.dumps({"event": _FIXTURE_EVENT}) + "\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(home))
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    calls = _child(monkeypatch, returncode=0, stdout="")
    opened: list[Path] = []
    real_open = Path.open

    def recording_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.resolve())
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)

    result = _emit_intake_payload()

    assert result.exit_code != 0
    assert "FACTORY_EVENTS_READ_PASSWORD" in result.stdout + result.stderr
    assert '"package_id"' not in result.stdout
    assert events_file.resolve() not in opened
    assert calls == []
