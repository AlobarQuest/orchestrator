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
from orchestrator.kernel.states import ActorRole
from orchestrator.services.claims import claim_unit
from orchestrator.services.lifecycle import ActorContext
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
