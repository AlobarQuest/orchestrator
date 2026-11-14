"""The approval read against FE-1's database chain on a disposable Postgres (FE-3).

Run by `scripts/check-integration.sh`, never by `make check`. `FACTORY_TEST_DSN` names a superuser
on a throwaway cluster; each test gets its own new database, set up by FE-1's real roles script
(`factory_events.db_setup`), and appends through FE-1's real `factory_events emit`, both under
this interpreter with `IT_SECURITY_STANDARDS_DIR`'s `src` on the path, as `package_sources` runs
its chain read. The read is `package_sources`' own, through the real `factory_chain_read` child.

Each role connects from its password and the three settings (ADR-0055 amendment 26), built from
`FACTORY_TEST_DSN`'s host, port and database; no test sets a whole DSN for either role.
`FACTORY_EVENTS_OWNER_DSN` is FE-4's one-time owner act's, not a role's (amendment 26 item 9).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

import orchestrator.package_sources as package_sources
from orchestrator.package_sources import PackageSourceError, VerifiedApproval

pytestmark = pytest.mark.integration

ROLE_PASSWORD = secrets.token_hex(16)  # throwaway, for this run's disposable cluster only
EVIDENCE = {"approved_hash": "h1", "revision": 1, "approver": "devon", "commit": "c1"}


CHAIN_VARS = (
    "FACTORY_EVENTS_READ_PASSWORD",
    "FACTORY_EVENTS_APPEND_PASSWORD",
    "FACTORY_EVENTS_DB_HOST",
    "FACTORY_EVENTS_DB_PORT",
    "FACTORY_EVENTS_DB_NAME",
    "FACTORY_EVENTS_APPEND_DSN",
    "FACTORY_EVENTS_READ_DSN",
    "FACTORY_EVENTS_IMPORT_DSN",
)


class Dsn(str):
    """A DSN whose repr is redacted, so a failing assert can't print its password."""

    def __repr__(self) -> str:
        return "'<dsn redacted>'"


def _psycopg() -> Any:
    return import_module("psycopg")


def _conninfo(base: str, **overrides: str) -> Dsn:
    conninfo = import_module("psycopg.conninfo")
    params = conninfo.conninfo_to_dict(base)
    params.update(overrides)
    return Dsn(conninfo.make_conninfo(**params))


@dataclass(frozen=True)
class ChainDb:
    """The owner's DSN, for fixtures, and the three settings every role connects with."""

    owner: Dsn
    host: str
    port: str
    dbname: str

    def settings(self, **overrides: str) -> dict[str, str]:
        settings = {
            "FACTORY_EVENTS_DB_HOST": self.host,
            "FACTORY_EVENTS_DB_PORT": self.port,
            "FACTORY_EVENTS_DB_NAME": self.dbname,
        }
        return {**settings, **overrides}

    def append_env(self) -> dict[str, str]:
        return {"FACTORY_EVENTS_APPEND_PASSWORD": ROLE_PASSWORD, **self.settings()}


def _read_chain(monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb, **overrides: str) -> None:
    """The read role's password and the three settings, each overridable by its variable."""
    env = {"FACTORY_EVENTS_READ_PASSWORD": ROLE_PASSWORD, **chain_db.settings(), **overrides}
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _sec_src() -> Path:
    return Path(os.environ["SECURITY_STANDARDS_DIR"]) / "src"


def _events(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """FE-1's `factory_events` under this interpreter (fixtures only)."""
    full_env = {**os.environ, "PYTHONPATH": str(_sec_src()), **env}
    for name in CHAIN_VARS:
        if name not in env:
            full_env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", *args], capture_output=True, text=True, env=full_env, timeout=60
    )


@pytest.fixture(autouse=True)
def _security_standards_with_fe1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sec = os.environ.get("IT_SECURITY_STANDARDS_DIR")
    assert sec and Path(sec, "src/factory_events/db.py").is_file(), (
        "IT_SECURITY_STANDARDS_DIR must name a checkout with FE-1 (scripts/check-integration.sh)"
    )
    monkeypatch.setenv("SECURITY_STANDARDS_DIR", sec)
    monkeypatch.setenv("FACTORY_EVENTS_HOME", str(tmp_path / "factory"))
    for name in CHAIN_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(package_sources, "_is_recognised_approver", lambda approver: True)
    monkeypatch.setattr(package_sources, "_verify_with_intent_packages_cli", lambda path: True)


@pytest.fixture()
def chain_db() -> Iterator[ChainDb]:
    base = os.environ.get("FACTORY_TEST_DSN")
    assert base, "FACTORY_TEST_DSN is required (scripts/check-integration.sh sets one)"
    psycopg = _psycopg()
    sql = import_module("psycopg.sql")
    name = f"orch_chain_{secrets.token_hex(6)}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    owner = _conninfo(base, dbname=name)
    try:
        setup = _events("factory_events.db_setup", FACTORY_EVENTS_OWNER_DSN=owner)
        assert setup.returncode == 0, "factory_events.db_setup failed"
        with psycopg.connect(owner, autocommit=True) as conn:
            for role in ("factory_events_append", "factory_events_read"):
                conn.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(ROLE_PASSWORD)
                    )
                )
        parts = import_module("psycopg.conninfo").conninfo_to_dict(base)
        yield ChainDb(
            owner=owner,
            host=str(parts.get("host", "127.0.0.1")),
            port=str(parts.get("port", "5432")),
            dbname=name,
        )
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _emit(action: str, evidence: dict[str, Any], **env: str) -> str:
    result = _events(
        "factory_events",
        "emit",
        "--actor",
        "claude-code-interactive",
        "--action",
        action,
        "--result",
        "success",
        "--ref",
        "pkg-1",
        "--evidence-json",
        json.dumps(evidence),
        **env,
    )
    assert result.returncode == 0, "factory_events emit failed"
    return result.stdout.strip().splitlines()[-1]


def _approve(chain_db: ChainDb | None, evidence: dict[str, Any] = EVIDENCE) -> str:
    """Append through FE-1's real `emit`: to the database under the append role, else the file."""
    env = chain_db.append_env() if chain_db else {}
    _emit("package.transitioned", {"to": "ready_for_review"}, **env)
    return _emit("package.approved", evidence, **env)


def _verify(event_id: str) -> VerifiedApproval:
    approval = {"approver": "devon", "commit": "c1", "event_id": event_id}
    return package_sources._verify_current_approval(Path("."), "pkg-1", "h1", 1, approval)


def _refusal_text(info: pytest.ExceptionInfo[PackageSourceError], capfd: Any) -> str:
    captured = capfd.readouterr()
    return str(info.value) + captured.out + captured.err


def test_a_matching_approval_in_the_database_verifies(
    monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb, capfd: pytest.CaptureFixture[str]
) -> None:
    event_id = _approve(chain_db)
    _read_chain(monkeypatch, chain_db)

    verified = _verify(event_id)

    assert verified.approval_event_id == event_id
    assert (verified.approved_by, verified.approval_ledger_commit) == ("devon", "c1")
    assert capfd.readouterr().out == ""  # the payload's stdout stays the payload's


def test_no_matching_approval_refuses(monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb) -> None:
    event_id = _approve(chain_db, {**EVIDENCE, "approved_hash": "h-other"})
    _read_chain(monkeypatch, chain_db)

    with pytest.raises(PackageSourceError, match="no matching package.approved event"):
        _verify(event_id)


def test_a_broken_chain_refuses_even_with_the_match_present(
    monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb
) -> None:
    event_id = _approve(chain_db)
    with _psycopg().connect(chain_db.owner) as conn:  # only the owner can lift the trigger
        conn.execute("ALTER TABLE factory_events DISABLE TRIGGER factory_events_no_update_delete")
        conn.execute(
            "UPDATE factory_events SET event = replace(event, 'ready_for_review', 'x') "
            "WHERE seq = 1"
        )
    _read_chain(monkeypatch, chain_db)

    with pytest.raises(PackageSourceError, match="did not verify past seq 0$"):
        _verify(event_id)


def test_an_unreachable_database_refuses_without_the_password(
    monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb, capfd: pytest.CaptureFixture[str]
) -> None:
    event_id = _approve(chain_db)
    _read_chain(monkeypatch, chain_db, FACTORY_EVENTS_DB_PORT="1")

    with pytest.raises(
        PackageSourceError, match=r"could not be read \(OperationalError\)$"
    ) as info:
        _verify(event_id)
    assert ROLE_PASSWORD not in _refusal_text(info, capfd)


def test_a_refused_role_refuses_without_the_password(
    monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb, capfd: pytest.CaptureFixture[str]
) -> None:
    event_id = _approve(chain_db)
    _read_chain(monkeypatch, chain_db, FACTORY_EVENTS_READ_PASSWORD="wrong")

    with pytest.raises(PackageSourceError, match="could not be read") as info:
        _verify(event_id)
    assert ROLE_PASSWORD not in _refusal_text(info, capfd)


def test_the_read_password_set_never_reads_the_file_that_holds_the_match(
    monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb, tmp_path: Path
) -> None:
    event_id = _approve(None)  # FE-1's real file append: the file holds the approval
    assert (tmp_path / "factory" / "events.jsonl").is_file()
    assert _verify(event_id).approval_event_id == event_id  # no password set: the file answers

    _read_chain(monkeypatch, chain_db)
    with pytest.raises(PackageSourceError, match="no matching package.approved event"):
        _verify(event_id)


def test_with_no_password_the_file_chain_verifies_as_before(tmp_path: Path) -> None:
    event_id = _approve(None)

    assert _verify(event_id).approval_event_id == event_id
    (tmp_path / "factory" / "events.jsonl").write_text("not a chain\n", encoding="utf-8")
    with pytest.raises(PackageSourceError, match="factory event chain did not verify$"):
        _verify(event_id)


@pytest.mark.parametrize(
    "missing", ["FACTORY_EVENTS_DB_HOST", "FACTORY_EVENTS_DB_PORT", "FACTORY_EVENTS_DB_NAME"]
)
def test_a_missing_setting_refuses_through_fe1s_chain_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
    chain_db: ChainDb,
    capfd: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    """FE-1's real `chain.connection_params` refuses it as ChainError, before any connection."""
    event_id = _approve(chain_db)
    _read_chain(monkeypatch, chain_db)
    monkeypatch.delenv(missing)

    with pytest.raises(PackageSourceError, match=r"could not be read \(ChainError\)$") as info:
        _verify(event_id)
    assert ROLE_PASSWORD not in _refusal_text(info, capfd)


def test_a_set_retired_variable_refuses_by_name_beside_a_working_read(
    monkeypatch: pytest.MonkeyPatch, chain_db: ChainDb, capfd: pytest.CaptureFixture[str]
) -> None:
    event_id = _approve(chain_db)
    _read_chain(monkeypatch, chain_db)
    assert _verify(event_id).approval_event_id == event_id
    retired = f"postgresql://factory_events_read:{ROLE_PASSWORD}@{chain_db.host}/{chain_db.dbname}"
    monkeypatch.setenv("FACTORY_EVENTS_READ_DSN", retired)

    with pytest.raises(PackageSourceError, match="FACTORY_EVENTS_READ_DSN is retired") as info:
        _verify(event_id)
    assert ROLE_PASSWORD not in _refusal_text(info, capfd)
