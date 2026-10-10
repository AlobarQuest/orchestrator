"""Rotation evidence: a strict schema and the secret scan, on rotation units only (ADR-0055).

Each refusal is paired with its control: the same payload on a unit that is not a rotation unit is
stored as before, so other producers' evidence is untouched.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.secret_metadata import SECRET_KEY_PARTS
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import Evidence, WorkUnit
from orchestrator.services.lifecycle.claims import LeaseGrant, claim_unit
from orchestrator.services.lifecycle.rotation_claims import NON_HOSTED_CONSTRAINT
from orchestrator.services.verifier.evidence import append_evidence, recover_evidence
from orchestrator.services.verifier.rotation_evidence import RotationStepEvidence
from tests.services.test_reclaim import expire
from tests.services.test_rotation_claims import EXECUTOR, EXECUTOR_ACTOR, seed_unit

FIXTURE = Path("tests/fixtures/rotation_evidence_schema.json")
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)

VALID: dict[str, Any] = {
    "format": "rotation-step/1",
    "step": "verify_new",
    "destination": "bws-secret Rotation / Keeper / openrouter-generic",
    "fingerprints": [
        {"which": "old", "sha256_prefix": "0123abcd", "length": 73},
        {"which": "new", "sha256_prefix": "89efcdab", "length": 73},
    ],
    "probes": [
        {
            "target": "provider openrouter",
            "subject": "new",
            "expected": "live",
            "http_status": 200,
            "outcome": "live",
        },
        {
            "target": "provider openrouter",
            "subject": "known_bad",
            "expected": "dead",
            "http_status": 401,
            "outcome": "dead",
        },
    ],
    "note": "The new value authenticates; the malformed control is refused.",
}


def _claimed(session: Session, key: str, *, rotation: bool = True) -> tuple[WorkUnit, LeaseGrant]:
    unit = seed_unit(
        session,
        key,
        capability="operational_action" if rotation else "repo.edit",
        constraints={NON_HOSTED_CONSTRAINT: True},
    )
    actor = EXECUTOR_ACTOR if rotation else WORKER
    grant = claim_unit(session, unit.id, actor, f"{key}-claim", rotation_executor=EXECUTOR)
    assert isinstance(grant, LeaseGrant), grant
    return unit, grant


def _append(
    session: Session,
    unit: WorkUnit,
    grant: LeaseGrant,
    payload: dict[str, Any] | None,
    *,
    stable_ref: str | None = None,
    actor: ActorContext = EXECUTOR_ACTOR,
    source_revision: str = "r1",
    lease_token: str | None = None,
) -> Evidence | DomainError:
    return append_evidence(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=grant.attempt,
        actor=actor,
        lease_token=grant.lease_token if lease_token is None else lease_token,
        evidence_type="observation",
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=str(uuid.uuid4()),
    )


def _stored(session: Session, unit: WorkUnit) -> int:
    with Session(session.get_bind()) as reader:
        count = reader.scalar(select(func.count()).where(Evidence.work_unit_id == unit.id))
        return count or 0


def _with(**changes: Any) -> dict[str, Any]:
    return {**VALID, **changes}


def test_well_formed_rotation_evidence_is_stored(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-valid")

    row = _append(migrated_session, unit, grant, VALID)

    assert isinstance(row, Evidence), row
    assert _stored(migrated_session, unit) == 1


def test_the_minimal_payload_is_a_format_and_a_step(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-minimal")

    row = _append(migrated_session, unit, grant, {"format": "rotation-step/1", "step": "mint"})

    assert isinstance(row, Evidence), row


MALFORMED = [
    pytest.param(_with(body="raw"), "rotation_evidence_invalid", id="extra_key"),
    pytest.param(_with(step="deploy"), "rotation_evidence_invalid", id="unknown_step"),
    pytest.param(_with(format="rotation-step/2"), "rotation_evidence_invalid", id="other_format"),
    pytest.param(
        {k: v for k, v in VALID.items() if k != "format"},
        "rotation_evidence_invalid",
        id="no_format",
    ),
    pytest.param(_with(note="x" * 501), "rotation_evidence_invalid", id="note_too_long"),
    pytest.param(_with(note=""), "rotation_evidence_invalid", id="note_empty"),
    pytest.param(_with(destination="x" * 201), "rotation_evidence_invalid", id="destination_long"),
    pytest.param(
        _with(fingerprints=[{"which": "new", "sha256_prefix": "ABCDEF01", "length": 1}]),
        "rotation_evidence_invalid",
        id="fingerprint_uppercase",
    ),
    pytest.param(
        _with(fingerprints=[{"which": "new", "sha256_prefix": "abcdef01"}]),
        "rotation_evidence_invalid",
        id="fingerprint_without_length",
    ),
    pytest.param(
        _with(fingerprints=[{"which": "staged", "sha256_prefix": "abcdef01", "length": 1}]),
        "rotation_evidence_invalid",
        id="fingerprint_of_what",
    ),
    pytest.param(
        _with(
            fingerprints=[{"which": "new", "sha256_prefix": "abcdef01", "length": 1, "raw": "x"}]
        ),
        "rotation_evidence_invalid",
        id="fingerprint_extra_key",
    ),
    pytest.param(
        _with(fingerprints=[{"which": "new", "sha256_prefix": "abcdef01", "length": 1}] * 5),
        "rotation_evidence_invalid",
        id="too_many_fingerprints",
    ),
    pytest.param(
        _with(probes=[{**VALID["probes"][0], "http_status": 700}]),
        "rotation_evidence_invalid",
        id="status_out_of_range",
    ),
    pytest.param(
        _with(probes=[{**VALID["probes"][0], "http_status": "200"}]),
        "rotation_evidence_invalid",
        id="status_as_text",
    ),
    pytest.param(
        _with(probes=[{**VALID["probes"][0], "raw": "x"}]),
        "rotation_evidence_invalid",
        id="probe_extra_key",
    ),
    pytest.param(
        _with(probes=[{**VALID["probes"][0], "outcome": "probably"}]),
        "rotation_evidence_invalid",
        id="outcome_unknown",
    ),
    pytest.param(
        _with(probes=[VALID["probes"][0]] * 17), "rotation_evidence_invalid", id="too_many_probes"
    ),
    pytest.param(
        _with(note="the new value is " + "sk" + "-or-v1-" + "ab12" * 16),
        "rotation_evidence_secret",
        id="note_holds_a_long_run",
    ),
    pytest.param(
        _with(destination="coolify-env " + "Z" * 40),
        "rotation_evidence_secret",
        id="destination_holds_a_long_run",
    ),
    pytest.param(
        _with(note="sent Authorization: Bearer abc.def to the probe"),
        "rotation_evidence_secret",
        id="note_holds_a_bearer",
    ),
    pytest.param(
        # The bootstrap-token shape, assembled so no token-shaped literal sits in the tree.
        _with(destination="0" + "." + "1" * 8 + "-" + "2" * 27 + ".abcdefghijk"),
        "rotation_evidence_secret",
        id="destination_holds_a_bws_token",
    ),
]


@pytest.mark.parametrize(("payload", "code"), MALFORMED)
def test_malformed_rotation_evidence_is_refused(
    migrated_session: Session, payload: dict[str, Any], code: str
) -> None:
    unit, grant = _claimed(migrated_session, f"evidence-bad-{uuid.uuid4().hex[:8]}")

    refused = _append(migrated_session, unit, grant, payload)

    assert isinstance(refused, DomainError) and refused.code == code, refused
    assert _stored(migrated_session, unit) == 0


def test_rotation_evidence_carries_no_stable_reference(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-ref")

    refused = _append(migrated_session, unit, grant, VALID, stable_ref="artifact://somewhere")

    assert isinstance(refused, DomainError) and refused.code == "rotation_evidence_invalid"
    assert _stored(migrated_session, unit) == 0


def test_a_rotation_unit_needs_a_payload(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-ref-only")

    refused = _append(migrated_session, unit, grant, None, stable_ref="artifact://somewhere")

    assert isinstance(refused, DomainError) and refused.code == "rotation_evidence_invalid"


@pytest.mark.parametrize(
    "payload",
    [_with(body="raw"), _with(step="deploy"), {"anything": "goes"}],
    ids=["body_key", "unknown_step", "unrelated_shape"],
)
def test_other_units_evidence_is_untouched(
    migrated_session: Session, payload: dict[str, Any]
) -> None:
    """factory-runner sends `body`; a scan of every unit would refuse it (ADR-0055 amendment 2)."""
    unit, grant = _claimed(
        migrated_session, f"evidence-other-{uuid.uuid4().hex[:8]}", rotation=False
    )

    row = _append(migrated_session, unit, grant, payload, actor=WORKER)

    assert isinstance(row, Evidence), row


def _recover(
    session: Session, unit: WorkUnit, attempt: int, payload: dict[str, Any]
) -> Evidence | DomainError:
    return recover_evidence(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=attempt,
        actor=SYSTEM,
        evidence_type="observation",
        stable_ref=None,
        payload=payload,
        source_revision="r1",
        idempotency_key=str(uuid.uuid4()),
    )


def test_recovered_rotation_evidence_is_held_to_the_schema(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-recover")
    expire(migrated_session, grant.claim_id)
    migrated_session.expire_all()

    refused = _recover(migrated_session, unit, grant.attempt, _with(body="raw"))
    assert isinstance(refused, DomainError) and refused.code == "rotation_evidence_invalid"
    assert _stored(migrated_session, unit) == 0

    row = _recover(migrated_session, unit, grant.attempt, VALID)
    assert isinstance(row, Evidence), row
    assert row.payload is not None and row.payload["recovery"]["reason"]
    migrated_session.expire_all()
    assert unit.state == WorkUnitState.FAILED


def test_the_contract_fixture_is_the_models_schema() -> None:
    """The executor (increment 4) conforms to this file; a model change must rewrite it."""
    assert json.loads(FIXTURE.read_text()) == RotationStepEvidence.model_json_schema()


def _keys(schema: object) -> set[str]:
    if isinstance(schema, dict):
        found = set(schema.get("properties", {}))
        for child in schema.values():
            found |= _keys(child)
        return found
    if isinstance(schema, list):
        return set().union(*(_keys(child) for child in schema)) if schema else set()
    return set()


def test_no_field_name_contains_a_secret_key_part() -> None:
    names = _keys(RotationStepEvidence.model_json_schema())
    assert names >= {"format", "step", "fingerprints", "probes", "sha256_prefix", "http_status"}
    assert [name for name in names if any(part in name for part in SECRET_KEY_PARTS)] == []


def test_a_destination_naming_a_uuid_is_not_a_long_run(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-uuid")

    row = _append(
        migrated_session,
        unit,
        grant,
        _with(destination="bws-secret 9661da8f-ac66-4e97-a31f-b42500bb849c"),
    )

    assert isinstance(row, Evidence), row


def test_the_source_revision_is_scanned_too(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-source")

    refused = _append(migrated_session, unit, grant, VALID, source_revision="Q" * 40)

    assert isinstance(refused, DomainError) and refused.code == "rotation_evidence_secret"


def test_a_refusal_names_the_field_and_never_its_value(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-where")
    payload = _with(fingerprints=[{"which": "new", "sha256_prefix": "SECRETVALUE", "length": 1}])

    refused = _append(migrated_session, unit, grant, payload)

    assert isinstance(refused, DomainError) and refused.code == "rotation_evidence_invalid"
    assert "fingerprints.0.sha256_prefix" in refused.message
    assert "SECRETVALUE" not in refused.message


def test_a_caller_without_the_claim_learns_nothing_about_the_schema(
    migrated_session: Session,
) -> None:
    unit, grant = _claimed(migrated_session, "evidence-no-claim")

    refused = _append(migrated_session, unit, grant, _with(body="raw"), lease_token="wrong")

    assert isinstance(refused, DomainError) and refused.code == "claim_not_owned"


def test_recovery_onto_a_settled_rotation_unit_says_so_first(migrated_session: Session) -> None:
    unit, grant = _claimed(migrated_session, "evidence-recover-settled")
    expire(migrated_session, grant.claim_id)
    unit.state = WorkUnitState.CANCELLED
    migrated_session.commit()

    refused = _recover(migrated_session, unit, grant.attempt, _with(body="raw"))

    assert isinstance(refused, DomainError) and refused.code == "recovery_not_allowed"
