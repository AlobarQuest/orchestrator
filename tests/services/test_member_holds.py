"""Holds in the member record: staged like any change, written only when a person confirms."""

from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import Event, SdsMemberHold
from orchestrator.services.membership.holds import release
from orchestrator.services.membership.members import list_members
from orchestrator.services.membership.proposals import (
    confirm_member_proposal,
    read_digest,
)
from orchestrator.services.reporting.pending_decisions import pending_decisions
from tests.services.test_member_proposals import (
    HUMAN,
    add_member,
    body,
    confirm,
    stage,
)

REPOSITORY = "AlobarQuest/brain"


def hold(action: str = "place", **overrides: Any) -> dict[str, Any]:
    return {
        "ecosystem": "uv",
        "dependency": "sqlalchemy",
        "action": action,
        **({"ceiling": "2.0.54"} if action == "place" else {}),
        **overrides,
    }


def holds_change(*holds: dict[str, Any], reason: str = "2.0.55 broke the build") -> dict:
    return {"repository": REPOSITORY, "reason": reason, "holds": list(holds)}


def place(session: Session, key: str = "place", **overrides: Any) -> None:
    confirm(session, stage(session, body(holds_change(hold(**overrides))), key))


def place_hold(
    session: Session,
    repository: str,
    *,
    ecosystem: str,
    dependency: str,
    ceiling: str,
    directory: str = "/",
) -> None:
    """A member holding one dependency, written the only way a hold is: confirmed by a person."""
    add_member(session, repository)
    change = {
        "repository": repository,
        "reason": "held for a test",
        "holds": [
            hold(ecosystem=ecosystem, dependency=dependency, ceiling=ceiling, directory=directory)
        ],
    }
    confirm(session, stage(session, body(change), f"hold:{repository}:{dependency}"))


def stored(session: Session) -> list[tuple[str, str, str, str]]:
    return [
        (row.ecosystem, row.directory, row.dependency, row.ceiling)
        for row in session.scalars(select(SdsMemberHold).order_by(SdsMemberHold.dependency))
    ]


def refused(session: Session, *holds: dict[str, Any]) -> str:
    with pytest.raises(DomainError) as raised:
        stage(session, body(holds_change(*holds)), "refused")
    return raised.value.code


def test_a_confirmed_hold_is_read_back(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session)
        session.commit()

    with Session(migrated_engine) as session:
        [member] = list_members(session)
        [view] = member.holds
        assert (view.ecosystem, view.directory, view.dependency, view.ceiling) == (
            "uv",
            "/",
            "sqlalchemy",
            "2.0.54",
        )
        assert view.reason == "2.0.55 broke the build"
        assert view.set_by == "devon"
        event = session.scalars(select(Event).where(Event.action == "member.changed")).one()
        assert event.payload["holds"] == [
            {
                "ecosystem": "uv",
                "directory": "/",
                "dependency": "sqlalchemy",
                "old": None,
                "new": "2.0.54",
            }
        ]


def test_a_member_with_no_holds_reads_an_empty_tuple(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        session.commit()
        assert list_members(session)[0].holds == ()


def test_a_place_for_an_existing_key_replaces_its_ceiling(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session, "first")
        place(session, "second", ceiling="2.0.60")
        session.commit()
        assert stored(session) == [("uv", "/", "sqlalchemy", "2.0.60")]


def test_a_lift_removes_it(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session)
        confirm(session, stage(session, body(holds_change(hold("lift"), reason="fixed")), "lift"))
        session.commit()
        assert stored(session) == []
        [lifted] = [
            event
            for event in session.scalars(select(Event).where(Event.action == "member.changed"))
            if event.payload["reason"] == "fixed"
        ]
        assert lifted.payload["holds"] == [
            {
                "ecosystem": "uv",
                "directory": "/",
                "dependency": "sqlalchemy",
                "old": "2.0.54",
                "new": None,
            }
        ]


def test_lifting_a_hold_that_does_not_exist_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session)
        assert refused(session, hold("lift", dependency="httpx")) == "hold_lift_invalid"
        assert refused(session, hold("lift", directory="/sub")) == "hold_lift_invalid"


def test_lifting_on_a_new_member_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, pytest.raises(DomainError) as raised:
        stage(
            session,
            body(
                {
                    **holds_change(hold("lift")),
                    "repository": "AlobarQuest/new-app",
                    "state": "onboarding",
                    "owner": "devon",
                }
            ),
        )
    assert raised.value.code == "hold_lift_invalid"


def test_a_lift_that_names_a_ceiling_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session)
        assert refused(session, hold("lift", ceiling="2.0.54")) == "hold_lift_invalid"


@pytest.mark.parametrize(
    "ceiling", [None, "latest", "", "1!2.0", "2..0", "1.x", "1.2.*", "1.*", "1.2.x"]
)
def test_a_place_needs_a_parseable_ceiling(migrated_engine: Engine, ceiling: str | None) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        assert refused(session, hold(ceiling=ceiling)) == "hold_ceiling_unparseable"


@pytest.mark.parametrize("spelling", ["npm", "yarn", "pnpm", "github-actions"])
def test_tooling_spellings_are_refused(migrated_engine: Engine, spelling: str) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        assert refused(session, hold(ecosystem=spelling)) == "hold_ecosystem_misspelled"


def test_the_dependabot_spelling_is_accepted(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session, ecosystem="npm_and_yarn", dependency="zod", ceiling="v3.23.8")
        assert stored(session) == [("npm_and_yarn", "/", "zod", "v3.23.8")]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"dependency": " "}, "member_text_blank"),
        ({"ecosystem": ""}, "member_text_blank"),
        ({"directory": "sub"}, "hold_directory_invalid"),
        ({"directory": "/sub/"}, "hold_directory_invalid"),
        ({"directory": "//"}, "hold_directory_invalid"),
        ({"ecosystem": " uv"}, "hold_text_padded"),
        ({"ecosystem": "uv "}, "hold_text_padded"),
        ({"directory": "/ "}, "hold_text_padded"),
        ({"dependency": "sqlalchemy\t"}, "hold_text_padded"),
        ({"dependency": " sqlalchemy"}, "hold_text_padded"),
        ({"ceiling": "2.0.54 "}, "hold_text_padded"),
        ({"ceiling": " 2.0.54"}, "hold_text_padded"),
        ({"ecosystem": "UV"}, "hold_ecosystem_not_lowercase"),
        ({"ecosystem": "Npm_and_yarn"}, "hold_ecosystem_not_lowercase"),
    ],
)
def test_a_malformed_hold_is_refused(
    migrated_engine: Engine, overrides: dict[str, str], code: str
) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        assert refused(session, hold(**overrides)) == code


@pytest.mark.parametrize("directory", ["/", "/frontend", "/apps/web"])
def test_a_valid_hold_stages_and_confirms(migrated_engine: Engine, directory: str) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session, ecosystem="github_actions", directory=directory, dependency="actions/cache")
        assert stored(session) == [("github_actions", directory, "actions/cache", "2.0.54")]


def test_a_padded_hold_is_refused_at_confirm_too(migrated_engine: Engine) -> None:
    """The confirm re-validates, so a payload that bypassed staging is refused before any write."""
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        row = stage(session, body(holds_change(hold())))
        row.payload = {
            **row.payload,
            "changes": [
                {**row.payload["changes"][0], "holds": [{**hold(), "dependency": "sqlalchemy "}]}
            ],
        }
        session.flush()
        with pytest.raises(DomainError) as raised:
            confirm(session, row)
        assert raised.value.code == "hold_text_padded"
        assert stored(session) == []


def test_one_key_twice_in_one_change_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        assert refused(session, hold(), hold(ceiling="3.0")) == "hold_change_duplicated"


def test_a_hold_changed_after_render_fails_the_confirm_digest(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        place(session, "first")
        read = stage(session, body(holds_change(hold("lift"), reason="fixed")), "read")
        digest = read_digest(session, read)
        # Another proposal moves the same hold after the page was rendered.
        place(session, "moving", ceiling="2.0.60")

        with pytest.raises(DomainError) as raised:
            confirm_member_proposal(
                session, actor=HUMAN, proposal_id=read.id, expected_digest=digest
            )

        assert raised.value.code == "member_proposal_changed"
        assert stored(session) == [("uv", "/", "sqlalchemy", "2.0.60")]


@pytest.mark.parametrize(
    "role", [ActorRole.SYSTEM, ActorRole.WORKER, ActorRole.VERIFIER, ActorRole.OBSERVER]
)
def test_only_a_human_confirms_a_hold(migrated_engine: Engine, role: ActorRole) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        row = stage(session, body(holds_change(hold())))
        with pytest.raises(DomainError) as raised:
            confirm(session, row, ActorContext("machine", role))
        assert raised.value.code == "human_actor_required"
        assert stored(session) == []


def test_a_staged_member_proposal_is_on_the_review_queue(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, REPOSITORY)
        row = stage(session, body(holds_change(hold()), summary="hold sqlalchemy"))
        session.commit()
        [entry] = [
            e
            for e in pending_decisions(session, execution_stall_grace_seconds=900)
            if e["kind"] == "member_proposal"
        ]
        assert entry["href"] == f"/review/member-proposals/{row.id}"
        assert "hold sqlalchemy" in entry["subject"]

        confirm(session, row)
        session.commit()
        assert not [
            e
            for e in pending_decisions(session, execution_stall_grace_seconds=900)
            if e["kind"] == "member_proposal"
        ]


@pytest.mark.parametrize(
    ("version", "parsed"),
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.2.3", (1, 2, 3)),
        ("2.0.55rc1", (2, 0, 55)),
        ("2.0.55-beta.1", (2, 0, 55)),
        ("1.2.3+build.5", (1, 2, 3)),
        ("1.2.3.post1", (1, 2, 3)),
        ("10", (10,)),
        ("1.2.3.dev0", (1, 2, 3)),
        ("1.2.3.rc1", (1, 2, 3)),
        ("1.2.3.a1", (1, 2, 3)),
        ("1.2.3.b2", (1, 2, 3)),
        (" 1.2 ", None),
        ("1.x", None),
        ("1.2.*", None),
        ("1.*", None),
        ("1.2.X", None),
        ("1*", None),
        ("latest", None),
        ("", None),
        ("v", None),
        ("1!2.0", None),
        ("2..0", None),
        ("1.", None),
        (">=1.2", None),
        ("^1.2", None),
    ],
)
def test_release_reads_the_leading_numeric_release(
    version: str, parsed: tuple[int, ...] | None
) -> None:
    assert release(version) == parsed
