"""The carrier's standing-approval check (ADR-0055 amendment 22 item 3).

A rotation revision a policy approved is carried only when the carry form refuses nothing and the
lineage's approval is the one the emitter verified in the chain; the carrier then records its check,
under its own identity, before it posts the intake. Each refusal below is driven from the passing
control by changing one input. The package is the one `generate.render` writes for the standing
approval tests' `LANE_TOKEN_ROW` (the landing ledger's lane-token row, with placeholder UUIDs),
approved by the standing approval's policy approver.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from rotation_worker.canonical import package_hash
from rotation_worker.registry import RegistryEntry, RegistryError
from tests.rotation_proposer import test_reissue_approval as reissue_fixtures
from tests.rotation_proposer.test_standing_approval import REGISTRY, _generated, _real_row, _row
from tests.work_carrier.test_workability import _Source, member, portfolio, project
from work_carrier import cli as cli_module
from work_carrier import standing
from work_carrier.change_manager import WorkRecord
from work_carrier.cli import EXIT_FINDINGS, EXIT_OK, run
from work_carrier.orchestrator_client import OrchestratorError
from work_carrier.prepare import Prepared, Refused, emit_key
from work_carrier.prepare import standing_approval as standing_gate
from work_carrier.workability import judge

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "landing-ledger-bws-token"
PACKAGE_ID = f"rotation-{TOKEN}"
APPROVER = "policy:non-software-operational@v4"
APPROVAL_COMMIT = "a" * 40
REGISTRY_COMMIT = "b" * 40
EVENT_ID = "evt-standing-1"
APPROVED_AT = "2026-11-11T12:00:00+00:00"
# Planted where a careless report could reach it; no line may ever carry it.
SENTINEL = "placeholder-secret-value-not-a-token"


def _package(**overrides: Any) -> dict[str, Any]:
    return {**_generated(_row(TOKEN)), "status": "approved", **overrides}


def _lineage(package: dict[str, Any], **approval: Any) -> dict[str, Any]:
    entry = {
        "revision": 1,
        "approved_hash": package_hash(package),
        "approver": APPROVER,
        "approved_at": APPROVED_AT,
        "commit": APPROVAL_COMMIT,
        "event_id": EVENT_ID,
        **approval,
    }
    return {
        "package_id": package["package_id"],
        "current_state": "approved",
        "revisions": [{"revision": 1, "hash": package_hash(package)}],
        "approvals": [entry],
    }


def _payload(package: dict[str, Any], rec: WorkRecord, **overrides: Any) -> dict[str, Any]:
    """What the emitter returns once it has verified the approval in the chain."""
    return {
        "package_id": package["package_id"],
        "revision": package["revision"],
        "content_hash": package_hash(package),
        "approved_by": APPROVER,
        "approved_at": APPROVED_AT,
        "approval_event_id": EVENT_ID,
        "approval_ledger_commit": APPROVAL_COMMIT,
        "profile": package["profile"],
        "source_repository": rec.package_source_repository,
        "change_record_id": rec.change_record_id,
        "idempotency_key": emit_key(rec),
        "expected_version": 0,
        "enforcement_snapshot": {
            "profile_fields": package["profile_fields"],
            "reach": package["reach"],
        },
        **overrides,
    }


def _write(directory: Path, package: dict[str, Any], lineage: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.yaml").write_text(yaml.safe_dump(package, sort_keys=False))
    (directory / "lineage.yaml").write_text(yaml.safe_dump(lineage, sort_keys=False))
    return directory


def _entry(row: dict[str, Any] | None = None) -> Callable[[str], RegistryEntry]:
    found = RegistryEntry(REGISTRY, REGISTRY_COMMIT, row if row is not None else _row(TOKEN))
    return lambda credential_id: found


def _record() -> WorkRecord:
    return WorkRecord(
        change_record_id=91,
        package_id=PACKAGE_ID,
        package_revision=1,
        package_source_repository="AlobarQuest/intent-packages",
        reasoning="a lane credential's first issue",
        decided_by="work-policy",
    )


def _check(
    tmp_path: Path,
    *,
    package: dict[str, Any] | None = None,
    lineage: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    find_entry: Callable[[str], RegistryEntry] | None = None,
) -> standing.Checked | str:
    package = package if package is not None else _package()
    lineage = lineage if lineage is not None else _lineage(package)
    directory = _write(tmp_path / PACKAGE_ID, package, lineage)
    payload = payload if payload is not None else _payload(package, _record())
    return standing.check(payload, directory, find_entry or _entry())


# ---- the check ------------------------------------------------------------------------------


def test_a_policy_approved_generated_revision_1_passes_and_composes_its_record(
    tmp_path: Path,
) -> None:
    checked = _check(tmp_path)

    assert isinstance(checked, standing.Checked), checked
    observation = checked.observation
    assert observation["source_system"] == "work_carrier"
    assert observation["observation_type"] == "standing_approval_check"
    assert observation["subject_type"] == "package_revision"
    assert observation["subject_reference"] == f"{PACKAGE_ID}@r1"
    assert observation["observed_at"] == APPROVED_AT
    assert observation["facts"] == {
        "package_id": PACKAGE_ID,
        "revision": 1,
        "content_hash": package_hash(_package()),
        "package_commit": APPROVAL_COMMIT,
        "approver": APPROVER,
        "registry_entry": TOKEN,
        "registry_repository": REGISTRY,
        "registry_commit": REGISTRY_COMMIT,
        "form": "issue",
        "refused": [],
    }
    # The reference names the form, the revision and the registry commit, so a moved registry is
    # a new row, and a row recorded before facts named the form is never replayed into conflict.
    assert observation["source_reference"].startswith(f"standing-approval:issue:{PACKAGE_ID}@r1:")
    assert observation["idempotency_key"].startswith(
        f"work-carrier-standing:issue:{PACKAGE_ID}@r1:"
    )
    assert f"{REGISTRY}@{REGISTRY_COMMIT}" in observation["source_reference"]
    assert len(observation["idempotency_key"]) <= standing.MAX_IDEMPOTENCY_KEY


def test_two_passes_over_one_revision_and_registry_compose_the_same_record(tmp_path: Path) -> None:
    first = _check(tmp_path / "one")
    second = _check(tmp_path / "two")
    assert isinstance(first, standing.Checked) and isinstance(second, standing.Checked)
    assert first.observation == second.observation


def test_a_registry_that_moved_is_a_new_reference(tmp_path: Path) -> None:
    first = _check(tmp_path / "one")
    moved = RegistryEntry(REGISTRY, "c" * 40, _row(TOKEN))
    second = _check(tmp_path / "two", find_entry=lambda credential_id: moved)
    assert isinstance(first, standing.Checked) and isinstance(second, standing.Checked)
    assert first.observation["source_reference"] != second.observation["source_reference"]
    assert first.observation["idempotency_key"] != second.observation["idempotency_key"]


def test_a_lineage_approval_the_emitter_didn_t_verify_refuses(tmp_path: Path) -> None:
    """The chain event the emitter matched is the payload's; a lineage naming another approver,
    commit or event id is not the approval the chain holds."""
    package = _package()
    for field, value in (
        ("approver", "policy:non-software-operational@v5"),
        ("commit", "d" * 40),
        ("event_id", "evt-another"),
    ):
        refused = _check(tmp_path / field, lineage=_lineage(package, **{field: value}))
        assert isinstance(refused, str)
        assert "isn't the one the emitter verified" in refused and field in refused


def test_a_checkout_that_no_longer_hashes_to_the_payload_refuses(tmp_path: Path) -> None:
    package = _package()
    payload = _payload(package, _record(), content_hash="0" * 64)
    refused = _check(tmp_path, package=package, payload=payload)
    assert refused == "the checkout no longer hashes to the payload's content_hash"


def test_a_carry_form_refusal_names_its_conditions(tmp_path: Path) -> None:
    """A registry that moved since the approval makes the re-render differ."""
    row = _row(TOKEN)
    for consumer in row["consumer"]:
        if consumer["kind"] == "coolify-env":
            consumer["key"] = "LANDING_LEDGER_MOVED_VARIABLE"
    refused = _check(tmp_path, find_entry=_entry(row))
    assert isinstance(refused, str)
    assert refused.startswith("the carry form refused: ")
    assert "proposer_generated" in refused and "destinations" in refused


def test_a_github_app_key_revision_the_policy_approved_is_refused(tmp_path: Path) -> None:
    """ADR-0055 amendment 35 item 15, D-84: a GitHub App key's package, approved under the
    standing policy's name, is refused before any intake, so a person decides it."""
    row = {**_real_row("lanes-read-app-private-key"), "rotated_by_sds": True}
    package = {**_generated(row, first_issue=False), "status": "approved"}
    refused = _check(tmp_path, package=package, find_entry=_entry(row))
    assert isinstance(refused, str)
    assert refused.startswith("the carry form refused: ")
    assert "lane_type (class 'github-app-private-key' is no lane type)" in refused


def test_an_anthropic_key_revision_the_policy_approved_is_refused(tmp_path: Path) -> None:
    """ADR-0055 amendment 36 item 13, D-85: the change window's Anthropic key package, approved
    under the standing policy's name, is refused before any intake, so Devon decides it."""
    row = {**_real_row("anthropic-change-window-key"), "rotated_by_sds": True}
    package = {**_generated(row, first_issue=False), "status": "approved"}
    refused = _check(tmp_path, package=package, find_entry=_entry(row))
    assert isinstance(refused, str)
    assert refused.startswith("the carry form refused: ")
    assert "lane_type (class 'anthropic-api-key' is no lane type)" in refused


@pytest.mark.parametrize(
    ("error", "named"),
    [
        (RegistryError(f"{TOKEN}: 0 registry entries, expected exactly 1"), True),
        (RuntimeError(SENTINEL), False),
    ],
)
def test_a_read_that_fails_refuses_and_names_only_what_it_composed(
    tmp_path: Path, error: Exception, named: bool
) -> None:
    def find(credential_id: str) -> RegistryEntry:
        raise error

    refused = _check(tmp_path, find_entry=find)
    assert isinstance(refused, str)
    assert type(error).__name__ in refused
    assert (str(error) in refused) is named
    assert SENTINEL not in refused


def test_a_missing_lineage_refuses(tmp_path: Path) -> None:
    directory = _write(tmp_path / PACKAGE_ID, _package(), _lineage(_package()))
    (directory / "lineage.yaml").unlink()
    refused = standing.check(_payload(_package(), _record()), directory, _entry())
    assert refused == "the carry form could not be run: FileNotFoundError"


# ---- a reissue (ADR-0055 amendment 39) ------------------------------------------------------


EARLIER = ("revision", "approver", "approved_hash", "commit", "event_id")


def _earlier_entries(lineage: dict[str, Any], tip: int) -> list[dict[str, Any]]:
    """What the emitter states when a verified event backs each earlier approval."""
    earlier = [a for a in lineage["approvals"] if a["revision"] < tip]
    last = max(a["revision"] for a in earlier)
    (first,) = [a for a in earlier if a["revision"] == 1]
    (latest,) = [a for a in earlier if a["revision"] == last]
    return [
        {"role": role, **{k: a[k] for k in EARLIER}, "verified": True, "reason": None}
        for role, a in (("revision_1", first), ("last_earlier", latest))
    ]


def _reissue(
    row: dict[str, Any] | None = None,
    edit: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], RegistryEntry]:
    """A policy-approved revision 2 of the operator bearer, its lineage, the emitter's payload,
    and its registry row, as `test_reissue_approval` builds them. `edit` changes the lineage
    before the emitter reads it, so the payload states what the lineage then says."""
    entry, _, tip, lineage = reissue_fixtures._covered(row)
    carried, verified = reissue_fixtures._carried(tip, lineage)
    if edit is not None:
        edit(carried)
    package = {**tip, "status": "approved"}
    approval = carried["approvals"][-1]
    payload = {
        **_payload(package, _record()),
        "approved_by": approval["approver"],
        "approved_at": approval["approved_at"],
        "approval_event_id": approval["event_id"],
        "approval_ledger_commit": approval["commit"],
        "earlier_approvals": _earlier_entries(carried, package["revision"]),
    }
    assert {k: payload[k] for k in ("revision", "content_hash")} == {
        "revision": verified["revision"],
        "content_hash": verified["approved_hash"],
    }
    return package, carried, payload, RegistryEntry(REGISTRY, REGISTRY_COMMIT, entry.credential)


def _check_reissue(
    tmp_path: Path,
    package: dict[str, Any],
    lineage: dict[str, Any],
    payload: dict[str, Any],
    found: RegistryEntry,
) -> standing.Checked | str:
    directory = _write(tmp_path / package["package_id"], package, lineage)
    return standing.check(payload, directory, lambda credential_id: found)


def test_a_policy_approved_reissue_passes_and_records_the_reissue_form(tmp_path: Path) -> None:
    package, lineage, payload, found = _reissue()
    checked = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(checked, standing.Checked), checked
    observation = checked.observation
    assert observation["facts"]["form"] == "reissue"
    assert observation["facts"]["revision"] == 2
    assert observation["facts"]["approver"] == reissue_fixtures.POLICY
    assert observation["source_reference"].startswith("standing-approval:reissue:")
    assert len(observation["idempotency_key"]) <= standing.MAX_IDEMPOTENCY_KEY


@pytest.mark.parametrize("field", ["approver", "commit", "event_id"])
def test_a_reissue_approval_the_emitter_didn_t_verify_refuses(tmp_path: Path, field: str) -> None:
    """D-84 condition 2's shape: a lineage approval on the tip that the verified chain event
    doesn't back (one the rotation root could write to intent-packages `main`) refuses in
    `_verified_approval`, before the carry form runs."""
    package, lineage, payload, found = _reissue()
    lineage["approvals"][-1][field] = "policy:non-software-operational@v9-forged"
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(refused, str)
    assert "isn't the one the emitter verified" in refused and field in refused


def test_the_verified_approval_is_the_one_on_the_revision_not_the_only_one(
    tmp_path: Path,
) -> None:
    """Revision 1's person approval stands beside it; the policy's on the tip is the one matched,
    wherever the lineage lists it."""
    package, lineage, payload, found = _reissue()
    assert [a["approver"] for a in lineage["approvals"]] == ["devon", reissue_fixtures.POLICY]
    lineage["approvals"].reverse()
    assert isinstance(_check_reissue(tmp_path, package, lineage, payload, found), standing.Checked)


@pytest.mark.parametrize("later", [3, "3", None])
def test_an_approval_of_a_later_revision_refuses(tmp_path: Path, later: object) -> None:
    package, lineage, payload, found = _reissue()
    lineage["approvals"].append({**lineage["approvals"][0], "revision": later})
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert refused == "the lineage records an approval of a later revision"


def test_a_second_approval_of_the_reissue_s_revision_refuses(tmp_path: Path) -> None:
    package, lineage, payload, found = _reissue()
    lineage["approvals"].insert(1, dict(lineage["approvals"][-1]))
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert refused == "the lineage doesn't record exactly one approval of the revision"


@pytest.mark.parametrize(
    ("row", "condition"),
    [
        (
            {**reissue_fixtures._real_row("orchestrator-system-bearer"), "rotated_by_sds": True},
            "not_hand_rotated",
        ),
        (
            {**reissue_fixtures._real_row(reissue_fixtures.OPERATOR), "rotated_by_sds": False},
            "rotated_by_sds",
        ),
    ],
    ids=["system-bearer", "not-sds-rotated"],
)
def test_the_reissue_carry_form_refusal_names_its_condition(
    tmp_path: Path, row: dict[str, Any], condition: str
) -> None:
    package, lineage, payload, found = _reissue(row=row)
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(refused, str)
    assert refused.startswith("the carry form refused: ")
    assert f"{condition} (" in refused


def test_a_reissue_whose_revision_1_a_policy_approved_refuses(tmp_path: Path) -> None:
    """A first hand-over is a person's (reading 2): revision 1 approved by policy can't seed a
    reissue the policy then approves."""

    def policy_first(lineage: dict[str, Any]) -> None:
        lineage["approvals"][0]["approver"] = reissue_fixtures.POLICY

    package, lineage, payload, found = _reissue(edit=policy_first)
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(refused, str)
    assert "policy_approved_reissue (" in refused
    assert "revision 1's approver isn't a named person" in refused


def _third_revision() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], RegistryEntry]:
    """Revision 3 the policy approved, over revisions 1 and 2 Devon approved: the two earlier
    roles name different approvals."""
    row = reissue_fixtures._real_row(reissue_fixtures.OPERATOR)
    first = reissue_fixtures._approved(row)
    second = {**reissue_fixtures._tip(first), "status": "approved"}
    tip = reissue_fixtures._tip(second, revision=3)
    lineage = reissue_fixtures._lineage(first, tip)
    lineage["revisions"][1]["hash"] = package_hash(second)
    lineage["approvals"].append(
        {
            **lineage["approvals"][0],
            "revision": 2,
            "approved_hash": package_hash(second),
            "commit": "2" * 40,
            "event_id": "evt-r2",
        }
    )
    carried, _ = reissue_fixtures._carried(tip, lineage)
    package = {**tip, "status": "approved"}
    approval = carried["approvals"][-1]
    payload = {
        **_payload(package, _record()),
        "approved_by": approval["approver"],
        "approved_at": approval["approved_at"],
        "approval_event_id": approval["event_id"],
        "approval_ledger_commit": approval["commit"],
        "earlier_approvals": _earlier_entries(carried, 3),
    }
    return package, carried, payload, RegistryEntry(REGISTRY, REGISTRY_COMMIT, row)


def test_a_third_revision_with_both_earlier_approvals_verified_passes(tmp_path: Path) -> None:
    package, lineage, payload, found = _third_revision()
    assert [e["revision"] for e in payload["earlier_approvals"]] == [1, 2]
    checked = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(checked, standing.Checked), checked
    # The record names the earlier events it relied on, for whoever reads it later.
    assert checked.observation["facts"]["earlier_approvals"] == [
        {
            "role": "revision_1",
            "event_id": lineage["approvals"][0]["event_id"],
            "commit": lineage["approvals"][0]["commit"],
        },
        {"role": "last_earlier", "event_id": "evt-r2", "commit": "2" * 40},
    ]


def test_a_revision_1_record_states_no_earlier_approvals(tmp_path: Path) -> None:
    checked = _check(tmp_path)
    assert isinstance(checked, standing.Checked)
    assert "earlier_approvals" not in checked.observation["facts"]


def test_a_wrong_shape_reissue_is_reported_by_its_condition_first(tmp_path: Path) -> None:
    """An unbacked earlier approval doesn't hide the carry form's own refusal."""
    row = {**reissue_fixtures._real_row("orchestrator-system-bearer"), "rotated_by_sds": True}
    package, lineage, payload, found = _reissue(row=row)
    payload.pop("earlier_approvals")
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(refused, str)
    assert refused.startswith("the carry form refused: ") and "not_hand_rotated (" in refused


def test_the_carrier_reads_the_emitter_s_roles_and_fields() -> None:
    from orchestrator import package_sources

    assert tuple(standing.EARLIER_ROLES) == package_sources._EARLIER_ROLES
    assert standing.EARLIER_FIELDS == package_sources._APPROVAL_FIELDS
    assert standing.ROTATION_PROFILE == package_sources._ROTATION_PROFILE


@pytest.mark.parametrize(
    ("role", "named"),
    [("revision_1", "revision 1's approval"), ("last_earlier", "the last approval before the tip")],
)
def test_a_forged_earlier_approval_the_chain_doesn_t_back_refuses(
    tmp_path: Path, role: str, named: str
) -> None:
    """HQ's call on D-88 (B): an earlier approval no verified event backs (one an
    intent-packages `main` writer with no append password wrote) refuses, naming which."""
    package, lineage, payload, found = _third_revision()
    for entry in payload["earlier_approvals"]:
        if entry["role"] == role:
            entry.update(verified=False, reason="no matching package.approved event")
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert refused == (
        f"{named} isn't backed by a verified chain event (no matching package.approved event)"
    )


@pytest.mark.parametrize("role", ["revision_1", "last_earlier"])
@pytest.mark.parametrize("field", EARLIER)
def test_an_earlier_approval_other_than_the_one_verified_refuses(
    tmp_path: Path, role: str, field: str
) -> None:
    """The emitter verified another approval than the one the lineage holds for the role."""
    package, lineage, payload, found = _third_revision()
    (entry,) = [e for e in payload["earlier_approvals"] if e["role"] == role]
    entry[field] = 9 if field == "revision" else "another-verified-approval"
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(refused, str)
    assert "isn't the one the emitter verified in the chain" in refused
    assert f"differs in {field}" in refused


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.pop("earlier_approvals"),
        lambda p: p.update(earlier_approvals="verified"),
        lambda p: p.update(earlier_approvals=p["earlier_approvals"][:1]),
        lambda p: p["earlier_approvals"].append(dict(p["earlier_approvals"][0])),
    ],
    ids=["absent", "not-a-list", "one-role-missing", "role-twice"],
)
def test_a_payload_without_one_entry_per_role_refuses(
    tmp_path: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    package, lineage, payload, found = _third_revision()
    change(payload)
    refused = _check_reissue(tmp_path, package, lineage, payload, found)
    assert isinstance(refused, str)
    assert "earlier approvals" in refused or "has no single verified chain event" in refused


def test_the_emitter_s_entries_are_what_the_carrier_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The emitter's `_earlier_approvals` output, from a chain holding both earlier events, is
    accepted; the same with revision 1's event missing is refused by name."""
    from orchestrator import package_sources

    monkeypatch.setattr(package_sources, "_is_human_operator", lambda agent_id: agent_id == "devon")
    package, lineage, payload, found = _third_revision()

    def events(skip: int | None) -> list[dict[str, Any]]:
        return [
            {
                "event_id": a["event_id"],
                "timestamp": a["approved_at"],
                "source": {"ref": package["package_id"]},
                "evidence": [
                    {k: a[k] for k in ("approved_hash", "revision", "approver", "commit")}
                ],
            }
            for a in lineage["approvals"]
            if a["revision"] < 3 and a["revision"] != skip
        ]

    for skip, passes in ((None, True), (1, False)):
        chain = events(skip)
        payload["earlier_approvals"] = package_sources._earlier_approvals(
            package["package_id"], 3, lineage["approvals"], lambda: chain
        )
        outcome = _check_reissue(tmp_path / str(skip), package, lineage, payload, found)
        assert isinstance(outcome, standing.Checked) is passes, outcome


# ---- which records the check applies to -----------------------------------------------------


def _prepared(tmp_path: Path, payload: dict[str, Any]) -> Prepared:
    package = _package()
    directory = _write(tmp_path / PACKAGE_ID, package, _lineage(package))
    return Prepared(record=_record(), package_path=directory, payload=payload)


def _unreachable(credential_id: str) -> RegistryEntry:
    raise AssertionError("the registry must not be read for this record")


def test_a_person_approved_rotation_revision_is_carried_as_before(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, _payload(_package(), _record(), approved_by="devon"))
    assert standing_gate(prepared, _unreachable, identified=False) is prepared


def test_a_policy_approved_bump_is_carried_as_before(tmp_path: Path) -> None:
    payload = _payload(
        _package(),
        _record(),
        approved_by="policy:dependency-update@v3",
        profile="dependency-update",
    )
    prepared = _prepared(tmp_path, payload)
    assert standing_gate(prepared, _unreachable, identified=False) is prepared


def test_the_check_keys_on_the_profile_not_the_record_s_change_class(tmp_path: Path) -> None:
    """A hand-proposed record has no `change_class`; the package's profile is what decides."""
    prepared = _prepared(tmp_path, _payload(_package(), _record()))
    assert prepared.record.change_class is None
    outcome = standing_gate(prepared, _entry(), identified=True)
    assert isinstance(outcome, Prepared) and outcome.standing_observation is not None


def test_no_identity_of_its_own_refuses_before_any_read(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, _payload(_package(), _record()))
    outcome = standing_gate(prepared, _unreachable, identified=False)
    assert isinstance(outcome, Refused)
    assert outcome.reason == "standing_approval_refused"
    assert "identity" in outcome.detail


# ---- the identity seam ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key_id", "token", "identified"),
    [
        ("work-carrier-observer", "t", True),
        ("", "t", False),
        ("work-carrier-observer", "", False),
        # Never a fallback to the shared SYSTEM key, which any SYSTEM caller holds.
        ("orchestrator-system", "t", False),
        ("  orchestrator-system  ", "t", False),
    ],
)
def test_the_observation_identity_is_never_the_system_key(
    monkeypatch: pytest.MonkeyPatch, key_id: str, token: str, identified: bool
) -> None:
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_KEY_ID", key_id)
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_TOKEN", token)
    assert (cli_module._observation_identity() is not None) is identified


# ---- through the carry ----------------------------------------------------------------------


class _Orchestrator:
    """The two clients' doubles, writing to one ordered log so the test sees which came first."""

    def __init__(self, log: list[str], *, fail: bool = False) -> None:
        self.log = log
        self.fail = fail
        self.observations: list[dict] = []
        self.payloads: list[dict] = []

    def carried_revisions(self, change_record_id: int) -> list[str]:
        return []

    def record_observation(self, observation: dict) -> dict:
        self.log.append("observation")
        if self.fail:
            raise OrchestratorError("the orchestrator answered 403 for /api/v1/observations")
        self.observations.append(observation)
        return {"id": "22222222-2222-2222-2222-222222222222"}

    def register_intake(self, payload: dict) -> dict:
        self.log.append("intake")
        self.payloads.append(payload)
        return {"id": "11111111-1111-1111-1111-111111111111"}


def _carry(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload_overrides: dict[str, Any] | None = None,
    lineage_overrides: dict[str, Any] | None = None,
    emitter_fails: bool = False,
    recorder: _Orchestrator | None = None,
    argv: tuple[str, ...] = ("--register",),
) -> tuple[int, str, list[str], _Orchestrator, _Orchestrator | None]:
    rec = _record()
    package = _package()
    _write(
        root / "intent-packages" / "packages" / PACKAGE_ID,
        package,
        _lineage(package, **(lineage_overrides or {})),
    )
    emitted = _payload(package, rec, **(payload_overrides or {}))

    def emitter(command, **kwargs):
        if emitter_fails:
            # The emitter refuses an approval with no matching `package.approved` chain event.
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="approval verification failed: no matching event"
            )
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(emitted), stderr="")

    import work_carrier.prepare as prepare_module

    original = prepare_module.prepare
    monkeypatch.setattr(cli_module, "prepare", lambda r, **kw: original(r, **kw, runner=emitter))
    monkeypatch.setattr(
        cli_module,
        "judge_workability",
        lambda subjects, out, grants, rotated, **_carried: judge(
            subjects, out, grants=grants, rotated=rotated, portfolio=portfolio(project())
        ),
    )
    log: list[str] = []
    writer = _Orchestrator(log)
    if recorder is not None:
        recorder.log = log
    code, text = _run(root, rec, writer, recorder, argv)
    return code, text, log, writer, recorder


def _run(root: Path, rec: WorkRecord, writer, recorder, argv: tuple[str, ...]) -> tuple[int, str]:
    """The `run` call, behind unannotated doubles, as `test_workability._run_registering` does."""
    out = io.StringIO()
    code = run(
        ["--checkout-root", str(root), *argv],
        source=_Source([rec]),
        registrar=writer,
        recorder=recorder,
        members=[member(REGISTRY, credential_rotation="granted")],
        rotated={TOKEN: REGISTRY},
        find_entry=_entry(),
        out=out,
    )
    return code, out.getvalue()


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "intent-packages" / "packages").mkdir(parents=True)
    return tmp_path


def test_a_pass_records_the_check_before_it_posts_the_intake(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Orchestrator([])
    code, text, log, writer, _ = _carry(root, monkeypatch, recorder=recorder)

    assert code == EXIT_OK, text
    assert log == ["observation", "intake"]
    assert recorder.observations[0]["facts"]["content_hash"] == writer.payloads[0]["content_hash"]
    assert writer.observations == [], "the intake client never records the check"


def test_a_failed_record_posts_no_intake(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Orchestrator([], fail=True)
    code, text, log, writer, _ = _carry(root, monkeypatch, recorder=recorder)

    assert code == EXIT_FINDINGS
    assert log == ["observation"]
    assert writer.payloads == []


def test_a_refusal_records_nothing_and_posts_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Orchestrator([])
    code, text, log, writer, _ = _carry(
        root, monkeypatch, recorder=recorder, lineage_overrides={"event_id": "evt-another"}
    )

    assert code == EXIT_FINDINGS
    assert "standing_approval_refused" in text
    assert log == []


def test_no_matching_chain_event_refuses_at_the_emitter(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Orchestrator([])
    code, text, log, _, _ = _carry(root, monkeypatch, recorder=recorder, emitter_fails=True)

    assert code == EXIT_FINDINGS
    assert "package_not_intakeable" in text
    assert log == []


def test_an_unset_identity_refuses_and_posts_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, log, writer, _ = _carry(root, monkeypatch, recorder=None)

    assert code == EXIT_FINDINGS
    assert "standing_approval_refused" in text and "identity" in text
    assert log == []


def test_a_system_key_named_as_the_identity_refuses(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_KEY_ID", "orchestrator-system")
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_TOKEN", SENTINEL)
    code, text, log, _, _ = _carry(root, monkeypatch, recorder=None)

    assert code == EXIT_FINDINGS
    assert "standing_approval_refused" in text
    assert log == []
    assert SENTINEL not in text


def test_a_person_approved_rotation_is_registered_with_no_record(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, text, log, writer, _ = _carry(
        root, monkeypatch, recorder=None, payload_overrides={"approved_by": "devon"}
    )

    assert code == EXIT_OK, text
    assert log == ["intake"]


def test_a_bare_pass_checks_and_writes_nothing(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Orchestrator([])
    code, text, log, _, _ = _carry(root, monkeypatch, recorder=recorder, argv=())

    assert code == EXIT_OK, text
    assert log == []


def test_no_report_line_carries_a_value(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Code Brain rule 5: neither the identity's token nor an error's own text reaches the report
    or the record; only ids, commits, conditions and error types do."""
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_KEY_ID", "work-carrier-observer")
    monkeypatch.setenv("WORK_CARRIER_OBSERVATION_TOKEN", SENTINEL)
    recorder = _Orchestrator([])
    code, text, _, _, _ = _carry(root, monkeypatch, recorder=recorder)

    assert code == EXIT_OK, text
    assert SENTINEL not in text
    assert SENTINEL not in json.dumps(recorder.observations)


def test_a_refusal_carries_no_error_text_it_didn_t_compose(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leaking(credential_id: str) -> RegistryEntry:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_module, "_registry_reader", lambda: leaking)
    rec = _record()
    package = _package()
    _write(root / "intent-packages" / "packages" / PACKAGE_ID, package, _lineage(package))
    outcome = standing_gate(
        Prepared(rec, root / "intent-packages" / "packages" / PACKAGE_ID, _payload(package, rec)),
        leaking,
        identified=True,
    )
    assert isinstance(outcome, Refused)
    assert SENTINEL not in outcome.detail


# ---- the carrier imports one module of the proposer -----------------------------------------


def test_the_carrier_imports_only_the_proposer_s_checks() -> None:
    """Amendment 22 item 3: the same checks, not a copy, and never the proposer's CLI or pass."""
    imported = set()
    for path in (ROOT / "src" / "work_carrier").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
    proposer = {name for name in imported if name.split(".")[0] == "rotation_proposer"}
    assert proposer == {"rotation_proposer.standing_approval"}


def test_the_record_the_carrier_composes_is_the_one_the_gate_reads() -> None:
    from orchestrator.services.intake import standing_approval as gate

    assert (gate.CARRIER_SOURCE, gate.CARRIER_CHECK) == (
        standing.SOURCE_SYSTEM,
        standing.OBSERVATION_TYPE,
    )
    assert gate.subject_for(PACKAGE_ID, 1) == standing.subject_for(PACKAGE_ID, 1)
    assert (gate.ISSUE_FORM, gate.REISSUE_FORM) == (standing.ISSUE_FORM, standing.REISSUE_FORM)
    assert gate.ROTATION_PROFILE == standing.ROTATION_PROFILE
