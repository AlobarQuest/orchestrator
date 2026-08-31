"""The lane clears the staleness its own landings create. ADR-0038 part 2.

The assertions that matter are about what did NOT happen: a branch touched when something other
than freshness stood in the way, a replay reported as a fresh act, a key from another act answered
as though this pull request had been brought up to date.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.estate_landing import EstateAnswer
from orchestrator.services.estate_landing_admission import EstateGatewayError
from orchestrator.services.inert_pr_branch_update import (
    INERT_BRANCH_UPDATE_ACTION,
    INERT_BRANCH_UPDATE_HEAD_MOVED,
    INERT_BRANCH_UPDATE_NOT_QUALIFIED,
    INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE,
    InertBranchUpdateCommand,
    update_inert_pull_request_branch,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.estate_doubles import LANDING_REDEPLOYS, FakeEstateLandingSource
from tests.services.estate_landing_doubles import (
    HEAD,
    FakeEstateGateway,
    pull_request,
    run,
)
from tests.services.inert_landing_doubles import (
    INERT_REPOSITORY,
    FakeInertPolicySource,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

PR = 3
UV_BRANCH = "dependabot/uv/typer-0.21.0"
# Content-addressed over the head, exactly as the caller composes it -- which is what makes a spent
# key mean "this same request against this same head" and nothing wider.
KEY = f"inert-branch-update:{INERT_REPOSITORY}:{PR}:{HEAD}"


def _command(*, key: str = KEY, head: str = HEAD, actor: ActorContext = SYSTEM):
    return InertBranchUpdateCommand(
        repository=INERT_REPOSITORY,
        pr_number=PR,
        actor=actor,
        idempotency_key=key,
        expected_head_sha=head,
    )


def _behind_gateway(**kwargs) -> FakeEstateGateway:
    kwargs.setdefault("pull", pull_request(number=PR, head_ref=UV_BRANCH))
    kwargs.setdefault("behind", 2)
    return FakeEstateGateway(**kwargs)


def _update(
    session: Session,
    *,
    gateway: FakeEstateGateway | None = None,
    landing_source: FakeEstateLandingSource | None = None,
    policy_source: FakeInertPolicySource | None = None,
    command: InertBranchUpdateCommand | None = None,
    enabled: bool = True,
    credentials_configured: bool = True,
):
    return update_inert_pull_request_branch(
        session,
        command or _command(),
        gateway or _behind_gateway(),
        landing_source or FakeEstateLandingSource(),
        policy_source or FakeInertPolicySource(),
        enabled=enabled,
        credentials_configured=credentials_configured,
    )


def test_a_branch_whose_only_obstacle_is_freshness_is_brought_up_to_date(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = _behind_gateway()

    outcome = _update(migrated_session, gateway=gateway)

    assert outcome.replayed is False
    assert outcome.head_sha == HEAD
    assert gateway.branch_updates == [(INERT_REPOSITORY, PR, HEAD)]

    with Session(migrated_engine) as reader:
        events = list(
            reader.scalars(select(Event).where(Event.action == INERT_BRANCH_UPDATE_ACTION))
        )
    assert len(events) == 1
    assert events[0].payload == {
        "repository": INERT_REPOSITORY,
        "pr_number": PR,
        "head_sha": HEAD,
    }


def test_a_branch_that_is_not_behind_is_never_touched(migrated_session: Session) -> None:
    """Freshness must be the obstacle; a current branch has nothing to clear."""
    gateway = _behind_gateway(behind=0)

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


@pytest.mark.parametrize(
    "beside",
    [
        {"mergeable_state": "dirty"},
        {"is_open": False},
        {"author_is_bot": False},
        {"base_ref": "release/2.0"},
    ],
)
def test_a_branch_carrying_a_second_obstacle_is_never_touched(
    migrated_session: Session, beside: dict[str, Any]
) -> None:
    """Updating a pull request that could not land anyway spends a real build on a branch whose
    answer does not change -- and a build running is indistinguishable from progress to whoever
    reads the report."""
    gateway = _behind_gateway(pull=pull_request(number=PR, head_ref=UV_BRANCH, **beside))

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_behind_branch_whose_checks_reached_no_verdict_is_still_brought_up_to_date(
    migrated_session: Session,
) -> None:
    """The one refusal excused because updating is what ANSWERS it: nothing else in the estate
    re-runs a check that was abandoned, so such a pull request waits forever otherwise."""
    gateway = _behind_gateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, mergeable_state="blocked"),
        runs=(run(conclusion="cancelled"),),
    )

    outcome = _update(migrated_session, gateway=gateway)

    assert outcome.replayed is False
    assert gateway.branch_updates == [(INERT_REPOSITORY, PR, HEAD)]


def test_a_behind_branch_whose_checks_are_failing_is_never_touched(
    migrated_session: Session,
) -> None:
    """A red verdict is not made green by a fresher base, and this boundary is the whole value of
    telling the three blocked causes apart."""
    gateway = _behind_gateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, mergeable_state="blocked"),
        runs=(run(conclusion="failure"),),
    )

    with pytest.raises(DomainError):
        _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == []


def test_a_deployment_that_may_not_land_may_not_touch_a_branch_either(
    migrated_session: Session,
) -> None:
    """By the term that already says so rather than by a second one somebody has to remember."""
    gateway = _behind_gateway()

    with pytest.raises(DomainError):
        _update(migrated_session, gateway=gateway, enabled=False)

    assert gateway.branch_updates == []


def test_a_repository_outside_the_declared_population_is_never_touched(
    migrated_session: Session,
) -> None:
    gateway = _behind_gateway()

    with pytest.raises(DomainError):
        _update(
            migrated_session,
            gateway=gateway,
            landing_source=FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS)),
        )

    assert gateway.branch_updates == []


@pytest.mark.parametrize("actor", [WORKER, HUMAN])
def test_only_the_system_actor_may_bring_a_branch_up_to_date(
    migrated_session: Session, actor: ActorContext
) -> None:
    gateway = _behind_gateway()

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway, command=_command(actor=actor))

    assert caught.value.code == "role_forbidden"
    assert gateway.branch_updates == []


def test_a_head_that_moved_since_the_caller_read_is_refused(migrated_session: Session) -> None:
    gateway = _behind_gateway()

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway, command=_command(head="e" * 40))

    assert caught.value.code == INERT_BRANCH_UPDATE_HEAD_MOVED
    assert gateway.branch_updates == []


def test_a_remote_refusal_records_nothing_and_bars_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The right behaviour for an act whose whole nature is that repeating it is harmless."""
    gateway = _behind_gateway(update_error=EstateGatewayError("branch_update_status", 422))

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE
    with Session(migrated_engine) as reader:
        assert (
            reader.scalars(select(Event).where(Event.action == INERT_BRANCH_UPDATE_ACTION)).all()
            == []
        )


def test_a_repeat_replays_the_event_and_never_calls_the_remote_again(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The idempotency claim in the coverage matrix. **`replayed` is the whole point:** the key is
    content-addressed over the head and a success moves the head, so a second request under the
    same key is a request about a branch that did NOT move -- the platform accepted the work and
    did not deliver it. Unreported, that failure describes itself as success forever."""
    first = _update(migrated_session, gateway=_behind_gateway())
    assert first.replayed is False

    second_gateway = _behind_gateway()
    second = _update(migrated_session, gateway=second_gateway)

    assert second.replayed is True
    assert second.head_sha == HEAD
    assert second_gateway.branch_updates == []
    with Session(migrated_engine) as reader:
        assert (
            len(
                reader.scalars(
                    select(Event).where(Event.action == INERT_BRANCH_UPDATE_ACTION)
                ).all()
            )
            == 1
        )


def test_a_key_spent_on_a_different_pull_request_is_refused_rather_than_replayed(
    migrated_session: Session,
) -> None:
    _update(migrated_session, gateway=_behind_gateway())

    other = InertBranchUpdateCommand(
        repository=INERT_REPOSITORY,
        pr_number=PR + 1,
        actor=SYSTEM,
        idempotency_key=KEY,
        expected_head_sha=HEAD,
    )
    gateway = _behind_gateway(pull=pull_request(number=PR + 1, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway, command=other)

    assert caught.value.code == "idempotency_conflict"
    assert gateway.branch_updates == []


def test_a_key_spent_by_a_different_ACT_is_refused_rather_than_replayed(
    migrated_session: Session,
) -> None:
    """**The event key space is GLOBAL and both lanes write into it**, so without the action clause
    a key spent by any other act in the system would be answered here as though this pull request
    had been brought up to date -- a success reported over something that never happened."""
    migrated_session.add(
        Event(
            actor_id="orchestrator-system",
            action="estate_pr_branch_update.updated",
            subject_type="estate_pull_request",
            subject_id=uuid.uuid4(),
            payload={"repository": INERT_REPOSITORY, "pr_number": PR, "head_sha": HEAD},
            correlation_id=uuid.uuid4(),
            idempotency_key=KEY,
        )
    )
    migrated_session.flush()
    gateway = _behind_gateway()

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway)

    assert caught.value.code == "idempotency_conflict"
    assert gateway.branch_updates == []
