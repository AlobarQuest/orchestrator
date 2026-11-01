"""The two `max_attempts` values: the column is the enforced cap, the envelope a policy ceiling.

`work_units.max_attempts` comes from the proposal. `authority.budgets.max_attempts` is compared
to it only on the two policy-approval branches of `decomposition.py`, so a unit policy approved
starts within its envelope. Above the budget policy does not approve, and the proposal waits for
a person. A person's retry may later raise the column past the envelope, which is why the ceiling
holds at approval and not for every stored unit.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import DecompositionProposal, WorkUnit
from orchestrator.services.intake.decomposition import submit_decomposition_proposal
from orchestrator.services.lifecycle.claims import authorize_retry
from tests.services import test_bump_decomposition_policy as bump
from tests.services import test_rotation_decomposition_policy as rotation
from tests.services.test_decomposition import package_ac_ids

BUDGET = 3
ABOVE_BUDGET = BUDGET + 1
Propose = Callable[[Session, int], DecompositionProposal]


def _rotation_proposal(session: Session, attempts: int) -> DecompositionProposal:
    revision = rotation._rotation_revision(session)
    command = rotation._command(
        revision.id, package_ac_ids(session, revision.id), retire_attempts=attempts
    )
    return submit_decomposition_proposal(session, command, rotation.SYSTEM)


def _bump_proposal(session: Session, attempts: int) -> DecompositionProposal:
    bump._grant(session)
    unit = bump._unit(bump._envelope(), max_attempts=attempts)
    return bump._propose(session, bump._revision(session), units=(unit,))


BRANCHES = pytest.mark.parametrize("propose", [_rotation_proposal, _bump_proposal])


@BRANCHES
def test_a_policy_approved_unit_stays_within_its_envelope_budget(
    migrated_session: Session, propose: Propose
) -> None:
    proposal = propose(migrated_session, BUDGET)

    assert proposal.state == "approved"
    units = migrated_session.scalars(select(WorkUnit)).all()
    assert units
    for unit in units:
        budget = normalize_authority(unit.authority).budgets.max_attempts
        assert budget is not None
        assert unit.max_attempts <= budget


@BRANCHES
def test_a_proposal_above_the_budget_is_refused(
    migrated_session: Session, propose: Propose
) -> None:
    """Policy's refusal: it creates no unit, and the proposal waits for a person."""
    proposal = propose(migrated_session, ABOVE_BUDGET)

    assert proposal.state == "proposed"
    assert migrated_session.scalars(select(WorkUnit)).first() is None


def test_a_persons_retry_may_raise_the_column_past_the_envelope(
    migrated_session: Session,
) -> None:
    """So the ceiling is a rule about approval: nothing holds stored units to it afterwards."""
    _bump_proposal(migrated_session, BUDGET)
    unit = migrated_session.scalars(select(WorkUnit)).one()
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = unit.max_attempts
    migrated_session.commit()

    authorize_retry(
        migrated_session,
        unit.id,
        ActorContext("human-1", ActorRole.HUMAN),
        new_max_attempts=ABOVE_BUDGET,
        reason="one more attempt",
        idempotency_key="retry-past-the-envelope",
    )

    migrated_session.refresh(unit)
    assert unit.max_attempts == ABOVE_BUDGET
    assert normalize_authority(unit.authority).budgets.max_attempts == BUDGET
