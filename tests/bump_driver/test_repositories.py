"""`bump-driver repositories`: what the lane's container mints its GitHub read token for."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bump_driver import cli
from sds_members.client import Member, MembersUnreadable

ENVIRON = {"ORCHESTRATOR_API_TOKEN": "system", "ORCHESTRATOR_API_CREDENTIAL_KEY_ID": "key"}


def _member(name: str, grant: str = "granted", *, state: str = "member") -> Member:
    return Member(
        repository=name,
        owner="AlobarQuest",
        state=state,
        working_copy=None,
        machine_local=False,
        apps=(),
        grants={"dependency_updates": grant},
    )


def _read(members: list[Member], seen: dict[str, Any]):
    def read(**kwargs: Any) -> list[Member]:
        seen.update(kwargs)
        return members

    return read


def test_it_prints_the_clones_and_each_dependency_updates_grant_sorted_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    members = [
        _member("AlobarQuest/change-manager"),
        # A case-only repeat of a clone is printed once, in the clones' spelling.
        _member("alobarquest/Intent-Packages"),
        _member("AlobarQuest/brain", "none"),
        _member("AlobarQuest/retired", state="retired"),
    ]
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "read_members", _read(members, seen))
    assert cli.repositories(ENVIRON) == 0
    assert capsys.readouterr().out.splitlines() == [
        "AlobarQuest/change-manager",
        "AlobarQuest/code-standards",
        "AlobarQuest/intent-packages",
        "AlobarQuest/project-standards",
        "AlobarQuest/security-standards",
    ]
    # The member record is read with the SYSTEM bearer, at the program's default URL.
    assert seen == {"token": "system", "credential_key_id": "key", "base_url": cli.ORCHESTRATOR_URL}


def test_an_unreadable_member_record_prints_nothing_and_is_a_failed_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreadable(**kwargs: Any) -> list[Member]:
        raise MembersUnreadable("the orchestrator answered 503")

    monkeypatch.setattr(cli, "read_members", unreadable)
    assert cli.repositories(ENVIRON) == cli.INCOMPLETE == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the member record could not be read: the orchestrator answered 503" in captured.err


@pytest.mark.parametrize("missing", sorted(ENVIRON))
def test_a_missing_credential_prints_nothing_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    monkeypatch.setattr(cli, "read_members", lambda **k: pytest.fail("read without a credential"))
    environ = {name: value for name, value in ENVIRON.items() if name != missing}
    assert cli.repositories(environ) == 1
    assert capsys.readouterr().out == ""


def test_main_routes_the_subcommand_and_refuses_extra_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "repositories", lambda environ: 0)
    assert cli.main(["repositories"]) == 0
    assert cli.main(["repositories", "--dry-run"]) == 1
    assert "usage: bump-driver repositories" in capsys.readouterr().err


def test_the_console_script_routes_the_subcommand() -> None:
    """Through the installed entry point, with no credential: refused, nothing on stdout."""
    script = Path(sys.executable).parent / "bump-driver"
    done = subprocess.run(
        [str(script), "repositories"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    assert done.stdout == ""
    assert "ORCHESTRATOR_API_TOKEN and its key id are required" in done.stderr
