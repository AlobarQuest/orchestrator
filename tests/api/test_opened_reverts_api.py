"""GET /api/v1/opened-reverts (Landing Part 3b): the reverts the SDS has opened and not yet landed.

`change_proposer` files a revert pull request's change record only when the orchestrator names it
here, and it reads with the observer bearer. So this route is the narrow read the observer may
make: database rows only, no App read, and nothing a caller supplies reaches a query.
"""

from __future__ import annotations

from dataclasses import fields

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert
from orchestrator.services.landing.revert_record import OpenedRevert
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER
from tests.api.test_membership_api import OBSERVER

ROUTE = "/api/v1/opened-reverts"


def _revert(**overrides: object) -> LandingRevert:
    row: dict[str, object] = {
        "repository": "alobarquest/brain",
        "pr_number": 79,
        "merge_commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "failed_job_names": ["deploy"],
        "failed_run_id": 1,
        "status": "revert_opened",
        "revert_pr_number": 91,
        "bumps": [],
    }
    row.update(overrides)
    return LandingRevert(**row)


def _seed(engine: Engine, *rows: LandingRevert) -> None:
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def test_the_observer_reads_each_opened_revert(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    _seed(migrated_engine, _revert())

    response = db_client.get(ROUTE, headers=OBSERVER)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "reverts": [{"repository": "alobarquest/brain", "pr_number": 79, "revert_pr_number": 91}]
    }


def test_only_an_opened_revert_is_named(db_client: TestClient, migrated_engine: Engine) -> None:
    """A revert that landed, or one never opened, names no pull request the producer may file:
    `revert_landed` is done, and `rerun_requested` or `repointed` have no revert pull request."""
    _seed(
        migrated_engine,
        _revert(),
        _revert(pr_number=80, merge_commit_sha="c" * 40, status="revert_landed"),
        _revert(
            pr_number=81, merge_commit_sha="d" * 40, status="rerun_requested", revert_pr_number=None
        ),
        _revert(pr_number=82, merge_commit_sha="e" * 40, status="repointed", revert_pr_number=None),
    )

    reverts = db_client.get(ROUTE, headers=OBSERVER).json()["reverts"]

    assert [r["pr_number"] for r in reverts] == [79]


def test_every_role_may_read_and_an_unauthenticated_caller_may_not(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    _seed(migrated_engine, _revert())

    for headers in (SYSTEM, HUMAN, WORKER, OBSERVER):
        assert db_client.get(ROUTE, headers=headers).status_code == 200
    assert db_client.get(ROUTE).status_code == 401


def test_the_observer_still_may_not_write_here(db_client: TestClient) -> None:
    """The observer's write surface is unchanged: a GET route answers a POST with no write."""
    assert db_client.post(ROUTE, headers=OBSERVER, json={}).status_code in {403, 405}


def test_the_body_carries_every_field_the_service_answers_with(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """A `response_model` drops what it does not declare, silently."""
    _seed(migrated_engine, _revert())

    served = db_client.get(ROUTE, headers=SYSTEM).json()["reverts"][0]

    assert set(served) == {field.name for field in fields(OpenedRevert)}
