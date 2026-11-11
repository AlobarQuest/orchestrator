"""The standing approval's carry form, on a policy-approved rotation revision (ADR-0055 a22).

A lane credential's revision 1 may be approved by the policy approver
`policy:non-software-operational@v<N>` instead of a person. Amendment 22 item 3 makes the carrier
the place that checks it: a rotation revision (keyed on the package's `profile`, never the
record's `change_class`) whose approver is a `policy:` string is carried only when the carry form
refuses nothing, and the carrier then records that it checked, under its own identity, before it
posts the intake. The orchestrator's decomposition gate admits the policy approver only beside that
record (`services/intake/standing_approval.py`), so this module is where the checks run and the
gate is where their record is required.

**THE CHECKS ARE THE PROPOSER'S, NOT A COPY.** `rotation_proposer.standing_approval.carry` is the
same set of conditions `rotation-proposer standing-approval` runs, in its carry form. This module
imports that one module of the proposer and nothing else of it.

**THE CHAIN EVENT IS THE EMITTER'S, VERIFIED.** `orchestrator emit-intake-payload` has already
verified the factory-events chain and matched a `package.approved` event on the lineage approval's
hash, revision, approver, commit and event id, and the payload carries what it matched. This
module re-reads the package and lineage from the same checkout and refuses unless they are the ones
the emitter verified: the package hashes to the payload's `content_hash`, and the lineage's one
approval is the payload's verified approval field for field. So the carrier relies on the verified
event, never on the lineage's own word, and there is no second chain reader to diverge.

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

from rotation_proposer.standing_approval import carry
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
    approval = _verified_approval(payload, lineage, content_hash)
    fields = package.get("profile_fields")
    credential_id = fields.get("credential_id") if isinstance(fields, Mapping) else None
    if not isinstance(credential_id, str) or not credential_id:
        raise StandingRefusal("the package names no credential_id")
    entry = find_entry(credential_id)
    conditions = carry(entry, package, lineage, package_hash=content_hash)
    refused = [condition for condition in conditions if not condition.passed]
    if refused:
        raise StandingRefusal(
            "the carry form refused: "
            + "; ".join(f"{condition.name} ({condition.reason})" for condition in refused)
        )
    return Checked(_observation(payload, approval, entry, credential_id))


def _verified_approval(
    payload: Mapping[str, Any], lineage: Mapping[str, Any], content_hash: str
) -> Mapping[str, Any]:
    """The lineage's one approval, refused unless it is the approval the emitter matched to a
    verified chain event: same approver, hash, revision, commit and event id."""
    approvals = lineage.get("approvals")
    if not isinstance(approvals, list) or len(approvals) != 1:
        raise StandingRefusal("the lineage doesn't record exactly one approval")
    approval = approvals[0]
    if not isinstance(approval, Mapping):
        raise StandingRefusal("the lineage's approval isn't a mapping")
    verified = {
        "approver": payload.get("approved_by"),
        "approved_hash": content_hash,
        "revision": payload.get("revision"),
        "commit": payload.get("approval_ledger_commit"),
        "event_id": payload.get("approval_event_id"),
    }
    differing = sorted(name for name, value in verified.items() if approval.get(name) != value)
    if differing:
        raise StandingRefusal(
            "the lineage's approval isn't the one the emitter verified in the chain "
            f"(differs in {', '.join(differing)})"
        )
    return approval


def _observation(
    payload: Mapping[str, Any],
    approval: Mapping[str, Any],
    entry: RegistryEntry,
    credential_id: str,
) -> dict[str, Any]:
    """The record of a pass. Every field is fixed by the revision and the registry commit, so a
    second pass over the same pair replays the row; `observed_at` is the verified approval's time,
    never a clock read, because it is part of the row's fact identity."""
    package_id = str(payload.get("package_id"))
    revision = payload.get("revision")
    content_hash = str(payload.get("content_hash"))
    approved_at = payload.get("approved_at")
    if not isinstance(revision, int) or not isinstance(approved_at, str) or not approved_at:
        raise StandingRefusal("the payload states no revision or approval time")
    registry = f"{entry.repository}@{entry.commit}"
    reference = f"standing-approval:{subject_for(package_id, revision)}:{content_hash}:{registry}"
    key = (
        f"work-carrier-standing:{subject_for(package_id, revision)}:"
        f"{content_hash[:16]}:{entry.commit[:16]}"
    )
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
        "refused": [],
    }
    summary = (
        f"the work carrier's carry form refused nothing on {package_id} revision {revision}, "
        f"with the registry at {entry.repository}@{entry.commit[:12]}"
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
