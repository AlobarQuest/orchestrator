"""The standing approval's carry form, on a policy-approved rotation revision (ADR-0055 a22, a39).

A lane credential's revision 1, and a reissue (revision 2 or later) of an SDS-rotated orchestrator
or change-manager bearer (amendment 39, D-88 A), may be approved by the policy approver
`policy:non-software-operational@v<N>` instead of a person. Amendment 22 item 3 makes the carrier
the place that checks it: a rotation revision (keyed on the package's `profile`, never the
record's `change_class`) whose approver is a `policy:` string is carried only when the carry form
refuses nothing, and the carrier then records that it checked, under its own identity, before it
posts the intake. The orchestrator's decomposition gate admits the policy approver only beside that
record (`services/intake/standing_approval.py`), so this module is where the checks run and the
gate is where their record is required.

**THE CHECKS ARE THE PROPOSER'S, NOT A COPY.** `rotation_proposer.standing_approval.carry` (at
revision 1) and `carry_reissue` (above it) are the conditions `rotation-proposer standing-approval`
runs, in their carry forms. This module imports that one module of the proposer and nothing else
of it. The observation states which form ran (`form`: `issue` or `reissue`).

**THE CHAIN EVENT IS THE EMITTER'S, VERIFIED.** `orchestrator emit-intake-payload` has already
verified the factory-events chain and matched a `package.approved` event on the lineage approval's
hash, revision, approver, commit and event id, and the payload carries what it matched. This
module re-reads the package and lineage from the same checkout and refuses unless they are the ones
the emitter verified: the package hashes to the payload's `content_hash`, and the lineage's one
approval on the payload's revision is the payload's verified approval field for field. A reissue
builds on two earlier approvals too, revision 1's and the last before the tip, so the emitter
matches each to a verified event in the same chain read and states it in `earlier_approvals`;
the carrier refuses a reissue unless both are verified and are the lineage's approvals field for
field. So the carrier relies on verified events, never on the lineage's own word, and there is no
second chain reader to diverge.

**NOTHING HERE WRITES.** `check` returns the observation body or the refusal; `cli.py` posts the
observation, and only on a `--register` pass. Any read that fails and any exception is a refusal,
never a pass (Code Brain rule 4), and a refusal names conditions, ids and error types, never a
value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from rotation_proposer.standing_approval import carry, carry_reissue
from rotation_worker.canonical import CanonicalError, package_hash
from rotation_worker.contract import ContractError
from rotation_worker.destinations import DestinationError
from rotation_worker.git_mirror import MirrorError
from rotation_worker.registry import RegistryEntry, RegistryError, load_package

REFUSED: Final = "standing_approval_refused"
ROTATION_PROFILE: Final = "non-software-operational"
POLICY_APPROVER: Final = "policy:"

SOURCE_SYSTEM: Final = "work_carrier"
OBSERVATION_TYPE: Final = "standing_approval_check"
SUBJECT_TYPE: Final = "package_revision"
TRUST_CLASSIFICATION: Final = "delivery_system"
STATUS: Final = "passed"
SEVERITY: Final = "info"
MAX_SUMMARY: Final = 512
# `CommandBase.idempotency_key` is `max_length=200`; exceeding it is a 422 before any service runs.
MAX_IDEMPOTENCY_KEY: Final = 200
# Which carry form ran: amendment 22's on a lane credential's revision 1, amendment 39's above it.
ISSUE_FORM: Final = "issue"
REISSUE_FORM: Final = "reissue"
# The emitter's `earlier_approvals` roles, each named as a refusal names it.
EARLIER_ROLES: Final = {  # not-a-vocabulary: roles the emitter's payload states
    "revision_1": "revision 1's approval",
    "last_earlier": "the last approval before the tip",
}
EARLIER_FIELDS: Final = (  # not-a-vocabulary: approval fields the emitter's entry states
    "revision",
    "approver",
    "approved_hash",
    "commit",
    "event_id",
)
VERIFIED_FIELDS: Final = (  # not-a-vocabulary: approval fields compared with the verified event
    "approver",
    "approved_hash",
    "revision",
    "commit",
    "event_id",
)

# Errors whose message names ids, paths and conditions, which a refusal may repeat. Any other
# exception is reported by its type alone, since its text is nothing this module composed.
_NAMED_ERRORS: Final = (
    CanonicalError,
    ContractError,
    DestinationError,
    MirrorError,
    RegistryError,
)


class StandingRefusal(Exception):
    """The carry form refused, or something it needed could not be read."""


@dataclass(frozen=True)
class Checked:
    """The carry form refused nothing; `observation` is what the carrier records before intake."""

    observation: dict[str, Any]


def is_policy_rotation(payload: Mapping[str, Any]) -> bool:
    """A rotation revision (by the package's profile) whose approver is a policy, not a person."""
    approver = payload.get("approved_by")
    return (
        payload.get("profile") == ROTATION_PROFILE
        and isinstance(approver, str)
        and approver.startswith(POLICY_APPROVER)
    )


def subject_for(package_id: str, revision: int) -> str:
    """The revision as the observation's subject: it has no orchestrator id until intake."""
    return f"{package_id}@r{revision}"


def check(
    payload: Mapping[str, Any],
    package_dir: Path,
    find_entry: Callable[[str], RegistryEntry],
) -> Checked | str:
    """The carry form on the checkout the emitter read: `Checked`, or the refusal's detail."""
    try:
        return _check(payload, package_dir, find_entry)
    except StandingRefusal as refusal:
        return str(refusal)
    except _NAMED_ERRORS as error:
        return f"the carry form could not be run: {type(error).__name__}: {error}"
    except Exception as error:
        # Never a pass: a check that could not be made sends the revision to a person.
        return f"the carry form could not be run: {type(error).__name__}"


def _check(
    payload: Mapping[str, Any],
    package_dir: Path,
    find_entry: Callable[[str], RegistryEntry],
) -> Checked:
    package = load_package((package_dir / "package.yaml").read_bytes())
    lineage = load_package((package_dir / "lineage.yaml").read_bytes())
    content_hash = package_hash(package)
    if content_hash != payload.get("content_hash"):
        raise StandingRefusal("the checkout no longer hashes to the payload's content_hash")
    verified = _verified(payload, content_hash)
    approval = _verified_approval(lineage, verified)
    fields = package.get("profile_fields")
    credential_id = fields.get("credential_id") if isinstance(fields, Mapping) else None
    if not isinstance(credential_id, str) or not credential_id:
        raise StandingRefusal("the package names no credential_id")
    entry = find_entry(credential_id)
    if payload.get("revision") == 1:
        form = ISSUE_FORM
        conditions = carry(entry, package, lineage, package_hash=content_hash)
    else:
        form = REISSUE_FORM
        conditions = carry_reissue(
            entry, package, lineage, package_hash=content_hash, verified=verified
        )
    refused = [condition for condition in conditions if not condition.passed]
    if refused:
        raise StandingRefusal(
            "the carry form refused: "
            + "; ".join(f"{condition.name} ({condition.reason})" for condition in refused)
        )
    # After the carry form, so a reissue of the wrong shape is reported by its own condition.
    earlier = (
        _earlier_backed(payload, lineage, verified["revision"]) if form == REISSUE_FORM else []
    )
    return Checked(_observation(payload, approval, entry, credential_id, form, earlier))


def _verified(payload: Mapping[str, Any], content_hash: str) -> dict[str, Any]:
    """The approval the emitter matched to a verified chain event, as the lineage spells it."""
    return {
        "approver": payload.get("approved_by"),
        "approved_hash": content_hash,
        "revision": payload.get("revision"),
        "commit": payload.get("approval_ledger_commit"),
        "event_id": payload.get("approval_event_id"),
    }


def _verified_approval(
    lineage: Mapping[str, Any], verified: Mapping[str, Any]
) -> Mapping[str, Any]:
    """The lineage's one approval on the payload's revision, refused unless no approval names a
    later revision and it is the approval the emitter matched to a verified chain event: same
    approver, hash, revision, commit and event id."""
    approvals = lineage.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        raise StandingRefusal("the lineage records no approval")
    if not all(isinstance(item, Mapping) for item in approvals):
        raise StandingRefusal("the lineage's approvals aren't all mappings")
    revision = verified["revision"]
    on_revision = [item for item in approvals if item.get("revision") == revision]
    if len(on_revision) != 1:
        raise StandingRefusal("the lineage doesn't record exactly one approval of the revision")
    if any(_is_later(item.get("revision"), revision) for item in approvals):
        raise StandingRefusal("the lineage records an approval of a later revision")
    approval = on_revision[0]
    differing = sorted(name for name in VERIFIED_FIELDS if approval.get(name) != verified.get(name))
    if differing:
        raise StandingRefusal(
            "the lineage's approval isn't the one the emitter verified in the chain "
            f"(differs in {', '.join(differing)})"
        )
    return approval


def _earlier_backed(
    payload: Mapping[str, Any], lineage: Mapping[str, Any], revision: object
) -> list[dict[str, Any]]:
    """A reissue builds on two earlier approvals, revision 1's and the last before the tip
    (`proposer_revised` compares with it), so each must be the one the emitter matched to a
    verified chain event, as the tip's is: the payload's `earlier_approvals` entry for its role
    says verified and names the lineage's approval field for field (ADR-0055 amendment 39).
    Returns each role's event id and commit, for the observation's facts."""
    stated = payload.get("earlier_approvals")
    if not isinstance(stated, list):
        raise StandingRefusal("the payload states no earlier approvals the emitter verified")
    approvals = lineage.get("approvals")
    earlier = [
        item
        for item in (approvals if isinstance(approvals, list) else [])
        if isinstance(item, Mapping) and _is_earlier(item.get("revision"), revision)
    ]
    numbers = [item["revision"] for item in earlier]
    roles = {
        "revision_1": [item for item in earlier if item["revision"] == 1],
        "last_earlier": [item for item in earlier if numbers and item["revision"] == max(numbers)],
    }
    backed: list[dict[str, Any]] = []
    for role, approval in roles.items():
        entries = [e for e in stated if isinstance(e, Mapping) and e.get("role") == role]
        if len(entries) != 1 or len(approval) != 1:
            raise StandingRefusal(f"{EARLIER_ROLES[role]} has no single verified chain event")
        entry = entries[0]
        if entry.get("verified") is not True:
            raise StandingRefusal(
                f"{EARLIER_ROLES[role]} isn't backed by a verified chain event "
                f"({entry.get('reason')})"
            )
        differing = sorted(
            name for name in EARLIER_FIELDS if approval[0].get(name) != entry.get(name)
        )
        if differing:
            raise StandingRefusal(
                f"{EARLIER_ROLES[role]} isn't the one the emitter verified in the chain "
                f"(differs in {', '.join(differing)})"
            )
        backed.append(
            {"role": role, "event_id": entry.get("event_id"), "commit": entry.get("commit")}
        )
    return backed


def _is_earlier(number: object, revision: object) -> bool:
    """Whether an approval's revision is an integer before the payload's."""
    return (
        isinstance(number, int)
        and not isinstance(number, bool)
        and isinstance(revision, int)
        and number < revision
    )


def _is_later(number: object, revision: object) -> bool:
    """Whether an approval's revision is after the payload's; a non-integer one counts as later,
    so a lineage it can't order refuses."""
    if not isinstance(revision, int) or isinstance(revision, bool):
        return True
    if not isinstance(number, int) or isinstance(number, bool):
        return True
    return number > revision


def _observation(
    payload: Mapping[str, Any],
    approval: Mapping[str, Any],
    entry: RegistryEntry,
    credential_id: str,
    form: str,
    earlier: list[dict[str, Any]],
) -> dict[str, Any]:
    """The record of a pass. Every field is fixed by the revision, the registry commit and the
    form, so a second pass over the same three replays the row; `observed_at` is the verified
    approval's time, never a clock read, because it is part of the row's fact identity. The form
    is in the reference and the key as well as the facts: a row recorded before facts carried it
    keeps its reference, so a later pass writes a new row beside it rather than conflicting."""
    package_id = str(payload.get("package_id"))
    revision = payload.get("revision")
    content_hash = str(payload.get("content_hash"))
    approved_at = payload.get("approved_at")
    if not isinstance(revision, int) or not isinstance(approved_at, str) or not approved_at:
        raise StandingRefusal("the payload states no revision or approval time")
    registry = f"{entry.repository}@{entry.commit}"
    subject = subject_for(package_id, revision)
    reference = f"standing-approval:{form}:{subject}:{content_hash}:{registry}"
    key = f"work-carrier-standing:{form}:{subject}:{content_hash[:16]}:{entry.commit[:16]}"
    if len(key) > MAX_IDEMPOTENCY_KEY:
        raise StandingRefusal(f"the observation key for {package_id} is over the route's limit")
    facts = {
        "package_id": package_id,
        "revision": revision,
        "content_hash": content_hash,
        "package_commit": approval.get("commit"),
        "approver": approval.get("approver"),
        "registry_entry": credential_id,
        "registry_repository": entry.repository,
        "registry_commit": entry.commit,
        "form": form,
        "refused": [],
    }
    if earlier:
        facts["earlier_approvals"] = earlier
    summary = (
        f"the work carrier's {form} carry form refused nothing on {package_id} revision "
        f"{revision}, with the registry at {entry.repository}@{entry.commit[:12]}"
    )[:MAX_SUMMARY]
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_reference": reference,
        "source_url": None,
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": subject_for(package_id, revision),
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": STATUS,
        "severity": SEVERITY,
        "observed_at": approved_at,
        "summary": summary,
        "facts": facts,
        "payload_digest": None,
    }
