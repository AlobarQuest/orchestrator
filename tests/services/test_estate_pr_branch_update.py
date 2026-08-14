"""The act: bringing a stale branch up to date with the base the lane itself moved.
ADR-0019 Increment 6.

Everything runs with no network. **The assertions that matter most are the ones about what did
NOT happen**, and specifically that `gateway.branch_updates` stayed empty: a test that only checks
the raised error would pass against an implementation that touched the branch first and complained
afterwards, which is this repository's standing lesson about the report surface and the acting
surface being different tests.

The rule under test is a CATEGORY rule, so it is exercised as one. A single positive case cannot
tell "freshness is the sole remaining obstacle" apart from "anything containing freshness", and a
single negative case cannot tell it apart from "at most one refusal" -- so both directions are
pinned with sets built to make the count uninformative.

Persistence is asserted through a SECOND SESSION, never by re-reading the one that wrote: a
flushed-but-uncommitted row is visible to its own transaction, so an in-session re-read passes
under the exact defect it would be written to catch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services import estate_pr_merge
from orchestrator.services.estate_landing_admission import (
    DELIBERATE_REFUSALS,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_OUTSIDE_CHANGE_WINDOW,
    LANDING_PACE_EXHAUSTED,
    LANDING_UPDATE_TYPE_UNPARSEABLE,
    EstateGatewayError,
    qualifies_for_branch_update,
)
from orchestrator.services.estate_pr_branch_update import (
    BRANCH_UPDATE_ACTION,
    BRANCH_UPDATE_HEAD_MOVED,
    BRANCH_UPDATE_NOT_QUALIFIED,
    BRANCH_UPDATE_REFUSED_BY_REMOTE,
    BRANCH_UPDATE_SUBJECT,
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
from orchestrator.services.estate_pr_merge import GitHubEstatePullRequests
from orchestrator.services.lifecycle import ActorContext
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import inert_source, redeploying_source
from tests.services.estate_landing_doubles import (
    HEAD,
    REPOSITORY,
    FakeEstateGateway,
    approved,
    pull_request,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

PR = 49
IN_WINDOW = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
OUT_OF_WINDOW = datetime(2026, 8, 11, 19, 30, tzinfo=UTC)

# A requirement-range bump: the real title of the pull request this lane must never touch. It
# states no single version delta, so no rule about update types can apply to it, and no amount of
# bringing its branch up to date will ever make it landable.
RANGE_TITLE = "build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1"

# A minor bump whose checks are red. Behind its base AND unlandable, like the one above, but for a
# reason that lives somewhere else entirely -- so the two together show the rule keying on the
# category rather than on any one code.
MINOR_TITLE = "build(deps): bump alembic from 1.18.5 to 1.19.0"


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, session: Session) -> datetime:
        return self._moment


def _update(
    session: Session,
    *,
    gateway: FakeEstateGateway,
    actor: ActorContext = SYSTEM,
    record=None,
    landing=None,
    enabled: bool = True,
    credentials: bool = True,
    moment: datetime = IN_WINDOW,
    expected_head: str = HEAD,
    key: str = "branch-update-1",
):
    return update_estate_pull_request_branch(
        session,
        EstateBranchUpdateCommand(
            repository=REPOSITORY,
            pr_number=PR,
            actor=actor,
            idempotency_key=key,
            expected_head_sha=expected_head,
        ),
        gateway,
        landing or redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, PR): record or approved()}),
        enabled=enabled,
        credentials_configured=credentials,
        clock=FixedClock(moment),
    )


def _behind(**kwargs) -> FakeEstateGateway:
    """A pull request that is behind its base and otherwise perfectly landable."""
    return FakeEstateGateway(behind=3, **kwargs)


# --------------------------------------------------------------------------------------------
# The predicate: a category rule, pinned in both directions.
# --------------------------------------------------------------------------------------------


def test_freshness_alone_qualifies() -> None:
    assert qualifies_for_branch_update((LANDING_HEAD_NOT_CURRENT_WITH_BASE,))


@pytest.mark.parametrize("deliberate", sorted(DELIBERATE_REFUSALS))
def test_freshness_beside_a_refusal_that_clears_itself_qualifies(deliberate: str) -> None:
    """The pace resets and the clock moves. Neither says anything about the branch, so neither is
    a reason to leave it behind -- and both co-occur constantly, the pace on every sibling once a
    landing has happened, which is precisely the population this lane exists to unstick."""
    assert qualifies_for_branch_update((LANDING_HEAD_NOT_CURRENT_WITH_BASE, deliberate))


def test_every_deliberate_refusal_together_with_freshness_still_qualifies() -> None:
    assert qualifies_for_branch_update((LANDING_HEAD_NOT_CURRENT_WITH_BASE, *DELIBERATE_REFUSALS))


@pytest.mark.parametrize(
    "real",
    [LANDING_CHECKS_NOT_CLEAN, LANDING_UPDATE_TYPE_UNPARSEABLE, "landing_record_absent"],
)
def test_freshness_beside_a_real_condition_does_NOT_qualify(real: str) -> None:
    """THE MUTANT THIS KILLS is "any refusal set containing freshness qualifies". Each of these
    pull requests is behind its base and cannot land whatever is done to its branch, so a build
    spent on it buys nothing and reads as progress to whoever sees it running."""
    assert not qualifies_for_branch_update((LANDING_HEAD_NOT_CURRENT_WITH_BASE, real))


def test_the_remainder_is_a_CATEGORY_and_not_a_COUNT() -> None:
    """TWO deliberate refusals beside freshness qualify; ONE real one does not.

    Built so the counts point the wrong way: the qualifying set has three members and the refusing
    set has two. A rule keyed on "at most one other refusal", or on any count at all, gets both of
    these backwards.
    """
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_PACE_EXHAUSTED, LANDING_OUTSIDE_CHANGE_WINDOW)
    )
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_NOT_CLEAN)
    )


def test_a_refusal_NOBODY_HAS_CLASSIFIED_does_not_qualify() -> None:
    """The polarity the whole lane argues for: a code a later increment invents and forgets to
    classify must fail toward leaving the branch alone, never toward touching it."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, "landing_something_nobody_has_thought_of")
    )


@pytest.mark.parametrize(
    "refusals",
    [
        (),
        (LANDING_PACE_EXHAUSTED,),
        (LANDING_OUTSIDE_CHANGE_WINDOW, LANDING_PACE_EXHAUSTED),
        (LANDING_CHECKS_NOT_CLEAN,),
    ],
)
def test_without_freshness_there_is_NOTHING_TO_DO_and_it_does_not_qualify(
    refusals: tuple[str, ...],
) -> None:
    """THE SECOND MUTANT: dropping the requirement that freshness be present at all.

    An empty refusal set is the sharpest case -- that is a pull request about to LAND, and a rule
    that acted on it would push a commit onto a branch seconds before squashing it.
    """
    assert not qualifies_for_branch_update(refusals)


def test_the_deliberate_refusals_are_exactly_the_landers_own() -> None:
    """TWO PACKAGES, ONE VOCABULARY. The lander cannot import the orchestrator -- it is isolated
    from it on purpose -- so the two copies can only be held together from outside, and this
    estate's standing lesson is that wherever two vocabularies must agree they do not, until
    something checks. A test may import both; the program may not.
    """
    from estate_lander.cli import _DELIBERATE

    assert DELIBERATE_REFUSALS == _DELIBERATE


# --------------------------------------------------------------------------------------------
# The act.
# --------------------------------------------------------------------------------------------


def test_a_branch_held_only_by_freshness_is_brought_up_to_date(migrated_session: Session) -> None:
    gateway = _behind()

    outcome = _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]
    assert outcome.repository == REPOSITORY
    assert outcome.pr_number == PR
    assert outcome.head_sha == HEAD


def test_a_branch_also_held_by_the_pace_is_brought_up_to_date(migrated_session: Session) -> None:
    """The ordinary case, and the one the lane creates for itself: something landed tonight, so
    every sibling is behind AND has no landing left in this window. Both facts clear by morning
    and neither is a reason to leave the branch stale."""
    migrated_session.add(_landed_tonight())
    migrated_session.flush()
    gateway = _behind()

    _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


def test_a_branch_is_brought_up_to_date_OUTSIDE_the_change_window_too(
    migrated_session: Session,
) -> None:
    """Pinned as a PAIR with the in-window case above, because a term that never reads its clock
    agrees with a single out-of-window assertion for most of the day.

    That this fires at any hour is deliberate and worth stating: the window governs changing
    something already serving, and this changes a topic branch that serves nothing. Doing it in
    daylight means the checks are green well before the window opens.
    """
    gateway = _behind()

    _update(migrated_session, gateway=gateway, moment=OUT_OF_WINDOW)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


@pytest.mark.parametrize(
    ("title", "state"),
    [
        pytest.param(RANGE_TITLE, "clean", id="can-never-be-classified"),
        pytest.param(MINOR_TITLE, "dirty", id="checks-are-red"),
    ],
)
def test_a_branch_that_could_not_land_ANYWAY_is_never_touched(
    migrated_session: Session, title: str, state: str
) -> None:
    """THE STANDING LIVE CONTROL, in the shape it has in production. `#48` is a requirement-range
    bump that is behind its base and can never be classified; a red-checked sibling is behind for
    the same reason and unlandable for a different one. Neither becomes landable because its
    branch moved, so the build each would trigger is spent for nothing.

    The assertion is on the EMPTY call list, not on the error: an implementation that acted and
    then raised would satisfy `pytest.raises` and fail this.
    """
    gateway = _behind(pull=pull_request(title=title, mergeable_state=state))

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_branch_that_is_ALREADY_CURRENT_is_never_touched(migrated_session: Session) -> None:
    """Nothing to do. A pull request whose head is current is either about to land or is held on
    something a fresher base cannot fix."""
    gateway = FakeEstateGateway(behind=0)

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_deployment_that_may_not_LAND_may_not_touch_a_branch_either(
    migrated_session: Session,
) -> None:
    """The off-switch, held by the same term rather than by a second one somebody has to
    remember. `landing_not_enabled` is a refusal like any other and is not one that clears
    itself, so a deployment nobody has enabled composes an answer that declines this too."""
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, enabled=False)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_without_credentials_nothing_is_asked_of_the_remote(migrated_session: Session) -> None:
    gateway = _behind()

    with pytest.raises(DomainError):
        _update(migrated_session, gateway=gateway, credentials=False)

    assert gateway.branch_updates == []


def test_a_repository_where_landing_is_INERT_is_not_this_lanes_business(
    migrated_session: Session,
) -> None:
    gateway = _behind()

    with pytest.raises(DomainError):
        _update(migrated_session, gateway=gateway, landing=inert_source())

    assert gateway.branch_updates == []


def test_a_head_that_moved_since_the_caller_read_it_is_refused(migrated_session: Session) -> None:
    """The same claim the landing makes, over the same window: the update bot rewriting its own
    branch between the answer and the request is the ordinary cause, and the next pass reads the
    new head and asks about that one."""
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, expected_head="c" * 40)

    assert raised.value.code == BRANCH_UPDATE_HEAD_MOVED
    assert gateway.branch_updates == []


def test_the_head_the_answer_named_is_the_head_the_remote_is_told_to_expect(
    migrated_session: Session,
) -> None:
    """The whole of the concurrency control. The platform refuses if the branch moved under us,
    which closes the window between deciding and doing without this side having to see the move."""
    gateway = _behind()

    _update(migrated_session, gateway=gateway)

    assert [sha for _, _, sha in gateway.branch_updates] == [HEAD]


@pytest.mark.parametrize("actor", [WORKER, HUMAN], ids=["worker", "human"])
def test_only_the_system_actor_may_bring_a_branch_up_to_date(
    migrated_session: Session, actor: ActorContext
) -> None:
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, actor=actor)

    assert raised.value.code == "role_forbidden"
    assert gateway.branch_updates == []


def test_a_remote_refusal_is_reported_and_bars_NOTHING(migrated_session: Session) -> None:
    """No record, so the next pass composes the answer again and may ask again. That is right for
    an act whose whole nature is that repeating it is harmless -- the opposite of the landing,
    whose row is permanent because its act cannot be retried."""
    gateway = _behind(update_error=EstateGatewayError("branch_update_status", 422))

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_REFUSED_BY_REMOTE
    assert (
        migrated_session.scalar(select(Event).where(Event.action == BRANCH_UPDATE_ACTION)) is None
    )


# --------------------------------------------------------------------------------------------
# What is written down.
# --------------------------------------------------------------------------------------------


def test_the_act_is_recorded_as_an_event_and_is_readable_from_ANOTHER_session(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Asserted through a second session, because a flushed-but-uncommitted row is visible to its
    own transaction -- so an in-session re-read passes under the very defect it would catch."""
    _update(migrated_session, gateway=_behind())

    with Session(migrated_engine) as reader:
        event = reader.scalar(select(Event).where(Event.action == BRANCH_UPDATE_ACTION))
        assert event is not None
        assert event.subject_type == BRANCH_UPDATE_SUBJECT
        assert event.actor_id == "orchestrator-system"
        assert event.payload["repository"] == REPOSITORY
        assert event.payload["pr_number"] == PR
        assert event.payload["head_sha"] == HEAD


def test_a_repeat_replays_the_event_and_never_calls_the_remote_again(
    migrated_session: Session,
) -> None:
    """The idempotency story named in the coverage matrix."""
    first = _update(migrated_session, gateway=_behind())
    gateway = _behind()

    second = _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == []
    assert (second.repository, second.pr_number, second.head_sha) == (
        first.repository,
        first.pr_number,
        first.head_sha,
    )
    # AND IT SAYS SO. A replay here means the head did not move, which -- because the platform
    # answers 202 and does the work afterwards -- is the shape of it having accepted and not
    # delivered. Unflagged, the caller prints that as a success on every pass forever.
    assert first.replayed is False
    assert second.replayed is True


def test_a_key_spent_on_a_DIFFERENT_subject_is_refused_rather_than_replayed(
    migrated_session: Session,
) -> None:
    """One globally unique column reaches both, so an operator who copies a request and edits only
    the number would otherwise be told the pull request they named was brought up to date when a
    different one had been."""
    _update(migrated_session, gateway=_behind(), key="shared")

    with pytest.raises(DomainError) as raised:
        update_estate_pull_request_branch(
            migrated_session,
            EstateBranchUpdateCommand(
                repository=REPOSITORY,
                pr_number=51,
                actor=SYSTEM,
                idempotency_key="shared",
                expected_head_sha=HEAD,
            ),
            _behind(),
            redeploying_source(),
            FakeChangeRecordSource({(REPOSITORY, 51): approved()}),
            enabled=True,
            credentials_configured=True,
            clock=FixedClock(IN_WINDOW),
        )

    assert raised.value.code == "idempotency_conflict"


def test_a_LATER_update_after_the_base_moves_again_is_never_barred_by_the_first(
    migrated_session: Session,
) -> None:
    """THE PROPERTY THAT MAKES A KEY SAFE ON A REPEATABLE ACT. A successful update changes the
    head, so the caller's next key -- content-addressed over the head -- is a different key. If it
    were not, the second night's landing would stale this branch forever with no way to clear it.
    """
    moved = "d" * 40
    _update(migrated_session, gateway=_behind(), key="branch-update-at-old-head")
    gateway = _behind(pull=pull_request(head_sha=moved))

    _update(
        migrated_session,
        gateway=gateway,
        expected_head=moved,
        key="branch-update-at-new-head",
    )

    assert gateway.branch_updates == [(REPOSITORY, PR, moved)]


def _landed_tonight():
    """A landing already recorded for this repository inside the open window, which is what makes
    the pace term refuse every sibling for the rest of the night."""
    from orchestrator.persistence.models import EstatePrMerge

    return EstatePrMerge(
        repository=REPOSITORY,
        pr_number=51,
        head_sha="e" * 40,
        status="merged",
        reason_code=None,
        merge_commit_sha="f" * 40,
        github_status=200,
        change_record_id=52,
        policy_version=2,
        idempotency_key="a-landing-tonight",
    )


def test_the_served_answer_DECLARES_the_verdict_the_caller_reads() -> None:
    """A `response_model` silently DROPS every key the service returns and the model does not
    name, with no error anywhere -- so "the service computes it" is never evidence "the caller
    receives it". This estate has already shipped that exact defect once, on the runner brief,
    where every service-level assertion passed and the wire carried nothing.

    Asserted as SET EQUALITY over the whole answer rather than as a membership check for this
    increment's one field, because the failure is not specific to this field: any future addition
    to the composed answer has the same silent hole, and a membership check would not see it.
    """
    from orchestrator.api.schemas import EstateLandingAdmissionResponse
    from orchestrator.services.estate_landing_admission import EstateLandingAdmission

    assert set(EstateLandingAdmissionResponse.model_fields) == set(
        EstateLandingAdmission.__dataclass_fields__
    )


# --------------------------------------------------------------------------------------------
# The REAL gateway. Everything above runs against a double, so nothing above can see what is
# actually sent or which status is actually believed -- and the status is the one fact about this
# call that is easy to get wrong by copying the landing call beside it.
# --------------------------------------------------------------------------------------------


class _Sent:
    """Stands in for the module-level `httpx.put`, recording what left the process."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append((url, json))
        return httpx.Response(self.status, json={"message": "Updating pull request branch"})


def _gateway(monkeypatch, sent: _Sent) -> GitHubEstatePullRequests:
    monkeypatch.setattr(estate_pr_merge.httpx, "put", sent)
    return GitHubEstatePullRequests(lambda: "a-token")


def test_the_platform_answers_202_AND_THAT_IS_SUCCESS(monkeypatch) -> None:
    """**202, NOT 200**, and it is the whole reason this call could not be copied from the landing
    beside it. The platform accepts the request and performs the work afterwards, so a `!= 200`
    check reads every success as a refusal -- silently, and in the direction where the lane simply
    stops working while reporting that the remote declined.
    """
    sent = _Sent(202)

    _gateway(monkeypatch, sent).update_branch(
        repository=REPOSITORY, number=PR, expected_head_sha=HEAD
    )

    assert len(sent.calls) == 1


def test_a_200_is_NOT_believed(monkeypatch) -> None:
    """The pair to the case above. Pinned in both directions, so a check widened to accept any 2xx
    -- which would look more permissive and more robust -- is caught as the loss of information it
    is."""
    with pytest.raises(EstateGatewayError):
        _gateway(monkeypatch, _Sent(200)).update_branch(
            repository=REPOSITORY, number=PR, expected_head_sha=HEAD
        )


def test_the_request_NAMES_the_head_and_addresses_the_right_pull_request(monkeypatch) -> None:
    """`expected_head_sha` is the whole of the concurrency control: omit it and the platform
    substitutes whatever the head is now, which is precisely what it is here to prevent."""
    sent = _Sent(202)

    _gateway(monkeypatch, sent).update_branch(
        repository=REPOSITORY, number=PR, expected_head_sha=HEAD
    )

    url, body = sent.calls[0]
    assert url.endswith(f"/repos/{REPOSITORY}/pulls/{PR}/update-branch")
    assert body == {"expected_head_sha": HEAD}


def test_a_refusal_from_the_platform_carries_its_status_and_never_the_token(monkeypatch) -> None:
    with pytest.raises(EstateGatewayError) as raised:
        _gateway(monkeypatch, _Sent(422)).update_branch(
            repository=REPOSITORY, number=PR, expected_head_sha=HEAD
        )

    assert raised.value.status_code == 422
    assert "a-token" not in str(raised.value)


def test_an_unreachable_platform_is_a_gateway_error_and_never_an_escape(monkeypatch) -> None:
    """A bare exception out of here reaches an unhandled HTTP 500: only `DomainError` and the
    authentication error have registered handlers."""

    def explode(url, *, headers, json, timeout):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(estate_pr_merge.httpx, "put", explode)

    with pytest.raises(EstateGatewayError) as raised:
        GitHubEstatePullRequests(lambda: "a-token").update_branch(
            repository=REPOSITORY, number=PR, expected_head_sha=HEAD
        )

    assert raised.value.code.startswith("request_error:")


# --------------------------------------------------------------------------------------------
# `events.idempotency_key` is unique across the WHOLE table rather than per act, so "spent on a
# different subject" has three shapes and every one of them reaches this replay path.
# --------------------------------------------------------------------------------------------


def test_a_key_spent_by_a_DIFFERENT_KIND_OF_ACT_is_refused(migrated_session: Session) -> None:
    """Answering from it would report a branch as brought up to date on the strength of an event
    about something else entirely."""
    migrated_session.add(
        Event(
            actor_id="orchestrator-system",
            action="something.else",
            subject_type="work_unit",
            subject_id=uuid.uuid4(),
            payload={"repository": REPOSITORY, "pr_number": PR, "head_sha": HEAD},
            correlation_id=uuid.uuid4(),
            idempotency_key="shared-across-acts",
        )
    )
    migrated_session.flush()
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, key="shared-across-acts")

    assert raised.value.code == "idempotency_conflict"
    assert gateway.branch_updates == []


def test_a_key_spent_on_a_DIFFERENT_REPOSITORY_is_refused(migrated_session: Session) -> None:
    """Same pull request number, different repository -- the shape an operator produces by copying
    a request and editing one field."""
    migrated_session.add(
        Event(
            actor_id="orchestrator-system",
            action=BRANCH_UPDATE_ACTION,
            subject_type=BRANCH_UPDATE_SUBJECT,
            subject_id=uuid.uuid4(),
            payload={"repository": "alobarquest/brain", "pr_number": PR, "head_sha": HEAD},
            correlation_id=uuid.uuid4(),
            idempotency_key="shared-across-repositories",
        )
    )
    migrated_session.flush()
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, key="shared-across-repositories")

    assert raised.value.code == "idempotency_conflict"
    assert gateway.branch_updates == []
