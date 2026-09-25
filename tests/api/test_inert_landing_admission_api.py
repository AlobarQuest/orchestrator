"""GET /api/v1/inert-pr-merge-admission -- the served surface of the composed answer.
ADR-0038 part 2.

**A `response_model` drops what it does not declare, silently and with no error**, so a field
added to the service alone passes every service-level assertion and reaches no caller. This estate
has shipped that defect twice: once on the runner brief, and once on this answer's own sibling,
where the enumerating agent read `branch_update_qualifies`, got nothing, and skipped every record
for two days while reporting zero. The set-equality assertion below is what makes the next addition
fall through visibly rather than quietly.

No network is reached. The client configures no App credentials, so the gateway raises before it
can send anything and every remote term refuses -- which is the answer under test, since these
fields must be served on a REFUSING answer as much as on a permitting one.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import orchestrator.api.routes as routes
from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.config import Settings, get_settings
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.main import create_app
from orchestrator.services.estate_landing_admission import EstateGatewayError
from orchestrator.services.inert_pr_branch_update import (
    INERT_BRANCH_UPDATE_SIBLING_HOLDING,
    INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    InertBranchUpdateCommand,
    update_inert_pull_request_branch,
)
from orchestrator.services.lifecycle import ActorContext
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.estate_doubles import inert_source
from tests.services.estate_landing_doubles import (
    HEAD,
    SiblingGateway,
    foreign_commit,
    pull_request,
)
from tests.services.inert_landing_doubles import INERT_REPOSITORY, FakeInertPolicySource

ROUTE = "/api/v1/inert-pr-merge-admission"


def _admission(db_client: TestClient) -> dict[str, Any]:
    response = db_client.get(
        ROUTE,
        params={"repository": "alobarquest/orchestrator", "pr_number": 3},
        headers=SYSTEM,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_body_carries_every_field_of_the_composed_answer(db_client: TestClient) -> None:
    """SET EQUALITY over the served keys rather than a membership check for one field: the
    silent-drop hole belongs to the model, not to any particular field."""
    from orchestrator.services.inert_landing_admission import InertLandingAdmission

    assert set(_admission(db_client)) == set(InertLandingAdmission.__dataclass_fields__)


def test_the_body_carries_the_branch_update_permission_a_freshening_pass_acts_on(
    db_client: TestClient,
) -> None:
    """The half of the answer that is not "may this land". A caller cannot compose it for itself,
    so undeclared it would read as absent and the freshening pass would skip every subject."""
    body = _admission(db_client)

    assert "branch_update_qualifies" in body
    assert body["branch_update_qualifies"] is False


def test_an_unconfigured_deployment_refuses_by_name_rather_than_erroring(
    db_client: TestClient,
) -> None:
    """Every refusal is a named string on a 200, never an exception: only `DomainError` and
    `APIAuthenticationError` have handlers, so anything else here would be a bare 500 from a gate
    that has stopped deciding."""
    body = _admission(db_client)

    assert body["satisfied"] is False
    assert "landing_not_enabled" in body["refusals"]
    assert "landing_app_credentials_missing" in body["refusals"]
    # The policy and the estate are both unconfigured in this client, and each says so in its own
    # words -- three causes, three different people.
    assert "inert_landing_policy_source_unconfigured" in body["refusals"]
    assert "landing_estate_source_unconfigured" in body["refusals"]


def test_the_route_reports_the_repository_it_was_asked_about_folded(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        ROUTE,
        params={"repository": "AlobarQuest/Orchestrator", "pr_number": 3},
        headers=SYSTEM,
    )

    assert response.status_code == 200, response.text
    assert response.json()["repository"] == "alobarquest/orchestrator"


def test_a_repository_that_is_not_owner_slash_name_is_refused_by_the_schema(
    db_client: TestClient,
) -> None:
    """Bounded in SHAPE because it is interpolated into GitHub API paths called with the App
    installation token."""
    response = db_client.get(
        ROUTE, params={"repository": "../../etc", "pr_number": 3}, headers=SYSTEM
    )

    assert response.status_code == 422


def test_the_body_names_how_the_landing_would_be_performed(db_client: TestClient) -> None:
    """Served on a REFUSING answer as much as on a permitting one, and never empty: a report-only
    pass reads this to say what a live pass would do, and a caller meeting an absent or invented
    value could not report anything. This answer refuses -- no credentials are configured, so the
    pull request is unreadable -- and still names the ordinary method."""
    from orchestrator.services.estate_pr_merge import SQUASH

    assert _admission(db_client)["merge_method"] == SQUASH


def test_a_body_that_observed_no_sibling_serves_the_withheld_fact_as_false(
    db_client: TestClient,
) -> None:
    """ADR-0045. No credentials, so nothing qualifies and no sibling scan runs -- and the key is
    still served."""
    body = _admission(db_client)

    assert body["branch_update_withheld_for_sibling"] is False


# --------------------------------------------------------------------------------------------
# ADR-0045 on the wire, driven to a TRUE answer: a field that only ever serializes False is
# unproven.
# --------------------------------------------------------------------------------------------

TARGET = 3
SIBLING = 4
SIBLING_HEAD = "b" * 40


def _sibling_gateway(*, sibling_edited: bool, **kwargs) -> SiblingGateway:
    return SiblingGateway(
        target=TARGET,
        pulls={
            TARGET: pull_request(number=TARGET, head_ref="dependabot/uv/typer-0.21.0"),
            SIBLING: pull_request(
                number=SIBLING, head_sha=SIBLING_HEAD, head_ref="dependabot/uv/alembic-1.19.0"
            ),
        },
        behind={TARGET: 2, SIBLING: 2},
        commits={SIBLING: (foreign_commit(SIBLING_HEAD),)} if sibling_edited else {},
        **kwargs,
    )


@pytest.fixture
def sibling_client(
    auth_config: AuthConfig, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, SiblingGateway]]]:
    slot: dict[str, SiblingGateway] = {}
    monkeypatch.setattr(routes, "GitHubInertPullRequests", lambda _provider: slot["gateway"])
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
            inert_landing_enabled=True,
        )

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = runtime_settings
    app.dependency_overrides[routes.get_landing_source] = inert_source
    app.dependency_overrides[routes.get_inert_landing_policy_source] = FakeInertPolicySource
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client, slot


def _served(client: TestClient) -> dict[str, Any]:
    response = client.get(
        ROUTE, params={"repository": INERT_REPOSITORY, "pr_number": TARGET}, headers=SYSTEM
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_body_serves_TRUE_when_an_edited_sibling_holds(sibling_client) -> None:
    client, slot = sibling_client
    slot["gateway"] = _sibling_gateway(sibling_edited=True)

    body = _served(client)

    assert body["branch_update_qualifies"] is True
    assert body["branch_update_withheld_for_sibling"] is True


def test_the_body_serves_FALSE_beside_an_owned_sibling(sibling_client) -> None:
    client, slot = sibling_client
    slot["gateway"] = _sibling_gateway(sibling_edited=False)

    body = _served(client)

    assert body["branch_update_qualifies"] is True
    assert body["branch_update_withheld_for_sibling"] is False


@pytest.mark.parametrize(
    ("gateway_kwargs", "served", "refused_with"),
    [
        pytest.param(
            {"sibling_edited": True}, True, INERT_BRANCH_UPDATE_SIBLING_HOLDING, id="holding"
        ),
        pytest.param(
            {
                "sibling_edited": True,
                "open_error": EstateGatewayError("open_pull_requests_status", 502),
            },
            False,
            INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE,
            id="unreadable",
        ),
    ],
)
def test_the_served_fact_is_true_EXACTLY_when_the_act_refuses_for_a_holding_sibling(
    sibling_client, migrated_engine: Engine, gateway_kwargs, served, refused_with
) -> None:
    """Spec test 8, for this lane."""
    client, slot = sibling_client
    slot["gateway"] = _sibling_gateway(**gateway_kwargs)
    assert _served(client)["branch_update_withheld_for_sibling"] is served

    gateway = _sibling_gateway(**gateway_kwargs)
    with Session(migrated_engine) as session, pytest.raises(DomainError) as raised:
        update_inert_pull_request_branch(
            session,
            InertBranchUpdateCommand(
                repository=INERT_REPOSITORY,
                pr_number=TARGET,
                actor=ActorContext("orchestrator-system", ActorRole.SYSTEM),
                idempotency_key=f"served-versus-act-{refused_with}",
                expected_head_sha=HEAD,
            ),
            gateway,
            inert_source(),
            FakeInertPolicySource(),
            enabled=True,
            credentials_configured=True,
        )

    assert raised.value.code == refused_with
    assert gateway.branch_updates == []
