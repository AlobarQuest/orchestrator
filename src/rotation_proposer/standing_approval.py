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

A condition never catches an exception: anything the generator or the plan reader raises reaches
the command, which exits 2, because a check it couldn't make is never a pass.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from rotation_proposer.generate import CREATED_BY, authority, plan_for, render
from rotation_worker.destinations import parse, same
from rotation_worker.destinations import render as render_destination
from rotation_worker.plans import candidates, select_plan
from rotation_worker.registry import RegistryEntry, package_path, standing_destinations
from rotation_worker.types import TYPES

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
