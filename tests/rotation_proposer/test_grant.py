"""The member record's `credential_rotation` grant gates every write the proposer makes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from rotation_proposer.alerts import finding_key, sync_alerts
from rotation_proposer.cli import EXIT_FINDINGS, EXIT_OK, run
from rotation_proposer.findings import Due, Unrecognised
from tests.rotation_proposer.test_pass import (
    AGE,
    CREDENTIAL,
    GENERATED,
    LEGACY,
    REQUESTED,
    _proposed_and_built,
)


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


def test_an_unreadable_record_writes_nothing_and_reports_an_error(rig, capsys) -> None:
    rig.granted = None
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert "unreachable" in capsys.readouterr().out
    ((outcomes, _, _),) = rig.synced
    assert [(o.credential_id, o.status) for o in outcomes] == [(CREDENTIAL, "error")]


def test_an_empty_record_is_unread_not_a_withdrawal(rig, capsys) -> None:
    rig.granted = []
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert "no rows" in capsys.readouterr().out


def test_an_unreadable_record_keeps_every_other_outcome(rig) -> None:
    """Kills: losing what the pass collected to one failed read, and its alert sync with it."""
    rig.granted = None
    second = Due(GENERATED, "github-pat-fine-grained", "age", "2026-07-01", "2026-07-01", True)
    rig.classes[GENERATED] = "github-pat-fine-grained"
    rig.due = [REQUESTED, LEGACY, second]
    rig.unrecognised = [Unrecognised("odd-one", "cred.unknown")]
    run(["--submit"])
    ((outcomes, _, _),) = rig.synced
    assert [o.status for o in outcomes] == ["unrecognised", "error", "legacy", "error"]


def test_an_unreadable_record_does_not_stop_a_finished_rotation_recording(rig, monkeypatch) -> None:
    recorded = _proposed_and_built(rig, monkeypatch)
    rig.granted = None
    assert run(["--submit"]) == EXIT_OK
    assert recorded == [CREDENTIAL]


def test_a_finished_rotation_records_with_its_grant_withdrawn(rig, capsys, monkeypatch) -> None:
    """Kills: the gate standing before `_act`. The rotation happened; infraops would report the
    credential due for ever."""
    recorded = _proposed_and_built(rig, monkeypatch)
    rig.granted = ["AlobarQuest/some-other-repo"]
    capsys.readouterr()
    assert run(["--submit"]) == EXIT_OK
    assert recorded == [CREDENTIAL]
    assert "ungranted" not in capsys.readouterr().out


def test_a_stranded_record_is_reported_while_the_tip_waits_and_the_grant_is_gone(
    rig, capsys
) -> None:
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    rig.due = [AGE]
    run(["--submit"])
    before = rig.acts()
    rig.granted = ["AlobarQuest/some-other-repo"]
    capsys.readouterr()
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == before
    out = capsys.readouterr().out
    assert "superseded" in out and "awaiting-approval" in out and "ungranted" not in out


def test_an_ungranted_proposal_still_reports_the_stranded_record(rig, capsys) -> None:
    run(["--submit"])
    rig.approve_by_name()
    run(["--submit"])
    rig.due = [AGE]
    run(["--submit"])
    rig.approve_by_name()
    before = rig.acts()
    rig.granted = ["AlobarQuest/some-other-repo"]
    capsys.readouterr()
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == before
    out = capsys.readouterr().out
    assert "ungranted" in out and "superseded" in out


def test_an_ungranted_approved_revision_is_not_proposed(rig) -> None:
    run(["--submit"])
    rig.approve_by_name()
    before = rig.acts()
    rig.granted = ["AlobarQuest/some-other-repo"]
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == before


def test_an_ungranted_credential_with_no_package_is_not_generated(rig, capsys) -> None:
    rig.due = [Due(GENERATED, "github-pat-fine-grained", "age", "2026-07-01", "2026-07-01", True)]
    rig.classes[GENERATED] = "github-pat-fine-grained"
    rig.granted = ["AlobarQuest/some-other-repo"]
    assert run(["--submit"]) == EXIT_FINDINGS
    assert rig.acts() == ([], 0, 0, 0)
    assert "ungranted" in capsys.readouterr().out


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


def test_a_pass_that_writes_nothing_reads_no_members(rig) -> None:
    run(["--submit"])
    reads = rig.member_reads
    assert run(["--submit"]) == EXIT_OK  # the revision waits for a named human
    assert rig.member_reads == reads


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
