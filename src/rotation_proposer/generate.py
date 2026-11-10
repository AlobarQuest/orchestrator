"""A standing rotation package generated from a registry row (ADR-0055 amendment 15, increment 4).

A credential the registry hands to the SDS needs a standing package before anything rotates it.
Everything a package declares that the executor reads follows from two things: the row (its id,
class, keeper and destinations) and its type's declaration (`rotation_worker.types`). So the
package is rendered from them:

* the **criteria** are the type's derived plan (`plans.derive`), one per machine step, numbered
  as the plan numbers them, with the evidence text `select_plan` and the verifier read. A step's
  unit is named only where its kind appears in more than one unit, as every authored package does.
  A type that declares an issue plan (`plans.derive_issue`, ADR-0055 amendment 19) gets it for
  a credential infraops has never recorded rotated (`last_rotated` null), and its rotation plan
  for a reissue; an issue's criteria say nothing is quarantined, retired or proved dead;
* the **reach** is `external_system`, plus `operator_machine` for a credential the Mac keeps and
  `live_estate` where a step writes a hosted service: a hosted type, or a Coolify setting among
  the destinations (a held copy distribute writes and restarts);
* the **authority** allows reading and writing secrets, creating where the provider mints by
  machine and its mint creates (`Capabilities.creates`, ADR-0055 amendment 23), revoking where
  it revokes by machine and the plan retires, and changing infrastructure wherever the reach is
  `live_estate`, and prohibits the rest;
* the **prose** is one template, naming the credential, its provider identity and its plan.

An authored package keeps its approved wording; the proposer revises it only for its occurrence
and destinations. A generated one is re-rendered at revision only when the plan its row derives
is no longer the plan it states (`reissue_body`, ADR-0055 amendment 20): a lane token's first
reissue, after its issue. The person approves the new wording in that revision's one click.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Final

from rotation_worker.contract import (
    HELD_KIND,
    ContractError,
    HumanAct,
    Plan,
    StepKind,
    UnitPlan,
)
from rotation_worker.destinations import HOSTED_KINDS, parse
from rotation_worker.plans import candidates, criteria_steps, select_plan
from rotation_worker.registry import RegistryEntry, RegistryError, check_name
from rotation_worker.types import CLASS_TYPES, TYPES

PROFILE: Final = "non-software-operational"
CREATED_BY: Final = "rotation-proposer"
OWNER: Final = "devon"

# Every capability a rotation package states, in the order authored packages list them.
_CAPABILITIES: Final = (
    "credential_create",
    "credential_revoke",
    "secret_read",
    "secret_write",
    "repository_write",
    "infra_mutation",
    "outward_publish",
    "email_send",
)

_CONDITIONS: Final[dict[StepKind, str]] = {
    StepKind.MINT: "The executor mints a new value with the provider's admin credential.",
    StepKind.STAGE: (
        "The new value is staged in the executor's work store, with a fingerprint different "
        "from the old value's."
    ),
    StepKind.HOST: "The hosted service accepts the new value beside the old one.",
    StepKind.VERIFY_NEW: (
        "The new value answers live at the provider's probe, beside a known-bad value the same "
        "probe refuses."
    ),
    StepKind.STORE: "The old value is quarantined and the keeper reads back the new one.",
    StepKind.DISTRIBUTE: "Every other copy the registry names holds the new value.",
    StepKind.VERIFY_CONSUMERS: (
        "Every consumer the registry names holds the new value, or has run on it since it was "
        "stored."
    ),
    StepKind.RETIRE_OLD: "The old value is revoked at the provider, proved live just before.",
    StepKind.CONFIRM_DEAD: (
        "The old value is refused and the new one answers live, each beside a refused known-bad "
        "value, and the staging and quarantine secrets are deleted."
    ),
}


# An issue has no old value (ADR-0055 amendment 19 item 3): the keeper held the empty marker.
_ISSUE: Final[dict[StepKind, str]] = {
    StepKind.STORE: (
        "The executor read the empty marker in the keeper before writing it, and the keeper reads "
        "back the new value; nothing is quarantined."
    ),
    StepKind.VERIFY_CONSUMERS: (
        "Every consumer the registry names holds the new value, and the staging secret is deleted."
    ),
}
# A type checked by its shape, then probed by a read-only lookup (amendment 21, D-31): a ping URL.
_BY_SHAPE: Final = (
    "The new value is exactly the self-hosted instance's ping base followed by one UUID, and no "
    "other keeper of its type holds it; nothing pings it, because a ping marks the check up."
)

# A mint that creates nothing reads the value its provider already holds (`Capabilities.creates`,
# ADR-0055 amendment 23): a ping URL, from the check the registry row names. The criterion names
# that check, so the approval's re-render and a person's read cover which check's URL a lane gets.
_READS_CHECK: Final = (
    "The executor reads the ping URL of the Healthchecks check named {check!r} from the "
    "Management key's check listing, before the unit is claimed; nothing is created."
)

# A type whose provider keeps no old value alive (`Capabilities.overlap` false) replaces it at the
# host step, and what depends on the service restarts. The package states that outage, because the
# person approving the revision is approving it.
_HOST_REPLACES: Final = (
    "The hosted service replaces the old value with the new one, and its dependents restart; "
    "they're unavailable until the restart finishes."
)
_RESTART_HOUR: Final = " The restart can happen at any hour of the change window."


class GenerateError(RuntimeError):
    """The row names no type the SDS rotates, or a type whose plan can't be derived."""


class StatedPlanError(RuntimeError):
    """A generated package's criteria describe no plan its row derives."""


def plan_for(entry: RegistryEntry, *, first_issue: bool | None = None) -> tuple[str, Plan]:
    """The adapter that rotates this row's credential, and the plan derived for it.

    `first_issue` is whether infraops has never recorded the credential rotated (its
    `last_rotated` is null), or `None` where infraops doesn't say. It chooses between a type's
    two plans only for a type that declares an issue plan: the issue plan for a first issue, the
    rotation plan for a reissue (ADR-0055 amendment 19 items 3 and 10). Every other type gets its
    rotation plan whatever it is. A choice that can't be made raises; it never falls back."""
    credential_class = entry.credential.get("class")
    adapter_name = CLASS_TYPES.get(str(credential_class))
    if adapter_name is None:
        raise GenerateError(f"class {credential_class!r} is no type the SDS rotates")
    declared = TYPES[adapter_name]
    plans = candidates(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    if not declared.capabilities.issues:
        return adapter_name, plans[0]
    issue = next(plan for plan in plans if plan.issue)
    rotation = next((plan for plan in plans if not plan.issue), None)
    if first_issue is True:
        return adapter_name, issue
    if rotation is None:
        if first_issue is False:
            raise GenerateError(
                f"{adapter_name} has no rotation plan, and infraops records the credential issued"
            )
        return adapter_name, issue  # Its one plan; an issue is all the type does.
    if first_issue is None:
        raise GenerateError(
            f"{adapter_name} has an issue plan and a rotation plan, and infraops serves no "
            "last_rotated to say which this is"
        )
    return adapter_name, rotation


def acceptance(
    plan: Plan, overlap: bool = True, *, by_shape: bool = False, reads_check: str | None = None
) -> list[dict[str, str]]:
    """One criterion per machine step, in the plan's numbering.

    A type with no overlap states its host step as the replacement and restart it is; an issue
    states that nothing was there before; a type checked by its shape says so at verify_new; a
    mint that reads a check's URL names the check."""
    conditions = {**_CONDITIONS, StepKind.HOST: _HOST_REPLACES} if not overlap else _CONDITIONS
    if plan.issue:
        conditions = {**conditions, **_ISSUE}
    if by_shape:
        conditions = {**conditions, StepKind.VERIFY_NEW: _BY_SHAPE}
    if reads_check is not None:
        conditions = {**conditions, StepKind.MINT: _READS_CHECK.format(check=reads_check)}
    steps = criteria_steps(plan)
    units_per_kind = Counter(kind for _, kind in set(steps.values()))
    criteria = []
    for ac_id, (unit, kind) in steps.items():
        named = f"the {unit} unit's " if units_per_kind[kind] > 1 else "the "
        criteria.append(
            {
                "id": ac_id,
                "condition": conditions[kind],
                "evidence_type": "external_attestation",
                "evidence": (
                    f"external: {named}{kind.value} step's rotation-step/1 evidence, filed by the "
                    "rotation executor."
                ),
                "approver": OWNER,
            }
        )
    return criteria


def writes_live_estate(adapter_name: str, destinations: Sequence[str]) -> bool:
    """Whether a step writes a hosted service's setting: the type hosts, or a destination is one."""
    kinds = {parse(destination)["kind"] for destination in destinations}
    return TYPES[adapter_name].capabilities.hosted or bool(kinds & {HELD_KIND, *HOSTED_KINDS})


def reach(adapter_name: str, destinations: Sequence[str]) -> list[str]:
    declared = TYPES[adapter_name]
    return [
        "external_system",
        *(["operator_machine"] if declared.store_kind == "keychain" else []),
        *(["live_estate"] if writes_live_estate(adapter_name, destinations) else []),
    ]


def authority(
    adapter_name: str, destinations: Sequence[str], *, issue: bool = False
) -> dict[str, Any]:
    """What the package allows. An issue revokes nothing, so it never allows `credential_revoke`
    (Infra Brain rule 1032)."""
    capabilities = TYPES[adapter_name].capabilities
    allowed = {"secret_read", "secret_write"}
    if capabilities.machine_mint and capabilities.creates:
        allowed.add("credential_create")
    if capabilities.machine_revoke and not issue:
        allowed.add("credential_revoke")
    if writes_live_estate(adapter_name, destinations):
        allowed.add("infra_mutation")
    return {
        "allowed": [c for c in _CAPABILITIES if c in allowed],
        "requires_approval": [],
        "prohibited": [c for c in _CAPABILITIES if c not in allowed],
        "budgets": {"max_attempts": 3, "max_llm_calls": None},
    }


def _risk(plan: Plan, overlap: bool) -> tuple[str, str]:
    """The package's maximum impact and rollback: an issue revokes nothing, a type whose provider
    keeps the old value alive overlaps, and one that doesn't replaces it at host."""
    if plan.issue:
        return (
            "A destination holds no value it can use until the issue completes; nothing is "
            "revoked, because no value was there before.",
            "Nothing is revoked, so nothing needs restoring: until store the keeper holds the "
            "empty marker, and after it recovery is forward, with a new value.",
        )
    if overlap:
        return (
            "A destination holds a value it can't use until the rotation completes or is "
            "undone; the old value stays valid until it is revoked.",
            "Before the revoke the old value stays valid and is quarantined; after it, the "
            "recovery is forward: a new value and the rotation run again.",
        )
    return (
        "The hosted service's dependents are unavailable while the service restarts on the new "
        "value, and the old value stops working at that step." + _RESTART_HOUR,
        "If the host step itself fails, the executor writes the old value back and restarts, "
        "leaving the keeper unchanged. Once host finishes, recovery is forward: a new value and "
        "the rotation run again.",
    )


def _read_check(adapter_name: str, entry: RegistryEntry) -> str | None:
    """The check a mint that creates nothing reads, from the row's `healthchecks_check`; `None`
    for every type whose mint creates or that mints by a person. A row that names none raises."""
    capabilities = TYPES[adapter_name].capabilities
    if not capabilities.machine_mint or capabilities.creates:
        return None
    try:
        return check_name(entry.credential)
    except RegistryError as error:
        raise GenerateError(f"{entry.credential.get('id')}: {error}") from None


def render(
    entry: RegistryEntry, occurrence: str, created_at: str, *, first_issue: bool | None = None
) -> dict[str, Any]:
    """Revision 1 of the standing package for this row's credential, as a draft. `first_issue`
    is `plan_for`'s."""
    credential_id = str(entry.credential["id"])
    adapter_name, plan = plan_for(entry, first_issue=first_issue)
    granted = authority(adapter_name, entry.destinations, issue=plan.issue)
    identity = str(entry.credential.get("provider_identity") or credential_id)
    overlap = TYPES[adapter_name].capabilities.overlap
    max_impact, rollback = _risk(plan, overlap)
    units = [s.name for s in plan.segments if isinstance(s, UnitPlan)]
    acts = [s.name for s in plan.segments if isinstance(s, HumanAct)]
    person = (
        f"A person's acts, each announced by an alert and observed by the executor: "
        f"{', '.join(acts)}."
        if acts
        else "No act is a person's."
    )
    return {
        "schema_version": 1,
        "package_id": f"rotation-{credential_id}",
        "title": f"Standing rotation of {credential_id}",
        "revision": 1,
        "status": "draft",
        "created_by": CREATED_BY,
        "owner": OWNER,
        "created_at": created_at,
        "supersedes": None,
        "reach": reach(adapter_name, entry.destinations),
        "profile": PROFILE,
        "profile_fields": {
            "owner": OWNER,
            "operating_procedure": (
                f"The rotation executor (orchestrator src/rotation_worker/) works this rotation "
                f"with its {adapter_name} adapter and the plan derived from the type's "
                f"capabilities and this credential's registry row: units {', '.join(units)}. "
                f"{person} Evidence carries fingerprints, probe status codes and notes only."
            ),
            "external_systems": [f"{identity}, the credential's provider"],
            "standing": True,
            "credential_id": credential_id,
            "occurrence": occurrence,
            "destinations": entry.destinations,
        },
        "outcome": {
            "what": (
                f"{credential_id} ({identity}) is issued: its keeper and every destination the "
                "registry names hold the new value."
                if plan.issue
                else f"{credential_id} ({identity}) is replaced by a new value, every destination "
                "the registry names holds it, and the old value is proved dead."
            ),
            "why": (
                f"The credential is due for rotation ({occurrence}), and the registry hands its "
                "rotation to the SDS."
            ),
            "beneficiary": "Devon, whose credentials the SDS rotates.",
            "success_signal": (
                "Every unit completes, the keeper holds a value different from the one it started "
                + ("with" if plan.issue else "with, the old value is refused,")
                + f" and infraops reports no rotation finding for {credential_id}."
            ),
        },
        "scope": {
            "included": [f"The {adapter_name} plan's units, worked by the rotation executor."],
            "excluded": ["Every other credential, each rotated by its own package."],
            "non_goals": ["Changing what the credential can reach."],
            "assumptions": [
                f"The executor instance that keeps this credential runs the {adapter_name} "
                "adapter at a revision with derived plans (ADR-0055 amendment 15)."
            ],
            "open_questions": [],
        },
        "sources": [
            {
                "location": (
                    "orchestrator docs/decisions/0055-credential-rotation-runs-in-a-pull-based-"
                    "executor.md, amendment 15 (one mechanism, a declaration per type)."
                ),
                "authority_level": "authoritative",
                "required_version": "2026-10-28",
                "trust": "trusted_instruction",
                "sensitivity": "internal",
            },
            {
                "location": (
                    f"{entry.repository} .cred-consumers.toml, credential {credential_id}: its "
                    "keeper and every destination."
                ),
                "authority_level": "authoritative",
                "required_version": created_at[:10],
                "trust": "trusted_instruction",
                "sensitivity": "internal",
            },
        ],
        "constraints": {
            "time_budget": "Each unit at the executor's next pass once its waits clear.",
            "technology": "The rotation executor at the orchestrator revision installed.",
            "policy_legal": None,
            "privacy_security": (
                "No value appears in a transcript, a tool argument, a process argument, a log or "
                "evidence. Values are identified by sha256 prefix and length only."
            ),
            "compatibility": None,
            "quality_accessibility": None,
            "operational": None,
            "other": [],
        },
        "acceptance": acceptance(
            plan,
            overlap,
            by_shape=TYPES[adapter_name].verify_by_shape,
            reads_check=_read_check(adapter_name, entry),
        ),
        "authority": granted,
        "deliverables": {
            "artifacts": [
                f"{'An issued' if plan.issue else 'A rotated'} credential in its keeper, and "
                "terminal work units carrying each step's evidence."
            ],
            "destination": "the production ledger",
            "recipient": OWNER,
            "definition_of_done": (
                "Every unit is completed and every acceptance criterion carries evidence and an "
                f"adjudication, and infraops reports no rotation finding for {credential_id}."
            ),
            "operator_responsibilities": [
                "Devon approves this revision and the change record, and performs each act the "
                "executor announces."
            ],
        },
        "dependencies": {
            "predecessor_packages": [],
            "external_decisions": [
                "ADR-0055 amendment 15: plans derived from a type's capabilities (2026-10-27)."
            ],
            "required_people_systems": ["The rotation executor", "Devon"],
            "required_capabilities": granted["allowed"],
            "blocking_conditions": [
                "The executor refuses a unit at its checks; the refusal names the reason."
            ],
        },
        "risk": {
            "failure_modes": [
                "A contract refusal fails a unit partway; the executor resumes from its store on "
                "a retry after a requeue."
            ],
            "max_impact": max_impact,
            "stop_conditions": ["A value appears anywhere it should not."],
            "rollback": rollback,
            "escalation_target": OWNER,
        },
        "verification": {
            "independent_review": [
                "The rotation verifier decides each criterion from the executor's step evidence."
            ],
            "non_mechanical": [],
        },
        "follow_up": {
            "required": False,
            "revisit_when": (
                f"The next rotation of {credential_id}, which arrives as a new revision."
            ),
            "signals": [],
            "owner": OWNER,
        },
        "applicable_standards": {"project": "1.0", "security": "1.0"},
    }


def reissue_body(
    entry: RegistryEntry, stated: Mapping[str, Any], occurrence: str, *, first_issue: bool | None
) -> dict[str, Any] | None:
    """The body a revision of this package re-renders to, or `None` to keep its wording.

    Only a package this program generated (`created_by`) is ever re-rendered, and only when the
    plan its row derives (`plan_for`) isn't the plan its criteria state (`select_plan`'s match
    among the row's candidates): a lane token's issue package once infraops dates its
    `last_rotated` (ADR-0055 amendment 20, HQ D-32). A hand-authored package, the pre-derivation
    ones among them, keeps its approved wording. `plan_for`'s refusal of an unknown signal
    raises; a generated package whose criteria describe no derived plan raises too, since which
    plan it states is unknown. It renders with the package's own `created_at`, so only the text a
    plan and the occurrence decide changes."""
    if stated.get("created_by") != CREATED_BY:
        return None
    adapter_name, derived = plan_for(entry, first_issue=first_issue)
    declared = TYPES[adapter_name]
    plans = candidates(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    try:
        current = select_plan(plans, stated.get("acceptance"))
    except ContractError as error:
        raise StatedPlanError(
            f"{stated.get('package_id')}'s criteria describe no plan its row derives: {error}"
        ) from error
    if current == derived:
        return None
    created_at = stated.get("created_at")
    if not isinstance(created_at, str):
        raise StatedPlanError(f"{stated.get('package_id')} states no created_at to re-render with")
    return render(entry, occurrence, created_at, first_issue=first_issue)
