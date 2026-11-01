"""A decomposition for an ungranted repository is refused at the proposal, and again at approval.

Nobody is asked to approve work that can't run. Intake stays open, a rotation answers to its own
check, and a unit naming no repository has no grant to read.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import (
    DecompositionProposal,
    DecompositionProposalUnit,
    Event,
    SdsMember,
    SdsMemberGrant,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.intake.decomposition import (
    AcMapping,
    ProposedUnit,
    RetainedAc,
    approve_decomposition_proposal,
    submit_decomposition_proposal,
)
from tests._support.seeding import grant_repository
from tests.services.test_decomposition import (
    package_ac_ids,
    proposal_command,
    register_intaken_revision,
    worker_actor,
)
from tests.services.test_package_intake import AUTHORITY, human_actor
from tests.services.test_rotation_decomposition_policy import (
    SYSTEM,
    _command,
    _rotation_revision,
)

REPOSITORY = "AlobarQuest/some-target"
OTHER = "AlobarQuest/other-target"
NOT_GRANTED = "target_repository_not_granted"


def _payload(repository: str | None, change_class: str | None = None) -> dict[str, Any]:
    constraints = {} if repository is None else {"target_repository": repository}
    return {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": change_class,
        "conformance": None,
        "constraints": constraints,
    }


def _unit(key: str, repository: str | None) -> ProposedUnit:
    return ProposedUnit(
        unit_key=key,
        title=key,
        outcome=f"{key} is done",
        required_capability="repo.edit",
        authority=AUTHORITY,
        authority_payload=_payload(repository),
    )


def _command_for(
    revision: WorkPackageRevision, session: Session, *units: ProposedUnit, key: str = "p-1"
) -> Any:
    ac_ids = package_ac_ids(session, revision.id)
    return proposal_command(
        revision.id,
        ac_ids,
        proposed_units=units,
        dependencies=(),
        ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key=units[0].unit_key),),
        retained_acs=(RetainedAc(ac_id=str(ac_ids["AC-002"]), rationale="package-level gate"),),
        idempotency_key=key,
    )


def _proposals(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(DecompositionProposal)) or 0


def _set_grant(session: Session, repository: str, state: str) -> None:
    for grant in session.scalars(
        select(SdsMemberGrant).join(SdsMember).where(SdsMember.repository == repository)
    ):
        grant.state = state
    session.flush()


def _approve(session: Session, proposal: DecompositionProposal, key: str = "decide-1") -> None:
    approve_decomposition_proposal(
        session, proposal.id, actor=human_actor(), reason="Looks right.", idempotency_key=key
    )


def _naming_the_repository(unit: ProposedUnit) -> ProposedUnit:
    payload = deepcopy(dict(unit.authority_payload or {}))
    payload["constraints"]["target_repository"] = REPOSITORY
    return replace(unit, authority=normalize_authority(payload), authority_payload=payload)


def test_a_proposal_for_an_ungranted_repository_is_refused_and_writes_nothing(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    events = migrated_session.scalar(select(func.count()).select_from(Event))

    with pytest.raises(DomainError) as refused:
        submit_decomposition_proposal(
            migrated_session,
            _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY)),
            human_actor(),
        )

    assert (refused.value.code, refused.value.recovery) == (NOT_GRANTED, "restore_grant")
    assert _proposals(migrated_session) == 0
    assert migrated_session.scalar(select(func.count()).select_from(Event)) == events


def test_one_ungranted_unit_refuses_the_whole_proposal(migrated_session: Session) -> None:
    grant_repository(migrated_session, REPOSITORY)
    revision = register_intaken_revision(migrated_session)

    with pytest.raises(DomainError) as refused:
        submit_decomposition_proposal(
            migrated_session,
            _command_for(
                revision,
                migrated_session,
                _unit("unit-1", REPOSITORY),
                _unit("unit-2", OTHER),
            ),
            human_actor(),
        )

    assert refused.value.code == NOT_GRANTED
    assert _proposals(migrated_session) == 0
    assert migrated_session.scalar(select(func.count()).select_from(DecompositionProposalUnit)) == 0


def test_the_grant_asked_is_the_one_the_unit_s_change_class_needs(
    migrated_session: Session,
) -> None:
    grant_repository(migrated_session, REPOSITORY)
    for grant in migrated_session.scalars(
        select(SdsMemberGrant).where(SdsMemberGrant.service == "app_changes")
    ):
        grant.state = "withdrawn"
    migrated_session.flush()
    revision = register_intaken_revision(migrated_session)

    with pytest.raises(DomainError) as refused:
        submit_decomposition_proposal(
            migrated_session,
            _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY)),
            human_actor(),
        )

    assert refused.value.code == NOT_GRANTED


def test_a_granted_proposal_is_taken(migrated_session: Session) -> None:
    grant_repository(migrated_session, REPOSITORY)
    revision = register_intaken_revision(migrated_session)

    proposal = submit_decomposition_proposal(
        migrated_session,
        _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY)),
        human_actor(),
    )

    assert proposal.state == "proposed"


def test_a_unit_naming_no_repository_has_no_grant_to_read(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)

    proposal = submit_decomposition_proposal(
        migrated_session,
        _command_for(revision, migrated_session, _unit("unit-1", None)),
        worker_actor(),
    )

    assert proposal.state == "proposed"


def test_a_replayed_proposal_answers_as_before_after_a_withdrawal(
    migrated_session: Session,
) -> None:
    grant_repository(migrated_session, REPOSITORY)
    revision = register_intaken_revision(migrated_session)
    command = _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY))
    first = submit_decomposition_proposal(migrated_session, command, human_actor())
    _set_grant(migrated_session, REPOSITORY, "withdrawn")

    assert submit_decomposition_proposal(migrated_session, command, human_actor()).id == first.id


def test_a_proposal_pending_at_a_withdrawal_refuses_at_approval(
    migrated_session: Session,
) -> None:
    grant_repository(migrated_session, REPOSITORY)
    revision = register_intaken_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY)),
        human_actor(),
    )
    _set_grant(migrated_session, REPOSITORY, "withdrawn")

    with pytest.raises(DomainError) as refused:
        _approve(migrated_session, proposal)

    assert (refused.value.code, refused.value.recovery) == (NOT_GRANTED, "restore_grant")
    assert proposal.state == "proposed"
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 0


def test_a_restored_grant_lets_the_same_proposal_proceed(migrated_session: Session) -> None:
    grant_repository(migrated_session, REPOSITORY)
    revision = register_intaken_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY)),
        human_actor(),
    )
    _set_grant(migrated_session, REPOSITORY, "withdrawn")
    with pytest.raises(DomainError):
        _approve(migrated_session, proposal)
    _set_grant(migrated_session, REPOSITORY, "granted")

    _approve(migrated_session, proposal)

    assert proposal.state == "approved"
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 1


def test_a_refused_proposal_may_be_posted_again_once_the_grant_is_back(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    command = _command_for(revision, migrated_session, _unit("unit-1", REPOSITORY))
    with pytest.raises(DomainError):
        submit_decomposition_proposal(migrated_session, command, human_actor())
    grant_repository(migrated_session, REPOSITORY)

    assert submit_decomposition_proposal(migrated_session, command, human_actor()).state == (
        "proposed"
    )


def test_a_rotation_naming_a_repository_answers_to_its_own_check(
    migrated_session: Session,
) -> None:
    """The member record grants nothing here, and policy still approves the rotation."""
    revision = _rotation_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    command = _command(revision.id, ac_ids)
    named = tuple(_naming_the_repository(unit) for unit in command.proposed_units)
    command = replace(command, proposed_units=named)

    proposal = submit_decomposition_proposal(migrated_session, command, SYSTEM)

    assert proposal.state in {"proposed", "approved"}
