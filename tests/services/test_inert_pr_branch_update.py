"""The lane clears the staleness its own landings create. ADR-0038 part 2.

The assertions that matter are about what did NOT happen: a branch touched when something other
than freshness stood in the way, a replay reported as a fresh act, a key from another act answered
as though this pull request had been brought up to date.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.estate_landing import EstateAnswer
from orchestrator.services.estate_landing_admission import EstateGatewayError
from orchestrator.services.inert_landing_policy import InertLandingAnswer
from orchestrator.services.inert_pr_branch_update import (
    INERT_BRANCH_UPDATE_ACTION,
    INERT_BRANCH_UPDATE_HEAD_MOVED,
    INERT_BRANCH_UPDATE_NOT_QUALIFIED,
    INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE,
    INERT_BRANCH_UPDATE_SIBLING_HOLDING,
    INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    InertBranchUpdateCommand,
    update_inert_pull_request_branch,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.estate_doubles import LANDING_REDEPLOYS, FakeEstateLandingSource
from tests.services.estate_landing_doubles import (
    HEAD,
    FakeEstateGateway,
    SiblingGateway,
    foreign_commit,
    pull_request,
    run,
)
from tests.services.inert_landing_doubles import (
    INERT_REPOSITORY,
    SYNC_BOT,
    SYNC_BRANCH,
    UPDATE_BOT,
    FakeInertPolicySource,
    rules,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

PR = 3
UV_BRANCH = "dependabot/uv/typer-0.21.0"
# Content-addressed over the head, exactly as the caller composes it -- which is what makes a spent
# key mean "this same request against this same head" and nothing wider. The head is TRUNCATED,
# matching the sibling lane; `test_the_caller_composes_the_key_THIS_TEST_FILE_IS_WRITTEN_AGAINST`
# below derives this from the caller rather than restating it, which it could not do until
# ADR-0038 part 2a gave this lane a caller at all.
KEY = f"inert-branch-update:{INERT_REPOSITORY}:{PR}:{HEAD[:12]}"


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
    clock=None,
):
    return update_inert_pull_request_branch(
        session,
        command or _command(),
        gateway or _behind_gateway(),
        landing_source or FakeEstateLandingSource(),
        policy_source or FakeInertPolicySource(),
        enabled=enabled,
        credentials_configured=credentials_configured,
        clock=clock,
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
    # The STATUS, not only the code. It was captured at the raise site and dropped here, so an
    # operator's whole answer was `branch_update_status` -- which does not say what was refused.
    # Measured 2026-09-01: telling "the App may not write this" from "the head moved" took six
    # probes without it.
    assert "422" in caught.value.message
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


# ------------------------------------------------------------------------------------------
# The caller's vocabulary, pinned from OUTSIDE both sides. ADR-0038 part 2a.
#
# `inert_lander` may not import the orchestrator -- that isolation is what makes a scheduled
# caller acceptable at all -- so the two sides carry the same strings and nothing in either
# would notice a rename. These tests may import both, exactly as the estate lane's equivalents
# do, because this estate's standing lesson is that wherever two vocabularies must agree they
# do not, until something checks.
# ------------------------------------------------------------------------------------------


def test_the_callers_SELF_CLEARING_set_is_exactly_the_codes_this_act_raises_for_a_moved_answer():
    """The caller reports these two as deliberate rather than as findings, because each says only
    that the answer moved between the read and the request -- which the next pass re-decides.

    EQUALITY, not containment. Containment would pass while the caller excused a code this act
    never raises, and it would pass while the act grew a third that the caller reported forever.
    """
    from inert_lander.cli import _UPDATE_SELF_CLEARING

    assert _UPDATE_SELF_CLEARING == {
        INERT_BRANCH_UPDATE_HEAD_MOVED,
        INERT_BRANCH_UPDATE_NOT_QUALIFIED,
        INERT_BRANCH_UPDATE_SIBLING_HOLDING,
    }


def test_the_remote_REFUSING_is_NOT_one_of_them() -> None:
    """A stated control, because it is the near miss. A remote that declined to bring the branch
    up to date is a condition somebody can act on, and folding it in with the two above would
    make a lane that has stopped working read as a lane that is working."""
    from inert_lander.cli import _UPDATE_SELF_CLEARING

    assert INERT_BRANCH_UPDATE_REFUSED_BY_REMOTE not in _UPDATE_SELF_CLEARING


def test_an_UNREADABLE_scan_is_NOT_self_clearing() -> None:
    """ADR-0045. A holding sibling clears when the branch ahead lands; a scan that could not read
    is the orchestrator not knowing, which clears on nothing. Folding the second in with the first
    would make a repository whose reads keep failing look like one waiting its turn."""
    from inert_lander.cli import _UPDATE_SELF_CLEARING

    assert INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE not in _UPDATE_SELF_CLEARING


def test_the_WIRE_KEY_the_caller_reads_the_withheld_fact_from_is_a_field_this_side_SERVES() -> None:
    """ADR-0045. Read by a name the server does not serve, the key reads as absent, the caller
    treats it as false, and every queued sibling is reported as a finding with nothing saying why.
    Pinned against the service dataclass, which is what the route serializes."""
    from inert_lander.cli import _WITHHELD_FOR_SIBLING
    from orchestrator.services.inert_landing_admission import InertLandingAdmission

    assert _WITHHELD_FOR_SIBLING in InertLandingAdmission.__dataclass_fields__


def test_composing_the_answer_alone_observes_no_sibling(migrated_session: Session) -> None:
    """The admission function never scans siblings; only the route fills the fact in."""
    from orchestrator.services.inert_landing_admission import inert_landing_admission

    answer = inert_landing_admission(
        migrated_session,
        INERT_REPOSITORY,
        PR,
        FakeEstateLandingSource(),
        FakeInertPolicySource(),
        _behind_gateway(),
        enabled=True,
        credentials_configured=True,
    )

    assert answer.branch_update_qualifies is True
    assert answer.branch_update_withheld_for_sibling is False


def test_the_callers_SETTLED_set_is_exactly_the_refusals_that_mean_there_is_nothing_left_to_land():
    """The two the caller reports as settled rather than as findings. Read from the admission
    module that raises them, so a rename there reddens this rather than making one landing a
    nightly page forever."""
    from inert_lander.cli import _SETTLED
    from orchestrator.services.estate_landing_admission import (
        LANDING_ALREADY_RECORDED,
        LANDING_PULL_REQUEST_NOT_OPEN,
    )

    assert _SETTLED == {LANDING_ALREADY_RECORDED, LANDING_PULL_REQUEST_NOT_OPEN}


def test_the_caller_composes_the_key_THIS_TEST_FILE_IS_WRITTEN_AGAINST() -> None:
    """`KEY` above claims to be the caller's own composition. Until ADR-0038 part 2a there was no
    caller, so the claim could not be checked and was written from the sibling lane's shape with
    the head at full length. This makes it true by derivation instead of by assertion."""
    from inert_lander.cli import _update_key

    assert _update_key(INERT_REPOSITORY, PR, HEAD) == KEY


# ------------------------------------------------------------------------------------------
# ADR-0045: the lane edits one Dependabot branch per repository at a time.
# ------------------------------------------------------------------------------------------

SIBLING = 4
SIBLING_HEAD = "b" * 40
SIBLING_BRANCH = "dependabot/uv/alembic-1.19.0"
MOMENT = datetime(2026, 9, 25, 6, 15, tzinfo=UTC)


class _FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, session: Session) -> datetime:
        return self._moment


def _beside_sibling(
    *,
    target_author: str = UPDATE_BOT,
    sibling_commits=None,
    target_commits=None,
    **kwargs,
) -> SiblingGateway:
    """The target behind its base beside one Dependabot sibling, also behind and otherwise
    landable -- so the sibling's own composed answer names only freshness, a holding answer."""
    return SiblingGateway(
        target=PR,
        pulls={
            PR: pull_request(
                number=PR,
                head_ref=SYNC_BRANCH if target_author == SYNC_BOT else UV_BRANCH,
                author_login=target_author,
            ),
            SIBLING: pull_request(number=SIBLING, head_sha=SIBLING_HEAD, head_ref=SIBLING_BRANCH),
        },
        behind={PR: 2, SIBLING: 2},
        commits={
            **({} if sibling_commits is None else {SIBLING: sibling_commits}),
            **({} if target_commits is None else {PR: target_commits}),
        },
        **kwargs,
    )


def _no_update_event_readable(engine: Engine) -> bool:
    with Session(engine) as reader:
        return (
            reader.scalar(select(Event).where(Event.action == INERT_BRANCH_UPDATE_ACTION)) is None
        )


def test_an_owned_branch_beside_an_edited_sibling_that_holds_is_never_touched(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = _beside_sibling(sibling_commits=(foreign_commit(SIBLING_HEAD),))

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_BRANCH_UPDATE_SIBLING_HOLDING
    assert gateway.branch_updates == []
    assert _no_update_event_readable(migrated_engine)


def test_the_same_branch_beside_an_OWNED_sibling_is_brought_up_to_date(
    migrated_session: Session,
) -> None:
    """The pair: identical but for the sibling's ownership."""
    gateway = _beside_sibling()

    _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(INERT_REPOSITORY, PR, HEAD)]


def test_an_already_edited_branch_is_freshened_again_beside_a_holding_sibling(
    migrated_session: Session,
) -> None:
    gateway = _beside_sibling(
        sibling_commits=(foreign_commit(SIBLING_HEAD),), target_commits=(foreign_commit(HEAD),)
    )

    _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(INERT_REPOSITORY, PR, HEAD)]


def test_an_unreadable_scan_refuses_with_its_own_code_and_touches_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = _beside_sibling(open_error=EstateGatewayError("open_pull_requests_status", 502))

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE
    assert gateway.branch_updates == []
    assert _no_update_event_readable(migrated_engine)


def test_a_sync_bot_branch_is_never_withheld(migrated_session: Session) -> None:
    """The sync bot rebuilds its branch daily with a force-push, so an edit disowns nothing and
    the rule's premise -- Dependabot refusing to maintain a branch -- does not hold for it."""
    gateway = _beside_sibling(
        target_author=SYNC_BOT, sibling_commits=(foreign_commit(SIBLING_HEAD),)
    )
    policy = FakeInertPolicySource(
        InertLandingAnswer(
            rules(
                permitted_authors=frozenset({UPDATE_BOT, SYNC_BOT}),
                non_ecosystem_authors=frozenset({SYNC_BOT}),
            )
        )
    )

    _update(migrated_session, gateway=gateway, policy_source=policy)

    assert gateway.branch_updates == [(INERT_REPOSITORY, PR, HEAD)]


def _update_event(session: Session, *, occurred_at: datetime) -> None:
    """An update this lane recorded against the SIBLING's current head -- the platform has
    accepted it and not yet delivered, so the sibling's commits are still all the bot's."""
    session.add(
        Event(
            occurred_at=occurred_at,
            actor_id="orchestrator-system",
            action=INERT_BRANCH_UPDATE_ACTION,
            subject_type="inert_pull_request",
            subject_id=uuid.uuid4(),
            payload={
                "repository": INERT_REPOSITORY,
                "pr_number": SIBLING,
                "head_sha": SIBLING_HEAD,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"earlier-{uuid.uuid4()}",
        )
    )
    session.flush()


def test_the_ten_minute_bound_reads_the_injected_clock(migrated_session: Session) -> None:
    """Eleven minutes: the undelivered update has lapsed, the sibling reads as the bot's, and the
    target is freshened."""
    _update_event(migrated_session, occurred_at=MOMENT - timedelta(minutes=11))
    gateway = _beside_sibling()

    _update(migrated_session, gateway=gateway, clock=_FixedClock(MOMENT))

    assert gateway.branch_updates == [(INERT_REPOSITORY, PR, HEAD)]


def test_inside_the_bound_the_undelivered_update_still_holds(migrated_session: Session) -> None:
    """The pair: nine minutes, same fixture. Only the injected clock separates the two, so an act
    that ignored it would read the event's age against the real clock and agree with one of them
    for the wrong reason."""
    _update_event(migrated_session, occurred_at=MOMENT - timedelta(minutes=9))
    gateway = _beside_sibling()

    with pytest.raises(DomainError) as caught:
        _update(migrated_session, gateway=gateway, clock=_FixedClock(MOMENT))

    assert caught.value.code == INERT_BRANCH_UPDATE_SIBLING_HOLDING
    assert gateway.branch_updates == []
