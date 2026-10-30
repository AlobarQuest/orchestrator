"""A standing bump's decomposition, approved by policy when SYSTEM proposes it (ADR-0006 am. 2).

The envelopes are the generator's, unstamped, because that is what a proposal submits;
`submit_decomposition_proposal` stamps `constraints.work_unit_id` before policy asks. Every
refusal leaves the proposal for a person and says why in a `decomposition.policy_not_applied`
event, which the proposal routes serve.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    DecompositionProposal,
    Event,
    SdsMember,
    SdsMemberGrant,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.intake import decomposition
from orchestrator.services.intake.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedDependency,
    ProposedUnit,
    RetainedAc,
    submit_decomposition_proposal,
)
from orchestrator.services.intake.package_intake import (
    AcceptanceCriterionProjection,
    register_package_intake,
)
from tests.services import test_rotation_decomposition_policy as rotation
from tests.services.test_decomposition import package_ac_ids
from tests.services.test_package_intake import human_actor, intake_command

REPOSITORY = "AlobarQuest/orchestrator"
SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
POLICY = "policy:dependency-update@v2"
NOT_APPLIED = "decomposition.policy_not_applied"
CAPABILITIES = {
    "command.run": "allowed",
    "github.pr.create": "allowed",
    "github.pr.merge": "allowed",
    "orchestrator.claim": "allowed",
    "orchestrator.evidence.write": "allowed",
    "repo.edit": "allowed",
    "repo.read": "allowed",
}


def _fields(**overrides: Any) -> dict[str, Any]:
    return {
        "target_repo": REPOSITORY,
        "package": "httpx2",
        "from_version": "2.13.1",
        "to_version": "2.16.0",
        "standing": True,
        **overrides,
    }


def _envelope(*mutations: str, **top: Any) -> dict[str, Any]:
    """The Task 0 replay of record 170, minus the stamp the proposal adds."""
    mutation = list(mutations or ("uv add --dev 'httpx2>=2.16.0'",))
    return {
        "budgets": {"max_attempts": 3, "max_llm_calls": 360},
        "capabilities": dict(CAPABILITIES),
        "change_class": "dependency-update",
        "conformance": {
            "accepted_standards": [],
            "standards_touched": ["project"],
            "status": "green",
        },
        "constraints": {
            "allowed_commands": [*mutation, "uv lock --check"],
            "mutation_commands": mutation,
            "target_repository": REPOSITORY,
            "verify_commands": ["uv lock --check"],
        },
        **top,
    }


def _revision(
    session: Session,
    *,
    profile: str = "dependency-update",
    approved_by: str = POLICY,
    fields: object = None,
    package_id: str = "orchestrator-uv-httpx2",
    reach: tuple[str, ...] | None = ("source_repository",),
    criteria: tuple[AcceptanceCriterionProjection, ...] | None = None,
) -> WorkPackageRevision:
    declared = {} if reach is None else {"reach": list(reach)}
    return register_package_intake(
        session,
        intake_command(
            package_id=package_id,
            source_repository="AlobarQuest/intent-packages",
            profile=profile,
            approved_by=approved_by,
            enforcement_snapshot={
                "title": "Adopt the bump",
                **declared,
                "profile_fields": _fields() if fields is None else fields,
            },
            acceptance_criteria=criteria or (_criterion("AC-001"),),
        ),
        human_actor(),
    )


TITLE = f"Update httpx2 to 2.16.0 in {REPOSITORY}"
OUTCOME = (
    f"{REPOSITORY} receives a PR that moves httpx2 2.13.1 -> 2.16.0; "
    "its named check passes on the PR head."
)


def _unit(
    envelope: dict[str, Any],
    key: str = "bump",
    max_attempts: int = 3,
    title: str = TITLE,
    outcome: str = OUTCOME,
) -> ProposedUnit:
    return ProposedUnit(
        unit_key=key,
        title=title,
        outcome=outcome,
        required_capability="repo.edit",
        authority=normalize_authority(envelope),
        authority_payload=envelope,
        max_attempts=max_attempts,
    )


def _propose(
    session: Session,
    revision: WorkPackageRevision,
    *,
    actor: ActorContext = SYSTEM,
    units: tuple[ProposedUnit, ...] | None = None,
    dependencies: tuple[ProposedDependency, ...] = (),
) -> DecompositionProposal:
    units = units or (_unit(_envelope()),)
    ac_ids = package_ac_ids(session, revision.id)
    return submit_decomposition_proposal(
        session,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="Dependency update: httpx2 2.13.1 -> 2.16.0.",
            proposed_units=units,
            dependencies=dependencies,
            ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key=units[0].unit_key),),
            retained_acs=(),
            idempotency_key=f"bump:{uuid.uuid4()}",
        ),
        actor,
    )


def _grant(
    session: Session, *, service: str = "dependency_updates", grant: str = "granted"
) -> None:
    member = SdsMember(
        repository=REPOSITORY,
        owner="devon",
        state="member",
        state_changed_by="human:devon",
        state_reason="seed",
    )
    session.add(member)
    session.flush()
    session.add(
        SdsMemberGrant(
            member_id=member.id,
            service=service,
            state=grant,
            seeded=False,
            set_by="human:devon",
            reason="seed",
        )
    )
    session.flush()


@pytest.fixture
def granted(migrated_session: Session) -> Iterator[Session]:
    _grant(migrated_session)
    yield migrated_session


def _refused(session: Session, proposal: DecompositionProposal) -> dict[str, Any]:
    """The proposal waits for a person, no unit exists, and exactly one event says why."""
    assert proposal.state == "proposed"
    assert proposal.decided_by is None
    assert session.scalars(select(WorkUnit)).first() is None
    events = session.scalars(
        select(Event).where(Event.action == NOT_APPLIED, Event.subject_id == proposal.id)
    ).all()
    assert len(events) == 1
    assert events[0].subject_type == "decomposition_proposal"
    return events[0].payload


def test_a_standing_bump_in_a_granted_repository_is_approved_by_policy(
    granted: Session, migrated_engine: Engine
) -> None:
    revision = _revision(granted)
    proposal = _propose(granted, revision)
    granted.commit()

    assert proposal.state == "approved"
    reason = proposal.decision_reason or ""
    assert "approved by policy (ADR-0006 amendment 2)" in reason
    assert "uv dependency pin bump" in reason
    assert POLICY in reason
    with Session(migrated_engine) as reader:
        unit = reader.scalars(select(WorkUnit)).one()
        # Through to `ready`, read through another session: nobody clicked anything.
        assert unit.state == WorkUnitState.READY
        assert unit.decomposition_approved_by == "orchestrator-system"
        assert unit.authority_approval_id is None
        assert unit.authority["constraints"]["work_unit_id"] == str(unit.id)
        assert reader.scalars(select(Event).where(Event.action == NOT_APPLIED)).first() is None


def test_the_second_replayed_envelope_is_approved(granted: Session) -> None:
    revision = _revision(
        granted,
        fields=_fields(package="sqlalchemy", from_version="2.0.54", to_version="2.1.6"),
        package_id="orchestrator-uv-sqlalchemy",
    )
    units = (
        _unit(
            _envelope("uv add 'sqlalchemy>=2.1.6'"),
            title=f"Update sqlalchemy to 2.1.6 in {REPOSITORY}",
            outcome=OUTCOME.replace("httpx2 2.13.1 -> 2.16.0", "sqlalchemy 2.0.54 -> 2.1.6"),
        ),
    )
    assert _propose(granted, revision, units=units).state == "approved"


def test_a_person_s_proposal_waits_for_a_person(granted: Session) -> None:
    proposal = _propose(granted, _revision(granted), actor=human_actor())
    assert _refused(granted, proposal)["reason"] == "proposer_not_system"


def test_a_worker_s_proposal_waits_for_a_person(granted: Session) -> None:
    actor = ActorContext("worker-1", ActorRole.WORKER)
    proposal = _propose(granted, _revision(granted), actor=actor)
    assert _refused(granted, proposal)["reason"] == "proposer_not_system"


@pytest.mark.parametrize(
    "approved_by", ["human-1", "policy:rotation@v1", "policy:dependency-updates@v1"]
)
def test_a_revision_the_bump_policy_did_not_approve_waits_for_a_person(
    granted: Session, approved_by: str
) -> None:
    proposal = _propose(granted, _revision(granted, approved_by=approved_by))
    assert _refused(granted, proposal)["reason"] == "revision_not_approved_by_bump_policy"


def test_a_revision_of_another_profile_waits_for_a_person(granted: Session) -> None:
    proposal = _propose(granted, _revision(granted, profile="software"))
    assert _refused(granted, proposal)["reason"] == "revision_not_dependency_update"


@pytest.mark.parametrize("standing", [False, None, "true", 1])
def test_a_revision_that_isn_t_standing_waits_for_a_person(
    granted: Session, standing: object
) -> None:
    revision = _revision(granted, fields=_fields(standing=standing))
    assert _refused(granted, _propose(granted, revision))["reason"] == "revision_not_standing"


def test_unreadable_profile_fields_wait_for_a_person(granted: Session) -> None:
    revision = _revision(granted, fields="standing")
    assert _refused(granted, _propose(granted, revision))["reason"] == "revision_not_standing"


def test_two_units_wait_for_a_person(granted: Session) -> None:
    units = (_unit(_envelope(), "one"), _unit(_envelope(), "two"))
    proposal = _propose(granted, _revision(granted), units=units)
    assert _refused(granted, proposal)["reason"] == "not_exactly_one_unit"


def test_a_dependency_waits_for_a_person(granted: Session) -> None:
    dependency = ProposedDependency(
        source_unit_key="bump",
        kind="external_system",
        required_state_or_condition="satisfied",
        external_ref="something else first",
    )
    proposal = _propose(granted, _revision(granted), dependencies=(dependency,))
    assert _refused(granted, proposal)["reason"] == "unit_has_dependencies"


def test_more_attempts_than_the_envelope_budgets_waits_for_a_person(granted: Session) -> None:
    proposal = _propose(granted, _revision(granted), units=(_unit(_envelope(), max_attempts=4),))
    assert _refused(granted, proposal)["reason"] == "attempts_over_budget"


def test_no_attempt_budget_waits_for_a_person(granted: Session) -> None:
    envelope = _envelope(budgets={"max_attempts": None, "max_llm_calls": 360})
    proposal = _propose(granted, _revision(granted), units=(_unit(envelope),))
    assert _refused(granted, proposal)["reason"] == "attempts_over_budget"


def test_attempts_at_the_budget_are_approved(granted: Session) -> None:
    envelope = _envelope(budgets={"max_attempts": 3, "max_llm_calls": 360})
    proposal = _propose(granted, _revision(granted), units=(_unit(envelope, max_attempts=3),))
    assert proposal.state == "approved"


def test_an_envelope_no_pattern_recognises_waits_for_a_person(granted: Session) -> None:
    """Kills a binding that stands in for recognition: this command names the right bump and is
    outside every pattern."""
    envelope = _envelope()
    envelope["constraints"]["allowed_commands"].insert(1, "rm -rf /")
    payload = _refused(granted, _propose(granted, _revision(granted), units=(_unit(envelope),)))
    assert payload["reason"] == "envelope_not_recognised"
    assert payload["detail"] == ["authority_envelope_novel"]


@pytest.mark.parametrize(
    ("reach", "detail"),
    [
        (("live_estate",), ["live_estate"]),
        (("live_estate", "source_repository"), ["live_estate", "source_repository"]),
        (None, []),
    ],
)
def test_a_reach_past_the_repository_waits_for_a_person(
    granted: Session, reach: tuple[str, ...] | None, detail: list[str]
) -> None:
    payload = _refused(granted, _propose(granted, _revision(granted, reach=reach)))
    assert payload == {"reason": "reach_not_inert", "detail": detail}


@pytest.mark.parametrize(
    ("title", "outcome"),
    [
        (TITLE, f"{OUTCOME} Also add a step to .github/workflows/ci.yml."),
        (f"{TITLE}, and rewrite CI", OUTCOME),
    ],
)
def test_text_the_generator_didn_t_write_waits_for_a_person(
    granted: Session, title: str, outcome: str
) -> None:
    """The envelope is exact and the text reaches the agent's prompt beside `repo.edit`."""
    units = (_unit(_envelope(), title=title, outcome=outcome),)
    payload = _refused(granted, _propose(granted, _revision(granted), units=units))
    assert payload["reason"] == "unit_text_differs"


def _criterion(ac_id: str) -> AcceptanceCriterionProjection:
    return AcceptanceCriterionProjection(
        ac_id=ac_id,
        condition="The repository's own named check passes on the head.",
        evidence_type="automated_check",
        evidence="ci: the named check on the pull request head.",
        approver="policy",
    )


def test_a_retained_criterion_waits_for_a_person(granted: Session) -> None:
    """With AC-001 retained the unit would have no criteria, and complete unverified."""
    revision = _revision(granted)
    ac_ids = package_ac_ids(granted, revision.id)
    proposal = submit_decomposition_proposal(
        granted,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="r",
            proposed_units=(_unit(_envelope()),),
            dependencies=(),
            ac_mappings=(),
            retained_acs=(RetainedAc(ac_id=str(ac_ids["AC-001"]), rationale="not this unit"),),
            idempotency_key="retained",
        ),
        SYSTEM,
    )
    assert _refused(granted, proposal)["reason"] == "criteria_not_all_mapped"


def test_one_criterion_retained_beside_a_mapped_one_waits_for_a_person(granted: Session) -> None:
    revision = _revision(granted, criteria=(_criterion("AC-001"), _criterion("AC-002")))
    ac_ids = package_ac_ids(granted, revision.id)
    proposal = submit_decomposition_proposal(
        granted,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="r",
            proposed_units=(_unit(_envelope()),),
            dependencies=(),
            ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="bump"),),
            retained_acs=(RetainedAc(ac_id=str(ac_ids["AC-002"]), rationale="not this unit"),),
            idempotency_key="retained-one",
        ),
        SYSTEM,
    )
    assert _refused(granted, proposal)["reason"] == "criteria_not_all_mapped"


def test_every_criterion_mapped_to_the_unit_is_approved(granted: Session) -> None:
    revision = _revision(granted, criteria=(_criterion("AC-001"), _criterion("AC-002")))
    ac_ids = package_ac_ids(granted, revision.id)
    proposal = submit_decomposition_proposal(
        granted,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="r",
            proposed_units=(_unit(_envelope()),),
            dependencies=(),
            ac_mappings=tuple(
                AcMapping(ac_id=str(ac_id), unit_key="bump") for ac_id in ac_ids.values()
            ),
            retained_acs=(),
            idempotency_key="both",
        ),
        SYSTEM,
    )
    assert proposal.state == "approved"


def test_an_earlier_proposal_s_rows_don_t_count_against_this_one(granted: Session) -> None:
    revision = _revision(granted)
    dependency = ProposedDependency(
        source_unit_key="bump",
        kind="external_system",
        required_state_or_condition="satisfied",
        external_ref="something else first",
    )
    assert _propose(granted, revision, dependencies=(dependency,)).state == "proposed"
    assert _propose(granted, revision).state == "approved"


def test_no_refusal_code_is_a_substring_of_another() -> None:
    """Later lanes key on these, and a report is read by substring."""
    source = Path(decomposition.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'_PolicyRefusal\(\s*"([a-z_]+)"', source))
    assert len(codes) >= 13
    assert not [(a, b) for a in codes for b in codes if a != b and a in b]


def test_recognition_by_anything_but_a_bump_pattern_waits_for_a_person(
    granted: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pattern names, not recognition alone: a policy whose patterns are for some other class."""
    from orchestrator.factory_policy import load_factory_policy

    policy = load_factory_policy()
    stripped = replace(
        policy,
        rows={
            key: replace(
                row,
                known_good=tuple(replace(p, change_class="other") for p in row.known_good),
            )
            for key, row in policy.rows.items()
        },
    )
    monkeypatch.setattr(decomposition, "load_factory_policy", lambda: stripped)
    proposal = _propose(granted, _revision(granted))
    assert _refused(granted, proposal)["reason"] == "envelope_not_recognised"


def test_an_unreadable_policy_waits_for_a_person(
    granted: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable() -> None:
        raise DomainError("policy_invalid", "the policy artifact will not load", None)

    monkeypatch.setattr(decomposition, "load_factory_policy", unreadable)
    proposal = _propose(granted, _revision(granted))
    assert _refused(granted, proposal)["reason"] == "policy_unreadable"


def test_a_mutation_of_something_else_waits_for_a_person(granted: Session) -> None:
    """Recognised by the uv pattern, whose shape says nothing about which package."""
    units = (_unit(_envelope("uv add 'other>=1'")),)
    payload = _refused(granted, _propose(granted, _revision(granted), units=units))
    assert payload["reason"] == "binding_violated"
    assert payload["detail"]


def test_a_repository_without_a_member_record_waits_for_a_person(
    migrated_session: Session,
) -> None:
    proposal = _propose(migrated_session, _revision(migrated_session))
    assert _refused(migrated_session, proposal) == {
        "reason": "repository_not_granted",
        "detail": [REPOSITORY],
    }


def test_a_grant_of_another_service_waits_for_a_person(migrated_session: Session) -> None:
    _grant(migrated_session, service="app_changes")
    proposal = _propose(migrated_session, _revision(migrated_session))
    assert _refused(migrated_session, proposal)["reason"] == "repository_not_granted"


def test_a_grant_not_in_force_waits_for_a_person(migrated_session: Session) -> None:
    _grant(migrated_session, grant="not_assessed")
    proposal = _propose(migrated_session, _revision(migrated_session))
    assert _refused(migrated_session, proposal)["reason"] == "repository_not_granted"


def test_an_approval_that_cannot_complete_says_so(
    granted: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Policy said yes and registration refused: nothing else would ever tell the lane, and the
    event must survive the savepoint that took the approval back."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise DomainError("unit_conflict", "a unit already holds that key", None)

    monkeypatch.setattr(decomposition, "_approve", refuse)
    payload = _refused(granted, _propose(granted, _revision(granted)))
    assert payload == {"reason": "approval_failed", "detail": ["unit_conflict"]}


def test_a_replayed_refusal_records_one_event(granted: Session) -> None:
    revision = _revision(granted)
    units = (_unit(_envelope("uv add 'other>=1'")),)
    ac_ids = package_ac_ids(granted, revision.id)
    command = DecompositionProposalCommand(
        work_package_revision_id=revision.id,
        rationale="r",
        proposed_units=units,
        dependencies=(),
        ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="bump"),),
        retained_acs=(),
        idempotency_key="replayed",
    )
    first = submit_decomposition_proposal(granted, command, SYSTEM)
    again = submit_decomposition_proposal(granted, command, SYSTEM)
    assert again.id == first.id
    assert _refused(granted, first)["reason"] == "binding_violated"


def test_the_rotation_branch_is_unchanged(migrated_session: Session) -> None:
    """`test_rotation_decomposition_policy.py` stays unedited; this adds that an approved rotation
    records nothing new."""
    rotation.test_system_proposing_a_recognised_rotation_decomposition_is_approved_by_policy(
        migrated_session
    )
    assert (
        migrated_session.scalars(select(Event).where(Event.action == NOT_APPLIED)).first() is None
    )


def test_a_refused_rotation_records_nothing(migrated_session: Session) -> None:
    """Only a bump's refusal has a lane waiting to read it."""
    rotation.test_a_revision_an_approval_policy_approved_waits_for_a_person(migrated_session)
    assert (
        migrated_session.scalars(select(Event).where(Event.action == NOT_APPLIED)).first() is None
    )
