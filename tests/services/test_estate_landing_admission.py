"""May a pull request with no work unit be landed where landing changes something serving?

ADR-0019 increment 5b. Everything here runs with no network: the estate answer, the change record
and GitHub are all injected.

**Each term gets a firing test AND the composed satisfied case is asserted**, because a cascade of
refusals is exactly the shape that can be green while permitting everything -- an affirmative
answer nobody exercises is a `satisfied` nobody has seen be True.

Every clock-dependent assertion is pinned to a PAIR whose answers must differ. `live_estate`'s
window is four hours, so a single out-of-window assertion agrees with a term that ignores its
clock for 83% of the day, and this repository has already shipped that mistake twice.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from orchestrator.persistence.models import EstatePrMerge
from orchestrator.services.change_record import ChangeRecordAnswer, WorkflowPin
from orchestrator.services.estate_landing_admission import (
    LANDING_APP_CREDENTIALS_MISSING,
    LANDING_AUTHOR_NOT_THE_UPDATE_BOT,
    LANDING_BASE_NOT_DEFAULT_BRANCH,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_CONDITIONS_UNREADABLE,
    LANDING_ESTATE_SOURCE_UNCONFIGURED,
    LANDING_ESTATE_UNKNOWN,
    LANDING_FRESHNESS_UNREADABLE,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_NOT_ENABLED,
    LANDING_OUTSIDE_CHANGE_WINDOW,
    LANDING_PACE_EXHAUSTED,
    LANDING_POLICY_VERSION_SUPERSEDED,
    LANDING_PULL_REQUEST_NOT_OPEN,
    LANDING_PULL_REQUEST_UNREADABLE,
    LANDING_RECORD_ABSENT,
    LANDING_RECORD_HAS_LIVE_OBJECTIONS,
    LANDING_RECORD_NOT_APPROVED,
    LANDING_RECORD_NOT_POLICY_APPROVED,
    LANDING_RECORD_SOURCE_UNREADABLE,
    LANDING_RECORD_UNIDENTIFIED,
    LANDING_ROLLOUT_MOVED,
    LANDING_ROLLOUT_UNPINNED,
    LANDING_ROLLOUT_UNREADABLE,
    LANDING_TARGET_NOT_ROUTED,
    LANDING_UPDATE_TYPE_NOT_PERMITTED,
    LANDING_UPDATE_TYPE_UNPARSEABLE,
    EstateGatewayError,
    estate_landing_admission,
    update_type_of,
)
from tests.services.change_record_doubles import (
    SOURCE_UNREADABLE,
    FakeChangeRecordSource,
)
from tests.services.estate_doubles import (
    LANDING_UNKNOWN,
    SOURCE_UNCONFIGURED,
    EstateAnswer,
    FakeEstateLandingSource,
    inert_source,
    redeploying_source,
)
from tests.services.estate_landing_doubles import (
    HEAD,
    POLICY_VERSION,
    REPOSITORY,
    ROLLOUT_BLOB,
    ROLLOUT_PATH,
    FakeEstateGateway,
    approved,
    conditions,
    pull_request,
)

PR = 49
IN_WINDOW = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
OUT_OF_WINDOW = datetime(2026, 8, 11, 19, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, session: Session) -> datetime:
        return self._moment


def _ask(
    session: Session,
    *,
    record: ChangeRecordAnswer | None = None,
    gateway: FakeEstateGateway | None = None,
    landing: FakeEstateLandingSource | None = None,
    enabled: bool = True,
    credentials: bool = True,
    moment: datetime = IN_WINDOW,
):
    return estate_landing_admission(
        session,
        REPOSITORY,
        PR,
        landing or redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, PR): record or approved()}),
        gateway or FakeEstateGateway(),
        enabled=enabled,
        credentials_configured=credentials,
        clock=FixedClock(moment),
    )


# ---------------------------------------------------------------------------
# The affirmative case, first. A cascade nobody has seen say yes is not a gate.
# ---------------------------------------------------------------------------


def test_a_conformant_pull_request_in_the_window_is_admitted(migrated_session: Session) -> None:
    answer = _ask(migrated_session)

    assert answer.satisfied, answer.refusals
    assert answer.refusals == ()
    assert answer.head_sha == HEAD
    assert answer.change_record_id == 52
    assert answer.policy_version == POLICY_VERSION


def test_the_rollout_pin_is_read_at_the_base_branch_and_not_the_pull_request(
    migrated_session: Session,
) -> None:
    """The landing fires the workflow on the BASE, so that is the copy the condition is about.

    Reading it at the pull request's own head would let a pull request that edits the rollout
    workflow describe the very rollout it is changing.
    """
    gateway = FakeEstateGateway()
    _ask(migrated_session, gateway=gateway)

    assert gateway.blobs == [(REPOSITORY, ROLLOUT_PATH, "main")]


# ---------------------------------------------------------------------------
# Configuration and the estate.
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_refuses(migrated_session: Session) -> None:
    assert LANDING_NOT_ENABLED in _ask(migrated_session, enabled=False).refusals


def test_missing_app_credentials_refuse(migrated_session: Session) -> None:
    assert LANDING_APP_CREDENTIALS_MISSING in _ask(migrated_session, credentials=False).refusals


def test_an_inert_repository_is_refused_by_this_lane(migrated_session: Session) -> None:
    """The OPPOSITE direction from the work-unit landing's term, and both are deliberate: that
    one exists only where a landed pull request is inert, this one only where it is not."""
    answer = _ask(migrated_session, landing=inert_source())

    assert not answer.satisfied
    assert LANDING_TARGET_NOT_ROUTED in answer.refusals


def test_an_unassessed_repository_is_not_permission(migrated_session: Session) -> None:
    source = FakeEstateLandingSource({REPOSITORY: EstateAnswer(LANDING_UNKNOWN, "not_assessed")})

    assert LANDING_ESTATE_UNKNOWN in _ask(migrated_session, landing=source).refusals


def test_an_unconfigured_estate_source_is_named_apart_from_an_unreadable_one(
    migrated_session: Session,
) -> None:
    """One sets an environment variable; the other looks at why a service is refusing."""
    source = FakeEstateLandingSource({REPOSITORY: EstateAnswer(None, SOURCE_UNCONFIGURED)})

    assert LANDING_ESTATE_SOURCE_UNCONFIGURED in _ask(migrated_session, landing=source).refusals


# ---------------------------------------------------------------------------
# The record, and the three shapes of `approved` this increment tells apart.
# ---------------------------------------------------------------------------


def test_no_record_refuses(migrated_session: Session) -> None:
    assert LANDING_RECORD_ABSENT in _ask(migrated_session, record=ChangeRecordAnswer(True)).refusals


def test_an_unreadable_record_service_is_not_an_absent_record(migrated_session: Session) -> None:
    answer = _ask(migrated_session, record=ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE))

    assert LANDING_RECORD_SOURCE_UNREADABLE in answer.refusals
    assert LANDING_RECORD_ABSENT not in answer.refusals


def test_a_pending_record_refuses(migrated_session: Session) -> None:
    assert (
        LANDING_RECORD_NOT_APPROVED
        in _ask(migrated_session, record=approved(status="pending")).refusals
    )


def test_a_record_a_human_approved_with_no_policy_behind_it_refuses(
    migrated_session: Session,
) -> None:
    """Production item 44's shape. It is a valid basis for a person and not for this lane: an
    unattended act binds itself to a standing rule, and "somebody approved this once" is not one.
    Keyed on `policy_version`, which is exactly the field a reader of `status` alone cannot see.
    """
    answer = _ask(migrated_session, record=approved(policy_version=None))

    assert not answer.satisfied
    assert LANDING_RECORD_NOT_POLICY_APPROVED in answer.refusals


def test_a_record_approved_under_a_superseded_version_refuses(migrated_session: Session) -> None:
    """THE term that makes narrowing the policy mean anything.

    Nothing re-evaluates a stored record except a fresh proposal of the same pull request, so
    without this the estate could move its policy and every approval already granted would go on
    being honoured.
    """
    answer = _ask(migrated_session, record=approved(policy_version=1))

    assert not answer.satisfied
    assert LANDING_POLICY_VERSION_SUPERSEDED in answer.refusals


def test_a_stored_approval_the_policy_has_since_overtaken_refuses(
    migrated_session: Session,
) -> None:
    """The third indistinguishable row: stored `approved`, live objections non-empty."""
    answer = _ask(migrated_session, record=approved(objections=("risk_not_in_policy",)))

    assert not answer.satisfied
    assert LANDING_RECORD_HAS_LIVE_OBJECTIONS in answer.refusals


def test_conditions_this_build_could_not_read_refuse(migrated_session: Session) -> None:
    """A record service that predates them, or a shape this build does not recognise. Proceeding
    would mean acting under conditions nobody stated."""
    answer = _ask(migrated_session, record=approved(landing_conditions=None))

    assert not answer.satisfied
    assert LANDING_CONDITIONS_UNREADABLE in answer.refusals


# ---------------------------------------------------------------------------
# The window, pinned to a PAIR.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "admitted"), [(IN_WINDOW, True), (OUT_OF_WINDOW, False)], ids=["inside", "outside"]
)
def test_the_window_admits_inside_and_refuses_outside(
    migrated_session: Session, moment: datetime, admitted: bool
) -> None:
    """A PAIR, because a single out-of-window assertion cannot kill a term that ignores its clock:
    whenever the real clock is also outside the window the mutant and the original agree."""
    answer = _ask(migrated_session, moment=moment)

    assert answer.satisfied is admitted
    assert (LANDING_OUTSIDE_CHANGE_WINDOW in answer.refusals) is not admitted


# ---------------------------------------------------------------------------
# Pace.
# ---------------------------------------------------------------------------


def _landed_row(session: Session, *, moment: datetime, repository: str = REPOSITORY) -> None:
    row = EstatePrMerge(
        repository=repository,
        pr_number=999,
        head_sha=HEAD,
        status="merged",
        idempotency_key=f"pace-{repository}-{moment.isoformat()}",
        created_at=moment,
    )
    session.add(row)
    session.commit()


def test_a_second_landing_in_the_same_window_is_refused(migrated_session: Session) -> None:
    # 02:30 New York on the same night as IN_WINDOW (06:30 UTC), i.e. inside this occurrence.
    _landed_row(migrated_session, moment=datetime(2026, 8, 11, 6, 30, tzinfo=UTC))

    answer = _ask(migrated_session)

    assert not answer.satisfied
    assert LANDING_PACE_EXHAUSTED in answer.refusals


def test_a_landing_from_a_previous_window_does_not_exhaust_this_one(
    migrated_session: Session,
) -> None:
    """The control. Without it the term would pass for a rule that simply refuses forever."""
    _landed_row(migrated_session, moment=datetime(2026, 8, 10, 6, 30, tzinfo=UTC))

    assert _ask(migrated_session).satisfied


def test_a_landing_into_another_repository_does_not_exhaust_this_one(
    migrated_session: Session,
) -> None:
    """One landing per REPOSITORY per window, not one across the estate."""
    _landed_row(
        migrated_session,
        moment=datetime(2026, 8, 11, 6, 30, tzinfo=UTC),
        repository="alobarquest/brain",
    )

    assert _ask(migrated_session).satisfied


# ---------------------------------------------------------------------------
# What only GitHub knows.
# ---------------------------------------------------------------------------


def test_an_unreadable_pull_request_refuses_and_asks_nothing_further(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(read_error=EstateGatewayError("read_status", 502))
    answer = _ask(migrated_session, gateway=gateway)

    assert LANDING_PULL_REQUEST_UNREADABLE in answer.refusals
    assert gateway.compares == [] and gateway.blobs == []


def test_a_closed_pull_request_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(is_open=False))

    assert LANDING_PULL_REQUEST_NOT_OPEN in _ask(migrated_session, gateway=gateway).refusals


def test_a_pull_request_against_another_base_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(base_ref="release"))

    assert LANDING_BASE_NOT_DEFAULT_BRANCH in _ask(migrated_session, gateway=gateway).refusals


@pytest.mark.parametrize(
    ("login", "is_bot"),
    [
        ("alobar-sds-dispatch[bot]", True),
        ("AlobarQuest", False),
        ("dependabot[bot]", False),
    ],
    ids=["another-app", "a-person", "the-right-login-wrong-type"],
)
def test_only_the_update_bot_itself_is_admitted(
    migrated_session: Session, login: str, is_bot: bool
) -> None:
    """NOT "any account of type Bot". That admits every GitHub App -- including this estate's own,
    which holds a write on every repository in the account."""
    gateway = FakeEstateGateway(pull=pull_request(author_login=login, author_is_bot=is_bot))

    assert LANDING_AUTHOR_NOT_THE_UPDATE_BOT in _ask(migrated_session, gateway=gateway).refusals


def test_a_pull_request_the_remote_will_not_land_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="blocked"))

    assert LANDING_CHECKS_NOT_CLEAN in _ask(migrated_session, gateway=gateway).refusals


# ---------------------------------------------------------------------------
# Freshness -- the term the whole increment turns on.
# ---------------------------------------------------------------------------


def test_a_head_behind_its_base_refuses_even_when_the_remote_calls_it_clean(
    migrated_session: Session,
) -> None:
    """THE case, and the pairing is the point: `mergeable_state` is stale-tolerant, so a pull
    request answers `clean` while being two commits behind -- which is exactly what all four
    waiting pull requests were on 2026-08-12. The squash would produce a tree no check has run.
    """
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="clean"), behind=2)
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals
    assert LANDING_CHECKS_NOT_CLEAN not in answer.refusals


@pytest.mark.parametrize(("behind", "admitted"), [(0, True), (1, False), (2, False)])
def test_one_commit_behind_is_behind(
    migrated_session: Session, behind: int, admitted: bool
) -> None:
    """The boundary, pinned. A test that only ever measured two commits behind cannot tell this
    term from one that tolerates a single commit -- and a single commit is the ordinary state of
    every sibling pull request the moment one of them lands."""
    answer = _ask(migrated_session, gateway=FakeEstateGateway(behind=behind))

    assert answer.satisfied is admitted
    assert (LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals) is not admitted


def test_an_unreadable_comparison_refuses_rather_than_assuming_current(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(compare_error=EstateGatewayError("read_status", 500))

    assert LANDING_FRESHNESS_UNREADABLE in _ask(migrated_session, gateway=gateway).refusals


def test_a_policy_that_does_not_require_freshness_does_not_ask(
    migrated_session: Session,
) -> None:
    """The condition is the policy's to state. A version that does not declare it is not
    second-guessed, and the round trip is not spent."""
    gateway = FakeEstateGateway(behind=5)
    answer = _ask(
        migrated_session,
        record=approved(landing_conditions=conditions(require_fresh=False)),
        gateway=gateway,
    )

    assert answer.satisfied, answer.refusals
    assert gateway.compares == []


# ---------------------------------------------------------------------------
# The update type, parsed from the title at the moment of the act.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("build(deps): bump alembic from 1.18.5 to 1.19.0", "semver-minor"),
        ("build(deps-dev): bump ruff from 0.15.20 to 0.15.21", "semver-patch"),
        ("build(deps-dev): bump ruff from 0.15.20 to 0.16.2", "semver-minor"),
        ("build(deps): bump zod from 3.25.76 to 4.4.3", "semver-major"),
        # Single-component versions, how the workflow-automation ecosystem is numbered.
        ("build(deps): bump actions/checkout from 4 to 7", "semver-major"),
        # A requirement RANGE carries no delta any rule could be applied to.
        ("build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1", None),
        ("chore(deps): update fastmcp requirement from <4,>=3.4.2 to >=3.4.4,<4", None),
        # A GROUPED bump names one dependency and changes several.
        ("build(deps-dev): bump tsx from 4.23.5 to 4.23.9 in the minor-and-patch group", None),
        ("build(deps): bump the minor-and-patch group across 1 directory with 5 updates", None),
        # No movement, and a downgrade: neither is an update this lane can classify.
        ("build(deps): bump x from 1.2.3 to 1.2.3", None),
        ("build(deps): bump x from 1.3.0 to 1.2.9", None),
        ("build(deps): bump x from 1.2.3 to 1.2.3.4.5", None),
    ],
)
def test_the_update_type_is_read_from_the_title(title: str, expected: str | None) -> None:
    """Every case here is a real title measured across the estate on 2026-08-12, except the last
    three, which are the shapes a parser must refuse rather than guess at.

    **The title, and measurably not the alternatives.** The update bot rewrites a pull request in
    place; on `intent-packages` #50 the branch still read `ruff-0.16.0` while the title read
    `0.16.1` -- and so did the bot's own machine-readable `dependency-version` trailer in the head
    commit, whose diff installs 0.16.1. The two identifiers that look more structured are the two
    that went stale.
    """
    assert update_type_of(title) == expected


def test_an_unparseable_title_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(
            title="build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1"
        )
    )

    assert LANDING_UPDATE_TYPE_UNPARSEABLE in _ask(migrated_session, gateway=gateway).refusals


def test_an_update_type_the_policy_does_not_permit_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(title="build(deps): bump actions/checkout from 4 to 7")
    )

    assert LANDING_UPDATE_TYPE_NOT_PERMITTED in _ask(migrated_session, gateway=gateway).refusals


# ---------------------------------------------------------------------------
# The rollout pin.
# ---------------------------------------------------------------------------


def test_a_moved_rollout_workflow_refuses(migrated_session: Session) -> None:
    """What a green rollout attests is what the record's criteria say. If the bytes producing it
    have changed, the criteria describe something that no longer runs."""
    gateway = FakeEstateGateway(blob="0000000000000000000000000000000000000000")
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_ROLLOUT_MOVED in answer.refusals


def test_a_deleted_or_renamed_rollout_workflow_refuses(migrated_session: Session) -> None:
    """A pinned path naming no file is a moved rollout. Reading it as "nothing to compare" would
    waive the condition exactly when it matters most."""
    gateway = FakeEstateGateway(blob=None)

    assert LANDING_ROLLOUT_MOVED in _ask(migrated_session, gateway=gateway).refusals


def test_a_repository_with_no_pin_refuses(migrated_session: Session) -> None:
    """A policy version that declares no pin is one that predates the condition. "Nobody said
    which bytes" is not "these bytes are fine"."""
    answer = _ask(migrated_session, record=approved(landing_conditions=conditions(pins={})))

    assert not answer.satisfied
    assert LANDING_ROLLOUT_UNPINNED in answer.refusals


def test_the_blob_is_compared_case_insensitively(migrated_session: Session) -> None:
    """GitHub serves object names lower-cased and a human transcribing one may not. Two spellings
    of one blob must not read as a moved rollout."""
    pins = {REPOSITORY: WorkflowPin(path=ROLLOUT_PATH, blob_sha=ROLLOUT_BLOB.upper())}
    answer = _ask(migrated_session, record=approved(landing_conditions=conditions(pins=pins)))

    assert answer.satisfied, answer.refusals


def test_a_pin_is_found_however_the_repository_is_spelled() -> None:
    """`pin_for` folds case as the record's own identity key does, so a repository named
    `AlobarQuest/change-manager` in one place and `alobarquest/change-manager` in another is one
    repository rather than an unpinned one."""
    pinned = conditions()

    assert pinned.pin_for(REPOSITORY.upper()) is not None
    assert pinned.pin_for(REPOSITORY) is not None
    assert pinned.pin_for("alobarquest/brain") is None


def test_an_unreadable_rollout_blob_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(blob_error=EstateGatewayError("read_status", 500))

    assert LANDING_ROLLOUT_UNREADABLE in _ask(migrated_session, gateway=gateway).refusals


# ---------------------------------------------------------------------------
# The composed answer.
# ---------------------------------------------------------------------------


def test_no_unmet_answer_is_silent(migrated_session: Session) -> None:
    """`satisfied` is a positive conjunction, so an unmet answer with an empty refusal list would
    be a refusal nobody could act on. Asserted over a spread of independently unmet cases."""
    cases = [
        {"enabled": False},
        {"record": ChangeRecordAnswer(True)},
        {"moment": OUT_OF_WINDOW},
        {"gateway": FakeEstateGateway(behind=3)},
        {"landing": inert_source()},
    ]
    for case in cases:
        answer = _ask(migrated_session, **case)  # type: ignore[arg-type]
        assert not answer.satisfied, case
        assert answer.refusals, case


def test_a_record_with_no_readable_identifier_refuses(migrated_session: Session) -> None:
    """The landing writes the record's identifier into the squash body, where the estate's ledger
    reads it back to classify the landing. Without one it would write a placeholder the ledger's
    parse matches nothing in, and the landing would record as having no accountable basis at all
    -- the class nothing reads. Refused here rather than discovered there.
    """
    answer = _ask(migrated_session, record=approved(record_id=None))

    assert not answer.satisfied
    assert LANDING_RECORD_UNIDENTIFIED in answer.refusals
