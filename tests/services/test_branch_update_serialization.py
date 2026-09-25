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
from orchestrator.services.branch_update_serialization import (
    BRANCH_UPDATE_ACTION,
    INERT_BRANCH_UPDATE_ACTION,
    Ownership,
    _commit_class,
    _ownership,
    _recently_updated_heads,
)
from orchestrator.services.estate_landing_admission import PullRequestCommit
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
