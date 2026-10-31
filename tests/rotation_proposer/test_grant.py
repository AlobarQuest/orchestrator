"""The member record's `credential_rotation` grant gates every write the proposer makes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from rotation_proposer.alerts import finding_key, sync_alerts
from rotation_proposer.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from tests.rotation_proposer.test_pass import CREDENTIAL, LEGACY


def test_an_ungranted_credential_writes_nothing(rig, capsys) -> None:
    """Kills: checking the grant after the observation or the revision, which cannot be undone."""
    rig.granted = ["AlobarQuest/some-other-repo"]
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert rig.spine.rows == {}
    out = capsys.readouterr().out
    assert "ungranted" in out and "AlobarQuest/infraops-mcp-server" in out


def test_a_granted_credential_proceeds(rig) -> None:
    rig.granted = ["alobarquest/INFRAOPS-mcp-server"]  # case-folded
    assert run(["--submit"]) == EXIT_OK
    assert rig.acts() == (["revise", "transition", "hash"], 1, 0, 1)


def test_an_unreadable_record_exits_2_and_writes_nothing(rig, capsys) -> None:
    rig.granted = None
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.acts() == ([], 0, 0, 0)
    assert "unreachable" in capsys.readouterr().err


def test_an_empty_record_is_unread_not_a_withdrawal(rig) -> None:
    rig.granted = []
    assert run(["--submit"]) == EXIT_UNUSABLE
    assert rig.acts() == ([], 0, 0, 0)


def test_nothing_due_reads_no_members(rig) -> None:
    rig.due = []
    assert run(["--submit"]) == EXIT_OK
    assert rig.member_reads == 0


def test_a_credential_infraops_still_rotates_reads_no_members(rig) -> None:
    rig.due = [LEGACY]
    assert run(["--submit"]) == EXIT_OK
    assert rig.member_reads == 0


def test_the_record_is_read_once_per_pass(rig) -> None:
    run(["--submit"])
    assert rig.member_reads == 1


def test_a_dry_run_says_the_grant_was_not_read(rig, capsys) -> None:
    rig.granted = None
    assert run([]) == EXIT_OK
    assert rig.member_reads == 0
    assert "grants were not checked" in capsys.readouterr().out


def test_ungranted_raises_its_finding_alert(rig, tmp_path) -> None:
    """The pass's own outcomes, through the real alert layer, fire the keyed finding."""
    rig.granted = ["AlobarQuest/some-other-repo"]
    run(["--submit"])
    ((outcomes, _, _),) = rig.synced
    sent: list[dict] = []

    def hub(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202, json={})

    sync_alerts(
        outcomes,
        token="t",
        state_dir=tmp_path / "alerts",
        now=datetime(2026, 11, 1, tzinfo=UTC),
        transport=httpx.MockTransport(hub),
    )
    assert [(row["key"], row["status"]) for row in sent] == [
        (finding_key(CREDENTIAL, "ungranted"), "firing")
    ]
