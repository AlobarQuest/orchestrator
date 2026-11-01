"""A rotation's decomposition, approved by policy when SYSTEM proposes it (ADR-0055 amendment 16).

The package revision a named person approved is a rotation's one click; the decomposition the
rotation decomposer derives from it is approved inside the proposal's own transaction when every
unit is a rotation unit the rotation pattern recognises. Anything else waits for a person.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import DecompositionProposal, Event, WorkUnit
from orchestrator.services.intake.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedDependency,
    ProposedUnit,
    submit_decomposition_proposal,
)
from orchestrator.services.intake.package_intake import (
    AcceptanceCriterionProjection,
    register_package_intake,
)
from tests.services.test_decomposition import package_ac_ids
from tests.services.test_package_intake import human_actor, intake_command

DESTINATIONS = [
    'bws-secret uuid="11111111-1111-4111-8111-111111111111"',
    'provider name="openrouter"',
]
SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)


def _criterion(ac_id: str) -> AcceptanceCriterionProjection:
    return AcceptanceCriterionProjection(
        ac_id=ac_id,
        condition="The step completes.",
        evidence_type="external_attestation",
        evidence="external: the step's rotation-step/1 evidence, filed by the rotation executor.",
        approver="devon",
    )


def _rotation_revision(
    session: Session, *, profile: str = "non-software-operational", approved_by: str = "human-1"
):
    return register_package_intake(
        session,
        intake_command(
            package_id="rotation-openrouter-generic",
            profile=profile,
            approved_by=approved_by,
            enforcement_snapshot={
                "title": "Standing rotation",
                "reach": ["external_system"],
                "profile_fields": {
                    "standing": True,
                    "credential_id": "openrouter-generic",
                    "destinations": DESTINATIONS,
                },
            },
            acceptance_criteria=(_criterion("AC-001"), _criterion("AC-002")),
        ),
        human_actor(),
    )


def _envelope(unit: str, **constraints: Any) -> dict[str, Any]:
    return {
        "capabilities": {"operational_action": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": None},
        "change_class": None,
        "conformance": None,
        "constraints": {
            "rotation_adapter": "openrouter",
            "rotation_unit": unit,
            "destinations": DESTINATIONS,
            "touches_no_hosted_service": True,
            **constraints,
        },
    }


def _command(
    revision_id: uuid.UUID,
    ac_ids: dict[str, uuid.UUID],
    *,
    retire: dict[str, Any] | None = None,
    dependency: ProposedDependency | None = None,
    key: str = "rotation-decomposer:r",
    retire_attempts: int = 3,
) -> DecompositionProposalCommand:
    return DecompositionProposalCommand(
        work_package_revision_id=revision_id,
        rationale="The openrouter type's derived plan.",
        proposed_units=(
            ProposedUnit(
                unit_key="openrouter-generic-mint",
                title="openrouter-generic: mint",
                outcome="the mint unit's steps complete",
                required_capability="operational_action",
                authority=normalize_authority(_envelope("mint")),
                authority_payload=_envelope("mint"),
                max_attempts=3,
            ),
            ProposedUnit(
                unit_key="openrouter-generic-retire",
                title="openrouter-generic: retire",
                outcome="the retire unit's steps complete",
                required_capability="operational_action",
                authority=normalize_authority(retire or _envelope("retire")),
                authority_payload=retire or _envelope("retire"),
                max_attempts=retire_attempts,
            ),
        ),
        dependencies=(
            dependency
            or ProposedDependency(
                source_unit_key="openrouter-generic-retire",
                kind="work_unit",
                required_state_or_condition="completed",
                target_unit_key="openrouter-generic-mint",
            ),
        ),
        ac_mappings=(
            AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="openrouter-generic-mint"),
            AcMapping(ac_id=str(ac_ids["AC-002"]), unit_key="openrouter-generic-retire"),
        ),
        retained_acs=(),
        idempotency_key=key,
    )


def _units(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(WorkUnit)) or 0


def test_system_proposing_a_recognised_rotation_decomposition_is_approved_by_policy(
    migrated_session: Session,
) -> None:
    revision = _rotation_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        SYSTEM,
    )

    assert proposal.state == "approved"
    assert proposal.decided_by == "orchestrator-system"
    reason = proposal.decision_reason or ""
    assert "approved by policy (ADR-0055 amendment 16)" in reason
    assert "credential rotation unit of an approved standing package" in reason
    assert "approved by human-1" in reason
    assert isinstance(proposal.created_work_unit_ids, dict)
    assert set(proposal.created_work_unit_ids) == {
        "openrouter-generic-mint",
        "openrouter-generic-retire",
    }
    assert _units(migrated_session) == 2


def test_a_replayed_proposal_creates_nothing_twice(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    command = _command(revision.id, package_ac_ids(migrated_session, revision.id))
    first = submit_decomposition_proposal(migrated_session, command, SYSTEM)
    again = submit_decomposition_proposal(migrated_session, command, SYSTEM)

    assert again.id == first.id and again.state == "approved"
    assert _units(migrated_session) == 2


def test_a_person_s_or_a_worker_s_proposal_waits_for_a_person(migrated_session: Session) -> None:
    """Kills: approving a decomposition a session wrote by hand, whose author meant a person to
    read it."""
    for actor in (human_actor(), ActorContext("worker-1", ActorRole.WORKER)):
        revision = _rotation_revision(migrated_session)
        proposal = submit_decomposition_proposal(
            migrated_session,
            _command(
                revision.id, package_ac_ids(migrated_session, revision.id), key=f"k-{actor.role}"
            ),
            actor,
        )
        assert proposal.state == "proposed"
    assert _units(migrated_session) == 0


def test_an_envelope_the_rotation_pattern_doesn_t_recognise_waits_for_a_person(
    migrated_session: Session,
) -> None:
    """A host with no overlap is the one declaration only a person makes."""
    revision = _rotation_revision(migrated_session)
    retire = _envelope("retire", host_without_overlap=True)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id), retire=retire),
        SYSTEM,
    )
    assert proposal.state == "proposed"
    assert _units(migrated_session) == 0


def test_a_dependency_on_a_person_s_act_waits_for_a_person(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    act = ProposedDependency(
        source_unit_key="openrouter-generic-retire",
        kind="external_system",
        required_state_or_condition="satisfied",
        external_ref="Devon revokes the old key",
    )
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id), dependency=act),
        SYSTEM,
    )
    assert proposal.state == "proposed"


def test_a_revision_that_isn_t_a_standing_rotation_waits_for_a_person(
    migrated_session: Session,
) -> None:
    revision = _rotation_revision(migrated_session, profile="software")
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        SYSTEM,
    )
    assert proposal.state == "proposed"
    reread = migrated_session.get(DecompositionProposal, proposal.id)
    assert reread is not None and reread.decided_by is None


def test_system_may_register_a_unit_only_for_a_decomposition_policy_approved(
    migrated_session: Session,
) -> None:
    """The second human gate behind approval: SYSTEM is admitted for a policy approval alone."""
    from orchestrator.services.intake.packages import register_approved_unit
    from tests.services.test_package_intake import NOW

    revision = _rotation_revision(migrated_session)
    with pytest.raises(DomainError, match="registered human actor"):
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="u",
            title="t",
            outcome="o",
            required_capability="operational_action",
            authority=normalize_authority(_envelope("mint")),
            approved_by="orchestrator-system",
            approved_at=NOW,
            actor_id="orchestrator-system",
            actor_role=ActorRole.SYSTEM,
        )


def test_a_dependency_bump_a_known_good_pattern_recognises_waits_for_a_person(
    migrated_session: Session,
) -> None:
    """Kills: a policy that asks the authority gate alone. A uv pin bump is recognised, but the
    one click it rests on is a rotation revision's, so only rotation units are approved."""
    from tests.services.test_authority_known_good import uv_bump

    revision = register_package_intake(
        migrated_session,
        intake_command(
            package_id="bump-ruff",
            enforcement_snapshot={"title": "Bump", "reach": ["source_repository"]},
            acceptance_criteria=(_criterion("AC-001"),),
        ),
        human_actor(),
    )
    envelope = uv_bump()
    del envelope["constraints"]["work_unit_id"]
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="The bump.",
            proposed_units=(
                ProposedUnit(
                    unit_key="bump",
                    title="bump ruff",
                    outcome="ruff moves",
                    required_capability="repo.edit",
                    authority=normalize_authority(envelope),
                    authority_payload=envelope,
                    max_attempts=3,
                ),
            ),
            dependencies=(),
            ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="bump"),),
            retained_acs=(),
            idempotency_key="bump",
        ),
        SYSTEM,
    )
    assert proposal.state == "proposed"


def test_a_dependency_on_anything_but_a_unit_completing_waits_for_a_person(
    migrated_session: Session,
) -> None:
    """Each half of the dependency rule alone: a unit in another state, and an outside condition
    that happens to be named `completed`."""
    cases = (
        ProposedDependency(
            source_unit_key="openrouter-generic-retire",
            kind="work_unit",
            required_state_or_condition="failed",
            target_unit_key="openrouter-generic-mint",
        ),
        ProposedDependency(
            source_unit_key="openrouter-generic-retire",
            kind="external_system",
            required_state_or_condition="completed",
            external_ref="Devon revokes the old key",
        ),
    )
    for index, dependency in enumerate(cases):
        revision = _rotation_revision(migrated_session)
        proposal = submit_decomposition_proposal(
            migrated_session,
            _command(
                revision.id,
                package_ac_ids(migrated_session, revision.id),
                dependency=dependency,
                key=f"dep-{index}",
            ),
            SYSTEM,
        )
        assert proposal.state == "proposed", dependency


def test_more_attempts_than_the_envelope_budgets_waits_for_a_person(
    migrated_session: Session,
) -> None:
    """The enforced cap is the unit's column, held to the envelope only on the policy branches."""
    revision = _rotation_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id), retire_attempts=4),
        SYSTEM,
    )
    assert proposal.state == "proposed"


def test_a_revision_an_approval_policy_approved_waits_for_a_person(
    migrated_session: Session,
) -> None:
    revision = _rotation_revision(migrated_session, approved_by="policy:rotation@v1")
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        SYSTEM,
    )
    assert proposal.state == "proposed"


def test_a_unit_no_rotation_pattern_recognises_waits_for_a_person(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recognition by any other pattern is not enough: the policy's basis is the rotation one."""
    from dataclasses import replace

    from orchestrator.factory_policy import load_factory_policy
    from orchestrator.services.intake import decomposition

    policy = load_factory_policy()
    stripped = replace(
        policy,
        rows={k: replace(row, rotation_known_good=()) for k, row in policy.rows.items()},
    )
    monkeypatch.setattr(decomposition, "load_factory_policy", lambda: stripped)
    revision = _rotation_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        SYSTEM,
    )
    assert proposal.state == "proposed"


def test_an_approval_that_cannot_complete_leaves_the_proposal_for_a_person(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.services.intake import decomposition

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise DomainError("unit_conflict", "a unit already holds that key", None)

    monkeypatch.setattr(decomposition, "_approve", refuse)
    revision = _rotation_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        SYSTEM,
    )
    assert proposal.state == "proposed"
    assert migrated_session.get(DecompositionProposal, proposal.id) is not None
    assert _units(migrated_session) == 0


def test_system_may_not_register_a_unit_of_a_decomposition_a_person_approved(
    migrated_session: Session,
) -> None:
    from orchestrator.persistence.models import ApprovedDecomposition
    from orchestrator.services.intake.decomposition import approve_decomposition_proposal
    from orchestrator.services.intake.packages import register_approved_unit
    from tests.services.test_package_intake import NOW

    revision = _rotation_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        human_actor(),
    )
    approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="looks right",
        idempotency_key="a",
    )
    approved = migrated_session.scalars(select(ApprovedDecomposition)).one()
    for actor_id in ("orchestrator-system", "human-1"):
        with pytest.raises(DomainError, match="registered human actor"):
            register_approved_unit(
                migrated_session,
                revision_id=revision.id,
                unit_key=f"extra-{actor_id}",
                title="t",
                outcome="o",
                required_capability="operational_action",
                authority=normalize_authority(_envelope("mint")),
                approved_by=actor_id,
                approved_at=NOW,
                actor_id=actor_id,
                actor_role=ActorRole.SYSTEM,
                approved_decomposition_id=approved.id,
            )


def test_system_may_not_register_a_unit_of_its_own_proposal_a_person_approved(
    migrated_session: Session,
) -> None:
    """A SYSTEM proposal policy left for a person, then approved by one, is not policy's."""
    from orchestrator.persistence.models import ApprovedDecomposition
    from orchestrator.services.intake.decomposition import approve_decomposition_proposal
    from orchestrator.services.intake.packages import register_approved_unit
    from tests.services.test_package_intake import NOW

    revision = _rotation_revision(migrated_session)
    retire = _envelope("retire", host_without_overlap=True)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id), retire=retire),
        SYSTEM,
    )
    approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="looks right",
        idempotency_key="a",
    )
    approved = migrated_session.scalars(select(ApprovedDecomposition)).one()
    with pytest.raises(DomainError, match="registered human actor"):
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="extra",
            title="t",
            outcome="o",
            required_capability="operational_action",
            authority=normalize_authority(_envelope("mint")),
            approved_by="orchestrator-system",
            approved_at=NOW,
            actor_id="orchestrator-system",
            actor_role=ActorRole.SYSTEM,
            approved_decomposition_id=approved.id,
        )


def test_an_approval_failing_midway_leaves_no_trace_of_itself(
    migrated_session: Session, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second unit's registration fails after the approval, the first unit and its events
    are written: the savepoint must take all of them back, and the proposal must survive."""
    from orchestrator.persistence.models import ApprovedDecomposition
    from orchestrator.services.intake import decomposition

    real = decomposition.register_approved_unit
    calls: list[str] = []

    def fail_second(session: Session, **kwargs: Any) -> WorkUnit:
        calls.append(kwargs["unit_key"])
        if len(calls) == 2:
            raise DomainError("unit_conflict", "a unit already holds that key", None)
        return real(session, **kwargs)

    monkeypatch.setattr(decomposition, "register_approved_unit", fail_second)
    revision = _rotation_revision(migrated_session)
    events_before = migrated_session.scalar(select(func.count()).select_from(Event))
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command(revision.id, package_ac_ids(migrated_session, revision.id)),
        SYSTEM,
    )
    migrated_session.commit()
    assert len(calls) == 2

    with Session(migrated_engine) as reader:
        stored = reader.get(DecompositionProposal, proposal.id)
        assert stored is not None
        assert (stored.state, stored.decided_by, stored.created_work_unit_ids) == (
            "proposed",
            None,
            None,
        )
        assert reader.scalar(select(func.count()).select_from(WorkUnit)) == 0
        assert reader.scalar(select(func.count()).select_from(ApprovedDecomposition)) == 0
        # The proposal's own event, written before the savepoint, and the record of why policy
        # didn't approve it, written after: nothing of the approval remains.
        assert reader.scalar(select(func.count()).select_from(Event)) == (events_before or 0) + 2
        recorded = reader.scalars(select(Event).where(Event.action == NOT_APPLIED)).one()
        assert recorded.payload == {"reason": "approval_failed", "detail": ["unit_conflict"]}


NOT_APPLIED = "decomposition.policy_not_applied"


def _refusals(session: Session, proposal: DecompositionProposal) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in session.scalars(
            select(Event).where(Event.action == NOT_APPLIED, Event.subject_id == proposal.id)
        )
    ]


def _propose(session: Session, revision_id: uuid.UUID, **changes: Any) -> DecompositionProposal:
    actor = changes.pop("actor", SYSTEM)
    return submit_decomposition_proposal(
        session, _command(revision_id, package_ac_ids(session, revision_id), **changes), actor
    )


def test_an_envelope_policy_refuses_records_why(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    retire = _envelope("retire", host_without_overlap=True)
    proposal = _propose(migrated_session, revision.id, retire=retire)
    [refusal] = _refusals(migrated_session, proposal)
    assert refusal["reason"] == "envelope_not_recognised"
    assert refusal["detail"]


def test_an_approval_policy_s_revision_records_why(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session, approved_by="policy:rotation@v1")
    proposal = _propose(migrated_session, revision.id)
    assert _refusals(migrated_session, proposal) == [
        {"reason": "revision_approved_by_policy", "detail": ["policy:rotation@v1"]}
    ]


def test_an_unreadable_policy_records_why(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.services.intake import decomposition

    def unreadable() -> None:
        raise DomainError("policy_unreadable", "no", None)

    monkeypatch.setattr(decomposition, "load_factory_policy", unreadable)
    revision = _rotation_revision(migrated_session)
    proposal = _propose(migrated_session, revision.id)
    assert [r["reason"] for r in _refusals(migrated_session, proposal)] == ["policy_unreadable"]


def test_a_dependency_policy_refuses_records_why(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    proposal = _propose(
        migrated_session,
        revision.id,
        dependency=ProposedDependency(
            source_unit_key="openrouter-generic-retire",
            kind="human_act",
            required_state_or_condition="completed",
            external_ref="mint",
        ),
    )
    assert [r["reason"] for r in _refusals(migrated_session, proposal)] == [
        "dependency_not_a_unit_completing"
    ]


def test_attempts_over_budget_record_why(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    proposal = _propose(migrated_session, revision.id, retire_attempts=4)
    assert [r["reason"] for r in _refusals(migrated_session, proposal)] == ["attempts_over_budget"]


def test_a_failed_approval_records_why(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.services.intake import decomposition

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise DomainError("unit_conflict", "a unit already holds that key", None)

    monkeypatch.setattr(decomposition, "_approve", refuse)
    revision = _rotation_revision(migrated_session)
    proposal = _propose(migrated_session, revision.id)
    assert _refusals(migrated_session, proposal) == [
        {"reason": "approval_failed", "detail": ["unit_conflict"]}
    ]


@pytest.mark.parametrize(
    "actor", [human_actor(), ActorContext("worker-1", ActorRole.WORKER)], ids=["human", "worker"]
)
def test_a_proposal_nobody_expected_policy_to_approve_records_nothing(
    migrated_session: Session, actor: ActorContext
) -> None:
    revision = _rotation_revision(migrated_session)
    retire = _envelope("retire", host_without_overlap=True)
    proposal = _propose(migrated_session, revision.id, retire=retire, actor=actor)
    assert proposal.state == "proposed"
    assert _refusals(migrated_session, proposal) == []


def test_an_approved_proposal_records_nothing(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    proposal = _propose(migrated_session, revision.id)
    assert proposal.state == "approved"
    assert _refusals(migrated_session, proposal) == []


def test_a_replay_does_not_record_a_second_refusal(migrated_session: Session) -> None:
    revision = _rotation_revision(migrated_session)
    retire = _envelope("retire", host_without_overlap=True)
    first = _propose(migrated_session, revision.id, retire=retire)
    replay = _propose(migrated_session, revision.id, retire=retire)
    assert replay.id == first.id
    assert len(_refusals(migrated_session, first)) == 1


def test_recording_a_refusal_twice_keeps_the_first(migrated_session: Session) -> None:
    from orchestrator.services.intake import decomposition
    from orchestrator.services.intake.bump_policy import PolicyRefusal

    revision = _rotation_revision(migrated_session)
    retire = _envelope("retire", host_without_overlap=True)
    proposal = _propose(migrated_session, revision.id, retire=retire)
    decomposition._record_policy_not_applied(
        migrated_session, proposal, SYSTEM, PolicyRefusal("approval_failed")
    )
    assert [r["reason"] for r in _refusals(migrated_session, proposal)] == [
        "envelope_not_recognised"
    ]
