"""Close an update-bot pull request that moves a held dependency past its ceiling, with a comment
naming the hold. Bridge layer Part 4, the plan's Ruling 7.

A hold clears only when a person lifts it, so a held pull request would otherwise sit open and
refused on every pass. Closing it is the one act this module performs, for both Dependabot lanes.

## What bounds it

The act RE-COMPOSES THE LANE'S OWN ADMISSION inside its transaction and closes only when that
answer refuses with `landing_version_ceiling_exceeded` -- never on the caller's word, and never on
`landing_dependency_list_unreadable`, which is a finding and is never closed. The update bot must
have opened the pull request, the repository must be in the lane's population (the inert lane's
declaration; the deploying lane's routed change record), the pull request must still be open, and
the lane must be switched on with its credentials configured. Every other refusal may sit beside
the ceiling: a held pull request whose checks fail is still closed.

## What the comment says

It is public, so it carries what the evidence-pack redaction rule allows and nothing more: each
held dependency, its ceiling, and one fixed sentence. Never the hold's reason, who placed it, any
actor id or name.

## Once per pull request

One `Event`, keyed `held-pr-close:<repository lowercased>#<number>` and unique in the table, read
first under a per-repository lock: a replay answers from it and touches GitHub no more. The key is
the subject's, never a caller's free choice -- a fresh key would otherwise post a second comment --
so a request naming any other key is refused. The order is comment, close, record. A close that
fails after its comment posted records nothing; the next call reads the conversation, finds the
fixed sentence, and closes without commenting again.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext
from orchestrator.persistence.models import Event
from orchestrator.services.landing.change_record import ChangeRecordSource
from orchestrator.services.landing.estate_landing import EstateLandingSource
from orchestrator.services.landing.estate_landing_admission import estate_landing_admission
from orchestrator.services.landing.inert_landing_admission import (
    INERT_LANDING_REPOSITORY_NOT_DECLARED,
    inert_landing_admission,
)
from orchestrator.services.landing.inert_landing_policy import InertLandingPolicySource
from orchestrator.services.landing.interfaces import (
    LANDING_APP_CREDENTIALS_MISSING,
    LANDING_NOT_ENABLED,
    LANDING_PULL_REQUEST_NOT_OPEN,
    LANDING_VERSION_CEILING_EXCEEDED,
    EstateGatewayError,
    EstateReadGateway,
    gateway_failure_detail,
)
from orchestrator.services.landing.lane_act import (
    authorize_actor,
    lock_repository,
    pull_request_subject_id,
)
from orchestrator.services.membership.holds import HoldBreach

HELD_CLOSE_ACTION: Final = "landing.held_pull_request_closed"

# The one sentence every close comment ends with. Also what the act looks for in the conversation
# before commenting, so a close that failed after its comment posted is not commented twice.
LIFT_SENTENCE: Final = "A person lifts the hold on /review."

# The lanes, as the route names them.
INERT: Final = "inert"
ESTATE: Final = "estate"

# Refusals. Nothing is commented, closed or recorded under any of them.
HELD_CLOSE_LANE_OFF: Final = "held_close_lane_off"
HELD_CLOSE_NOT_HELD: Final = "held_close_not_held"
HELD_CLOSE_AUTHOR_NOT_UPDATE_BOT: Final = "held_close_author_not_update_bot"
HELD_CLOSE_OUTSIDE_POPULATION: Final = "held_close_outside_population"
HELD_CLOSE_NOT_OPEN: Final = "held_close_not_open"
HELD_CLOSE_HEAD_MOVED: Final = "held_close_head_moved"
HELD_CLOSE_REFUSED_BY_REMOTE: Final = "held_close_refused_by_remote"

_LOCK: Final = "held_pr_close"
_VERB: Final = "close a held pull request"

# The admission refusals saying the lane may not act at all: switched off, or no credential.
_LANE_OFF: Final = frozenset({LANDING_NOT_ENABLED, LANDING_APP_CREDENTIALS_MISSING})


class HeldCloseGateway(EstateReadGateway, Protocol):
    """Everything the lane's admission reads, plus the conversation read and the two writes."""

    def comment_bodies(self, *, repository: str, number: int) -> tuple[str, ...]: ...

    def comment(self, *, repository: str, number: int, body: str) -> None: ...

    def close(self, *, repository: str, number: int) -> None: ...


class _HeldAdmission(Protocol):
    """The part of either lane's composed answer this act reads."""

    @property
    def refusals(self) -> tuple[str, ...]: ...
    @property
    def head_sha(self) -> str | None: ...
    @property
    def ceiling_breaches(self) -> tuple[HoldBreach, ...]: ...
    @property
    def update_bot_authored(self) -> bool: ...


@dataclass(frozen=True)
class HeldCloseCommand:
    repository: str
    pr_number: int
    actor: ActorContext
    # Must equal `held_close_key(repository, pr_number)`; see the module docstring.
    idempotency_key: str
    # The head the caller read, as every act on a pull request names it: the update bot rewriting
    # its branch between the read and this call is refused, and the next pass asks again.
    expected_head_sha: str


@dataclass(frozen=True)
class Ceiling:
    """One line of the comment: a held dependency and its ceiling."""

    dependency: str
    ceiling: str


@dataclass(frozen=True)
class HeldCloseOutcome:
    lane: str
    repository: str
    pull_request_number: int
    ceilings: tuple[Ceiling, ...]
    # False when the conversation already carried the comment from a close that failed after it.
    commented: bool
    # True when this answer came from the record, with nothing read or sent.
    replayed: bool


def held_close_key(repository: str, pr_number: int) -> str:
    """The one key a close of this pull request may carry."""
    return f"held-pr-close:{repository.lower()}#{pr_number}"


def close_inert_held_pull_request(
    session: Session,
    command: HeldCloseCommand,
    gateway: HeldCloseGateway,
    landing_source: EstateLandingSource,
    policy_source: InertLandingPolicySource,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> HeldCloseOutcome:
    """The inert lane's close: its population is the repositories its policy declares."""
    return _own_transaction(
        session,
        lambda: _close(
            session,
            command,
            INERT,
            gateway,
            admit=lambda repository: inert_landing_admission(
                session,
                repository,
                command.pr_number,
                landing_source,
                policy_source,
                gateway,
                enabled=enabled,
                credentials_configured=credentials_configured,
            ),
            # `policy_version` is None exactly when the policy could not be read, and then nobody
            # can say the repository is declared.
            in_population=lambda admission: (
                admission.policy_version is not None
                and INERT_LANDING_REPOSITORY_NOT_DECLARED not in admission.refusals
            ),
        ),
    )


def close_estate_held_pull_request(
    session: Session,
    command: HeldCloseCommand,
    gateway: HeldCloseGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    *,
    enabled: bool,
    credentials_configured: bool,
) -> HeldCloseOutcome:
    """The deploying lane's close: its population is the changes somebody routed, so the pull
    request must have an identified change record."""
    return _own_transaction(
        session,
        lambda: _close(
            session,
            command,
            ESTATE,
            gateway,
            admit=lambda repository: estate_landing_admission(
                session,
                repository,
                command.pr_number,
                landing_source,
                record_source,
                gateway,
                enabled=enabled,
                credentials_configured=credentials_configured,
            ),
            in_population=lambda admission: admission.change_record_id is not None,
        ),
    )


def _own_transaction(session: Session, act: Callable[[], HeldCloseOutcome]) -> HeldCloseOutcome:
    """Commit what the act recorded, or roll back everything it did not finish."""
    try:
        outcome = act()
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise


def _close[Admission: _HeldAdmission](
    session: Session,
    command: HeldCloseCommand,
    lane: str,
    gateway: HeldCloseGateway,
    *,
    admit: Callable[[str], Admission],
    in_population: Callable[[Admission], bool],
) -> HeldCloseOutcome:
    authorize_actor(command.actor, _VERB)
    repository = command.repository.lower()
    key = held_close_key(repository, command.pr_number)
    if command.idempotency_key != key:
        raise DomainError(
            "idempotency_conflict",
            "a held pull request's close is keyed by the pull request alone",
            f"send idempotency_key {key}",
        )

    lock_repository(session, _LOCK, repository)
    spent = session.scalar(select(Event).where(Event.idempotency_key == key))
    if spent is not None:
        return _replay(spent, key)

    admission = admit(repository)
    _refuse_unless_held(admission, in_population(admission))
    if admission.head_sha != command.expected_head_sha:
        raise DomainError(
            HELD_CLOSE_HEAD_MOVED,
            "the pull request's head is not the one the caller read",
            "re-read the landing-admission answer and ask again",
        )
    ceilings = _ceilings(admission.ceiling_breaches)
    try:
        commented = LIFT_SENTENCE not in "\n".join(
            gateway.comment_bodies(repository=repository, number=command.pr_number)
        )
        if commented:
            gateway.comment(
                repository=repository, number=command.pr_number, body=comment_body(ceilings)
            )
        gateway.close(repository=repository, number=command.pr_number)
    except EstateGatewayError as error:
        raise DomainError(
            HELD_CLOSE_REFUSED_BY_REMOTE,
            f"the held pull request was not closed: {gateway_failure_detail(error)}",
            "nothing was recorded; the next pass asks again and does not comment twice",
        ) from error

    session.add(
        Event(
            actor_id=command.actor.actor_id,
            action=HELD_CLOSE_ACTION,
            subject_type=f"{lane}_pull_request",
            subject_id=pull_request_subject_id(repository, command.pr_number),
            payload={
                "lane": lane,
                "repository": repository,
                "pr_number": command.pr_number,
                "head_sha": admission.head_sha,
                "commented": commented,
                "ceilings": [{"dependency": c.dependency, "ceiling": c.ceiling} for c in ceilings],
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=key,
        )
    )
    session.flush()
    return HeldCloseOutcome(lane, repository, command.pr_number, ceilings, commented, False)


def _refuse_unless_held(admission: _HeldAdmission, in_population: bool) -> None:
    """Every bound, each its own refusal, asked of the answer composed in this transaction."""
    refusals = set(admission.refusals)
    clauses = (
        (not refusals & _LANE_OFF, HELD_CLOSE_LANE_OFF, "the lane may not act"),
        (
            LANDING_VERSION_CEILING_EXCEEDED in refusals,
            HELD_CLOSE_NOT_HELD,
            "the lane's answer does not refuse this pull request for a hold",
        ),
        (
            admission.update_bot_authored,
            HELD_CLOSE_AUTHOR_NOT_UPDATE_BOT,
            "the update bot did not open this pull request",
        ),
        (in_population, HELD_CLOSE_OUTSIDE_POPULATION, "the repository is not this lane's"),
        (
            LANDING_PULL_REQUEST_NOT_OPEN not in refusals,
            HELD_CLOSE_NOT_OPEN,
            "the pull request is not open",
        ),
    )
    for held, code, message in clauses:
        if not held:
            raise DomainError(
                code,
                f"{message}: " + (", ".join(admission.refusals) or "no refusals"),
                "read the landing-admission answer",
            )


def _ceilings(breaches: Sequence[HoldBreach]) -> tuple[Ceiling, ...]:
    """Each held dependency and its ceiling once, in the order the matcher found them."""
    return tuple(dict.fromkeys(Ceiling(b.dependency, b.ceiling) for b in breaches))


def comment_body(ceilings: Sequence[Ceiling]) -> str:
    """The public comment: each held dependency and its ceiling, then the fixed sentence."""
    lines = [f"- {_code(c.dependency)}: ceiling {_code(c.ceiling)}" for c in ceilings]
    return "\n".join([*lines, "", LIFT_SENTENCE])


def _code(text: str) -> str:
    """`text` as one Markdown code span, whatever backticks it holds: the fence is one backtick
    longer than its longest run, and padded so a backtick at either end is not read as fence."""
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    fence = "`" * (longest + 1)
    padded = f" {text} " if text.startswith("`") or text.endswith("`") else text
    return f"{fence}{padded}{fence}"


def _replay(spent: Event, key: str) -> HeldCloseOutcome:
    """Already closed under this key: answer from the record. A key spent by another act is
    refused rather than read as this one."""
    payload = spent.payload if isinstance(spent.payload, dict) else {}
    if spent.action != HELD_CLOSE_ACTION:
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different act",
            f"nothing uses {key} but a held close",
        )
    rows = payload.get("ceilings")
    return HeldCloseOutcome(
        lane=str(payload.get("lane")),
        repository=str(payload.get("repository")),
        pull_request_number=int(payload.get("pr_number", 0)),
        ceilings=tuple(
            Ceiling(str(row.get("dependency")), str(row.get("ceiling")))
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        ),
        commented=bool(payload.get("commented")),
        replayed=True,
    )
