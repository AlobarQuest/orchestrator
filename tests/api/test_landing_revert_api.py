"""GET /api/v1/landing-watch and POST /api/v1/landing-revert (Landing Part 3a).

The wiring under test: each verdict path reaches its act through the route, the grant term and the
lane's switch refuse by name, and both bodies carry every field the service answers with (a
`response_model` drops what it does not declare, silently). No network is reached: the App gateway
is the recording double the service tests use.
"""

from collections.abc import Iterator
from dataclasses import fields
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.api.routes import landing as landing_routes
from orchestrator.config import Settings, get_settings
from orchestrator.main import create_app
from orchestrator.services.landing.revert_act import (
    LANDING_REVERT_LANE_OFF,
    RevertActOutcome,
    WatchedLanding,
)
from orchestrator.services.landing.terms import LANDING_REPOSITORY_NOT_GRANTED
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.estate_landing_doubles import job_id_of
from tests.services.inert_landing_doubles import INERT_REPOSITORY, LANDED_COMMIT
from tests.services.test_member_proposals import grant_services
from tests.services.test_revert_act import (
    BRAIN_BUMP,
    BUMP,
    MERGE_79,
    REVERT,
    RUN,
    BrainGateway,
    WatchGateway,
    everywhere,
    failing,
    job,
    landed_bump,
    landed_on_brain,
)

WATCH = "/api/v1/landing-watch"
REVERT_ROUTE = "/api/v1/landing-revert"


@pytest.fixture
def watch_client(
    auth_config: AuthConfig, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    slot: dict[str, Any] = {
        "gateway": WatchGateway(jobs=failing()),
        "estate": WatchGateway(),
        "inert_enabled": True,
        "estate_enabled": True,
    }
    monkeypatch.setattr(
        landing_routes, "GitHubInertPullRequests", lambda _provider: slot["gateway"]
    )
    monkeypatch.setattr(
        landing_routes, "GitHubEstatePullRequests", lambda _provider: slot["estate"]
    )
    with Session(migrated_engine) as seeding:
        landed_bump(seeding)
    app = create_app(auth_config)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    def runtime_settings() -> Settings:
        return Settings(
            database_url="postgresql+psycopg://postgres:postgres@127.0.0.1/orchestrator_test",
            github_app_id="123456",
            github_app_installation_id="78901234",
            github_app_private_key_b64=SecretStr("cGVt"),
            inert_landing_enabled=slot["inert_enabled"],
            estate_landing_enabled=slot["estate_enabled"],
        )

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = runtime_settings
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client, slot


def _watch(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(WATCH, headers=SYSTEM)
    assert response.status_code == 200, response.text
    return response.json()["landings"]


def _revert(client: TestClient, key: str) -> Any:
    """Ask as the lander does: naming the record status the watch served."""
    [served] = _watch(client)
    return client.post(
        REVERT_ROUTE,
        json={
            "repository": INERT_REPOSITORY,
            "pr_number": BUMP,
            "expected_status": served["status"],
            "idempotency_key": key,
        },
        headers=SYSTEM,
    )


def _acted(client: TestClient, key: str) -> dict[str, Any]:
    response = _revert(client, key)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_watch_serves_every_field_of_a_watched_landing(watch_client) -> None:
    client, _ = watch_client

    [served] = _watch(client)

    assert set(served) == {field.name for field in fields(WatchedLanding)}
    assert (served["verdict"], served["failed_runs"]) == ("flaky_retry", [[RUN, 1]])


def test_the_act_serves_every_field_of_its_outcome(watch_client) -> None:
    client, _ = watch_client

    body = _acted(client, "rerun")

    assert set(body) == {field.name for field in fields(RevertActOutcome)}


def test_a_flaky_landing_is_rerun_through_the_route(watch_client) -> None:
    client, slot = watch_client

    body = _acted(client, "rerun")

    assert (body["verdict"], body["acted"], body["status"]) == (
        "flaky_retry",
        True,
        "rerun_requested",
    )
    assert slot["gateway"].reruns == [job_id_of("test", RUN, 1)]
    assert _watch(client)[0]["status"] == "rerun_requested"


def test_a_bad_landing_opens_its_revert_through_the_route(watch_client) -> None:
    client, slot = watch_client
    _acted(client, "rerun")
    slot["gateway"].jobs = failing(attempt=2)

    body = _acted(client, "revert")

    assert (body["verdict"], body["status"], body["revert_pr_number"]) == (
        "bad",
        "revert_opened",
        REVERT,
    )
    [served] = _watch(client)
    assert (served["status"], served["revert_pr_number"]) == ("revert_opened", REVERT)


def test_a_clean_landing_is_answered_and_nothing_is_done(watch_client) -> None:
    client, slot = watch_client
    slot["gateway"].jobs[LANDED_COMMIT] = (job("test", "success"),)

    body = _acted(client, "clean")

    assert (body["verdict"], body["acted"], body["status"]) == ("clean", False, None)
    assert slot["gateway"].reruns == []


def test_a_repository_the_record_does_not_grant_is_refused_by_name(
    watch_client, migrated_engine: Engine
) -> None:
    client, slot = watch_client
    with Session(migrated_engine) as seeding:
        grant_services(seeding, INERT_REPOSITORY, "dependency_updates", state="withdrawn")
        seeding.commit()

    response = _revert(client, "refused")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == LANDING_REPOSITORY_NOT_GRANTED
    assert slot["gateway"].reruns == []


def test_a_deployment_without_app_credentials_refuses_by_name(db_client: TestClient) -> None:
    response = db_client.post(
        REVERT_ROUTE,
        json={
            "repository": INERT_REPOSITORY,
            "pr_number": BUMP,
            "expected_status": None,
            "idempotency_key": "k",
        },
        headers=SYSTEM,
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == LANDING_REVERT_LANE_OFF


def test_a_repository_that_is_not_owner_slash_name_is_unusable_input(
    db_client: TestClient,
) -> None:
    response = db_client.post(
        REVERT_ROUTE,
        json={
            "repository": "../../etc",
            "pr_number": BUMP,
            "expected_status": None,
            "idempotency_key": "k",
        },
        headers=SYSTEM,
    )

    assert response.status_code == 422


def test_the_watch_refuses_by_name_without_app_credentials(db_client: TestClient) -> None:
    response = db_client.get(WATCH, headers=SYSTEM)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == LANDING_REVERT_LANE_OFF


def test_a_landing_that_redeploys_is_read_through_the_estate_gateway(
    watch_client, migrated_engine: Engine
) -> None:
    client, slot = watch_client
    with Session(migrated_engine) as seeding:
        landed_on_brain(seeding)
        everywhere(seeding, MERGE_79, minutes_ago=120)
        seeding.commit()
    slot["estate"] = BrainGateway()

    served = {row["pr_number"]: row for row in _watch(client)}

    assert served[BRAIN_BUMP]["verdict"] == "clean"
    assert [number for _, number in slot["estate"].reads] == [BRAIN_BUMP]
    assert [number for _, number in slot["gateway"].reads] == [BUMP]


def test_each_lane_is_under_its_own_switch(watch_client, migrated_engine: Engine) -> None:
    client, slot = watch_client
    with Session(migrated_engine) as seeding:
        landed_on_brain(seeding)
        everywhere(seeding, MERGE_79, minutes_ago=120)
        seeding.commit()
    slot["estate"] = BrainGateway()
    slot["estate_enabled"] = False

    served = {row["pr_number"]: row for row in _watch(client)}

    assert (served[BUMP]["verdict"], served[BRAIN_BUMP]["verdict"]) == ("flaky_retry", "unreadable")
    assert served[BRAIN_BUMP]["detail"] == LANDING_REVERT_LANE_OFF
    assert slot["estate"].reads == []

    slot["inert_enabled"], slot["estate_enabled"] = False, True
    served = {row["pr_number"]: row for row in _watch(client)}
    assert (served[BUMP]["verdict"], served[BRAIN_BUMP]["verdict"]) == ("unreadable", "clean")

    slot["estate_enabled"] = False
    response = client.get(WATCH, headers=SYSTEM)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == LANDING_REVERT_LANE_OFF
