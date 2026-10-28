"""The held-close act: an update-bot pull request a hold refuses is closed, once, with a comment.

Bridge layer Part 4, Ruling 7. Everything runs with no network. Most assertions are about what was
NOT sent: a comment on a pull request the lane does not own, a second comment, a close the
re-composed answer does not support.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services.landing.change_record import ChangeRecordAnswer
from orchestrator.services.landing.estate_landing import EstateAnswer
from orchestrator.services.landing.estate_pr_merge import GitHubEstatePullRequests
from orchestrator.services.landing.held_close import (
    HELD_CLOSE_ACTION,
    HELD_CLOSE_AUTHOR_NOT_UPDATE_BOT,
    HELD_CLOSE_HEAD_MOVED,
    HELD_CLOSE_LANE_OFF,
    HELD_CLOSE_NOT_HELD,
    HELD_CLOSE_NOT_OPEN,
    HELD_CLOSE_OUTSIDE_POPULATION,
    HELD_CLOSE_REFUSED_BY_REMOTE,
    LIFT_SENTENCE,
    Ceiling,
    HeldCloseCommand,
    close_estate_held_pull_request,
    close_inert_held_pull_request,
    held_close_key,
)
from orchestrator.services.landing.inert_landing_policy import InertLandingAnswer
from orchestrator.services.landing.interfaces import EstateGatewayError
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import LANDING_REDEPLOYS, FakeEstateLandingSource
from tests.services.estate_landing_doubles import (
    HEAD,
    REPOSITORY,
    FakeEstateGateway,
    approved,
    dependabot_commit,
    pull_request,
    trailer,
)
from tests.services.inert_landing_doubles import (
    INERT_REPOSITORY,
    FakeInertPolicySource,
    rules,
)
from tests.services.test_member_proposals import add_member, body, confirm, stage

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

PR = 3
UV_BRANCH = "dependabot/uv/typer-0.21.0"
TYPER_TITLE = "build(deps): bump typer from 0.20.0 to 0.21.0"
GROUP_TITLE = "build(deps): bump the python group with 2 updates"
# A reason a person might write, which must never reach a public comment.
PRIVATE_REASON = "0.21 leaks the session cookie into logs; see the incident"


def _hold(
    session: Session, repository: str, dependency: str, ceiling: str, *, key: str = "h"
) -> None:
    """A hold confirmed the only way one is written: staged, then confirmed by a person."""
    add_member(session, repository)
    hold = {"ecosystem": "uv", "dependency": dependency, "action": "place", "ceiling": ceiling}
    change: dict[str, Any] = {"repository": repository, "reason": PRIVATE_REASON, "holds": [hold]}
    confirm(session, stage(session, body(change), f"hold:{key}"))
    # Committed, as a confirmed hold is: a refused close rolls its own transaction back.
    session.commit()


class ClosingGateway(FakeEstateGateway):
    """The reading gateway plus the conversation read and the two writes, in one ordered log."""

    def __init__(
        self,
        *,
        existing_comments: tuple[str, ...] = (),
        comment_error: EstateGatewayError | None = None,
        close_error: EstateGatewayError | None = None,
        comments_error: EstateGatewayError | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.posted: list[str] = list(existing_comments)
        self.sent: list[tuple[str, str, int]] = []
        self._comment_error = comment_error
        self._close_error = close_error
        self._comments_error = comments_error

    def comment_bodies(self, *, repository: str, number: int) -> tuple[str, ...]:
        if self._comments_error is not None:
            raise self._comments_error
        return tuple(self.posted)

    def comment(self, *, repository: str, number: int, body: str) -> None:
        if self._comment_error is not None:
            raise self._comment_error
        self.sent.append(("comment", repository, number))
        self.posted.append(body)

    def close(self, *, repository: str, number: int) -> None:
        if self._close_error is not None:
            raise self._close_error
        self.sent.append(("close", repository, number))


def _typer_gateway(*, title: str = TYPER_TITLE, message: str | None = None, **kwargs: Any):
    return ClosingGateway(
        pull=kwargs.pop("pull", None) or pull_request(number=PR, head_ref=UV_BRANCH, title=title),
        commits={
            PR: (dependabot_commit(HEAD, message or trailer(("typer", "0.21.0"), title=title)),)
        },
        **kwargs,
    )


def _command(
    *,
    repository: str = INERT_REPOSITORY,
    number: int = PR,
    key: str | None = None,
    actor: ActorContext = SYSTEM,
    head: str = HEAD,
) -> HeldCloseCommand:
    return HeldCloseCommand(
        repository=repository,
        pr_number=number,
        actor=actor,
        idempotency_key=key if key is not None else held_close_key(repository, number),
        expected_head_sha=head,
    )


def _close_inert(
    session: Session,
    gateway: ClosingGateway,
    *,
    command: HeldCloseCommand | None = None,
    policy: FakeInertPolicySource | None = None,
    enabled: bool = True,
    credentials_configured: bool = True,
):
    return close_inert_held_pull_request(
        session,
        command or _command(),
        gateway,
        FakeEstateLandingSource(),
        policy or FakeInertPolicySource(),
        enabled=enabled,
        credentials_configured=credentials_configured,
    )


def _events(engine: Engine) -> list[Event]:
    with Session(engine) as reader:
        return list(reader.scalars(select(Event).where(Event.action == HELD_CLOSE_ACTION)))


def _refused(call) -> str:
    with pytest.raises(DomainError) as raised:
        call()
    return raised.value.code


# ---------------------------------------------------------------------------------------------
# The affirmative case.
# ---------------------------------------------------------------------------------------------


def test_a_held_bump_is_commented_then_closed_and_recorded(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, "AlobarQuest/Orchestrator", "typer", "0.20.5")
    gateway = _typer_gateway()

    outcome = _close_inert(migrated_session, gateway)

    assert gateway.sent == [
        ("comment", INERT_REPOSITORY, PR),
        ("close", INERT_REPOSITORY, PR),
    ]
    assert outcome.ceilings == (Ceiling("typer", "0.20.5"),)
    assert outcome.commented is True
    assert outcome.replayed is False
    # THROUGH A SECOND SESSION: the event is committed, not merely flushed.
    events = _events(migrated_engine)
    assert len(events) == 1
    assert events[0].idempotency_key == f"held-pr-close:{INERT_REPOSITORY}#{PR}"
    assert events[0].payload["ceilings"] == [{"dependency": "typer", "ceiling": "0.20.5"}]
    assert events[0].payload["lane"] == "inert"


def test_the_comment_names_each_held_dependency_and_ceiling_and_nothing_private(
    migrated_session: Session,
) -> None:
    """A public comment: the evidence-pack redaction rule. The reason and the person who placed
    the hold never appear, nor in the recorded event."""
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5", key="a")
    message = trailer(("click", "8.2.0"), ("typer", "0.21.0"), title=GROUP_TITLE)
    gateway = _typer_gateway(title=GROUP_TITLE, message=message)

    _close_inert(migrated_session, gateway)

    (comment,) = gateway.posted
    assert comment == f"- `typer`: ceiling 0.20.5\n\n{LIFT_SENTENCE}"
    assert "click" not in comment
    for private in (PRIVATE_REASON, "devon", "orchestrator-system"):
        assert private not in comment


def test_a_held_pull_request_with_failing_checks_is_still_closed(
    migrated_session: Session,
) -> None:
    """Every refusal may sit beside the ceiling but the five bounds."""
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway(behind=2)

    _close_inert(migrated_session, gateway)

    assert [act for act, _, _ in gateway.sent] == ["comment", "close"]


# ---------------------------------------------------------------------------------------------
# The bounds. Each refusal sends nothing and records nothing.
# ---------------------------------------------------------------------------------------------


def _assert_untouched(gateway: ClosingGateway, engine: Engine) -> None:
    assert gateway.sent == []
    assert _events(engine) == []


def test_a_pull_request_the_answer_does_not_refuse_for_a_hold_is_not_closed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.21.0")
    gateway = _typer_gateway()

    assert _refused(lambda: _close_inert(migrated_session, gateway)) == HELD_CLOSE_NOT_HELD
    _assert_untouched(gateway, migrated_engine)


def test_an_unreadable_dependency_list_is_a_finding_and_is_never_closed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """`landing_dependency_list_unreadable` is a hold-related refusal and still not a hold."""
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway(message="no trailer here")

    assert _refused(lambda: _close_inert(migrated_session, gateway)) == HELD_CLOSE_NOT_HELD
    _assert_untouched(gateway, migrated_engine)


def test_a_pull_request_the_update_bot_did_not_open_is_not_closed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    pull = pull_request(
        number=PR,
        head_ref=UV_BRANCH,
        title=TYPER_TITLE,
        author_login="someone",
        author_is_bot=False,
    )
    gateway = _typer_gateway(pull=pull)

    code = _refused(lambda: _close_inert(migrated_session, gateway))

    assert code == HELD_CLOSE_AUTHOR_NOT_UPDATE_BOT
    _assert_untouched(gateway, migrated_engine)


def test_a_repository_outside_the_declared_population_is_not_closed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway()
    policy = FakeInertPolicySource(InertLandingAnswer(rules(repositories=frozenset({"x/y"}))))

    code = _refused(lambda: _close_inert(migrated_session, gateway, policy=policy))

    assert code == HELD_CLOSE_OUTSIDE_POPULATION
    _assert_untouched(gateway, migrated_engine)


def test_an_unreadable_policy_is_not_a_population(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway()
    policy = FakeInertPolicySource(InertLandingAnswer(None, "unreadable"))

    code = _refused(lambda: _close_inert(migrated_session, gateway, policy=policy))

    assert code == HELD_CLOSE_OUTSIDE_POPULATION
    _assert_untouched(gateway, migrated_engine)


@pytest.mark.parametrize(
    ("enabled", "credentials"), [(False, True), (True, False)], ids=["off", "no-credentials"]
)
def test_a_lane_that_may_not_act_closes_nothing(
    migrated_session: Session, migrated_engine: Engine, enabled: bool, credentials: bool
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway()

    code = _refused(
        lambda: _close_inert(
            migrated_session, gateway, enabled=enabled, credentials_configured=credentials
        )
    )

    assert code == HELD_CLOSE_LANE_OFF
    _assert_untouched(gateway, migrated_engine)


def test_a_pull_request_no_longer_open_is_not_closed_again(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    pull = pull_request(number=PR, head_ref=UV_BRANCH, title=TYPER_TITLE, is_open=False)
    gateway = _typer_gateway(pull=pull)

    assert _refused(lambda: _close_inert(migrated_session, gateway)) == HELD_CLOSE_NOT_OPEN
    _assert_untouched(gateway, migrated_engine)


def test_only_the_system_actor_may_close(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway()

    code = _refused(lambda: _close_inert(migrated_session, gateway, command=_command(actor=HUMAN)))

    assert code == "role_forbidden"
    _assert_untouched(gateway, migrated_engine)


def test_a_key_that_is_not_the_pull_requests_own_is_refused(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A fresh key would otherwise post a second comment on a pull request already closed."""
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway()

    code = _refused(lambda: _close_inert(migrated_session, gateway, command=_command(key="new")))

    assert code == "idempotency_conflict"
    _assert_untouched(gateway, migrated_engine)


# ---------------------------------------------------------------------------------------------
# Once per pull request.
# ---------------------------------------------------------------------------------------------


def test_a_replay_answers_from_the_record_and_touches_github_no_more(
    migrated_session: Session,
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    _close_inert(migrated_session, _typer_gateway())
    again = _typer_gateway()

    outcome = _close_inert(migrated_session, again)

    assert outcome.replayed is True
    assert outcome.ceilings == (Ceiling("typer", "0.20.5"),)
    assert again.sent == []
    assert again.reads == []


def test_a_close_that_fails_after_its_comment_comments_once_across_two_calls(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    failing = _typer_gateway(close_error=EstateGatewayError("close_status", 502))

    code = _refused(lambda: _close_inert(migrated_session, failing))

    assert code == HELD_CLOSE_REFUSED_BY_REMOTE
    assert _events(migrated_engine) == []
    second = _typer_gateway(existing_comments=tuple(failing.posted))
    outcome = _close_inert(migrated_session, second)
    assert second.sent == [("close", INERT_REPOSITORY, PR)]
    assert outcome.commented is False
    assert len(_events(migrated_engine)) == 1


def test_a_conversation_that_cannot_be_read_comments_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway(comments_error=EstateGatewayError("list_status", 500))

    code = _refused(lambda: _close_inert(migrated_session, gateway))

    assert code == HELD_CLOSE_REFUSED_BY_REMOTE
    _assert_untouched(gateway, migrated_engine)


# ---------------------------------------------------------------------------------------------
# The deploying lane: its population is the routed change record.
# ---------------------------------------------------------------------------------------------

ESTATE_PR = 49


def _close_estate(
    session: Session, gateway: ClosingGateway, record: ChangeRecordAnswer | None = None
):
    return close_estate_held_pull_request(
        session,
        _command(repository=REPOSITORY, number=ESTATE_PR),
        gateway,
        FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS)),
        FakeChangeRecordSource({(REPOSITORY, ESTATE_PR): record or approved()}),
        enabled=True,
        credentials_configured=True,
    )


def test_the_deploying_lane_closes_a_held_bump_with_a_routed_record(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, "AlobarQuest/Change-Manager", "alembic", "1.18.9")
    gateway = ClosingGateway()

    outcome = _close_estate(migrated_session, gateway)

    assert gateway.sent == [("comment", REPOSITORY, ESTATE_PR), ("close", REPOSITORY, ESTATE_PR)]
    assert outcome.ceilings == (Ceiling("alembic", "1.18.9"),)
    assert _events(migrated_engine)[0].payload["lane"] == "estate"


def test_the_deploying_lane_does_not_close_a_pull_request_nobody_routed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, "AlobarQuest/Change-Manager", "alembic", "1.18.9")
    gateway = ClosingGateway()

    code = _refused(lambda: _close_estate(migrated_session, gateway, ChangeRecordAnswer(True)))

    assert code == HELD_CLOSE_OUTSIDE_POPULATION
    _assert_untouched(gateway, migrated_engine)


def test_the_landers_key_is_the_orchestrators_key() -> None:
    """Two copies across the process boundary: the landers may not import the orchestrator."""
    from lander.core import held_close_key as landers_key

    for repository, number in (("AlobarQuest/Orchestrator", 3), ("a/b", 12345)):
        assert landers_key(repository, number) == held_close_key(repository, number)


# ---------------------------------------------------------------------------------------------
# The REAL gateway's two writes and the conversation read: what leaves the process, and which
# status is believed.
# ---------------------------------------------------------------------------------------------


class _Wire:
    """Stands in for one module-level httpx call, recording what was sent."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, url, *, headers, timeout, json=None):
        self.calls.append((url, json))
        return httpx.Response(self.status, json=self.body, request=httpx.Request("X", url))


def _real(monkeypatch: pytest.MonkeyPatch, verb: str, status: int, body: Any = None):
    wire = _Wire(status, body)
    monkeypatch.setattr(httpx, verb, wire)
    return GitHubEstatePullRequests(lambda: "t"), wire


def test_the_comment_posts_to_the_pull_requests_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, wire = _real(monkeypatch, "post", 201, {"id": 1})

    gateway.comment(repository="a/b", number=7, body="text")

    assert wire.calls == [("https://api.github.com/repos/a/b/issues/7/comments", {"body": "text"})]


def test_the_close_sets_the_pull_request_closed_and_believes_only_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, wire = _real(monkeypatch, "patch", 200, {"state": "closed"})

    gateway.close(repository="a/b", number=7)

    assert wire.calls == [("https://api.github.com/repos/a/b/pulls/7", {"state": "closed"})]
    still_open, _ = _real(monkeypatch, "patch", 200, {"state": "open"})
    with pytest.raises(EstateGatewayError):
        still_open.close(repository="a/b", number=7)


@pytest.mark.parametrize(("verb", "status"), [("post", 200), ("post", 403), ("patch", 422)])
def test_a_write_answered_with_any_other_status_raises(
    monkeypatch: pytest.MonkeyPatch, verb: str, status: int
) -> None:
    gateway, _ = _real(monkeypatch, verb, status, {"state": "closed"})
    with pytest.raises(EstateGatewayError):
        if verb == "post":
            gateway.comment(repository="a/b", number=7, body="x")
        else:
            gateway.close(repository="a/b", number=7)


def test_the_conversation_is_read_whole_and_a_row_without_text_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, wire = _real(monkeypatch, "get", 200, [{"body": "one"}, {"body": "two"}])

    assert gateway.comment_bodies(repository="a/b", number=7) == ("one", "two")
    assert wire.calls[0][0].startswith("https://api.github.com/repos/a/b/issues/7/comments?")
    broken, _ = _real(monkeypatch, "get", 200, [{"body": None}])
    with pytest.raises(EstateGatewayError):
        broken.comment_bodies(repository="a/b", number=7)


def test_a_head_the_caller_did_not_read_is_not_closed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _hold(migrated_session, INERT_REPOSITORY, "typer", "0.20.5")
    gateway = _typer_gateway()

    code = _refused(
        lambda: _close_inert(migrated_session, gateway, command=_command(head="f" * 40))
    )

    assert code == HELD_CLOSE_HEAD_MOVED
    _assert_untouched(gateway, migrated_engine)
