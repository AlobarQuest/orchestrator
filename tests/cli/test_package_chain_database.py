"""The approval read against the factory-events database chain (ADR-0059 amendment 7, FE-3).

With `FACTORY_EVENTS_READ_DSN` set, `package_sources` verifies the chain and reads its
`package.approved` events in one snapshot, through `orchestrator.factory_chain_read`, and refuses
on anything else; the file is never read. With only the append DSN set it refuses. With neither,
the file chain answers as before. No test here reaches a database: the walk against a real one is
in `test_package_chain_database_integration.py`, run by `scripts/check-integration.sh`.
"""

from __future__ import annotations

import json
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
READ_DSN = f"postgresql://factory_events_read:{SECRET}@127.0.0.1:1/factory_events"

_EVENT = {
    "action": "package.approved",
    "event_id": "evt-1",
    "timestamp": "2026-07-05T00:02:00Z",
    "source": {"ref": "pkg-1"},
    "evidence": [{"approved_hash": "h1", "revision": 1, "approver": "devon", "commit": "c1"}],
}
_APPROVAL = {"approver": "devon", "commit": "c1", "event_id": "evt-1"}


@pytest.fixture(autouse=True)
def _no_chain_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FACTORY_EVENTS_READ_DSN", raising=False)
    monkeypatch.delenv("FACTORY_EVENTS_APPEND_DSN", raising=False)
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path / "factory"))
    monkeypatch.setattr(package_sources, "_is_recognised_approver", lambda approver: True)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: True)
    monkeypatch.setattr(package_sources, "_security_standards_dir", lambda: tmp_path / "sec")


def _verify() -> VerifiedApproval:
    return package_sources._verify_current_approval(Path("."), "pkg-1", "h1", 1, _APPROVAL)


def _file_holds_the_match(tmp_path: Path) -> None:
    home = tmp_path / "factory"
    home.mkdir()
    (home / "events.jsonl").write_text(json.dumps({"event": _EVENT}) + "\n", encoding="utf-8")


def _forbid_the_file_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object) -> None:
        raise AssertionError("the file chain was consulted")

    monkeypatch.setattr(package_sources, "_verify_factory_chain", forbidden)
    monkeypatch.setattr(package_sources, "_iter_package_approval_events", forbidden)


def _child(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int, stdout: str, stderr: str = ""
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(package_sources.subprocess, "run", run)
    return calls


def _record(outcome: str, **fields: object) -> str:
    return json.dumps({"outcome": outcome, **fields}) + "\n"


# ---- Read DSN set: the database answers, and only it -------------------------------------------


def test_a_verified_walk_holding_the_approval_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    _forbid_the_file_chain(monkeypatch)
    calls = _child(monkeypatch, returncode=0, stdout=_record("verified", package_approved=[_EVENT]))

    assert _verify() == VerifiedApproval("devon", "2026-07-05T00:02:00Z", "evt-1", "c1")
    (call,) = calls
    assert call["argv"] == [sys.executable, "-m", "orchestrator.factory_chain_read"]
    assert call["env"]["FACTORY_EVENTS_READ_DSN"] == READ_DSN  # by environment, never argv
    assert str(Path(call["env"]["PYTHONPATH"].split(":")[0])).endswith("sec/src")
    assert call["capture_output"] is True


def test_a_verified_walk_without_the_approval_refuses_even_when_the_file_holds_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _file_holds_the_match(tmp_path)
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, stdout: str, cause: str
) -> None:
    _file_holds_the_match(tmp_path)
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    _child(monkeypatch, returncode=returncode, stdout=stdout, stderr=f"driver said {READ_DSN}")

    with pytest.raises(PackageSourceError, match=cause) as info:
        _verify()
    assert SECRET not in str(info.value)  # the child's stderr is never relayed


@pytest.mark.parametrize("stdout", ["", "not json\n"], ids=["empty", "not-json"])
def test_an_unreadable_answer_refuses_with_its_cause_chained(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
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
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    _forbid_the_file_chain(monkeypatch)

    def run(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(package_sources.subprocess, "run", run)

    with pytest.raises(PackageSourceError, match=type(error).__name__) as info:
        _verify()
    assert info.value.__cause__ is error


def test_a_missing_security_standards_checkout_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    monkeypatch.setattr(package_sources, "_security_standards_dir", lambda: None)
    _child(monkeypatch, returncode=0, stdout=_record("verified", package_approved=[_EVENT]))

    with pytest.raises(PackageSourceError, match="security-standards checkout is unavailable"):
        _verify()


def test_an_unreachable_database_refuses_through_the_real_child_without_the_dsn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The real subprocess and the real driver, against a closed port: no database is reached.

    FE-1's `store` is stood in by a module that is never called, since the connect fails first.
    """
    store = tmp_path / "sec" / "src" / "factory_events"
    store.mkdir(parents=True)
    (store / "__init__.py").write_text("", encoding="utf-8")
    (store / "store.py").write_text("def walk_records(records):\n    raise SystemExit(9)\n")
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    _forbid_the_file_chain(monkeypatch)

    with pytest.raises(PackageSourceError, match=r"could not be read \(OperationalError\)$"):
        _verify()
    captured = capfd.readouterr()
    assert captured.out == ""  # nothing reaches the stdout emit-intake-payload reserves
    assert SECRET not in captured.err


# ---- Append DSN only: refuse rather than verify one chain and match another --------------------


def test_the_append_dsn_alone_refuses_without_reading_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _file_holds_the_match(tmp_path)
    monkeypatch.setenv("FACTORY_EVENTS_APPEND_DSN", READ_DSN)
    _forbid_the_file_chain(monkeypatch)
    calls = _child(monkeypatch, returncode=0, stdout="")

    with pytest.raises(PackageSourceError, match="refusing to read the file chain$") as info:
        _verify()
    assert SECRET not in str(info.value)
    assert calls == []


# ---- Neither DSN set: the file chain, as before -----------------------------------------------


def test_with_neither_dsn_the_file_chain_answers_and_the_database_is_never_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _file_holds_the_match(tmp_path)
    verified_file: list[bool] = []
    monkeypatch.setattr(
        package_sources, "_verify_factory_chain", lambda: verified_file.append(True)
    )

    def no_database() -> None:
        raise AssertionError("the database chain was read")

    monkeypatch.setattr(package_sources, "_database_package_approval_events", no_database)

    assert _verify() == VerifiedApproval("devon", "2026-07-05T00:02:00Z", "evt-1", "c1")
    assert verified_file == [True]


def test_an_empty_read_dsn_is_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _file_holds_the_match(tmp_path)
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", "")
    monkeypatch.setattr(package_sources, "_verify_factory_chain", lambda: None)

    assert _verify().approval_event_id == "evt-1"


# ---- The child: one snapshot, one record, never the DSN --------------------------------------


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
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class _Psycopg:
        class IsolationLevel:
            REPEATABLE_READ = "repeatable-read"

        @staticmethod
        def connect(dsn: str, **kwargs: object) -> _FakeConnection:
            seen["dsn"] = dsn
            if connect_error is not None:
                raise connect_error
            return _FakeConnection(rows or [], seen)

    class _Store:
        @staticmethod
        def walk_records(records: Iterator[dict[str, Any]]) -> tuple[list[str], dict | None, int]:
            walked = list(records)
            return list(walk_errors or []), (walked[-1] if walked else None), len(walked)

    modules = {"psycopg": _Psycopg, "factory_events.store": _Store}
    monkeypatch.setattr(factory_chain_read, "import_module", modules.__getitem__)
    return seen


def _row(seq: int, event: dict[str, Any]) -> tuple[Any, ...]:
    return (seq, "p" * 64, f"{seq:064x}", event.get("event_id"), json.dumps(event))


def _main(capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str]:
    code = factory_chain_read.main()
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1  # one record, the whole of stdout
    return code, json.loads(lines[0]), out


def test_the_child_walks_in_one_read_only_repeatable_read_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    other = {**_EVENT, "action": "package.transitioned", "event_id": "evt-0"}
    seen = _fake_driver(monkeypatch, rows=[_row(1, other), _row(2, _EVENT)])

    code, record, out = _main(capsys)

    assert code == factory_chain_read.VERIFIED
    assert record["outcome"] == "verified"
    assert record["package_approved"] == [_EVENT]
    assert (record["seq"], record["events"], record["head"]) == (2, 2, f"{2:064x}"[:12])
    assert seen["read_only"] is True and seen["isolation_level"] == "repeatable-read"
    assert seen["query"] == factory_chain_read.WALK
    assert seen["dsn"] == READ_DSN and SECRET not in out


def test_the_child_reports_a_broken_walk_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    _fake_driver(monkeypatch, rows=[_row(1, _EVENT)], walk_errors=["seq 1: hash mismatch"])

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert record["outcome"] == "broken"
    assert "package_approved" not in record  # a match in a broken chain counts for nothing


def test_the_child_reports_a_driver_error_by_type_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", READ_DSN)
    _fake_driver(monkeypatch, connect_error=ConnectionError(f"cannot reach {READ_DSN}"))

    code, record, out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert record == {
        "logger": "orchestrator.factory_chain_read",
        "op": "read-package-approvals",
        "outcome": "failure",
        "error_type": "ConnectionError",
    }
    assert SECRET not in out


def test_the_child_without_a_read_dsn_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _fake_driver(monkeypatch)

    code, record, _out = _main(capsys)

    assert code == factory_chain_read.UNAVAILABLE
    assert record["error_type"] == "LookupError"
    assert "dsn" not in seen
