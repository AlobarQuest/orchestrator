"""A standing rotation package generated from a registry row (ADR-0055 amendment 15, increment 4).

A credential the registry hands to the SDS needs a standing package before anything rotates it.
Everything a package declares that the executor reads follows from two things: the row (its id,
class, keeper and destinations) and its type's declaration (`rotation_worker.types`). So the
package is rendered from them:

* the **criteria** are the type's derived plan (`plans.derive`), one per machine step, numbered
  as the plan numbers them, with the evidence text `select_plan` and the verifier read. A step's
  unit is named only where its kind appears in more than one unit, as every authored package does;
* the **reach** is `external_system`, plus `operator_machine` for a credential the Mac keeps and
  `live_estate` where a step writes a hosted service: a hosted type, or a Coolify setting among
  the destinations (a held copy distribute writes and restarts);
* the **authority** allows reading and writing secrets, minting where the provider mints by
  machine, revoking where it revokes by machine, and changing infrastructure wherever the reach
  is `live_estate`, and prohibits the rest;
* the **prose** is one template, naming the credential, its provider identity and its plan.

Only a new package is generated. An authored package keeps its approved wording; the proposer
revises it only for its occurrence and destinations.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any, Final

from rotation_worker.contract import HELD_KIND, HumanAct, Plan, StepKind, UnitPlan
from rotation_worker.destinations import HOSTED_KINDS, parse
from rotation_worker.plans import criteria_steps, derive
from rotation_worker.registry import RegistryEntry
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


# A type whose provider keeps no old value alive (`Capabilities.overlap` false) replaces it at the
# host step, and what depends on the service restarts. The package states that outage, because the
# person approving the revision is approving it.
_HOST_WITHOUT_OVERLAP: Final = (
    "The hosted service replaces the old value with the new one, and its dependents restart; "
    "they're unavailable until the restart finishes."
)
_RESTART_HOUR: Final = (
    " The restart can happen at any hour of the change window, so the outage lands in the window "
    "and not at a time Devon chooses."
)


class GenerateError(RuntimeError):
    """The row names no type the SDS rotates, or a type whose plan can't be derived."""


def plan_for(entry: RegistryEntry) -> tuple[str, Plan]:
    """The adapter that rotates this row's credential, and the plan derived for it."""
    credential_class = entry.credential.get("class")
    adapter_name = CLASS_TYPES.get(str(credential_class))
    if adapter_name is None:
        raise GenerateError(f"class {credential_class!r} is no type the SDS rotates")
    declared = TYPES[adapter_name]
    plan = derive(declared.capabilities, entry.destinations, entry.keeper, declared.store_kind)
    return adapter_name, plan


def acceptance(plan: Plan, overlap: bool = True) -> list[dict[str, str]]:
    """One criterion per machine step, in the plan's numbering.

    A type with no overlap states its host step as the replacement and restart it is."""
    conditions = (
        {**_CONDITIONS, StepKind.HOST: _HOST_WITHOUT_OVERLAP} if not overlap else _CONDITIONS
    )
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


def authority(adapter_name: str, destinations: Sequence[str]) -> dict[str, Any]:
    capabilities = TYPES[adapter_name].capabilities
    allowed = {"secret_read", "secret_write"}
    if capabilities.machine_mint:
        allowed.add("credential_create")
    if capabilities.machine_revoke:
        allowed.add("credential_revoke")
    if writes_live_estate(adapter_name, destinations):
        allowed.add("infra_mutation")
    return {
        "allowed": [c for c in _CAPABILITIES if c in allowed],
        "requires_approval": [],
        "prohibited": [c for c in _CAPABILITIES if c not in allowed],
        "budgets": {"max_attempts": 3, "max_llm_calls": None},
    }


def render(entry: RegistryEntry, occurrence: str, created_at: str) -> dict[str, Any]:
    """Revision 1 of the standing package for this row's credential, as a draft."""
    credential_id = str(entry.credential["id"])
    adapter_name, plan = plan_for(entry)
    identity = str(entry.credential.get("provider_identity") or credential_id)
    overlap = TYPES[adapter_name].capabilities.overlap
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
                f"{credential_id} ({identity}) is replaced by a new value, every destination the "
                "registry names holds it, and the old value is proved dead."
            ),
            "why": (
                f"The credential is due for rotation ({occurrence}), and the registry hands its "
                "rotation to the SDS."
            ),
            "beneficiary": "Devon, whose credentials the SDS rotates.",
            "success_signal": (
                "Every unit completes, the keeper holds a value different from the one it started "
                "with, the old value is refused, and infraops reports no rotation finding for "
                f"{credential_id}."
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
        "acceptance": acceptance(plan, overlap),
        "authority": authority(adapter_name, entry.destinations),
        "deliverables": {
            "artifacts": [
                "A rotated credential in its keeper, and terminal work units carrying each step's "
                "evidence."
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
            "required_capabilities": authority(adapter_name, entry.destinations)["allowed"],
            "blocking_conditions": [
                "The executor refuses a unit at its checks; the refusal names the reason."
            ],
        },
        "risk": {
            "failure_modes": [
                "A contract refusal fails a unit partway; the executor resumes from its store on "
                "a retry after a requeue."
            ],
            "max_impact": (
                "A destination holds a value it can't use until the rotation completes or is "
                "undone; the old value stays valid until it is revoked."
                if overlap
                else "The hosted service's dependents are unavailable while the service restarts "
                "on the new value, and the old value stops working at that step." + _RESTART_HOUR
            ),
            "stop_conditions": ["A value appears anywhere it should not."],
            "rollback": (
                "Before the revoke the old value stays valid and is quarantined; after it, the "
                "recovery is forward: a new value and the rotation run again."
                if overlap
                else "The host step is the cutover: the executor undoes it by writing the old value back "
                "and restarting. After the store step the recovery is forward: a new value and "
                "the rotation run again."
            ),
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
