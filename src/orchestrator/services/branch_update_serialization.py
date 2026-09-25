"""The lane edits one Dependabot branch per repository at a time. ADR-0045.

Bringing a Dependabot pull request up to date with its base writes a commit under this estate's
identity, and from then on Dependabot refuses to rebase that branch. Two such branches in one
repository, and a landing of either, is the whole precondition of the deadlock this rule exists to
prevent: the landing conflicts the other, Dependabot will not rebase it, and the lane will not
freshen a conflicted head.

This module owns the rule so that both branch-update acts and both admission reads ask one
question in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import Enum
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Event
from orchestrator.services.estate_landing_admission import UPDATE_BOT_LOGIN, PullRequestCommit

# The event actions the two branch-update acts record, one per lane. They live HERE rather than in
# the act modules because the sibling rule reads them back out of the event log, and the act
# modules import this one -- so this one cannot import them. Each act re-imports its own under the
# same name, so every existing reader keeps working.
BRANCH_UPDATE_ACTION: Final = "estate_pr_branch_update.updated"
INERT_BRANCH_UPDATE_ACTION: Final = "inert_pr_branch_update.updated"

# The platform's own identity as COMMITTER of a commit it wrote on someone's behalf. Every commit
# the update bot writes carries it, measured on 63 of 63 across six repositories (2026-09-25). So
# does this estate's App's update-branch commit -- which is why the committer is not what separates
# the two (the author does). What it adds is the case the author cannot see: an update-bot commit a
# person rewrote in a local checkout keeps its author and takes that person as committer, and
# Dependabot disowns the branch all the same.
DEPENDABOT_COMMITTER: Final = "web-flow"

# How long a recorded branch update counts as an edit the platform may not have delivered yet.
#
# The update call answers 202 and does the work afterwards, so a commit read taken seconds after
# one sibling was updated can still show only the bot's commits, and the next sibling in the same
# pass would be updated too -- which is how two of the four stuck pairs were made. The event closes
# that window: it is written in the same transaction as the call, under the same repository lock,
# so the next act in the pass sees it.
#
# The bound exists because a 202 the platform accepts and never delivers would otherwise leave the
# pre-update head current forever, and the branch "edited" forever with it. Delivery took about 12
# seconds when measured, and sibling acts inside one pass are 3-4 seconds apart; ten minutes is
# more than forty times the measured delivery, longer than a whole lander pass, and shorter than
# the hourly cadence of both landers -- so a never-delivered update has lapsed before the next pass
# reads it. The trade, stated: a delivery slower than ten minutes reopens the window for one pass.
EDIT_EVENT_BOUND: Final = timedelta(minutes=10)


class Ownership(Enum):
    """Whose branch this is, as far as can be positively established."""

    # Every commit is the update bot's, the list is complete, and it ends at the listed head.
    OWNED = "owned"
    # Something other than the update bot has written to it, or this estate just did.
    EDITED = "edited"
    # Neither could be established. Which way that fails is decided by the side it is on.
    UNESTABLISHED = "unestablished"


def _commit_class(commit: PullRequestCommit) -> Literal["dependabot", "foreign", "unclassified"]:
    """Exactly one of three classes, from the fields the commits listing already carries.

    **The update bot's own:** linked author `dependabot[bot]`, committer `web-flow`, and the
    platform's signature. The signature is what closes the cheap spoof: a committer address set to
    the platform's noreply links to web-flow but cannot carry the platform's signature.

    **Foreign:** a linked author that is anyone else, or a linked committer that is anyone but
    web-flow. Dependabot disowns a branch whoever pushed to it, so a person's commit counts exactly
    as this estate's App's does -- nothing here consults identities the orchestrator knows.

    **Unclassified:** everything else -- no linked author, no linked committer, or an unsigned
    web-flow commit. Not treated as either, because each side of the rule must fail in a
    different direction on an unknown.
    """
    author = commit.author_login
    committer = commit.committer_login
    if (author is not None and author != UPDATE_BOT_LOGIN) or (
        committer is not None and committer != DEPENDABOT_COMMITTER
    ):
        return "foreign"
    if author == UPDATE_BOT_LOGIN and committer == DEPENDABOT_COMMITTER and commit.verified:
        return "dependabot"
    return "unclassified"


def _ownership(
    commits: tuple[PullRequestCommit, ...], *, listed_head: str, edited_by_event: bool
) -> Ownership:
    """Positively edited, positively owned, or neither.

    **Edited** when any commit is foreign, or this estate updated the branch at its current head
    moments ago -- a foreign commit is a fact about the branch whatever head it was read at.

    **Owned** only when every commit is the bot's, there is at least one, and the LAST one is the
    head the list call named. The list and the commits are two reads: if the head moved between
    them, the commits describe a different head from the one being reasoned about, and a moved
    head is an unestablished answer rather than a wrong one.
    """
    classes = [_commit_class(c) for c in commits]
    if edited_by_event or "foreign" in classes:
        return Ownership.EDITED
    if commits and all(c == "dependabot" for c in classes) and commits[-1].sha == listed_head:
        return Ownership.OWNED
    return Ownership.UNESTABLISHED


def _recently_updated_heads(
    session: Session, repository: str, numbers: Iterable[int], now: datetime
) -> dict[int, set[str]]:
    """For each named pull request, the heads this estate updated it FROM inside the bound.

    One query over both lanes' update events, because both edit update-bot branches and share the
    repository lock. The recorded head is the head BEFORE the update, so it matches the pull
    request's current head exactly while the platform has not delivered, and retires the moment
    the head moves -- whether by delivery (the App's commit then reads as foreign) or by a user's
    recreate (the branch is the bot's again).

    A failing query propagates. The caller's transaction is then broken, and a broken transaction
    must never be converted into an answer about ownership.
    """
    wanted = set(numbers)
    rows = session.scalars(
        select(Event.payload).where(
            Event.action.in_((BRANCH_UPDATE_ACTION, INERT_BRANCH_UPDATE_ACTION)),
            Event.occurred_at > now - EDIT_EVENT_BOUND,
            Event.payload["repository"].astext == repository,
        )
    )
    heads: dict[int, set[str]] = {}
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        number = payload.get("pr_number")
        head = payload.get("head_sha")
        # `True == 1`: a boolean is not a pull request number, however it compares.
        if not isinstance(number, int) or isinstance(number, bool) or number not in wanted:
            continue
        if isinstance(head, str) and head:
            heads.setdefault(number, set()).add(head)
    return heads
