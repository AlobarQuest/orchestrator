"""The producer's composed row, put through the REAL ingestion route (G1+G2 increment 2).

**EVERY OTHER TEST OF THIS PAYLOAD MOCKS THE CLIENT THAT WOULD HAVE REFUSED IT**, which is exactly
how the revision watcher shipped a lane that measured six applications correctly and filed
nothing: `source_system` and `observation_type` are closed vocabularies backed by CHECK
constraints, neither had a member for that producer, and a 5197-test gate said nothing. The
vocabulary pin in `tests/bump_proposer/test_observation.py` closes the half a static test can see;
this closes the half it cannot, by letting the route, the request model, the service and the
database each have their say.

**AND IT IS WHERE THE REPLAY PROPERTY IS PROVEN RATHER THAN REASONED.** `record_observation`
returns the existing row for a repeat of `(source_system, source_reference,
normalized_fact_hash)`, so the producer names the observation it already filed instead of filing a
second one. That is the property that decides whether a scheduled producer can run twice, it is
invisible on a first pass, and a stub asserting it would only be asserting what the stub was
written to do.

A test may import both sides even where the packages may not -- the estate's arrangement for
holding a cross-program boundary to something.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from bump_proposer.observation import bump_observation
from landing_ledger.model import PendingUpdate, UpdateMetadata
from landing_ledger.titles import bump_of
from orchestrator.persistence.models import Observation
from tests.api.test_observer_role_confinement import OBSERVER

REPOSITORY = "AlobarQuest/infraops-mcp-server"
TITLE = "build(deps): bump zod from 3.25.76 to 4.4.3"


def _row(*, title: str = TITLE, number: int = 71, head: str = "b" * 40) -> dict[str, object]:
    bump = bump_of(title)
    assert bump is not None
    pending = PendingUpdate(
        repository=REPOSITORY,
        number=number,
        head_commit=head,
        opened_at=datetime(2026, 8, 1, 9, 30, tzinfo=UTC),
        armed=False,
        title=title,
        update=UpdateMetadata(
            dependency="zod", ecosystem="npm_and_yarn", update_type="version-update:semver-major"
        ),
    )
    return bump_observation(pending, bump)


def test_the_composed_row_is_ACCEPTED_by_the_real_route(db_client: TestClient) -> None:
    """A 201 here is the whole claim: the vocabulary members exist, the CHECK constraints admit
    them, the request model validates the payload and the OBSERVER credential may write it."""
    response = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row())

    assert response.status_code == 201, response.text
    assert response.json()["source_system"] == "bump_proposer"
    assert response.json()["observation_type"] == "dependency_update"


def test_the_route_answers_an_ID_the_producer_can_put_on_the_proposal(
    db_client: TestClient,
) -> None:
    """The client refuses a body without one rather than reading absence as "no cause"."""
    body = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row()).json()

    assert isinstance(body.get("id"), str)
    assert body["id"]


def test_a_SECOND_PASS_over_the_same_bump_names_the_SAME_row_rather_than_filing_another(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The property that decides whether this producer can run twice.

    Asserted on the DATABASE as well as on the response, because the response alone cannot tell a
    replay from a second row that happens to answer similarly -- and a second row is what the
    failure would actually be.
    """
    first = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row())
    second = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row())

    with Session(migrated_engine) as session:
        stored = session.scalar(
            select(func.count())
            .select_from(Observation)
            .where(Observation.source_system == "bump_proposer")
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert stored == 1


def test_a_REWRITTEN_bump_takes_its_own_row_rather_than_conflicting(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The update bot rewrites a pull request IN PLACE when a newer version appears. Both versions
    are in the reference, so that rewrite is a different bump and takes a different row -- where a
    reference naming only the pull request would reach the same-source/different-facts branch and
    refuse this pull request forever, with no supersession model and no delete route to undo it.
    """
    first = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row())
    moved = db_client.post(
        "/api/v1/observations",
        headers=OBSERVER,
        json=_row(title="build(deps): bump zod from 3.25.76 to 4.5.4"),
    )

    with Session(migrated_engine) as session:
        stored = session.scalar(
            select(func.count())
            .select_from(Observation)
            .where(Observation.source_system == "bump_proposer")
        )

    assert moved.status_code == 201, moved.text
    assert moved.json()["id"] != first.json()["id"]
    assert stored == 2


def test_a_REBASED_pull_request_is_still_the_same_row(db_client: TestClient) -> None:
    """`head_commit` moves on a rebase and the bump does not, so nothing that moves is in the row.
    A fact carrying the head would refuse on the next pass while the delta had not changed."""
    first = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row())
    rebased = db_client.post("/api/v1/observations", headers=OBSERVER, json=_row(head="f" * 40))

    assert rebased.status_code == 201
    assert rebased.json()["id"] == first.json()["id"]
