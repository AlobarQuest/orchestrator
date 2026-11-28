"""The standing approval of a reissue (ADR-0055 amendment 39, D-88 A), as refusals.

The covered row is `orchestrator-operator-bearer` from this repository's `.cred-consumers.toml`.
Its approved revision 1 and its tip are `generate.render`'s package for the row on the rotation
plan, as `factory-create` would leave it; the tip differs only in what a proposer's revision
changes. Most tests run `reissue` or `carry_reissue` in-process and assert the one condition that
refuses, so removing that condition's check fails the test. The command's own reads (the package
at the approval's commit, the `rotated_before` fact, the choice of form) run through the real
mirror over throwaway git origins.
"""

from __future__ import annotations

import copy
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from rotation_proposer import cli
from rotation_proposer.generate import render
from rotation_proposer.standing_approval import (
    AUTHORITY,
    DESTINATIONS_EQUAL,
    HAND_ROTATED_ENTRY_PREFIXES,
    HAND_ROTATED_IDS,
    LANE_TYPE,
    MACHINE_PLAN,
    NAMED_APPROVERS,
    NO_NEW_COPY,
    NOT_HAND_ROTATED,
    POLICY_REISSUE_LINEAGE,
    PROPOSER_REVISED,
    REACH,
    REISSUE_LINEAGE,
    REISSUE_TYPE,
    REISSUE_TYPES,
    SDS_ROTATED,
    Condition,
    carry_reissue,
    machine_plan,
    reissue,
    reissue_type,
)
from rotation_worker.canonical import package_hash
from rotation_worker.contract import ContractError
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryEntry,
    lineage_path,
    package_path,
)
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = "orchestrator-operator-bearer"
REGISTRY = "AlobarQuest/orchestrator"
APPROVED_OCCURRENCE = "2026-10-15-requested"
OCCURRENCE = "2026-11-28-requested"
CREATED_AT = "2026-10-15T14:06:54Z"
POLICY = "policy:non-software-operational@v5"
APPROVAL_COMMIT = "a" * 40
EVERY = [
    SDS_ROTATED,
    REISSUE_TYPE,
    NOT_HAND_ROTATED,
    MACHINE_PLAN,
    REISSUE_LINEAGE,
    PROPOSER_REVISED,
    NO_NEW_COPY,
    DESTINATIONS_EQUAL,
    AUTHORITY,
    REACH,
]
CARRIED = [
    SDS_ROTATED,
    REISSUE_TYPE,
    NOT_HAND_ROTATED,
    MACHINE_PLAN,
    POLICY_REISSUE_LINEAGE,
    DESTINATIONS_EQUAL,
    AUTHORITY,
    REACH,
]


def _real_row(credential_id: str) -> dict[str, Any]:
    document = tomllib.loads((ROOT / REGISTRY_PATH).read_text())
    (row,) = [c for c in document["credential"] if c["id"] == credential_id]
    return copy.deepcopy(row)


def _entry(row: dict[str, Any]) -> RegistryEntry:
    return RegistryEntry(REGISTRY, "0" * 40, row)


def _approved(row: dict[str, Any]) -> dict[str, Any]:
    """Revision 1 as a person approved it: the row's rotation-plan package."""
    package = render(_entry(row), APPROVED_OCCURRENCE, CREATED_AT, first_issue=False)
    return {**package, "created_by": "factory-create", "status": "approved"}


def _tip(approved: dict[str, Any], *, revision: int = 2) -> dict[str, Any]:
    """The proposer's revision of `approved`: a new occurrence, in review."""
    tip = copy.deepcopy(approved)
    tip.update(revision=revision, status="ready_for_review")
    tip["profile_fields"]["occurrence"] = OCCURRENCE
    return tip


def _lineage(
    approved: dict[str, Any], tip: dict[str, Any], *, commit: str = APPROVAL_COMMIT
) -> dict[str, Any]:
    """Revisions 1 to the tip's, revision 1 approved by Devon on `approved`'s hash; a revision
    between them is listed and never approved."""
    number = tip["revision"]
    revisions = [{"revision": n, "hash": f"{n:064x}"} for n in range(1, number + 1)]
    revisions[0]["hash"] = package_hash(approved)
    revisions[-1]["hash"] = package_hash(tip)
    return {
        "package_id": tip["package_id"],
        "current_state": "ready_for_review",
        "revisions": revisions,
        "approvals": [
            {
                "revision": 1,
                "approved_hash": package_hash(approved),
                "approver": "devon",
                "approved_at": "2026-10-15T16:00:00+00:00",
                "commit": commit,
                "event_id": "evt-1",
            }
        ],
        "grants": [],
    }


def _refused(conditions: list[Condition]) -> list[str]:
    return [condition.name for condition in conditions if not condition.passed]


def _reason(conditions: list[Condition], name: str) -> str:
    (condition,) = [condition for condition in conditions if condition.name == name]
    return condition.reason


def _covered(
    row: dict[str, Any] | None = None,
) -> tuple[RegistryEntry, dict[str, Any], dict[str, Any], dict[str, Any]]:
    row = row if row is not None else _real_row(OPERATOR)
    approved = _approved(row)
    tip = _tip(approved)
    return _entry(row), approved, tip, _lineage(approved, tip)


# ---- the covered revision ----------------------------------------------------------------------


def test_the_operator_bearer_row_is_a_covered_reissue_type() -> None:
    """The fixture's row stands in for the real one, so it must stay SDS-rotated and typed."""
    row = _real_row(OPERATOR)
    assert row["rotated_by_sds"] is True
    assert reissue_type(_entry(row), _approved(row)).passed


def test_a_proposer_revision_of_an_approved_reissue_refuses_nothing() -> None:
    entry, approved, tip, lineage = _covered()
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert [condition.name for condition in conditions] == EVERY
    assert _refused(conditions) == []


def test_a_revision_never_approved_between_doesn_t_refuse() -> None:
    """Revision 3 over an approved revision 1, revision 2 never approved: the comparison is with
    the last approved one."""
    row = _real_row(OPERATOR)
    approved = _approved(row)
    tip = _tip(approved, revision=3)
    assert _refused(reissue(_entry(row), tip, _lineage(approved, tip), approved=approved)) == []


def test_the_comparison_is_with_the_last_approved_revision() -> None:
    """Revision 2 approved too, with a different occurrence: the tip is compared with it."""
    row = _real_row(OPERATOR)
    first = _approved(row)
    second = {**_tip(first), "status": "approved"}
    second["profile_fields"]["occurrence"] = "2026-10-28-requested"
    second["title"] = "Standing rotation, reworded and approved"
    tip = _tip(second, revision=3)
    lineage = _lineage(first, tip)
    lineage["revisions"][1]["hash"] = package_hash(second)
    lineage["approvals"].append(
        {**lineage["approvals"][0], "revision": 2, "approved_hash": package_hash(second)}
    )
    assert _refused(reissue(_entry(row), tip, lineage, approved=second)) == []
    assert _refused(reissue(_entry(row), tip, lineage, approved=first)) == [PROPOSER_REVISED]


# ---- the standing answer's condition 1: an SDS-rotated row of a reissue type --------------------


@pytest.mark.parametrize("flag", [False, None])
def test_a_row_not_rotated_by_sds_refuses(flag: bool | None) -> None:
    row = _real_row(OPERATOR)
    if flag is None:
        del row["rotated_by_sds"]
    else:
        row["rotated_by_sds"] = flag
    entry, approved, tip, lineage = _covered(row)
    assert _refused(reissue(entry, tip, lineage, approved=approved)) == [SDS_ROTATED]


def test_a_lane_type_at_revision_2_refuses_reissue_type() -> None:
    """A lane token's rotation plan has a person's mint, so `machine_plan` refuses it too."""
    row = _real_row("secrets-broker-bws-token")
    entry, approved, tip, lineage = _covered(row)
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert _refused(conditions) == [REISSUE_TYPE, MACHINE_PLAN]
    assert _reason(conditions, REISSUE_TYPE) == "class 'bws-lane-token' is no reissue type"


@pytest.mark.parametrize(
    ("credential_class", "provider"),
    [("openrouter-key", "openrouter"), ("postgres-role-password", "postgres-role")],
)
def test_a_p1_type_no_sds_component_reads_refuses_reissue_type(
    credential_class: str, provider: str
) -> None:
    """Reading 4: `openrouter` and `postgres-role` rotate by machine, but no row is SDS-used."""
    row = {**_real_row(OPERATOR), "class": credential_class}
    package = _tip(_approved(_real_row(OPERATOR)))
    package["profile_fields"]["destinations"].append(f'provider name="{provider}"')
    condition = reissue_type(_entry(row), package)
    assert not condition.passed
    assert condition.reason == f"class {credential_class!r} is no reissue type"


def test_a_package_naming_no_reissue_provider_refuses() -> None:
    entry, approved, tip, lineage = _covered()
    tip["profile_fields"]["destinations"] = [
        d for d in tip["profile_fields"]["destinations"] if not d.startswith("provider ")
    ]
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert REISSUE_TYPE in _refused(conditions)
    assert _reason(conditions, REISSUE_TYPE) == 'the package lists no provider name="orchestrator"'


def test_the_reissue_types_are_the_two_sds_services() -> None:
    assert REISSUE_TYPES == ("orchestrator", "change-manager")


# ---- decision 4's identities, refused by name --------------------------------------------------


@pytest.mark.parametrize("credential_id", HAND_ROTATED_IDS)
def test_a_hand_rotated_id_refuses_even_flagged(credential_id: str) -> None:
    """The id alone refuses: the row keeps the operator's hashed entry and sets the flag."""
    row = {**_real_row(OPERATOR), "id": credential_id, "rotated_by_sds": True}
    entry, approved, tip, lineage = _covered(row)
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert _refused(conditions) == [NOT_HAND_ROTATED]
    assert _reason(conditions, NOT_HAND_ROTATED) == f"{credential_id} is a hand-rotated identity"


@pytest.mark.parametrize("prefix", HAND_ROTATED_ENTRY_PREFIXES)
def test_a_hashed_entry_under_a_hand_rotated_prefix_refuses(prefix: str) -> None:
    row = _real_row(OPERATOR)
    (hashed,) = [c for c in row["consumer"] if c["kind"] == "coolify-env-hash"]
    hashed["entry"] = f"{prefix}-v2"
    entry, approved, tip, lineage = _covered(row)
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert _refused(conditions) == [NOT_HAND_ROTATED]
    assert f"{prefix}-v2" in _reason(conditions, NOT_HAND_ROTATED)


def test_the_real_system_and_verifier_rows_refuse_by_id_and_entry() -> None:
    for credential_id in HAND_ROTATED_IDS:
        row = {**_real_row(credential_id), "rotated_by_sds": True}
        entry, approved, tip, lineage = _covered(row)
        assert NOT_HAND_ROTATED in _refused(reissue(entry, tip, lineage, approved=approved))


# ---- the standing answer's condition 2: no person's act ---------------------------------------


def test_a_plan_with_a_person_s_act_refuses_machine_plan() -> None:
    """A fine-grained PAT is minted by a person (path P2)."""
    row = {
        "id": OPERATOR,
        "class": "github-pat-fine-grained",
        "provider": "github-pat-fine-grained",
        "bws_uuid": "11111111-1111-4111-8111-111111111111",
        "rotated_by_sds": True,
        "consumer": [
            {"kind": "bws-secret", "uuid": "11111111-1111-4111-8111-111111111111"},
            {"kind": "gh-actions-secret", "repo": "AlobarQuest/example", "name": "T"},
        ],
    }
    entry, approved, tip, lineage = _covered(row)
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert MACHINE_PLAN in _refused(conditions)
    assert _reason(conditions, MACHINE_PLAN) == "the stated plan has a person's act"


def test_a_stated_plan_other_than_the_derived_one_refuses_machine_plan() -> None:
    """A lane token's criteria stating its issue plan, at a reissue: `plan_for` derives the
    rotation plan. (The only type with both plans; reissue types have one.)"""
    lane = _entry(_real_row("secrets-broker-bws-token"))
    tip = _tip(_approved(_real_row("secrets-broker-bws-token")))
    tip["acceptance"] = render(lane, OCCURRENCE, CREATED_AT, first_issue=True)["acceptance"]
    condition = machine_plan(lane, tip)
    assert not condition.passed
    assert "the stated plan isn't the one plan_for derives for the row" in condition.reason


def test_criteria_that_state_no_plan_of_the_row_are_unreadable_never_a_pass() -> None:
    entry, approved, tip, lineage = _covered()
    lane = _entry(_real_row("secrets-broker-bws-token"))
    tip["acceptance"] = render(lane, OCCURRENCE, CREATED_AT, first_issue=False)["acceptance"]
    with pytest.raises(ContractError):
        reissue(entry, tip, lineage, approved=approved)


# ---- the standing answer's condition 3 and reading 3: destinations, authority, reach ------------


def test_registry_destinations_unequal_refuse() -> None:
    """A reader the row doesn't name: no copy, so only the registry comparison refuses."""
    entry, approved, tip, lineage = _covered()
    observer = _entry(_real_row("orchestrator-observer-bearer"))
    reader = next(d for d in observer.destinations if d.startswith("runtime-fetch "))
    tip["profile_fields"]["destinations"].append(reader)
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert _refused(conditions) == [DESTINATIONS_EQUAL]


@pytest.mark.parametrize(
    "consumer",
    [
        {"kind": "gh-actions-secret", "repo": "AlobarQuest/example", "name": "ORCH_TOKEN"},
        {
            "kind": "coolify-env",
            "instance": "prod",
            "resource_type": "application",
            "uuid": "anotherapplication000000",
            "key": "ORCH_TOKEN",
            "redeploy": False,
        },
    ],
    ids=["gh-actions-secret", "coolify-env"],
)
def test_an_added_written_copy_refuses_no_new_copy(consumer: dict[str, Any]) -> None:
    """The row and the tip both gain the copy, so only the comparison with the approved revision
    can see it; the approved revision is given the new row's criteria so nothing else differs."""
    row = _real_row(OPERATOR)
    widened = copy.deepcopy(row)
    widened["consumer"].append(consumer)
    approved = _approved(widened)
    approved["profile_fields"]["destinations"] = _approved(row)["profile_fields"]["destinations"]
    tip = _tip(_approved(widened))
    conditions = reissue(_entry(widened), tip, _lineage(approved, tip), approved=approved)
    assert _refused(conditions) == [NO_NEW_COPY]
    assert _reason(conditions, NO_NEW_COPY) == f"the tip adds a written copy: {consumer['kind']}"


def test_the_same_kind_at_a_new_place_is_a_new_copy() -> None:
    row = _real_row(OPERATOR)
    (keeper,) = [c for c in row["consumer"] if c["kind"] == "bws-secret"]
    moved = copy.deepcopy(row)
    (moved_keeper,) = [c for c in moved["consumer"] if c["kind"] == "bws-secret"]
    moved_keeper["uuid"] = "44444444-4444-4444-8444-444444444444"
    moved["bws_uuid"] = moved_keeper["uuid"]
    approved = _approved(moved)
    approved["profile_fields"]["destinations"] = _approved(row)["profile_fields"]["destinations"]
    tip = _tip(_approved(moved))
    conditions = reissue(_entry(moved), tip, _lineage(approved, tip), approved=approved)
    assert keeper["uuid"] != moved_keeper["uuid"]
    assert _refused(conditions) == [NO_NEW_COPY]


def test_an_added_runtime_fetch_reader_passes() -> None:
    """Reading 3: a reader fetches the keeper by its own grant, so it holds no copy."""
    observer = _real_row("orchestrator-observer-bearer")
    row = _real_row(OPERATOR)
    readers = [c for c in observer["consumer"] if c["kind"] == "runtime-fetch"]
    widened = copy.deepcopy(row)
    widened["consumer"].append(readers[0])
    approved = _approved(widened)
    approved["profile_fields"]["destinations"] = _approved(row)["profile_fields"]["destinations"]
    tip = _tip(_approved(widened))
    assert _refused(reissue(_entry(widened), tip, _lineage(approved, tip), approved=approved)) == []


def test_a_widened_authority_refuses() -> None:
    entry, approved, _, _ = _covered()
    approved["authority"]["allowed"].append("repository_write")
    approved["authority"]["prohibited"].remove("repository_write")
    tip = _tip(approved)
    conditions = reissue(entry, tip, _lineage(approved, tip), approved=approved)
    assert _refused(conditions) == [AUTHORITY]


def test_a_changed_reach_refuses() -> None:
    entry, approved, _, _ = _covered()
    approved["reach"] = [*approved["reach"], "source_repository"]
    tip = _tip(approved)
    conditions = reissue(entry, tip, _lineage(approved, tip), approved=approved)
    assert _refused(conditions) == [REACH]


# ---- reading 1: the tip is the approved revision, revised by the proposer -----------------------


@pytest.mark.parametrize(
    "edit",
    [
        lambda tip: tip.update(title="Standing rotation, reworded"),
        lambda tip: tip["profile_fields"].update(operating_procedure="Reworded."),
        lambda tip: tip["acceptance"][0].update(condition="Reworded."),
        lambda tip: tip["outcome"].update(why="Reworded."),
    ],
    ids=["title", "operating_procedure", "criterion", "outcome"],
)
def test_a_field_changed_outside_the_four_refuses(edit: Callable[[dict[str, Any]], None]) -> None:
    entry, approved, tip, _ = _covered()
    edit(tip)
    conditions = reissue(entry, tip, _lineage(approved, tip), approved=approved)
    assert _refused(conditions) == [PROPOSER_REVISED]
    assert "the tip differs from the approved revision in" in _reason(conditions, PROPOSER_REVISED)


def test_the_approved_revision_must_hash_to_its_approval() -> None:
    entry, approved, tip, lineage = _covered()
    edited = {**approved, "title": "Not what was approved"}
    conditions = reissue(entry, tip, lineage, approved=edited)
    assert PROPOSER_REVISED in _refused(conditions)
    assert "doesn't hash to its approved_hash" in _reason(conditions, PROPOSER_REVISED)


def test_an_approval_commit_off_main_refuses() -> None:
    entry, _, tip, lineage = _covered()
    conditions = reissue(entry, tip, lineage, approved=None)
    assert set(_refused(conditions)) == {PROPOSER_REVISED, NO_NEW_COPY}


# ---- the lineage, the command's form -----------------------------------------------------------


def _lineage_edits() -> list[Any]:
    def policy_first(lineage: dict[str, Any]) -> None:
        lineage["approvals"][0]["approver"] = POLICY

    def unknown_first(lineage: dict[str, Any]) -> None:
        lineage["approvals"][0]["approver"] = "claude-code-interactive"

    def no_first(lineage: dict[str, Any]) -> None:
        lineage["approvals"] = []

    def tip_approved(lineage: dict[str, Any]) -> None:
        tip_hash = lineage["revisions"][-1]["hash"]
        lineage["approvals"].append({"revision": 2, "approved_hash": tip_hash, "approver": POLICY})

    def gap(lineage: dict[str, Any]) -> None:
        lineage["revisions"][-1]["revision"] = 3

    def wrong_hash(lineage: dict[str, Any]) -> None:
        lineage["approvals"][0]["approved_hash"] = "f" * 64

    def twice(lineage: dict[str, Any]) -> None:
        lineage["approvals"].append(dict(lineage["approvals"][0]))

    def unlisted(lineage: dict[str, Any]) -> None:
        lineage["approvals"].append({**lineage["approvals"][0], "revision": 7})

    def unknown_later(lineage: dict[str, Any]) -> None:
        lineage["revisions"].insert(1, {"revision": 2, "hash": "2" * 64})
        lineage["revisions"][-1]["revision"] = 3
        lineage["approvals"].append(
            {"revision": 2, "approved_hash": "2" * 64, "approver": "someone"}
        )

    def another_package(lineage: dict[str, Any]) -> None:
        lineage["package_id"] = "rotation-another"

    def not_in_review(lineage: dict[str, Any]) -> None:
        lineage["current_state"] = "approved"

    return [
        pytest.param(policy_first, "revision 1's approver isn't a named person", id="policy-r1"),
        pytest.param(unknown_first, "revision 1's approver isn't a named person", id="unknown-r1"),
        pytest.param(no_first, "revision 1 isn't approved", id="r1-unapproved"),
        pytest.param(tip_approved, "the lineage records an approval of the tip", id="tip-approved"),
        pytest.param(gap, "the lineage doesn't list revisions 1 to 2", id="revisions-gap"),
        pytest.param(wrong_hash, "revision 1's approval isn't on its listed hash", id="hash"),
        pytest.param(twice, "revision 1 is approved twice", id="twice"),
        pytest.param(unlisted, "names a revision the lineage doesn't list", id="unlisted"),
        pytest.param(unknown_later, "neither a named person nor policy", id="unknown-later"),
        pytest.param(another_package, "the lineage names another package", id="package"),
        pytest.param(not_in_review, "current_state isn't ready_for_review", id="state"),
    ]


@pytest.mark.parametrize(("edit", "reason"), _lineage_edits())
def test_each_lineage_exclusion_refuses(
    edit: Callable[[dict[str, Any]], None], reason: str
) -> None:
    entry, approved, tip, lineage = _covered()
    edit(lineage)
    if edit.__name__ == "unknown_later":
        tip["revision"] = 3
    conditions = reissue(entry, tip, lineage, approved=approved)
    # An edit that moves the last approval (a wrong hash, a repeat, an unlisted revision) also
    # breaks the content comparison; the lineage condition names the exclusion either way.
    assert REISSUE_LINEAGE in _refused(conditions)
    assert set(_refused(conditions)) <= {REISSUE_LINEAGE, PROPOSER_REVISED}
    assert reason in _reason(conditions, REISSUE_LINEAGE)


def test_a_tip_not_in_review_refuses() -> None:
    entry, approved, tip, lineage = _covered()
    tip["status"] = "draft"
    conditions = reissue(entry, tip, lineage, approved=approved)
    assert _refused(conditions) == [REISSUE_LINEAGE]


def test_the_named_approvers_are_the_lineages_own() -> None:
    """Every approver of these packages' revisions at intent-packages `e82a246a` is Devon."""
    assert NAMED_APPROVERS == ("devon",)


# ---- the carrier's form ------------------------------------------------------------------------


def _carried(tip: dict[str, Any], lineage: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The lineage once the policy approved the tip, and the approval the emitter verified."""
    tip = {**tip, "status": "approved"}
    approval = {
        "revision": tip["revision"],
        "approved_hash": package_hash(tip),
        "approver": POLICY,
        "approved_at": "2026-11-28T18:00:00+00:00",
        "commit": "b" * 40,
        "event_id": "evt-2",
    }
    lineage = copy.deepcopy(lineage)
    lineage["revisions"][-1]["hash"] = package_hash(tip)
    lineage["current_state"] = "approved"
    lineage["approvals"].append(approval)
    verified = {k: approval[k] for k in ("approver", "approved_hash", "revision", "commit")}
    return lineage, {**verified, "event_id": approval["event_id"]}


def _carry(
    entry: RegistryEntry, tip: dict[str, Any], lineage: dict[str, Any], verified: dict[str, Any]
) -> list[Condition]:
    tip = {**tip, "status": "approved"}
    return carry_reissue(entry, tip, lineage, package_hash=package_hash(tip), verified=verified)


def test_the_carry_form_refuses_nothing_on_a_policy_approved_reissue() -> None:
    entry, _, tip, lineage = _covered()
    carried, verified = _carried(tip, lineage)
    conditions = _carry(entry, tip, carried, verified)
    assert [condition.name for condition in conditions] == CARRIED
    assert _refused(conditions) == []


@pytest.mark.parametrize("field", ["approver", "approved_hash", "revision", "commit", "event_id"])
def test_a_tip_approval_the_verified_chain_event_doesn_t_back_refuses(field: str) -> None:
    """D-84 condition 2's shape: a lineage approval written without the chain event refuses."""
    entry, _, tip, lineage = _covered()
    carried, verified = _carried(tip, lineage)
    forged = {**verified, field: "forged"}
    conditions = _carry(entry, tip, carried, forged)
    assert _refused(conditions) == [POLICY_REISSUE_LINEAGE]
    assert f"differs in {field}" in _reason(conditions, POLICY_REISSUE_LINEAGE)


def _carry_edits() -> list[Any]:
    def person_on_tip(lineage: dict[str, Any], verified: dict[str, Any]) -> None:
        lineage["approvals"][-1]["approver"] = "devon"
        verified["approver"] = "devon"

    def twice_on_tip(lineage: dict[str, Any], verified: dict[str, Any]) -> None:
        lineage["approvals"].append(dict(lineage["approvals"][-1]))

    def none_on_tip(lineage: dict[str, Any], verified: dict[str, Any]) -> None:
        lineage["approvals"].pop()

    def other_hash(lineage: dict[str, Any], verified: dict[str, Any]) -> None:
        lineage["approvals"][-1]["approved_hash"] = "e" * 64
        verified["approved_hash"] = "e" * 64

    def policy_first(lineage: dict[str, Any], verified: dict[str, Any]) -> None:
        lineage["approvals"][0]["approver"] = POLICY

    def unknown_first(lineage: dict[str, Any], verified: dict[str, Any]) -> None:
        lineage["approvals"][0]["approver"] = "someone"

    return [
        pytest.param(person_on_tip, "the tip's approver isn't policy:", id="person-on-tip"),
        pytest.param(twice_on_tip, "exactly one approval of the tip", id="twice-on-tip"),
        pytest.param(none_on_tip, "exactly one approval of the tip", id="none-on-tip"),
        pytest.param(other_hash, "the tip's approval hash isn't the package's", id="hash"),
        pytest.param(policy_first, "revision 1's approver isn't a named person", id="policy-r1"),
        pytest.param(unknown_first, "revision 1's approver isn't a named person", id="unknown-r1"),
    ]


@pytest.mark.parametrize(("edit", "reason"), _carry_edits())
def test_each_carry_lineage_exclusion_refuses(
    edit: Callable[[dict[str, Any], dict[str, Any]], None], reason: str
) -> None:
    entry, _, tip, lineage = _covered()
    carried, verified = _carried(tip, lineage)
    edit(carried, verified)
    conditions = _carry(entry, tip, carried, verified)
    assert _refused(conditions) == [POLICY_REISSUE_LINEAGE]
    assert reason in _reason(conditions, POLICY_REISSUE_LINEAGE)


@pytest.mark.parametrize("credential_id", HAND_ROTATED_IDS)
def test_the_carry_form_refuses_a_hand_rotated_id(credential_id: str) -> None:
    row = {**_real_row(OPERATOR), "id": credential_id}
    entry, _, tip, lineage = _covered(row)
    carried, verified = _carried(tip, lineage)
    assert _refused(_carry(entry, tip, carried, verified)) == [NOT_HAND_ROTATED]


def test_the_carry_form_refuses_a_lane_type() -> None:
    entry, _, tip, lineage = _covered(_real_row("secrets-broker-bws-token"))
    carried, verified = _carried(tip, lineage)
    assert _refused(_carry(entry, tip, carried, verified)) == [REISSUE_TYPE, MACHINE_PLAN]


def test_the_carry_form_refuses_a_row_not_rotated_by_sds() -> None:
    entry, _, tip, lineage = _covered({**_real_row(OPERATOR), "rotated_by_sds": False})
    carried, verified = _carried(tip, lineage)
    assert _refused(_carry(entry, tip, carried, verified)) == [SDS_ROTATED]


# ---- the command --------------------------------------------------------------------------------


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(value)


def _registry_text(row: dict[str, Any]) -> str:
    lines = ["version = 1", "", "[[credential]]"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in row.items() if k != "consumer"]
    for consumer in row.get("consumer", []):
        lines += ["", "  [[credential.consumer]]"]
        lines += [f"  {k} = {_toml_value(v)}" for k, v in consumer.items()]
    return "\n".join(lines) + "\n"


class Rig:
    def __init__(self, origins: Origins, mirror: RepoMirror, monkeypatch: pytest.MonkeyPatch):
        self.origins = origins
        self.served: dict[str, str | None] | None = {}
        monkeypatch.setattr(cli, "_standing_approval_mirror", lambda: mirror)
        monkeypatch.setattr(cli, "read_due", lambda: ([], [], self.served))

    def publish(
        self,
        row: dict[str, Any],
        approved: dict[str, Any],
        tip: dict[str, Any],
        *,
        last_rotated: str | None = None,
        approval_commit: str | None = None,
    ) -> None:
        """Commit the row, then revision 1 at its approval commit, then the tip and its lineage."""
        credential_id = row["id"]
        for repository in REGISTRY_REPOSITORIES:
            text = _registry_text(row) if repository == REGISTRY else "version = 1\n"
            self.origins.commit(repository, {REGISTRY_PATH: text})
        commit = self.origins.commit(
            PACKAGES_REPOSITORY,
            {package_path(credential_id): yaml.safe_dump(approved, sort_keys=False)},
        )
        lineage = _lineage(approved, tip, commit=approval_commit or commit)
        self.origins.commit(
            PACKAGES_REPOSITORY,
            {
                package_path(credential_id): yaml.safe_dump(tip, sort_keys=False),
                lineage_path(credential_id): yaml.safe_dump(lineage, sort_keys=False),
            },
        )
        self.served = {credential_id: last_rotated}


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    origins = Origins(tmp_path / "origins")
    return Rig(origins, RepoMirror(tmp_path / "mirrors", origin=origins.url), monkeypatch)


def _run(capsys: pytest.CaptureFixture[str], credential_id: str) -> tuple[int, list[dict]]:
    code = cli.run(["standing-approval", credential_id])
    out, err = capsys.readouterr()
    assert err == ""
    return code, [json.loads(line) for line in out.splitlines()]


def _verdict(lines: list[dict[str, Any]]) -> list[str]:
    (verdict,) = [line for line in lines if line["event"] == "standing_approval.verdict"]
    return verdict["refused"]


@pytest.mark.parametrize(
    ("last_rotated", "after"),
    [(None, None), ("2026-10-01", False), ("2026-10-15", True), ("2026-10-21", True)],
)
def test_the_command_runs_the_reissue_form_and_reports_rotated_before(
    rig: Rig, capsys: pytest.CaptureFixture[str], last_rotated: str | None, after: bool | None
) -> None:
    """`rotated_before` is a fact, never a condition (HQ's call on #838): a null or earlier
    `last_rotated` refuses nothing."""
    row = _real_row(OPERATOR)
    approved = _approved(row)
    rig.publish(row, approved, _tip(approved), last_rotated=last_rotated)
    code, lines = _run(capsys, OPERATOR)
    assert code == 0
    conditions = [line["condition"] for line in lines if line["event"].endswith(".condition")]
    assert conditions == EVERY
    (fact,) = [line for line in lines if line["event"] == "standing_approval.fact"]
    assert fact == {
        "event": "standing_approval.fact",
        "credential_id": OPERATOR,
        "fact": "rotated_before",
        "last_rotated": last_rotated,
        "revision_1_approved_on": "2026-10-15",
        "on_or_after_revision_1_approval": after,
    }


def test_the_command_reads_the_approved_revision_at_its_commit(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the approval's commit holds is compared, not what the caller supplies: a package
    committed there that isn't the approved one refuses on its hash."""
    row = _real_row(OPERATOR)
    approved = _approved(row)
    tip = _tip(approved)
    credential_id = row["id"]
    for repository in REGISTRY_REPOSITORIES:
        text = _registry_text(row) if repository == REGISTRY else "version = 1\n"
        rig.origins.commit(repository, {REGISTRY_PATH: text})
    edited = {**approved, "title": "Not what was approved"}
    commit = rig.origins.commit(
        PACKAGES_REPOSITORY, {package_path(credential_id): yaml.safe_dump(edited, sort_keys=False)}
    )
    rig.origins.commit(
        PACKAGES_REPOSITORY,
        {
            package_path(credential_id): yaml.safe_dump(tip, sort_keys=False),
            lineage_path(credential_id): yaml.safe_dump(
                _lineage(approved, tip, commit=commit), sort_keys=False
            ),
        },
    )
    rig.served = {credential_id: None}
    code, lines = _run(capsys, OPERATOR)
    assert (code, _verdict(lines)) == (1, [PROPOSER_REVISED])
    (revised,) = [line for line in lines if line.get("condition") == PROPOSER_REVISED]
    assert "doesn't hash to its approved_hash" in revised["reason"]


def test_an_approval_commit_the_mirror_lacks_refuses(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _real_row(OPERATOR)
    approved = _approved(row)
    rig.publish(row, approved, _tip(approved), approval_commit="c" * 40)
    code, lines = _run(capsys, OPERATOR)
    assert (code, set(_verdict(lines))) == (1, {PROPOSER_REVISED, NO_NEW_COPY})


def test_revision_1_of_a_reissue_type_runs_the_lane_form_and_refuses(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """A first hand-over (reading 2): revision 1 of an orchestrator bearer stays Devon's."""
    row = _real_row(OPERATOR)
    approved = _approved(row)
    first = {**approved, "status": "ready_for_review"}
    credential_id = row["id"]
    for repository in REGISTRY_REPOSITORIES:
        text = _registry_text(row) if repository == REGISTRY else "version = 1\n"
        rig.origins.commit(repository, {REGISTRY_PATH: text})
    lineage = _lineage(approved, _tip(approved))
    lineage.update(approvals=[], revisions=lineage["revisions"][:1])
    rig.origins.commit(
        PACKAGES_REPOSITORY,
        {
            package_path(credential_id): yaml.safe_dump(first, sort_keys=False),
            lineage_path(credential_id): yaml.safe_dump(lineage, sort_keys=False),
        },
    )
    rig.served = {credential_id: None}
    code, lines = _run(capsys, OPERATOR)
    assert code == 1
    assert LANE_TYPE in _verdict(lines)
    assert not [line for line in lines if line["event"] == "standing_approval.fact"]


def test_a_last_rotated_infraops_doesn_t_serve_is_unreadable_for_a_reissue(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fact doesn't refuse, but a read that fails is still exit 2 (Code Brain rule 4)."""
    row = _real_row(OPERATOR)
    approved = _approved(row)
    rig.publish(row, approved, _tip(approved))
    rig.served = None
    code, lines = _run(capsys, OPERATOR)
    assert code == 2
    assert lines[-1]["event"] == "standing_approval.unreadable"
