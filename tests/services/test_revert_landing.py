"""A recorded revert lands through the inert lane, the hold follows, and the version never lands
again. Landing Part 3a.

Everything runs with no network. Persistence is asserted through a second session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import EstatePrMerge, LandingRevert, SdsMemberHold
from orchestrator.services.landing.estate_landing import LANDING_INERT, EstateAnswer
from orchestrator.services.landing.inert_landing_admission import (
    LANDING_REVERT_NOT_EXACT_INVERSE,
    LANDING_REVERT_NOT_RECORDED,
    LANDING_REVERT_UNREADABLE,
    inert_landing_admission,
)
from orchestrator.services.landing.inert_pr_merge import InertMergeCommand, land_inert_pull_request
from orchestrator.services.landing.interfaces import (
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    BranchHead,
    MergeOutcome,
    Term,
    Tree,
    TreeEntry,
)
from orchestrator.services.landing.revert_build import REVERT_BRANCH_PREFIX
from orchestrator.services.landing.terms import (
    LANDING_VERSION_REVERTED,
    UpdateBumps,
    version_reverted_term,
)
from orchestrator.services.membership.holds import Bump
from tests.services.estate_doubles import FakeEstateLandingSource
from tests.services.estate_landing_doubles import HEAD, FakeEstateGateway, pull_request
from tests.services.inert_landing_doubles import (
    INERT_REPOSITORY,
    LANDED_COMMIT,
    ActingInertGateway,
    FakeInertPolicySource,
)
from tests.services.test_estate_landing_admission import REPOSITORY as ESTATE_REPOSITORY
from tests.services.test_estate_landing_admission import _ask as estate_answer
from tests.services.test_inert_landing_admission import _answer as inert_answer
from tests.services.test_inert_pr_merge import SYSTEM
from tests.services.test_member_holds import place_hold
from tests.services.test_member_proposals import grant_services
from tests.services.test_revert_hold import revert_record

ORIGINAL = 41
REVERT = 42
BRANCH = f"{REVERT_BRANCH_PREFIX}pr-{ORIGINAL}"
APP = "alobar-sds-dispatch[bot]"

PARENT = "p" * 40
MERGE = "m" * 40
BASE = "b" * 40


def _entry(sha: str) -> TreeEntry:
    return TreeEntry(mode="100644", type="blob", sha=sha)


def _trees(*, revert: dict[str, TreeEntry] | None = None) -> dict[str, Tree]:
    """The bump moved `uv.lock`; `README.md` changed on the default branch after it landed; the
    revert restores the lock on top of that."""
    parent = {"uv.lock": _entry("l1"), "README.md": _entry("r1")}
    merged = {"uv.lock": _entry("l2"), "README.md": _entry("r1")}
    base = {"uv.lock": _entry("l2"), "README.md": _entry("r2")}
    head = {"uv.lock": _entry("l1"), "README.md": _entry("r2")} if revert is None else revert
    return {
        PARENT: Tree("tp", parent),
        MERGE: Tree("tm", merged),
        BASE: Tree("tb", base),
        HEAD: Tree("th", head),
    }


def _revert_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "pull": pull_request(
            number=REVERT,
            head_ref=kwargs.pop("head_ref", BRANCH),
            title='Revert "build(deps): bump typer from 0.20.0 to 0.21.0"',
            author_login=APP,
            author_is_bot=kwargs.pop("author_is_bot", True),
        ),
        "base_head": BranchHead(name="main", sha=BASE),
        "trees": kwargs.pop("trees", _trees()),
        **kwargs,
    }


def _revert_gateway(**kwargs: Any) -> FakeEstateGateway:
    """The revert pull request, opened by the App on the record's branch, exactly inverse."""
    return FakeEstateGateway(**_revert_kwargs(kwargs))


def _acting_revert_gateway(**kwargs: Any) -> ActingInertGateway:
    return ActingInertGateway(**_revert_kwargs(kwargs))


def _opened(session: Session, **overrides: Any) -> LandingRevert:
    return revert_record(
        session,
        status="revert_opened",
        repository=INERT_REPOSITORY,
        pr_number=ORIGINAL,
        revert_pr_number=REVERT,
        **overrides,
    )


def _ask(session: Session, gateway: FakeEstateGateway):
    grant_services(session, INERT_REPOSITORY, "dependency_updates")
    return inert_landing_admission(
        session,
        INERT_REPOSITORY,
        REVERT,
        FakeEstateLandingSource(default=EstateAnswer(LANDING_INERT)),
        FakeInertPolicySource(),
        gateway,
        enabled=True,
        credentials_configured=True,
    )


def _with_merge_parent(row: LandingRevert) -> LandingRevert:
    row.merge_commit_sha, row.parent_sha = MERGE, PARENT
    return row


def test_a_recorded_exact_revert_is_admissible_without_the_bump_terms(
    migrated_session: Session,
) -> None:
    """The author is no account the policy names, the dependency is held, and its version was
    reverted: none of that is asked of a revert, which is not a bump."""
    _with_merge_parent(_opened(migrated_session))
    place_hold(migrated_session, INERT_REPOSITORY, ecosystem="uv", dependency="typer", ceiling="0")
    revert_record(migrated_session, repository=INERT_REPOSITORY, pr_number=7, revert_pr_number=8)
    gateway = _revert_gateway()

    answer = _ask(migrated_session, gateway)

    assert answer.refusals == ()
    assert answer.satisfied is True
    assert answer.head_sha == HEAD
    assert gateway.commit_reads == []
    assert gateway.tree_reads == [PARENT, MERGE, BASE, HEAD]


def test_an_unrecorded_sds_revert_branch_is_refused(migrated_session: Session) -> None:
    """No row; a row that is not `revert_opened`; a row for another branch; a person's account."""
    assert LANDING_REVERT_NOT_RECORDED in _ask(migrated_session, _revert_gateway()).refusals

    landed = revert_record(
        migrated_session, repository=INERT_REPOSITORY, pr_number=ORIGINAL, revert_pr_number=REVERT
    )
    answer = _ask(migrated_session, _revert_gateway())
    assert answer.satisfied is False
    assert LANDING_REVERT_NOT_RECORDED in answer.refusals
    landed.status = "revert_opened"
    migrated_session.flush()

    answer = _ask(migrated_session, _revert_gateway(head_ref=f"{REVERT_BRANCH_PREFIX}pr-40"))
    assert LANDING_REVERT_NOT_RECORDED in answer.refusals

    gateway = _revert_gateway(author_is_bot=False)
    answer = _ask(migrated_session, gateway)
    assert LANDING_REVERT_NOT_RECORDED in answer.refusals
    assert gateway.tree_reads == []

    # Control: the same row and branch, opened by a machine, is not refused this way.
    assert LANDING_REVERT_NOT_RECORDED not in _ask(migrated_session, _revert_gateway()).refusals


def test_a_revert_whose_head_is_no_longer_the_exact_inverse_is_refused(
    migrated_session: Session,
) -> None:
    _with_merge_parent(_opened(migrated_session))
    extra = {"uv.lock": _entry("l1"), "README.md": _entry("r2"), "pyproject.toml": _entry("x")}

    answer = _ask(migrated_session, _revert_gateway(trees=_trees(revert=extra)))

    assert answer.satisfied is False
    assert answer.refusals == (LANDING_REVERT_NOT_EXACT_INVERSE,)
    assert answer.branch_update_qualifies is False


def test_a_revert_behind_its_base_is_refused_as_behind_and_may_be_freshened(
    migrated_session: Session,
) -> None:
    """Freshness is asked BEFORE the inverse check: a merely-behind revert differs from its base on
    every path the base moved, and reporting that as not-an-inverse would never be freshened."""
    _with_merge_parent(_opened(migrated_session))
    gateway = _revert_gateway(behind=1)

    answer = _ask(migrated_session, gateway)

    assert answer.refusals == (LANDING_HEAD_NOT_CURRENT_WITH_BASE,)
    assert answer.branch_update_qualifies is True
    assert gateway.tree_reads == []


def test_a_revert_whose_trees_cannot_be_read_is_refused_by_its_own_name(
    migrated_session: Session,
) -> None:
    _with_merge_parent(_opened(migrated_session))

    answer = _ask(migrated_session, _revert_gateway(trees={}))

    assert answer.refusals == (LANDING_REVERT_UNREADABLE,)
    assert answer.satisfied is False


def test_landing_the_revert_records_it_and_places_the_hold_in_one_transaction(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    row = _with_merge_parent(_opened(migrated_session))
    row.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    migrated_session.commit()
    grant_services(migrated_session, INERT_REPOSITORY, "dependency_updates")
    gateway = _acting_revert_gateway()

    record = land_inert_pull_request(
        migrated_session,
        InertMergeCommand(INERT_REPOSITORY, REVERT, SYSTEM, "revert-1", HEAD),
        gateway,
        FakeEstateLandingSource(default=EstateAnswer(LANDING_INERT)),
        FakeInertPolicySource(),
        enabled=True,
        credentials_configured=True,
    )

    assert record.status == "merged"
    with Session(migrated_engine) as reader:
        stored = reader.scalars(select(LandingRevert)).one()
        assert stored.status == "revert_landed"
        assert stored.revert_merge_sha == LANDED_COMMIT
        assert stored.hold_outcome == "held uv / typer at 0.20.0"
        assert stored.updated_at > datetime(2026, 1, 1, tzinfo=UTC)
        hold = reader.scalars(select(SdsMemberHold)).one()
        assert (hold.dependency, hold.ceiling, hold.set_by) == ("typer", "0.20.0", "sds-revert")
        assert reader.scalars(select(EstatePrMerge)).one().pr_number == REVERT


def test_a_revert_the_remote_refused_records_no_landing_and_no_hold(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _with_merge_parent(_opened(migrated_session))
    migrated_session.commit()
    grant_services(migrated_session, INERT_REPOSITORY, "dependency_updates")
    gateway = _acting_revert_gateway(
        outcome=MergeOutcome(landed=False, commit_sha=None, status_code=405)
    )

    land_inert_pull_request(
        migrated_session,
        InertMergeCommand(INERT_REPOSITORY, REVERT, SYSTEM, "revert-1", HEAD),
        gateway,
        FakeEstateLandingSource(default=EstateAnswer(LANDING_INERT)),
        FakeInertPolicySource(),
        enabled=True,
        credentials_configured=True,
    )

    with Session(migrated_engine) as reader:
        stored = reader.scalars(select(LandingRevert)).one()
        assert (stored.status, stored.hold_outcome) == ("revert_opened", None)
        assert reader.scalars(select(SdsMemberHold)).all() == []


def test_a_reverted_version_is_refused_in_both_lanes(migrated_session: Session) -> None:
    """Both default subjects move alembic to 1.19.0. A landed revert of that version refuses it,
    by name, and an exception rather than a finding; a different version is a control."""
    for repository in (INERT_REPOSITORY, ESTATE_REPOSITORY):
        revert_record(
            migrated_session,
            repository=repository,
            bumps=[
                {
                    "ecosystem": "uv",
                    "directory": "/",
                    "dependency": "Alembic",
                    "from_version": "1.18.5",
                    "to_version": "1.19.0",
                }
            ],
        )

    inert = inert_answer(migrated_session)
    estate = estate_answer(migrated_session)

    assert (inert.satisfied, inert.refusals) == (False, (LANDING_VERSION_REVERTED,))
    assert (estate.satisfied, estate.refusals) == (False, (LANDING_VERSION_REVERTED,))


def test_a_different_version_of_a_reverted_dependency_still_lands(
    migrated_session: Session,
) -> None:
    revert_record(
        migrated_session,
        repository=INERT_REPOSITORY,
        bumps=[
            {
                "ecosystem": "uv",
                "directory": "/",
                "dependency": "alembic",
                "from_version": "1.18.5",
                "to_version": "1.18.9",
            }
        ],
    )
    revert_record(
        migrated_session,
        repository=INERT_REPOSITORY,
        pr_number=99,
        status="revert_opened",
        bumps=[
            {
                "ecosystem": "uv",
                "directory": "/",
                "dependency": "alembic",
                "from_version": "1.18.5",
                "to_version": "1.19.0",
            }
        ],
    )

    answer = inert_answer(migrated_session)

    assert answer.satisfied is True, answer.refusals


def test_an_unreadable_list_in_a_repository_with_a_reverted_version_refuses(
    migrated_session: Session,
) -> None:
    from orchestrator.services.landing.interfaces import LANDING_DEPENDENCY_LIST_UNREADABLE
    from tests.services.estate_landing_doubles import dependabot_commit

    revert_record(migrated_session, repository=INERT_REPOSITORY)
    gateway = FakeEstateGateway(
        pull=pull_request(number=3, head_ref="dependabot/uv/typer-0.21.0"),
        commits={3: (dependabot_commit(HEAD, "no trailer here"),)},
    )

    answer = inert_answer(migrated_session, gateway=gateway)

    assert answer.refusals == (LANDING_DEPENDENCY_LIST_UNREADABLE,)


def test_a_repository_with_holds_and_a_reverted_version_reads_the_list_once(
    migrated_session: Session,
) -> None:
    place_hold(migrated_session, INERT_REPOSITORY, ecosystem="uv", dependency="other", ceiling="1")
    revert_record(migrated_session, repository=INERT_REPOSITORY)
    gateway = FakeEstateGateway(pull=pull_request(number=3, head_ref="dependabot/uv/typer-0.21.0"))

    inert_answer(migrated_session, gateway=gateway)

    assert gateway.commit_reads == [(INERT_REPOSITORY, 3)]


GONE = Bump("uv", "/", "alembic", "1.19.0")


@pytest.mark.parametrize(
    ("offered", "gone", "refused"),
    [
        (Bump("uv", "/", "alembic", "1.19.0"), GONE, True),
        (Bump("UV", "/", "Alembic", "1.19.0"), GONE, True),
        (Bump("uv", "/", "alembic", "1.19.0.0"), GONE, True),
        (Bump("uv", "/", "alembic", "1.19.1"), GONE, False),
        (Bump("pip", "/", "alembic", "1.19.0"), GONE, False),
        (Bump("uv", "/", "alembic-utils", "1.19.0"), GONE, False),
        (Bump("uv", "/api", "alembic", "1.19.0"), GONE, False),
        (Bump("uv", "/api/", "alembic", "1.19.0"), Bump("uv", "/api", "alembic", "1.19.0"), True),
        # A side naming no directory matches any; a version either side can't read matches.
        (Bump("uv", None, "alembic", "1.19.0"), GONE, True),
        (Bump("uv", "/api", "alembic", "1.19.0"), Bump("uv", None, "alembic", "1.19.0"), True),
        (Bump("uv", "/", "alembic", ""), GONE, True),
        (Bump("uv", "/", "alembic", "1.19.1"), Bump("uv", "/", "alembic", "latest"), True),
    ],
)
def test_the_never_again_match(offered: Bump, gone: Bump, refused: bool) -> None:
    update = UpdateBumps(INERT_REPOSITORY, pull_request(), FakeEstateGateway())
    update.__dict__["bumps"] = (offered,)  # the cached read, so no gateway is asked

    term = version_reverted_term((gone,), update)

    assert term == (Term(False, (LANDING_VERSION_REVERTED,)) if refused else Term(True, ()))
