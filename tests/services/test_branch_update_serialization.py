"""The lane edits one Dependabot branch per repository at a time. ADR-0045.

Everything here runs with no network. The rule has two sides whose unknowns must fail in
OPPOSITE directions -- an unestablished target may not be treated as edited, an unestablished
sibling may not be treated as owned -- so every classification is pinned from both sides rather
than by one representative case.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services import estate_landing_admission, inert_landing_admission
from orchestrator.services.branch_update_serialization import (
    _HOLDING_BESIDE_THE_CRITERION,
    BRANCH_UPDATE_ACTION,
    INERT_BRANCH_UPDATE_ACTION,
    READ_FAILURE_REFUSALS,
    Ownership,
    SiblingAnswer,
    _answer_class,
    _commit_class,
    _ownership,
    _recently_updated_heads,
)
from orchestrator.services.estate_landing_admission import (
    DELIBERATE_REFUSALS,
    LANDING_CHECKS_AWAITING_VERDICT,
    LANDING_CHECKS_IN_FLIGHT,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_MERGEABILITY_UNKNOWN,
    LANDING_OUTSIDE_CHANGE_WINDOW,
    LANDING_PACE_EXHAUSTED,
    LANDING_PULL_REQUEST_CONFLICTED,
    LANDING_ROLLOUT_MOVED,
    PullRequestCommit,
)
from orchestrator.services.estate_pr_branch_update import (
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import redeploying_source
from tests.services.estate_landing_doubles import (
    HEAD,
    REPOSITORY,
    FakeEstateGateway,
    approved,
    dependabot_commit,
    foreign_commit,
)

NOW = datetime(2026, 9, 25, 6, 15, tzinfo=UTC)


def _commit(
    *, author: str | None, committer: str | None, verified: bool, sha: str = "c1"
) -> PullRequestCommit:
    return PullRequestCommit(
        sha=sha, author_login=author, committer_login=committer, verified=verified
    )


# --------------------------------------------------------------------------------------------
# Commit classification, one row per case.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("author", "committer", "verified", "expected"),
    [
        # A person's commit on the branch disowns it, whoever they are.
        ("AlobarQuest", "web-flow", True, "foreign"),
        # The update bot's commit rewritten locally and pushed: the author survives a rebase, the
        # committer becomes the person -- and Dependabot disowns the branch all the same.
        ("dependabot[bot]", "AlobarQuest", True, "foreign"),
        ("dependabot[bot]", "web-flow", True, "dependabot"),
        # No linked account is not the update bot, and not somebody else either.
        (None, "web-flow", True, "unclassified"),
        ("dependabot[bot]", None, True, "unclassified"),
        # An unsigned web-flow commit is the cheap spoof: an address set to the platform's noreply
        # links to web-flow but cannot carry the platform's signature.
        ("dependabot[bot]", "web-flow", False, "unclassified"),
        # THIS ESTATE'S OWN APP. Its update-branch commits ALSO carry committer web-flow and a
        # platform signature, so the committer cannot tell it from the update bot; the author does.
        ("alobar-sds-dispatch[bot]", "web-flow", True, "foreign"),
    ],
)
def test_every_commit_falls_into_exactly_one_class(author, committer, verified, expected) -> None:
    assert _commit_class(_commit(author=author, committer=committer, verified=verified)) == expected


# --------------------------------------------------------------------------------------------
# Ownership: positively owned, positively edited, or neither.
# --------------------------------------------------------------------------------------------


def test_every_commit_the_bot_s_and_the_last_one_at_the_head_is_OWNED() -> None:
    commits = (dependabot_commit("a"), dependabot_commit(HEAD))

    assert _ownership(commits, listed_head=HEAD, edited_by_event=False) is Ownership.OWNED


def test_a_last_commit_that_is_not_the_listed_head_is_UNESTABLISHED() -> None:
    """The list call and the commits call are two reads. If the head moved between them the
    commits describe a different head than the one the rule reasons about."""
    commits = (dependabot_commit("a"), dependabot_commit("moved"))

    assert _ownership(commits, listed_head=HEAD, edited_by_event=False) is Ownership.UNESTABLISHED


def test_one_foreign_commit_is_EDITED_even_when_the_head_moved() -> None:
    """A foreign commit is a fact about the branch whichever head it is read at: Dependabot has
    already disowned it."""
    commits = (dependabot_commit("a"), foreign_commit("moved"))

    assert _ownership(commits, listed_head=HEAD, edited_by_event=False) is Ownership.EDITED


def test_an_empty_commit_list_is_UNESTABLISHED() -> None:
    assert _ownership((), listed_head=HEAD, edited_by_event=False) is Ownership.UNESTABLISHED


def test_an_unclassified_commit_is_UNESTABLISHED() -> None:
    commits = (_commit(author=None, committer="web-flow", verified=True, sha=HEAD),)

    assert _ownership(commits, listed_head=HEAD, edited_by_event=False) is Ownership.UNESTABLISHED


def test_a_fresh_update_event_makes_all_bot_commits_EDITED() -> None:
    """The 202 race: the platform has accepted the update and not yet delivered it, so the commits
    still read as the bot's own. The event is what says otherwise."""
    commits = (dependabot_commit(HEAD),)

    assert _ownership(commits, listed_head=HEAD, edited_by_event=True) is Ownership.EDITED


# --------------------------------------------------------------------------------------------
# Arm (b): the event log, bounded in time and keyed on the current head.
# --------------------------------------------------------------------------------------------


def _event(
    session: Session,
    *,
    occurred_at: datetime,
    head_sha: str = HEAD,
    pr_number: object = 49,
    repository: str = REPOSITORY,
    action: str = BRANCH_UPDATE_ACTION,
) -> None:
    session.add(
        Event(
            occurred_at=occurred_at,
            actor_id="orchestrator-system",
            action=action,
            subject_type="estate_pull_request",
            subject_id=uuid.uuid4(),
            payload={"repository": repository, "pr_number": pr_number, "head_sha": head_sha},
            correlation_id=uuid.uuid4(),
            idempotency_key=f"event-{uuid.uuid4()}",
        )
    )
    session.flush()


def _edited(session: Session, *, number: int = 49, head: str = HEAD) -> bool:
    return head in _recently_updated_heads(session, REPOSITORY, [number], NOW).get(number, set())


def test_an_update_event_just_inside_the_bound_at_the_current_head_marks_it_edited(
    migrated_session: Session,
) -> None:
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=9, seconds=59))

    assert _edited(migrated_session)


def test_an_update_event_just_OUTSIDE_the_bound_does_not(migrated_session: Session) -> None:
    """The pair to the case above, so a mutant that ignores the event's age reddens: a 202 that
    is never delivered must hold the repository for one pass and not forever."""
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=10, seconds=1))

    assert not _edited(migrated_session)


def test_an_update_event_at_a_DIFFERENT_head_does_not(migrated_session: Session) -> None:
    """A user's recreate puts the branch back in Dependabot's hands with a new head. Keyed on the
    current head, the event retires the moment the head moves."""
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=1), head_sha="before-recreate")

    assert not _edited(migrated_session)


def test_an_update_event_for_ANOTHER_pull_request_does_not(migrated_session: Session) -> None:
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=1), pr_number=50)

    assert not _edited(migrated_session)


def test_an_update_event_for_ANOTHER_repository_does_not(migrated_session: Session) -> None:
    _event(
        migrated_session,
        occurred_at=NOW - timedelta(minutes=1),
        repository="alobarquest/brain",
    )

    assert not _edited(migrated_session)


def test_a_boolean_pr_number_in_a_payload_is_not_a_pull_request_number(
    migrated_session: Session,
) -> None:
    """`True == 1` in Python. A payload whose number is a boolean must not match pull request 1."""
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=1), pr_number=True)

    assert not _edited(migrated_session, number=1)


@pytest.mark.parametrize("action", [BRANCH_UPDATE_ACTION, INERT_BRANCH_UPDATE_ACTION])
def test_either_lane_s_update_event_counts(migrated_session: Session, action: str) -> None:
    """Both lanes share the repository lock and both edit Dependabot branches, so an edit by one
    is an edit the other must see."""
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=1), action=action)

    assert _edited(migrated_session)


def test_an_unrelated_event_does_not(migrated_session: Session) -> None:
    _event(migrated_session, occurred_at=NOW - timedelta(minutes=1), action="something.else")

    assert not _edited(migrated_session)


def test_the_event_the_REAL_act_writes_is_the_one_arm_b_reads(migrated_session: Session) -> None:
    """Hand-inserted rows prove the query; this proves the query and the writer agree. The act is
    asked about a repository spelled in mixed case, and what it records must still be found by a
    reader asking in the folded form -- a disagreement would fail OPEN, reading a branch the lane
    has just edited as still owned."""
    gateway = FakeEstateGateway(behind=3)
    update_estate_pull_request_branch(
        migrated_session,
        EstateBranchUpdateCommand(
            repository="AlobarQuest/Change-Manager",
            pr_number=49,
            actor=ActorContext("orchestrator-system", ActorRole.SYSTEM),
            idempotency_key="real-act",
            expected_head_sha=HEAD,
        ),
        gateway,
        redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, 49): approved()}),
        enabled=True,
        credentials_configured=True,
        clock=_InWindow(),
    )
    assert gateway.branch_updates, "the act must have acted for this pin to mean anything"

    now = TransactionClock().now(migrated_session)

    assert HEAD in _recently_updated_heads(migrated_session, REPOSITORY, [49], now).get(49, set())


class _InWindow:
    def now(self, session: Session) -> datetime:
        return datetime(2026, 8, 11, 7, 30, tzinfo=UTC)


# --------------------------------------------------------------------------------------------
# The three sets a sibling's refusals fall into: holding, read failure, releasing.
# --------------------------------------------------------------------------------------------

# THE RELEASING SET, SPELLED OUT. At runtime releasing is the complement -- a code in neither of
# the other two sets releases, which is the pre-change behaviour and the right polarity for a
# positively named condition. This literal exists so that a NEW code reds the completeness test
# below until somebody decides which set it belongs in; without it a new `..._unreadable` code
# would silently release, which is the one direction the read-failure set exists to prevent.
RELEASING = {
    "inert_landing_author_not_permitted",
    "inert_landing_repository_not_declared",
    "inert_landing_rules_undeclared",
    "inert_landing_target_not_inert",
    "landing_already_recorded",
    "landing_author_not_the_update_bot",
    "landing_base_not_default_branch",
    "landing_change_window_not_declared",
    "landing_checks_not_clean",
    "landing_ecosystem_excluded",
    "landing_not_enabled",
    "landing_policy_version_superseded",
    "landing_pull_request_conflicted",
    "landing_pull_request_not_open",
    "landing_record_absent",
    "landing_record_has_live_objections",
    "landing_record_not_approved",
    "landing_record_not_policy_approved",
    "landing_rollout_unpinned",
    "landing_target_not_routed",
    "landing_update_type_not_permitted",
    "landing_update_type_unparseable",
}

# The holding vocabulary: the two deliberate refusals, the freshness criterion's two members, and
# the three an edited branch reports in the minutes after an update. `landing_rollout_moved` is
# holding only when freshness-derived; it sits here as the criterion's vocabulary, and the
# releasing case of a genuinely moved rollout is pinned separately below.
HOLDING = (
    DELIBERATE_REFUSALS
    | {LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED}
    | _HOLDING_BESIDE_THE_CRITERION
)


def _refusal_codes() -> set[str]:
    """Every refusal-code constant both admission modules define, by VALUE.

    The value predicate excludes the platform words (`clean`, `blocked`...), the run vocabulary,
    the update-type labels, the bot login and the branch prefix, none of which is a refusal.
    """
    codes: set[str] = set()
    for module in (estate_landing_admission, inert_landing_admission):
        for name, value in vars(module).items():
            if (
                name.isupper()
                and isinstance(value, str)
                and value.startswith(("landing_", "inert_landing_"))
            ):
                codes.add(value)
    return codes


def test_every_refusal_code_is_classified_exactly_once() -> None:
    """A new refusal code reds this until it is placed in one of the three sets."""
    codes = _refusal_codes()

    assert HOLDING | READ_FAILURE_REFUSALS | RELEASING == codes, (
        f"{len(codes)} refusal codes defined; unclassified: "
        f"{sorted(codes - HOLDING - READ_FAILURE_REFUSALS - RELEASING)}; "
        f"classified but undefined: {sorted((HOLDING | READ_FAILURE_REFUSALS | RELEASING) - codes)}"
    )
    assert not HOLDING & READ_FAILURE_REFUSALS
    assert not HOLDING & RELEASING
    assert not READ_FAILURE_REFUSALS & RELEASING
    assert (len(HOLDING), len(READ_FAILURE_REFUSALS), len(RELEASING)) == (7, 18, 22)


def _class(*refusals: str, base_matches: bool = False) -> str:
    return _answer_class(SiblingAnswer(tuple(refusals), rollout_base_matches_pin=base_matches))


@pytest.mark.parametrize(
    "code",
    sorted(
        {
            LANDING_PACE_EXHAUSTED,
            LANDING_OUTSIDE_CHANGE_WINDOW,
            LANDING_HEAD_NOT_CURRENT_WITH_BASE,
            LANDING_CHECKS_IN_FLIGHT,
            LANDING_CHECKS_AWAITING_VERDICT,
            LANDING_MERGEABILITY_UNKNOWN,
        }
    ),
)
def test_each_holding_code_holds_alone_and_beside_behind(code: str) -> None:
    assert _class(code) == "holding"
    assert _class(code, LANDING_HEAD_NOT_CURRENT_WITH_BASE) == "holding"


def test_a_rollout_pin_that_differs_BECAUSE_the_head_is_behind_holds() -> None:
    assert (
        _class(LANDING_ROLLOUT_MOVED, LANDING_HEAD_NOT_CURRENT_WITH_BASE, base_matches=True)
        == "holding"
    )


def test_a_GENUINELY_moved_rollout_releases() -> None:
    """The base does not carry the pinned bytes, so the workflow really moved and no freshening
    puts it right. Such a sibling is not queued to land; the queue moves past it."""
    assert (
        _class(LANDING_ROLLOUT_MOVED, LANDING_HEAD_NOT_CURRENT_WITH_BASE, base_matches=False)
        == "releasing"
    )


def test_a_moved_rollout_on_a_head_that_is_NOT_behind_releases() -> None:
    """A refusal cannot be caused by a position the head is not in: a pull request whose own diff
    edits the pinned workflow is not merely stale, whatever the base says."""
    assert _class(LANDING_ROLLOUT_MOVED, base_matches=True) == "releasing"


@pytest.mark.parametrize(
    "code",
    [LANDING_CHECKS_NOT_CLEAN, LANDING_PULL_REQUEST_CONFLICTED, "landing_invented_condition"],
)
def test_a_positively_named_condition_releases(code: str) -> None:
    """Releasing is exactly the pre-change behaviour, so on a condition the orchestrator read and
    named the rule can never withhold more than the lane did before it existed. A made-up code is
    here because at runtime releasing is the complement."""
    assert _class(code) == "releasing"
    assert _class(code, LANDING_HEAD_NOT_CURRENT_WITH_BASE) == "releasing"


@pytest.mark.parametrize("code", sorted(READ_FAILURE_REFUSALS))
def test_each_read_failure_is_unreadable_even_beside_a_releasing_code(code: str) -> None:
    """Checked FIRST: a sibling whose reads failed is a sibling whose state was not established,
    and a releasing code beside the failure does not establish it."""
    assert _class(code) == "unreadable"
    assert _class(code, LANDING_CHECKS_NOT_CLEAN) == "unreadable"
    assert _class(code, LANDING_HEAD_NOT_CURRENT_WITH_BASE) == "unreadable"


def test_a_sibling_whose_answer_names_nothing_holds() -> None:
    """Its own admission is satisfied: it would land. That is the branch queued next."""
    assert _class() == "holding"
