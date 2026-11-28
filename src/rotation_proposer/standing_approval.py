"""The standing approval's conditions on a lane credential's revision 1 (ADR-0055 amendment 22).

Amendment 22 decision item 2 lists what must hold before a standing answer may approve revision 1
of a lane credential's generated package. Each condition here is one refusal, checked on what the
caller has already read: the credential's registry row, the stored package and its lineage at one
intent-packages commit, and the `last_rotated` infraops serves for the credential. Nothing here
reads, writes or approves anything. `rotation-proposer standing-approval` (`cli.py`) reads the
inputs, runs `evaluate`, and reports every condition.

The module imports only the generator and the executor's pure modules, so the work carrier runs
the same checks, not a copy (amendment 22 item 3). `carry` is the carrier's form, and differs in
three ways: it replaces `unapproved_revision_1` with `policy_approved_revision_1` (exactly one
policy approval, on revision 1, whose hash is the package's); its caller reads the registry at
`main`, because the lineage records only the package commit; and it reads no infraops, so it
accepts only the issue-plan re-render (`first_issue=True`) and states the plan from the row and
the package's criteria alone.

**A REISSUE HAS ITS OWN TWO FORMS** (ADR-0055 amendment 39, D-88 A). At revision 2 or later the
command runs `reissue` and the carrier `carry_reissue`: the row is SDS-rotated, its type is one of
`REISSUE_TYPES`, it isn't a hand-rotated identity, its plan has no person's act, the lineage's
revision 1 was approved by a named person (`NAMED_APPROVERS`), the destinations equal the
registry's, and the authority and reach are the type's standard ones. The command also compares
the tip with the last approved revision, read at that approval's commit (`proposer_revised`,
`no_new_copy`); the carrier's depth-1 clone can't, so it relies on the command's run (amendment 39
reading 8). infraops' `last_rotated` is a reported fact for a reissue (`rotated_before`), never a
condition (HQ's call on #838).

A condition never catches an exception: anything the generator or the plan reader raises reaches
the command, which exits 2, because a check it couldn't make is never a pass.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from rotation_proposer.generate import CREATED_BY, authority, plan_for, reach, render
from rotation_worker.canonical import package_hash as content_hash
from rotation_worker.contract import HumanAct
from rotation_worker.destinations import parse, same
from rotation_worker.destinations import render as render_destination
from rotation_worker.plans import candidates, select_plan
from rotation_worker.registry import RegistryEntry, package_path, standing_destinations
from rotation_worker.types import CLASS_TYPES, TYPES

# The two lane types the standing answer covers, each with the destination kinds its grant allows
# (amendment 22 item 2, "Destinations equal"). A lane token's `coolify-env` must not redeploy.
LANE_KINDS: Final[Mapping[str, Counter[str]]] = {
    "bws-lane-token": Counter({"provider": 1, "bws-secret": 1, "coolify-env": 1}),
    "healthchecks-ping-url": Counter({"provider": 1, "bws-secret": 1}),
}
READY_FOR_REVIEW: Final = "ready_for_review"
# The approver the standing approval records (amendment 22 item 3). Keyed on the prefix, as
# `bump_policy` keys on `policy:dependency-update@`, so a later schema version doesn't stop it.
POLICY_APPROVER_PREFIX: Final = "policy:non-software-operational@"

GENERATED: Final = "proposer_generated"
REVISION_1: Final = "unapproved_revision_1"
POLICY_REVISION_1: Final = "policy_approved_revision_1"
ISSUE_PLAN: Final = "issue_plan"
LANE_TYPE: Final = "lane_type"
DESTINATIONS: Final = "destinations"
AUTHORITY: Final = "standard_authority"

# A reissue (ADR-0055 amendment 39, D-88 A). The types whose provider is an SDS service (reading 4);
# a type joins only beside a row that meets the standing answer's condition 1, by Devon's decision.
# Sorted, and so never an argv whose head is the orchestrator's console script to the producer
# guard (`test_external_content_observes_only.py`).
REISSUE_TYPES: Final = ("change-manager", "orchestrator")  # not-a-vocabulary: type names
# Decision 4's identities, refused by name whatever their flag says (reading 6): the SYSTEM and
# VERIFIER bearers by id, and any row whose hashed entry is theirs or the executor's own.
HAND_ROTATED_IDS: Final = (  # not-a-vocabulary: registry ids; the gate's copy is held equal by test
    "orchestrator-system-bearer",
    "orchestrator-verifier-bearer",
)
HAND_ROTATED_ENTRY_PREFIXES: Final = (  # not-a-vocabulary: M2M entry-name prefixes
    "orchestrator-system",
    "orchestrator-verifier",
    "rotation-executor",
)
# The people whose approval of revision 1 a reissue may build on: every approver in these
# packages' intent-packages lineages at `e82a246a`. Any other string refuses (HQ's call on #838).
NAMED_APPROVERS: Final = ("devon",)  # not-a-vocabulary: approver ids read from the lineages
# The one destination kind that holds no copy (reading 3): its reader fetches the keeper by its
# own grant. Every other kind, known or not, is a copy the executor writes.
NO_COPY_KINDS: Final = frozenset({"runtime-fetch"})  # not-a-vocabulary: a destination kind
# What a proposer's revision may change from the last approved one (reading 1).
REVISED_FIELDS: Final = frozenset({"revision", "status"})  # not-a-vocabulary: package fields
REVISED_PROFILE_FIELDS: Final = frozenset(  # not-a-vocabulary: package profile fields
    {"occurrence", "destinations"}
)

SDS_ROTATED: Final = "rotated_by_sds"
REISSUE_TYPE: Final = "reissue_type"
NOT_HAND_ROTATED: Final = "not_hand_rotated"
MACHINE_PLAN: Final = "machine_plan"
REISSUE_LINEAGE: Final = "reissue_lineage"
POLICY_REISSUE_LINEAGE: Final = "policy_approved_reissue"
PROPOSER_REVISED: Final = "proposer_revised"
NO_NEW_COPY: Final = "no_new_copy"
DESTINATIONS_EQUAL: Final = "destinations_equal"
REACH: Final = "reach_unchanged"


@dataclass(frozen=True)
class Condition:
    """One condition's result. `reason` names fields and ids, never a field's contents."""

    name: str
    passed: bool
    reason: str


def _condition(name: str, problems: list[str], satisfied: str) -> Condition:
    return Condition(name, not problems, "; ".join(problems) if problems else satisfied)


def _fields(package: Mapping[str, Any]) -> Mapping[str, Any]:
    fields = package.get("profile_fields")
    return fields if isinstance(fields, Mapping) else {}


def proposer_generated(
    entry: RegistryEntry, package: Mapping[str, Any], *, first_issue: bool
) -> Condition:
    """The re-render from the row, with the package's own occurrence and `created_at`, equals the
    stored package in every field apart from `status`, and `created_by` is the proposer's."""
    problems = []
    if package.get("created_by") != CREATED_BY:
        problems.append(f"created_by isn't {CREATED_BY!r}")
    occurrence = _fields(package).get("occurrence")
    created_at = package.get("created_at")
    if not isinstance(occurrence, str) or not isinstance(created_at, str):
        problems.append("the package states no occurrence or created_at to re-render with")
        return _condition(GENERATED, problems, "")
    rendered = render(entry, occurrence, created_at, first_issue=first_issue)
    differing = sorted(
        key
        for key in (rendered.keys() | package.keys()) - {"status"}
        if key not in rendered or key not in package or rendered[key] != package[key]
    )
    if differing:
        problems.append(f"the re-render differs in {', '.join(differing)}")
    return _condition(GENERATED, problems, "the re-render equals the package apart from status")


def unapproved_revision_1(package: Mapping[str, Any], lineage: Mapping[str, Any]) -> Condition:
    """Revision 1, its lineage one revision with no approval, in `ready_for_review`."""
    problems = []
    if package.get("revision") != 1:
        problems.append("the package's revision isn't 1")
    if lineage.get("package_id") != package.get("package_id"):
        problems.append("the lineage names another package")
    revisions = lineage.get("revisions")
    if not isinstance(revisions, list) or len(revisions) != 1:
        problems.append("the lineage doesn't list exactly one revision")
    elif not isinstance(revisions[0], Mapping) or revisions[0].get("revision") != 1:
        problems.append("the lineage's one revision isn't revision 1")
    if lineage.get("approvals") != []:
        problems.append("the lineage records an approval")
    if lineage.get("current_state") != READY_FOR_REVIEW:
        problems.append(f"the lineage's current_state isn't {READY_FOR_REVIEW}")
    return _condition(REVISION_1, problems, "revision 1, unapproved, ready_for_review")


def policy_approved_revision_1(
    package: Mapping[str, Any], lineage: Mapping[str, Any], *, package_hash: str
) -> Condition:
    """The carry form's lineage condition: revision 1, its lineage one revision and exactly one
    approval, on revision 1, by the standing approval's policy approver, whose hash is the
    package's. `package_hash` is the package's content hash, which the caller computes (the
    carrier with `rotation_worker.canonical.package_hash`), as it reads every other input."""
    problems = []
    if package.get("revision") != 1:
        problems.append("the package's revision isn't 1")
    if lineage.get("package_id") != package.get("package_id"):
        problems.append("the lineage names another package")
    revisions = lineage.get("revisions")
    if not isinstance(revisions, list) or len(revisions) != 1:
        problems.append("the lineage doesn't list exactly one revision")
    elif not isinstance(revisions[0], Mapping) or revisions[0].get("revision") != 1:
        problems.append("the lineage's one revision isn't revision 1")
    approvals = lineage.get("approvals")
    if not isinstance(approvals, list) or len(approvals) != 1:
        problems.append("the lineage doesn't record exactly one approval")
        return _condition(POLICY_REVISION_1, problems, "")
    approval = approvals[0]
    if not isinstance(approval, Mapping):
        problems.append("the lineage's approval isn't a mapping")
        return _condition(POLICY_REVISION_1, problems, "")
    if approval.get("revision") != 1:
        problems.append("the approval isn't on revision 1")
    approver = approval.get("approver")
    if not isinstance(approver, str) or not approver.startswith(POLICY_APPROVER_PREFIX):
        problems.append(f"the approver isn't {POLICY_APPROVER_PREFIX}<version>")
    if approval.get("approved_hash") != package_hash:
        problems.append("the approval's hash isn't the package's")
    return _condition(
        POLICY_REVISION_1, problems, "revision 1, approved once by the standing approval's policy"
    )


def issue_plan(
    entry: RegistryEntry, package: Mapping[str, Any], *, last_rotated: str | None
) -> Condition:
    """infraops records the credential never rotated, and the plan `plan_for` chooses for the row
    and the plan the package's criteria state are both the issue plan."""
    problems = []
    if last_rotated is not None:
        problems.append(f"infraops records the credential rotated {last_rotated[:10]}")
    problems += _plan_problems(entry, package, first_issue=last_rotated is None)
    return _condition(ISSUE_PLAN, problems, "last_rotated is null and both plans are the issue")


def stated_issue_plan(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The carry form's issue-plan condition: it reads no infraops, so it accepts only the issue
    plan, chosen for the row and stated by the package's criteria (amendment 22 item 3)."""
    problems = _plan_problems(entry, package, first_issue=True)
    return _condition(ISSUE_PLAN, problems, "both plans are the issue (infraops not read)")


def _plan_problems(
    entry: RegistryEntry, package: Mapping[str, Any], *, first_issue: bool
) -> list[str]:
    problems = []
    adapter_name, derived = plan_for(entry, first_issue=first_issue)
    if not derived.issue:
        problems.append("plan_for chooses the rotation plan for the row")
    declared = TYPES[adapter_name]
    plans = candidates(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    if not select_plan(plans, package.get("acceptance")).issue:
        problems.append("the package's criteria state the rotation plan")
    return problems


def lane_type(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The row's class is one of the two lane types, and the package names it as its provider."""
    credential_class = entry.credential.get("class")
    if credential_class not in LANE_KINDS:
        return Condition(LANE_TYPE, False, f"class {credential_class!r} is no lane type")
    provider = render_destination({"kind": "provider", "name": credential_class})
    listed = _fields(package).get("destinations")
    if not isinstance(listed, list) or provider not in listed:
        return Condition(LANE_TYPE, False, f"the package lists no {provider}")
    return Condition(LANE_TYPE, True, f"{credential_class}, named by its provider destination")


def lane_destinations(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The package lists exactly the row's destinations, in any order, and they are the lane
    type's kinds: one keeper, one provider, and for a lane token one `coolify-env` that doesn't
    redeploy. A destination list that doesn't parse raises."""
    credential_id = str(entry.credential.get("id"))
    listed = standing_destinations(
        package.get("profile_fields"), credential_id, package_path(credential_id)
    )
    problems = []
    if not same(entry.destinations, listed):
        problems.append("the package's destinations aren't the registry's")
    consumers = [parse(destination) for destination in listed]
    kinds = LANE_KINDS.get(str(entry.credential.get("class")))
    if kinds is None:
        problems.append("the class is no lane type, so no destination kinds are allowed")
    elif Counter(consumer["kind"] for consumer in consumers) != kinds:
        problems.append(f"the destination kinds aren't {', '.join(sorted(kinds.elements()))}")
    if any(c["kind"] == "coolify-env" and c["redeploy"] is not False for c in consumers):
        problems.append("a coolify-env destination redeploys")
    return _condition(DESTINATIONS, problems, "equal to the registry's, and of the type's kinds")


def standard_authority(
    entry: RegistryEntry, package: Mapping[str, Any], *, first_issue: bool
) -> Condition:
    """The package's `authority` is `generate.authority` for the type, the row's destinations and
    the plan, compared whole."""
    adapter_name, plan = plan_for(entry, first_issue=first_issue)
    expected = authority(adapter_name, entry.destinations, issue=plan.issue)
    if package.get("authority") != expected:
        return Condition(AUTHORITY, False, f"the authority isn't {adapter_name}'s standard set")
    return Condition(AUTHORITY, True, f"{adapter_name}'s standard set")


def evaluate(
    entry: RegistryEntry,
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
    *,
    last_rotated: str | None,
) -> list[Condition]:
    """Every condition of amendment 22 item 2, in its order. `last_rotated` is what infraops
    serves for the credential: null for one it has never recorded rotated."""
    first_issue = last_rotated is None
    return [
        proposer_generated(entry, package, first_issue=first_issue),
        unapproved_revision_1(package, lineage),
        issue_plan(entry, package, last_rotated=last_rotated),
        lane_type(entry, package),
        lane_destinations(entry, package),
        standard_authority(entry, package, first_issue=first_issue),
    ]


def carry(
    entry: RegistryEntry,
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
    *,
    package_hash: str,
) -> list[Condition]:
    """The carrier's form of item 2's conditions (amendment 22 item 3), in item 2's order. The
    caller reads `entry` at the registry's `main` and computes `package_hash`; nothing here reads
    infraops."""
    return [
        proposer_generated(entry, package, first_issue=True),
        policy_approved_revision_1(package, lineage, package_hash=package_hash),
        stated_issue_plan(entry, package),
        lane_type(entry, package),
        lane_destinations(entry, package),
        standard_authority(entry, package, first_issue=True),
    ]


def sds_rotated(entry: RegistryEntry) -> Condition:
    """The registry row sets `rotated_by_sds = true` (the standing answer's condition 1)."""
    if not entry.rotated_by_sds:
        return Condition(SDS_ROTATED, False, "the registry row doesn't set rotated_by_sds = true")
    return Condition(SDS_ROTATED, True, "the registry row sets rotated_by_sds = true")


def reissue_type(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The row's class maps to a type in `REISSUE_TYPES`, and the package names it as provider."""
    credential_class = entry.credential.get("class")
    adapter_name = CLASS_TYPES.get(str(credential_class))
    if adapter_name not in REISSUE_TYPES:
        return Condition(REISSUE_TYPE, False, f"class {credential_class!r} is no reissue type")
    provider = render_destination({"kind": "provider", "name": adapter_name})
    listed = _fields(package).get("destinations")
    if not isinstance(listed, list) or provider not in listed:
        return Condition(REISSUE_TYPE, False, f"the package lists no {provider}")
    return Condition(REISSUE_TYPE, True, f"{adapter_name}, named by its provider destination")


def _listed(entry: RegistryEntry, package: Mapping[str, Any]) -> list[str]:
    """The package's destinations; a list that doesn't parse raises."""
    credential_id = str(entry.credential.get("id"))
    return standing_destinations(
        package.get("profile_fields"), credential_id, package_path(credential_id)
    )


def not_hand_rotated(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """Neither the row's id nor any `coolify-env-hash` entry, in the row or the package, is one
    of decision 4's identities (amendment 39 reading 6)."""
    problems = []
    credential_id = entry.credential.get("id")
    if credential_id in HAND_ROTATED_IDS:
        problems.append(f"{credential_id} is a hand-rotated identity")
    for destination in sorted({*entry.destinations, *_listed(entry, package)}):
        consumer = parse(destination)
        hashed = consumer.get("entry") if consumer["kind"] == "coolify-env-hash" else None
        if isinstance(hashed, str) and hashed.startswith(HAND_ROTATED_ENTRY_PREFIXES):
            problems.append(f"the hashed entry {hashed!r} is a hand-rotated identity's")
    return _condition(NOT_HAND_ROTATED, problems, "no hand-rotated id or entry")


def machine_plan(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The plan the package's criteria state has no person's act, and it is the rotation plan
    `plan_for` derives for the row (amendment 39 reading 5), so never an issue plan."""
    adapter_name, derived = plan_for(entry, first_issue=False)
    declared = TYPES[adapter_name]
    plans = candidates(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    stated = select_plan(plans, package.get("acceptance"))
    problems = []
    if any(isinstance(segment, HumanAct) for segment in stated.segments):
        problems.append("the stated plan has a person's act")
    if stated != derived:
        problems.append("the stated plan isn't the one plan_for derives for the row")
    return _condition(MACHINE_PLAN, problems, "the derived rotation plan, with no person's act")


def _revision_number(package: Mapping[str, Any]) -> int | None:
    number = package.get("revision")
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def _listed_revisions(
    package: Mapping[str, Any], lineage: Mapping[str, Any]
) -> tuple[list[str], int | None, dict[int, object] | None]:
    """The package's revision N, at least 2, and the hash of each of the lineage's revisions 1
    to N; `None` for what can't be read, beside the problem that says why."""
    number = _revision_number(package)
    if number is None or number < 2:
        return ["the package's revision isn't 2 or later"], None, None
    problems = []
    if lineage.get("package_id") != package.get("package_id"):
        problems.append("the lineage names another package")
    revisions = lineage.get("revisions")
    if not isinstance(revisions, list) or [
        item.get("revision") if isinstance(item, Mapping) else None for item in revisions
    ] != list(range(1, number + 1)):
        problems.append(f"the lineage doesn't list revisions 1 to {number}")
        return problems, number, None
    return problems, number, {item["revision"]: item.get("hash") for item in revisions}


def _earlier_problems(approval: Mapping[str, Any], hashes: Mapping[int, object]) -> list[str]:
    """An approval before the tip stands when it is on a listed revision's hash, by a named
    person or the policy."""
    revision = approval.get("revision")
    if not isinstance(revision, int) or revision not in hashes:
        return ["an approval names a revision the lineage doesn't list"]
    problems = []
    if approval.get("approved_hash") != hashes[revision]:
        problems.append(f"revision {revision}'s approval isn't on its listed hash")
    approver = approval.get("approver")
    if not isinstance(approver, str) or not (
        approver in NAMED_APPROVERS or approver.startswith(POLICY_APPROVER_PREFIX)
    ):
        problems.append(f"revision {revision}'s approver is neither a named person nor policy")
    return problems


def _approval_problems(
    package: Mapping[str, Any], lineage: Mapping[str, Any]
) -> tuple[list[str], int | None, list[Mapping[str, Any]]]:
    """What both reissue lineage conditions require of the lineage and of every approval before
    the tip: the revisions are 1 to N with N at least 2, each earlier approval stands
    (`_earlier_problems`), at most one per revision, and revision 1's is a named person's.
    Returns the problems, N, and the approvals on the tip."""
    problems, number, hashes = _listed_revisions(package, lineage)
    if number is None or hashes is None:
        return problems, number, []
    approvals = lineage.get("approvals")
    if not isinstance(approvals, list) or not all(isinstance(a, Mapping) for a in approvals):
        return [*problems, "the lineage's approvals aren't a list of mappings"], number, []
    on_tip = [approval for approval in approvals if approval.get("revision") == number]
    earlier = [approval for approval in approvals if approval.get("revision") != number]
    for approval in earlier:
        problems += _earlier_problems(approval, hashes)
    approved = Counter(approval.get("revision") for approval in earlier)
    problems += [
        f"revision {revision} is approved twice" for revision, n in approved.items() if n > 1
    ]
    first = [approval for approval in earlier if approval.get("revision") == 1]
    if not first:
        problems.append("revision 1 isn't approved")
    elif any(approval.get("approver") not in NAMED_APPROVERS for approval in first):
        problems.append("revision 1's approver isn't a named person")
    return problems, number, on_tip


def last_approval(lineage: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The approval of the highest revision the lineage records approved, or `None`."""
    approvals = lineage.get("approvals")
    if not isinstance(approvals, list):
        return None
    numbered = [
        approval
        for approval in approvals
        if isinstance(approval, Mapping)
        and isinstance(approval.get("revision"), int)
        and not isinstance(approval.get("revision"), bool)
    ]
    return max(numbered, key=lambda approval: approval["revision"], default=None)


def reissue_lineage(package: Mapping[str, Any], lineage: Mapping[str, Any]) -> Condition:
    """The command's lineage condition: revisions 1 to N, the tip unapproved and
    `ready_for_review`, and revision 1 approved by a named person. A revision never approved
    doesn't refuse: the comparison is with the last approved one."""
    problems, _, on_tip = _approval_problems(package, lineage)
    if on_tip:
        problems.append("the lineage records an approval of the tip")
    if package.get("status") != READY_FOR_REVIEW:
        problems.append(f"the package's status isn't {READY_FOR_REVIEW}")
    if lineage.get("current_state") != READY_FOR_REVIEW:
        problems.append(f"the lineage's current_state isn't {READY_FOR_REVIEW}")
    return _condition(
        REISSUE_LINEAGE, problems, "the tip is unapproved; revision 1 is a named person's"
    )


def policy_reissue_lineage(
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
    *,
    package_hash: str,
    verified: Mapping[str, Any],
) -> Condition:
    """The carry form's lineage condition: the tip's one approval is the policy approver's, on
    the tip's hash, and is field for field the approval the emitter matched to a verified chain
    event (`verified`); every earlier approval stands as `reissue_lineage` requires."""
    problems, number, on_tip = _approval_problems(package, lineage)
    if number is not None and len(on_tip) != 1:
        problems.append("the lineage doesn't record exactly one approval of the tip")
    elif on_tip:
        approval = on_tip[0]
        approver = approval.get("approver")
        if not isinstance(approver, str) or not approver.startswith(POLICY_APPROVER_PREFIX):
            problems.append(f"the tip's approver isn't {POLICY_APPROVER_PREFIX}<version>")
        if approval.get("approved_hash") != package_hash:
            problems.append("the tip's approval hash isn't the package's")
        differing = sorted(
            name
            for name in ("approver", "approved_hash", "revision", "commit", "event_id")
            if approval.get(name) != verified.get(name)
        )
        if differing:
            problems.append(
                "the tip's approval isn't the verified chain event's "
                f"(differs in {', '.join(differing)})"
            )
    return _condition(
        POLICY_REISSUE_LINEAGE,
        problems,
        "the tip is approved once by the policy, as verified; revision 1 is a named person's",
    )


def proposer_revised(
    package: Mapping[str, Any],
    approved: Mapping[str, Any] | None,
    approval: Mapping[str, Any] | None,
) -> Condition:
    """The tip equals the last approved revision, read at that approval's commit and hashing to
    its `approved_hash`, apart from `revision`, `status` and the profile's `occurrence` and
    `destinations` (amendment 39 reading 1). `approved` is `None` when that commit isn't on
    intent-packages' `main`."""
    if approval is None:
        return Condition(PROPOSER_REVISED, False, "the lineage records no approval to compare")
    if approved is None:
        return Condition(
            PROPOSER_REVISED, False, "the last approval's commit isn't on intent-packages main"
        )
    problems = []
    if content_hash(dict(approved)) != approval.get("approved_hash"):
        problems.append("the package at the approval's commit doesn't hash to its approved_hash")
    if approved.get("revision") != approval.get("revision"):
        problems.append("the package at the approval's commit isn't the approved revision")
    differing = _differing(package, approved, REVISED_FIELDS | {"profile_fields"})
    tip_fields, approved_fields = package.get("profile_fields"), approved.get("profile_fields")
    if isinstance(tip_fields, Mapping) and isinstance(approved_fields, Mapping):
        differing += [
            f"profile_fields.{key}"
            for key in _differing(tip_fields, approved_fields, REVISED_PROFILE_FIELDS)
        ]
    elif tip_fields != approved_fields:
        differing.append("profile_fields")
    if differing:
        problems.append(f"the tip differs from the approved revision in {', '.join(differing)}")
    return _condition(
        PROPOSER_REVISED,
        problems,
        "the tip differs from the approved revision only in revision, status, occurrence and "
        "destinations",
    )


def _differing(a: Mapping[str, Any], b: Mapping[str, Any], allowed: frozenset[str]) -> list[str]:
    return sorted(
        key
        for key in (a.keys() | b.keys()) - allowed
        if key not in a or key not in b or a[key] != b[key]
    )


def no_new_copy(
    entry: RegistryEntry, package: Mapping[str, Any], approved: Mapping[str, Any] | None
) -> Condition:
    """The tip lists no destination that holds a copy (any kind but `runtime-fetch`) which the
    last approved revision didn't list (amendment 39 reading 3)."""
    if approved is None:
        return Condition(NO_NEW_COPY, False, "no approved revision was read to compare with")
    before = set(_listed(entry, approved))
    added = sorted(
        parse(destination)["kind"]
        for destination in _listed(entry, package)
        if destination not in before and parse(destination)["kind"] not in NO_COPY_KINDS
    )
    if added:
        return Condition(NO_NEW_COPY, False, f"the tip adds a written copy: {', '.join(added)}")
    return Condition(NO_NEW_COPY, True, "no written copy added since the approved revision")


def destinations_equal(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The package lists exactly the row's destinations, the `compare-destinations` read."""
    if not same(entry.destinations, _listed(entry, package)):
        return Condition(
            DESTINATIONS_EQUAL, False, "the package's destinations aren't the registry's"
        )
    return Condition(DESTINATIONS_EQUAL, True, "equal to the registry's")


def reach_unchanged(entry: RegistryEntry, package: Mapping[str, Any]) -> Condition:
    """The package's `reach` is `generate.reach` for the row's type and destinations."""
    adapter_name, _ = plan_for(entry, first_issue=False)
    if package.get("reach") != reach(adapter_name, entry.destinations):
        return Condition(REACH, False, f"the reach isn't {adapter_name}'s for the row")
    return Condition(REACH, True, f"{adapter_name}'s reach for the row")


def rotated_before(last_rotated: str | None, lineage: Mapping[str, Any]) -> dict[str, Any]:
    """A reported fact, never a condition (HQ's call on #838): infraops' `last_rotated`, and
    whether it is on or after the day revision 1 was approved; `None` where either is unknown."""
    approvals = lineage.get("approvals")
    first = next(
        (
            approval
            for approval in (approvals if isinstance(approvals, list) else [])
            if isinstance(approval, Mapping) and approval.get("revision") == 1
        ),
        None,
    )
    approved_at = first.get("approved_at") if first is not None else None
    approved_day = approved_at[:10] if isinstance(approved_at, str) else None
    rotated_day = last_rotated[:10] if isinstance(last_rotated, str) else None
    return {
        "last_rotated": rotated_day,
        "revision_1_approved_on": approved_day,
        "on_or_after_revision_1_approval": (
            rotated_day >= approved_day if rotated_day and approved_day else None
        ),
    }


def reissue(
    entry: RegistryEntry,
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
    *,
    approved: Mapping[str, Any] | None,
) -> list[Condition]:
    """Amendment 39's conditions on revision 2 or later, the command's form. `approved` is the
    package at the last approval's commit, or `None` when that commit isn't on `main`."""
    return [
        sds_rotated(entry),
        reissue_type(entry, package),
        not_hand_rotated(entry, package),
        machine_plan(entry, package),
        reissue_lineage(package, lineage),
        proposer_revised(package, approved, last_approval(lineage)),
        no_new_copy(entry, package, approved),
        destinations_equal(entry, package),
        standard_authority(entry, package, first_issue=False),
        reach_unchanged(entry, package),
    ]


def carry_reissue(
    entry: RegistryEntry,
    package: Mapping[str, Any],
    lineage: Mapping[str, Any],
    *,
    package_hash: str,
    verified: Mapping[str, Any],
) -> list[Condition]:
    """The carrier's form of `reissue` (amendment 39 reading 8): every condition but the two
    that need the last approved revision's content, with the lineage condition requiring the
    tip's one approval to be the policy's and the emitter's verified one."""
    return [
        sds_rotated(entry),
        reissue_type(entry, package),
        not_hand_rotated(entry, package),
        machine_plan(entry, package),
        policy_reissue_lineage(package, lineage, package_hash=package_hash, verified=verified),
        destinations_equal(entry, package),
        standard_authority(entry, package, first_issue=False),
        reach_unchanged(entry, package),
    ]
