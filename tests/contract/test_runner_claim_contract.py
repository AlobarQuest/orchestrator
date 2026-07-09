"""The claim seam: what `runner_brief` serves must be claimable by the runner.

`runner_brief` emits `standing_context: {}` for a revision that requires no standing
context, and factory-runner passes that value straight back into `claim`. The kernel is
right to say a *supplied* standing context must be complete — an empty mapping is missing
every `REQUIRED_CONTEXT_FIELDS` entry. So the orchestrator was producing a value it then
rejected with `context_missing_required` (HTTP 409), and no test crossed the two.
"""

from typing import Any, cast

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.services.claims import claim_unit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import record_approval
from orchestrator.services.runner_brief import runner_brief
from tests.services.test_dispatch import ready_unit

WORKER = ActorContext("factory-runner", ActorRole.WORKER)


def test_the_standing_context_the_brief_serves_is_accepted_by_claim(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="claim-contract")
    served = cast(dict[str, Any], runner_brief(migrated_session, unit.id)["standing_context"])

    result = claim_unit(
        migrated_session,
        unit.id,
        WORKER,
        idempotency_key="claim-contract-1",
        expected_version=unit.version,
        standing_context=served,
    )

    assert not isinstance(result, DomainError), getattr(result, "code", None)


def test_an_empty_standing_context_still_fails_closed_when_the_revision_requires_one(
    migrated_session: Session,
) -> None:
    """Emptiness must not become a way to skip a genuinely required standing context."""
    unit = ready_unit(
        migrated_session,
        key="claim-contract-required",
        enforcement_snapshot={"required_context": {"capabilities": ["repo.edit"]}},
    )

    result = claim_unit(
        migrated_session,
        unit.id,
        WORKER,
        idempotency_key="claim-contract-required-1",
        expected_version=unit.version,
        standing_context={},
    )

    assert isinstance(result, DomainError)
    assert result.code == "context_missing_required"


def test_the_whole_prepare_sequence_the_runner_performs_succeeds(
    migrated_session: Session,
) -> None:
    """claim -> start, both fed the standing context the brief serves.

    The claim path and the execution path each had their own copy of the same check, and
    fixing only the first left the runner failing one line later, at `start`.
    """
    unit = ready_unit(migrated_session, key="prepare-sequence")
    served = cast(dict[str, Any], runner_brief(migrated_session, unit.id)["standing_context"])
    # The runner reads the version from the brief, before claiming, and starts at version+1.
    brief_version = unit.version

    grant = claim_unit(
        migrated_session,
        unit.id,
        WORKER,
        idempotency_key="prepare-sequence-claim",
        expected_version=brief_version,
        standing_context=served,
    )
    assert not isinstance(grant, DomainError), getattr(grant, "code", None)

    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=brief_version + 1,
            idempotency_key="prepare-sequence-start",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            standing_context=served,
        ),
    )

    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.EXECUTING.value


def test_replaying_a_claim_made_with_an_empty_context_is_idempotent(
    migrated_session: Session,
) -> None:
    """A claim with `{}` stores no snapshot; its replay must not raise idempotency_conflict."""
    unit = ready_unit(migrated_session, key="claim-replay")

    first = claim_unit(
        migrated_session, unit.id, WORKER, idempotency_key="replay-1", standing_context={}
    )
    assert not isinstance(first, DomainError), getattr(first, "code", None)

    replay = claim_unit(
        migrated_session, unit.id, WORKER, idempotency_key="replay-1", standing_context={}
    )

    assert not isinstance(replay, DomainError), getattr(replay, "code", None)
    assert replay.claim_id == first.claim_id


def test_an_authority_approval_with_an_empty_standing_context_still_binds(
    migrated_session: Session,
) -> None:
    """`{}` must not silently turn an authority approval into a context-bound one."""
    unit = ready_unit(migrated_session, key="approval-empty-context")
    unit.authority_approval_id = None
    migrated_session.flush()

    record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="devon",
        actor_role=ActorRole.HUMAN,
        reason="approved",
        idempotency_key="approval-empty-context-1",
        expected_version=unit.version,
        standing_context={},
    )

    migrated_session.refresh(unit)
    assert unit.authority_approval_id is not None
