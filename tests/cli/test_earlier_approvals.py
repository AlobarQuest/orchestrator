"""The emitter's earlier approvals (ADR-0055 amendment 39, HQ's call on D-88's R1 and R2).

A reissue builds on revision 1's approval and on the last approval before the tip, and only the
tip's is otherwise matched to the verified chain. For a later revision a policy approved, the
emitter states each of the two with whether a verified `package.approved` event backs it, by the
tip's rules: approver, hash, revision, commit and event id. It refuses nothing itself; the work
carrier refuses a reissue whose entries aren't both verified
(`tests/work_carrier/test_standing.py`). The chain is a list of events in the shape
`_verified_package_approval_events` returns, and the emitter tests run it through the one read the
tip's verification made.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from orchestrator import package_sources
from orchestrator.package_sources import (
    VerifiedApproval,
    _earlier_approvals,
    load_package_intake_payload,
)
from tests.rotation_proposer import test_reissue_approval as reissue_fixtures

PACKAGE_ID = "rotation-orchestrator-operator-bearer"
POLICY = "policy:non-software-operational@v5"


def approval(revision: int, **fields: Any) -> dict[str, Any]:
    return {
        "revision": revision,
        "approved_hash": f"{revision:064x}",
        "approver": "devon",
        "approved_at": f"2026-10-{14 + revision:02d}T16:00:00+00:00",
        "commit": f"{revision:040x}",
        "event_id": f"evt-{revision}",
        **fields,
    }


def event(stated: dict[str, Any], **evidence: Any) -> dict[str, Any]:
    """The chain's `package.approved` event for `stated`, with any evidence field replaced."""
    return {
        "event_id": stated["event_id"],
        "timestamp": stated["approved_at"],
        "source": {"ref": PACKAGE_ID},
        "evidence": [
            {
                "approved_hash": stated["approved_hash"],
                "revision": stated["revision"],
                "approver": stated["approver"],
                "commit": stated["commit"],
                **evidence,
            }
        ],
    }


@pytest.fixture(autouse=True)
def _devon_is_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_sources, "_is_human_operator", lambda agent_id: agent_id == "devon")


def _roles(entries: list[dict[str, object]]) -> dict[object, dict[str, object]]:
    return {entry["role"]: entry for entry in entries}


def _three_revisions() -> list[dict[str, Any]]:
    """Revision 1 and 2 approved by Devon, revision 3 (the tip) by the policy."""
    return [approval(1), approval(2), approval(3, approver=POLICY)]


def test_both_earlier_approvals_backed_by_events_are_verified() -> None:
    approvals = _three_revisions()
    chain = [event(item) for item in approvals]
    roles = _roles(_earlier_approvals(PACKAGE_ID, 3, approvals, lambda: chain))
    assert roles["revision_1"] == {
        "role": "revision_1",
        **_fields(approvals[0]),
        "verified": True,
        "reason": None,
    }
    assert roles["last_earlier"] == {
        "role": "last_earlier",
        **_fields(approvals[1]),
        "verified": True,
        "reason": None,
    }


def _fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        name: item[name] for name in ("revision", "approver", "approved_hash", "commit", "event_id")
    }


def test_a_revision_2_states_revision_1_in_both_roles() -> None:
    approvals = [approval(1), approval(2, approver=POLICY)]
    roles = _roles(_earlier_approvals(PACKAGE_ID, 2, approvals, lambda: [event(approvals[0])]))
    assert roles["revision_1"]["verified"] is True
    assert roles["last_earlier"] == {**roles["revision_1"], "role": "last_earlier"}


@pytest.mark.parametrize("role,index", [("revision_1", 0), ("last_earlier", 1)])
def test_a_forged_approval_with_no_event_is_unbacked(role: str, index: int) -> None:
    """A lineage approval written by an intent-packages `main` writer that can't append to the
    chain: no event backs it."""
    approvals = _three_revisions()
    chain = [event(item) for i, item in enumerate(approvals) if i != index]
    entry = _roles(_earlier_approvals(PACKAGE_ID, 3, approvals, lambda: chain))[role]
    assert entry["verified"] is False
    assert entry["reason"] == "approval verification failed: no matching package.approved event"


@pytest.mark.parametrize("role,index", [("revision_1", 0), ("last_earlier", 1)])
@pytest.mark.parametrize(
    "evidence",
    [
        {"approved_hash": "f" * 64},
        {"revision": 9},
        {"approver": "claude-code-interactive"},
        {"commit": "e" * 40},
    ],
    ids=["hash", "revision", "approver", "commit"],
)
def test_an_event_naming_another_approval_doesn_t_back_it(
    role: str, index: int, evidence: dict[str, Any]
) -> None:
    approvals = _three_revisions()
    chain = [event(item, **(evidence if i == index else {})) for i, item in enumerate(approvals)]
    entry = _roles(_earlier_approvals(PACKAGE_ID, 3, approvals, lambda: chain))[role]
    assert entry["verified"] is False
    assert "no matching package.approved event" in str(entry["reason"])


@pytest.mark.parametrize("role,index", [("revision_1", 0), ("last_earlier", 1)])
def test_an_event_with_another_id_or_package_doesn_t_back_it(role: str, index: int) -> None:
    approvals = _three_revisions()
    for change in ({"event_id": "evt-other"}, {"source": {"ref": "rotation-another"}}):
        chain = [
            {**event(item), **(change if i == index else {})} for i, item in enumerate(approvals)
        ]
        entry = _roles(_earlier_approvals(PACKAGE_ID, 3, approvals, lambda: chain))[role]
        assert entry["verified"] is False, change


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"approver": "someone"}, "approver is not recognised"),
        ({"approved_hash": ""}, "approval hash is empty"),
        ({"commit": ""}, "approval commit is empty"),
        ({"event_id": None}, "approval event id is empty"),
    ],
)
def test_an_approval_the_tip_s_rules_refuse_is_unbacked(
    change: dict[str, Any], reason: str
) -> None:
    approvals = _three_revisions()
    approvals[0].update(change)
    chain = [event(item) for item in _three_revisions()]
    entry = _roles(_earlier_approvals(PACKAGE_ID, 3, approvals, lambda: chain))["revision_1"]
    assert (entry["verified"], entry["reason"]) == (False, reason)


@pytest.mark.parametrize(
    "approvals",
    [
        [approval(2), approval(3, approver=POLICY)],
        [approval(1), approval(1, event_id="evt-1b"), approval(3, approver=POLICY)],
    ],
    ids=["no-revision-1", "two-of-revision-1"],
)
def test_not_exactly_one_revision_1_approval_is_unbacked(approvals: list[dict[str, Any]]) -> None:
    chain = [event(item) for item in approvals]
    entry = _roles(_earlier_approvals(PACKAGE_ID, 3, approvals, lambda: chain))["revision_1"]
    assert (entry["verified"], entry["reason"]) == (False, "not exactly one approval")


def test_a_chain_that_can_t_be_read_is_unbacked_never_verified() -> None:
    def unreadable() -> list[dict[str, Any]]:
        raise package_sources._approval_refused("FACTORY_EVENTS_READ_PASSWORD is unset")

    approvals = _three_revisions()
    for entry in _earlier_approvals(PACKAGE_ID, 3, approvals, unreadable):
        assert entry["verified"] is False


# ---- through the emitter ----------------------------------------------------------------------


def _approved_reissue(tmp_path: Path, approvals: list[dict[str, Any]]) -> Path:
    """The operator bearer's revision 2, approved, with `approvals` as its lineage's."""
    entry, _, tip, lineage = reissue_fixtures._covered()
    package = {**tip, "status": "approved"}
    lineage = {**copy.deepcopy(lineage), "current_state": "approved", "approvals": approvals}
    directory = tmp_path / PACKAGE_ID
    directory.mkdir()
    (directory / "package.yaml").write_text(yaml.safe_dump(package, sort_keys=False))
    (directory / "lineage.yaml").write_text(yaml.safe_dump(lineage, sort_keys=False))
    return directory


def _emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approver: str, chain: list[dict[str, Any]]
) -> dict[str, object]:
    """Emit with the tip's verification passing as `approver`, and `chain` as the one read."""
    reads: list[int] = []
    directory = _approved_reissue(tmp_path, [approval(1)])
    tip_hash = package_sources.canonical_package_hash(
        yaml.safe_load((directory / "package.yaml").read_text())
    )
    lineage = yaml.safe_load((directory / "lineage.yaml").read_text())
    lineage["approvals"].append(approval(2, approver=approver, approved_hash=tip_hash))
    (directory / "lineage.yaml").write_text(yaml.safe_dump(lineage, sort_keys=False))

    def verify(
        path: Path, package_id: str, approved_hash: str, revision: object, stated: Any, read: Any
    ) -> VerifiedApproval:
        read()  # The tip's one chain read.
        return VerifiedApproval(approver, "2026-11-28T18:00:00+00:00", "evt-2", "2" * 40)

    def read_chain() -> list[dict[str, Any]]:
        reads.append(1)
        return chain

    monkeypatch.setattr(package_sources, "_verify_current_approval", verify)
    monkeypatch.setattr(package_sources, "_verified_package_approval_events", read_chain)
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    payload = load_package_intake_payload(
        directory, source_repository="AlobarQuest/intent-packages"
    )
    assert reads == [1], "the earlier approvals must reuse the tip's chain read"
    return payload


def test_a_policy_approved_reissue_s_payload_states_its_earlier_approvals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _emit(tmp_path, monkeypatch, POLICY, [event(approval(1))])
    entries = payload["earlier_approvals"]
    assert isinstance(entries, list)
    assert [(e["role"], e["revision"], e["verified"]) for e in entries] == [
        ("revision_1", 1, True),
        ("last_earlier", 1, True),
    ]


def test_a_forged_revision_1_reaches_the_payload_unbacked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _emit(tmp_path, monkeypatch, POLICY, [event(approval(1), approver="someone-else")])
    entries = payload["earlier_approvals"]
    assert isinstance(entries, list)
    assert {e["role"]: e["verified"] for e in entries} == {
        "revision_1": False,
        "last_earlier": False,
    }


def test_a_person_approved_revision_states_no_earlier_approvals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a policy's later revision builds on an earlier approval; every other payload is
    unchanged."""
    payload = _emit(tmp_path, monkeypatch, "devon", [event(approval(1))])
    assert "earlier_approvals" not in payload
