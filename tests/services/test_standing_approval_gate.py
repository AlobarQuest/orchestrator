"""The decomposition gate's exception for a standing approval (ADR-0055 amendments 22 and 39).

The gate keeps refusing a `policy:` approver on a rotation revision, except a lane credential's
revision 1, or an orchestrator or change-manager bearer's reissue (revision 2 or later) whose
credential isn't a hand-rotated identity, that the policy `policy:non-software-operational@`
approved and the work carrier recorded checking. Each refusal below is driven from the admitted
control by changing one input, so a refusal that fired for another reason would fail the control
rather than pass for free.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    DecompositionProposal,
    Event,
    Observation,
    WorkPackageRevision,
)
from orchestrator.services.intake import standing_approval as gate
from orchestrator.services.intake.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedUnit,
    submit_decomposition_proposal,
)
from orchestrator.services.intake.package_intake import (
    AcceptanceCriterionProjection,
    register_package_intake,
)
from orchestrator.services.release.observations import ObservationCommand, record_observation
from rotation_proposer.standing_approval import HAND_ROTATED_IDS, REISSUE_TYPES
from rotation_worker.destinations import render
from tests.services.test_decomposition import package_ac_ids
from tests.services.test_package_intake import human_actor, intake_command

PACKAGE_ID = "rotation-landing-ledger-ping-url"
CONTENT_HASH = "c" * 64
APPROVER = "policy:non-software-operational@v4"
CARRIER = "work-carrier-observer"
SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
LANE = 'provider name="healthchecks-ping-url"'
KEEPER = 'bws-secret uuid="11111111-1111-4111-8111-111111111111"'
# A reissue: the orchestrator operator bearer's destinations and its retire unit's envelope.
BEARER = "orchestrator-operator-bearer"
BEARER_PACKAGE = f"rotation-{BEARER}"
BEARER_DESTINATIONS = (
    'bws-secret uuid="bd71bed1-4aac-4af8-9094-b4970180bc59"',
    'coolify-env-hash instance="prod" resource_type="application" uuid="eqj5l7k705fhi12x9i74fqf0" '
    'key="ORCHESTRATOR_M2M_CREDENTIALS" entry="orchestrator-operator"',
    'provider name="orchestrator"',
)


def _revision(
    session: Session,
    *,
    approved_by: str = APPROVER,
    profile: str = "non-software-operational",
    revision: int = 1,
    destinations: tuple[str, ...] = (KEEPER, LANE),
    key: str = "intake-1",
    package_id: str = PACKAGE_ID,
    credential_id: object = "landing-ledger-ping-url",
    reach: tuple[str, ...] = ("external_system",),
) -> WorkPackageRevision:
    return register_package_intake(
        session,
        intake_command(
            package_id=package_id,
            revision=revision,
            content_hash=CONTENT_HASH,
            profile=profile,
            approved_by=approved_by,
            enforcement_snapshot={
                "title": "Standing rotation",
                "reach": list(reach),
                "profile_fields": {
                    "standing": True,
                    "credential_id": credential_id,
                    "destinations": list(destinations),
                },
            },
            acceptance_criteria=(
                AcceptanceCriterionProjection(
                    ac_id="AC-001",
                    condition="The step completes.",
                    evidence_type="external_attestation",
                    evidence="external: the store step's evidence, filed by the executor.",
                    approver="devon",
                ),
            ),
            idempotency_key=key,
        ),
        human_actor(),
    )


def _envelope() -> dict[str, Any]:
    return {
        "capabilities": {"operational_action": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": None},
        "change_class": None,
        "conformance": None,
        "constraints": {
            "rotation_adapter": "healthchecks-ping-url",
            "rotation_unit": "store",
            "destinations": [KEEPER, LANE],
            "touches_no_hosted_service": True,
        },
    }


def _reissue_envelope() -> dict[str, Any]:
    return {
        "capabilities": {"operational_action": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": None},
        "change_class": None,
        "conformance": None,
        "constraints": {
            "rotation_adapter": "orchestrator",
            "rotation_unit": "retire",
            "destinations": list(BEARER_DESTINATIONS),
        },
    }


def _propose(
    session: Session,
    revision: WorkPackageRevision,
    *,
    carrier: str | None = CARRIER,
    envelope: dict[str, Any] | None = None,
) -> DecompositionProposal:
    envelope = envelope if envelope is not None else _envelope()
    ac_ids = package_ac_ids(session, revision.id)
    return submit_decomposition_proposal(
        session,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="The ping URL's issue plan.",
            proposed_units=(
                ProposedUnit(
                    unit_key="landing-ledger-ping-url-store",
                    title="landing-ledger-ping-url: store",
                    outcome="the store unit's steps complete",
                    required_capability="operational_action",
                    authority=normalize_authority(envelope),
                    authority_payload=envelope,
                    max_attempts=3,
                ),
            ),
            dependencies=(),
            ac_mappings=(
                AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="landing-ledger-ping-url-store"),
            ),
            retained_acs=(),
            idempotency_key=f"rotation-decomposer:{revision.id}",
        ),
        SYSTEM,
        work_carrier_agent_id=carrier,
    )


def _record(
    session: Session,
    *,
    actor: str = CARRIER,
    role: ActorRole = ActorRole.OBSERVER,
    package_id: str = PACKAGE_ID,
    subject_revision: int = 1,
    **facts: Any,
) -> None:
    stated = {
        "package_id": package_id,
        "revision": subject_revision,
        "content_hash": CONTENT_HASH,
        "package_commit": "a" * 40,
        "approver": APPROVER,
        "registry_entry": "landing-ledger-ping-url",
        "registry_repository": "AlobarQuest/orchestrator",
        "registry_commit": "b" * 40,
        "refused": [],
        **facts,
    }
    outcome = record_observation(
        session,
        ObservationCommand(
            actor=ActorContext(actor, role),
            source_system="work_carrier",
            source_reference=f"standing-approval:{package_id}@r{subject_revision}:{uuid.uuid4()}",
            source_url=None,
            trust_classification="delivery_system",
            subject_type="package_revision",
            subject_reference=f"{package_id}@r{subject_revision}",
            environment=None,
            observation_type="standing_approval_check",
            status="passed",
            severity="info",
            observed_at=datetime(2026, 11, 11, 12, tzinfo=UTC),
            summary="the work carrier's carry form refused nothing",
            facts=stated,
            payload_digest=None,
            idempotency_key=f"work-carrier-standing:{uuid.uuid4()}",
        ),
    )
    assert isinstance(outcome, Observation), outcome


def _refusals(session: Session, proposal: DecompositionProposal) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in session.scalars(
            select(Event).where(
                Event.action == "decomposition.policy_not_applied",
                Event.subject_id == proposal.id,
            )
        )
    ]


def _unrecorded(detail: str) -> list[dict[str, Any]]:
    return [{"reason": "standing_approval_unrecorded", "detail": [detail]}]


def test_the_carrier_s_record_lets_the_standing_approval_stand_for_a_person(
    migrated_session: Session,
) -> None:
    revision = _revision(migrated_session)
    _record(migrated_session)
    proposal = _propose(migrated_session, revision)

    assert proposal.state == "approved", _refusals(migrated_session, proposal)
    assert "whose check the work carrier recorded" in (proposal.decision_reason or "")
    assert APPROVER in (proposal.decision_reason or "")


def test_another_policy_s_approval_still_refuses_as_before(migrated_session: Session) -> None:
    revision = _revision(migrated_session, approved_by="policy:rotation@v1")
    _record(migrated_session)
    proposal = _propose(migrated_session, revision)

    assert proposal.state == "proposed"
    assert _refusals(migrated_session, proposal) == [
        {"reason": "revision_approved_by_policy", "detail": ["policy:rotation@v1"]}
    ]


def test_a_revision_of_another_profile_refuses(migrated_session: Session) -> None:
    revision = _revision(migrated_session, profile="software")
    _record(migrated_session)
    proposal = _propose(migrated_session, revision)
    assert _refusals(migrated_session, proposal) == _unrecorded("not_a_rotation_revision")


def _bearer_revision(session: Session, **overrides: Any) -> WorkPackageRevision:
    return _revision(
        session,
        **{
            "revision": 2,
            "package_id": BEARER_PACKAGE,
            "credential_id": BEARER,
            "destinations": BEARER_DESTINATIONS,
            "reach": ("external_system", "live_estate"),
            **overrides,
        },
    )


def _record_bearer(session: Session, *, subject_revision: int = 2) -> None:
    _record(
        session,
        package_id=BEARER_PACKAGE,
        subject_revision=subject_revision,
        registry_entry=BEARER,
        form="reissue",
    )


def test_the_carrier_s_record_lets_the_standing_approval_stand_for_a_reissue(
    migrated_session: Session,
) -> None:
    revision = _bearer_revision(migrated_session)
    _record_bearer(migrated_session)
    proposal = _propose(migrated_session, revision, envelope=_reissue_envelope())
    assert proposal.state == "approved", _refusals(migrated_session, proposal)
    assert "whose check the work carrier recorded" in (proposal.decision_reason or "")


def test_a_reissue_with_no_carrier_record_refuses(migrated_session: Session) -> None:
    revision = _bearer_revision(migrated_session)
    proposal = _propose(migrated_session, revision, envelope=_reissue_envelope())
    assert proposal.state == "proposed"
    assert _refusals(migrated_session, proposal) == _unrecorded("no_carrier_record")


def test_a_reissue_with_an_unset_carrier_identity_refuses(migrated_session: Session) -> None:
    revision = _bearer_revision(migrated_session)
    _record_bearer(migrated_session)
    proposal = _propose(migrated_session, revision, carrier=None, envelope=_reissue_envelope())
    assert _refusals(migrated_session, proposal) == _unrecorded("carrier_identity_unset")


def test_a_lane_provider_at_revision_2_refuses(migrated_session: Session) -> None:
    """A lane credential's later revision stays Devon's: only its revision 1 is covered."""
    revision = _revision(migrated_session, revision=2)
    _record(migrated_session, subject_revision=2)
    proposal = _propose(migrated_session, revision)
    assert _refusals(migrated_session, proposal) == _unrecorded("not_a_reissue_type")


@pytest.mark.parametrize(
    "destinations",
    [
        (KEEPER, 'provider name="openrouter"'),
        (KEEPER, 'provider name="postgres-role"'),
        (KEEPER,),
        (KEEPER, 'provider name="orchestrator"', 'provider name="change-manager"'),
    ],
    ids=["openrouter", "postgres-role", "no-provider", "both-types"],
)
def test_a_reissue_naming_no_one_reissue_type_refuses(
    migrated_session: Session, destinations: tuple[str, ...]
) -> None:
    revision = _bearer_revision(migrated_session, destinations=destinations)
    _record_bearer(migrated_session)
    proposal = _propose(migrated_session, revision, envelope=_reissue_envelope())
    assert _refusals(migrated_session, proposal) == _unrecorded("not_a_reissue_type")


@pytest.mark.parametrize("credential_id", gate.HAND_ROTATED_IDS)
def test_a_reissue_of_a_hand_rotated_identity_refuses_beside_a_record(
    migrated_session: Session, credential_id: str
) -> None:
    revision = _bearer_revision(migrated_session, credential_id=credential_id)
    _record_bearer(migrated_session)
    proposal = _propose(migrated_session, revision, envelope=_reissue_envelope())
    assert _refusals(migrated_session, proposal) == _unrecorded("hand_rotated_identity")


@pytest.mark.parametrize("credential_id", [None, "", 7])
def test_a_reissue_naming_no_credential_id_refuses(
    migrated_session: Session, credential_id: object
) -> None:
    revision = _bearer_revision(migrated_session, credential_id=credential_id)
    _record_bearer(migrated_session)
    proposal = _propose(migrated_session, revision, envelope=_reissue_envelope())
    assert _refusals(migrated_session, proposal) == _unrecorded("no_credential_id")


def test_a_reissue_type_at_revision_1_refuses_as_no_lane_type(migrated_session: Session) -> None:
    """A first hand-over is a person's (amendment 39 reading 2)."""
    revision = _bearer_revision(migrated_session, revision=1)
    _record_bearer(migrated_session, subject_revision=1)
    proposal = _propose(migrated_session, revision, envelope=_reissue_envelope())
    assert _refusals(migrated_session, proposal) == _unrecorded("not_a_lane_type")


@pytest.mark.parametrize(
    "destinations",
    [
        (KEEPER, 'provider name="openrouter"'),
        (KEEPER,),
        (KEEPER, LANE, 'provider name="bws-lane-token"'),
        # A GitHub App key's package (ADR-0055 amendment 35 item 15, D-84).
        (KEEPER, 'provider name="github"'),
        # An Anthropic API key's package (ADR-0055 amendment 36 item 13, D-85).
        (KEEPER, 'provider name="anthropic"'),
    ],
    ids=["another-type", "no-provider", "both-types", "github-app-key", "anthropic-key"],
)
def test_a_package_naming_no_one_lane_type_refuses(
    migrated_session: Session, destinations: tuple[str, ...]
) -> None:
    revision = _revision(migrated_session, destinations=destinations)
    _record(migrated_session)
    proposal = _propose(migrated_session, revision)
    assert _refusals(migrated_session, proposal) == _unrecorded("not_a_lane_type")


def test_an_unset_carrier_identity_refuses(migrated_session: Session) -> None:
    revision = _revision(migrated_session)
    _record(migrated_session)
    proposal = _propose(migrated_session, revision, carrier=None)
    assert _refusals(migrated_session, proposal) == _unrecorded("carrier_identity_unset")


def test_no_record_refuses(migrated_session: Session) -> None:
    revision = _revision(migrated_session)
    proposal = _propose(migrated_session, revision)
    assert _refusals(migrated_session, proposal) == _unrecorded("no_carrier_record")


@pytest.mark.parametrize(
    ("actor", "role"),
    [("orchestrator-system", ActorRole.SYSTEM), ("another-observer", ActorRole.OBSERVER)],
)
def test_a_record_by_another_actor_refuses(
    migrated_session: Session, actor: str, role: ActorRole
) -> None:
    revision = _revision(migrated_session)
    _record(migrated_session, actor=actor, role=role)
    proposal = _propose(migrated_session, revision)
    assert _refusals(migrated_session, proposal) == _unrecorded("no_carrier_record")


@pytest.mark.parametrize(
    "facts",
    [
        {"package_id": "rotation-another"},
        {"revision": 2},
        {"revision": True},
        {"content_hash": "d" * 64},
        {"refused": ["proposer_generated"]},
    ],
    ids=["package", "revision", "boolean-revision", "hash", "refused"],
)
def test_a_record_that_doesn_t_bind_this_revision_refuses(
    migrated_session: Session, facts: dict[str, Any]
) -> None:
    revision = _revision(migrated_session)
    _record(migrated_session, **facts)
    proposal = _propose(migrated_session, revision)
    assert _refusals(migrated_session, proposal) == _unrecorded("no_carrier_record")


def test_a_read_that_fails_refuses_and_the_proposal_stands(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = _revision(migrated_session)
    _record(migrated_session)

    def failing(*args: object, **kwargs: object) -> object:
        raise OperationalError("SELECT", {}, Exception("connection reset"))

    monkeypatch.setattr(gate, "select", failing)
    proposal = _propose(migrated_session, revision)
    assert proposal.state == "proposed"
    assert _refusals(migrated_session, proposal) == [
        {
            "reason": "standing_approval_unrecorded",
            "detail": ["carrier_record_unreadable", "OperationalError"],
        }
    ]


def test_the_lane_providers_are_the_executor_s_destination_strings() -> None:
    assert gate.LANE_PROVIDERS == tuple(
        render({"kind": "provider", "name": name})
        for name in ("bws-lane-token", "healthchecks-ping-url")
    )


def test_the_reissue_providers_are_the_executor_s_destination_strings() -> None:
    assert gate.REISSUE_PROVIDERS == tuple(
        render({"kind": "provider", "name": name}) for name in REISSUE_TYPES
    )


def test_the_hand_rotated_ids_are_the_proposer_s() -> None:
    assert gate.HAND_ROTATED_IDS == HAND_ROTATED_IDS
