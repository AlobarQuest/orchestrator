"""GET /api/v1/estate-pr-merge-admission -- the served surface of the composed answer.

**The shape pin lives beside the predicate it protects**, in the branch-update tests, because that
is where somebody adding a field to the answer is working. What it cannot show is the
one thing that matters to the caller: that the key is actually IN THE BODY. A `response_model`
drops what it does not declare silently, and this estate has shipped that defect once already --
so the model-versus-dataclass assertion and this request are two different claims, and only this
one is measured at the surface the reporting agent reads.

No network is reached. The client configures no App credentials, so the gateway raises before it
can send anything and every remote term refuses -- which is the answer under test here, since the
field must be served on a REFUSING answer as much as on a permitting one.
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
from orchestrator.services.estate_pr_branch_update import (
    BRANCH_UPDATE_SIBLING_HOLDING,
    BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
from orchestrator.services.lifecycle import ActorContext
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import redeploying_source
from tests.services.estate_landing_doubles import (
    HEAD,
    REPOSITORY,
    SiblingGateway,
    approved,
    foreign_commit,
    pull_request,
)


def _admission(db_client: TestClient) -> dict[str, Any]:
    response = db_client.get(
        "/api/v1/estate-pr-merge-admission",
        params={"repository": "alobarquest/brain", "pr_number": 31},
        headers=SYSTEM,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_body_carries_the_base_comparison_the_reporting_agent_classifies_on(
    db_client: TestClient,
) -> None:
    """ADR-0024. The agent cannot observe this for itself -- it reads no repository -- so an
    undeclared field would leave it falling back to its fail-toward-a-finding default forever,
    with every term on this side computing correctly and nothing on the wire."""
    body = _admission(db_client)

    assert "rollout_base_matches_pin" in body
    assert body["rollout_base_matches_pin"] is False


def test_the_body_carries_every_field_of_the_composed_answer(db_client: TestClient) -> None:
    """Stated as SET EQUALITY over the served keys rather than as a membership check for this
    increment's one field: the silent-drop hole belongs to the model, not to the field, and a
    membership check would not see the next addition fall through it."""
    from orchestrator.services.estate_landing_admission import EstateLandingAdmission

    assert set(_admission(db_client)) == set(EstateLandingAdmission.__dataclass_fields__)


def test_a_body_that_observed_no_sibling_serves_the_withheld_fact_as_false(
    db_client: TestClient,
) -> None:
    """ADR-0045. No credentials, so nothing qualifies and no sibling scan runs -- and the key is
    still served, because a caller reading an absent key and a false one must never differ."""
    body = _admission(db_client)

    assert body["branch_update_qualifies"] is False
    assert body["branch_update_withheld_for_sibling"] is False


# --------------------------------------------------------------------------------------------
# ADR-0045 on the wire. A field that only ever serializes False is unproven -- a response model
# that dropped it, or a route that never filled it, would look exactly the same -- so this drives
# the route to a TRUE answer through a gateway that answers each pull request for itself.
# --------------------------------------------------------------------------------------------

TARGET = 49
SIBLING = 50
SIBLING_HEAD = "b" * 40


def _sibling_gateway(*, sibling_edited: bool, **kwargs) -> SiblingGateway:
    """Target and sibling both behind their base and otherwise landable, so the sibling's own
    composed answer holds whenever the sibling is edited."""
    return SiblingGateway(
        target=TARGET,
        pulls={
            TARGET: pull_request(number=TARGET),
            SIBLING: pull_request(number=SIBLING, head_sha=SIBLING_HEAD),
        },
        behind={TARGET: 3, SIBLING: 3},
        commits={SIBLING: (foreign_commit(SIBLING_HEAD),)} if sibling_edited else {},
        **kwargs,
    )


def _records() -> FakeChangeRecordSource:
    return FakeChangeRecordSource(
        {(REPOSITORY, TARGET): approved(), (REPOSITORY, SIBLING): approved()}
    )


@pytest.fixture
def sibling_client(
    auth_config: AuthConfig, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, dict[str, SiblingGateway]]]:
    """A client whose route builds whichever gateway the test installs in `slot["gateway"]`.

    The route constructs its gateway by name at call time, so patching that name is how the
    fake is reached -- and App credentials are configured so the answer can qualify at all.
    """
    slot: dict[str, SiblingGateway] = {}
    monkeypatch.setattr(routes, "GitHubEstatePullRequests", lambda _provider: slot["gateway"])
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
            estate_landing_enabled=True,
        )

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = runtime_settings
    app.dependency_overrides[routes.get_landing_source] = redeploying_source
    app.dependency_overrides[routes.get_change_record_source] = _records
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client, slot


def _served(client: TestClient) -> dict[str, Any]:
    response = client.get(
        "/api/v1/estate-pr-merge-admission",
        params={"repository": REPOSITORY, "pr_number": TARGET},
        headers=SYSTEM,
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
    """The pair: identical but for the sibling's ownership."""
    client, slot = sibling_client
    slot["gateway"] = _sibling_gateway(sibling_edited=False)

    body = _served(client)

    assert body["branch_update_qualifies"] is True
    assert body["branch_update_withheld_for_sibling"] is False


def test_a_failed_scan_leaves_the_answer_answering_with_the_fact_FALSE(sibling_client) -> None:
    """The admission read must still answer when the scan cannot. False, never true: a scan whose
    reads failed must never produce a quiet line -- the act meets the same failure and refuses with
    its own code, which the landers keep a finding."""
    client, slot = sibling_client
    slot["gateway"] = _sibling_gateway(
        sibling_edited=True, open_error=EstateGatewayError("open_pull_requests_status", 502)
    )

    body = _served(client)

    assert body["branch_update_qualifies"] is True
    assert body["branch_update_withheld_for_sibling"] is False


@pytest.mark.parametrize(
    ("gateway_kwargs", "served", "refused_with"),
    [
        pytest.param({"sibling_edited": True}, True, BRANCH_UPDATE_SIBLING_HOLDING, id="holding"),
        pytest.param(
            {
                "sibling_edited": True,
                "open_error": EstateGatewayError("open_pull_requests_status", 502),
            },
            False,
            BRANCH_UPDATE_SIBLINGS_UNREADABLE,
            id="unreadable",
        ),
    ],
)
def test_the_served_fact_is_true_EXACTLY_when_the_act_refuses_for_a_holding_sibling(
    sibling_client, migrated_engine: Engine, gateway_kwargs, served, refused_with
) -> None:
    """Spec test 8. The route and the act ask one question in two transactions; over one fixture
    they must agree -- true with the holding code, false with the unreadable one."""
    client, slot = sibling_client
    slot["gateway"] = _sibling_gateway(**gateway_kwargs)
    assert _served(client)["branch_update_withheld_for_sibling"] is served

    gateway = _sibling_gateway(**gateway_kwargs)
    with Session(migrated_engine) as session, pytest.raises(DomainError) as raised:
        update_estate_pull_request_branch(
            session,
            EstateBranchUpdateCommand(
                repository=REPOSITORY,
                pr_number=TARGET,
                actor=ActorContext("orchestrator-system", ActorRole.SYSTEM),
                idempotency_key=f"served-versus-act-{refused_with}",
                expected_head_sha=HEAD,
            ),
            gateway,
            redeploying_source(),
            _records(),
            enabled=True,
            credentials_configured=True,
        )

    assert raised.value.code == refused_with
    assert gateway.branch_updates == []
