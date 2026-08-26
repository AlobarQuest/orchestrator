"""POST /work-units/{unit_id}/pr-merge (ADR-0020, Increment 4b).

The served surface of the act. What only an HTTP test can see: that the response model declares
every field the record carries (a `response_model` silently DROPS the rest), that the role gate
holds at the route rather than only in the service, and that the gateway is built from the same
credential resolution the rest of the App-token surface uses.

The gateway is substituted the way the workflow client already is — by patching the name the route
resolves at call time — so nothing here reaches the network.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import orchestrator.api.routes as routes
from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.api.schemas import PrMergeResponse
from orchestrator.config import Settings, get_settings
from orchestrator.main import create_app
from orchestrator.persistence.models import UnitPrMerge
from orchestrator.services.pr_merge import MergeOutcome, PullRequestState
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER
from tests.api.test_pr_merge_admission_api import TARGET_REPOSITORY, _register_ready_unit
from tests.services.estate_doubles import FakeEstateLandingSource, inert_source

LANDED_COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class FakeGateway:
    calls: list[tuple[str, int, str]] = []

    def __init__(self, token_provider) -> None:
        self.token_provider = token_provider

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
        return PullRequestState(base_ref="main", default_branch="main", open=True, landed=False)

    def submit_merge(self, *, repository: str, number: int, head_sha: str) -> MergeOutcome:
        FakeGateway.calls.append((repository, number, head_sha))
        return MergeOutcome(landed=True, commit_sha=LANDED_COMMIT, status_code=200)


def _fake_landing_source(**_: object) -> FakeEstateLandingSource:
    return inert_source()


@pytest.fixture
def merge_client(
    auth_config: AuthConfig,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    FakeGateway.calls = []
    monkeypatch.setattr(routes, "GitHubPullRequests", FakeGateway)
    monkeypatch.setattr(routes, "HttpEstateLandingSource", _fake_landing_source)
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
            app_brain_url="https://app-brain.example",
            app_brain_read_key=SecretStr("read-only-value"),
        )

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = runtime_settings
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client


def test_the_response_model_declares_every_field_the_record_carries() -> None:
    """The `response_model` silent-drop invariant, pinned structurally: a field the service
    returns and the model does not declare never reaches the caller, and never errors."""
    # `idempotency_key` is deliberately not served, matching every other record response in this
    # API: it is the CALLER's key, echoing it back tells the caller nothing it did not send, and a
    # response that carries it invites a reader to treat it as the record's identity when the
    # identity is the row.
    columns = {column.name for column in UnitPrMerge.__table__.columns} - {"idempotency_key"}

    assert set(PrMergeResponse.model_fields) == columns


def test_a_unit_that_may_not_be_landed_is_refused_by_name(merge_client: TestClient) -> None:
    """The shape every unit in production has: nothing has been adjudicated from observed
    evidence and no envelope grants the capability, so the act refuses before it calls."""
    unit_id = _register_ready_unit(merge_client, "merge-act")

    response = merge_client.post(
        f"/api/v1/work-units/{unit_id}/pr-merge",
        headers=SYSTEM,
        json={"idempotency_key": "act-1", "expected_version": 2},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "pr_merge_not_admissible"
    assert FakeGateway.calls == []


@pytest.mark.parametrize(("label", "headers"), [("worker", WORKER), ("human", HUMAN)])
def test_only_the_system_actor_may_ask(merge_client: TestClient, label: str, headers: dict) -> None:
    unit_id = _register_ready_unit(merge_client, f"merge-role-{label}")

    response = merge_client.post(
        f"/api/v1/work-units/{unit_id}/pr-merge",
        headers=headers,
        json={"idempotency_key": "role-check", "expected_version": 2},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"
    assert FakeGateway.calls == []


def test_the_route_requires_authentication(merge_client: TestClient) -> None:
    unit_id = _register_ready_unit(merge_client, "merge-auth")

    response = merge_client.post(
        f"/api/v1/work-units/{unit_id}/pr-merge",
        json={"idempotency_key": "no-auth", "expected_version": 2},
    )

    assert response.status_code == 401
    assert FakeGateway.calls == []


def test_an_unknown_unit_is_a_named_domain_error(merge_client: TestClient) -> None:
    response = merge_client.post(
        f"/api/v1/work-units/{uuid.uuid4()}/pr-merge",
        headers=SYSTEM,
        json={"idempotency_key": "missing", "expected_version": 2},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "work_unit_not_found"


def test_the_target_repository_is_the_units_own(merge_client: TestClient) -> None:
    """Routing is a property of the unit, never of the process -- the same rule the workflow
    trigger follows, and for the same reason: a process-global target misroutes rather than
    fails."""
    unit_id = _register_ready_unit(merge_client, "merge-target")

    body = merge_client.get(
        f"/api/v1/work-units/{unit_id}/pr-merge-admission", headers=SYSTEM
    ).json()

    assert body["target_repository"] == TARGET_REPOSITORY


@pytest.mark.parametrize(
    "override",
    [{}, {"reason": None}, {"reason": "  "}],
    ids=["absent", "null", "whitespace"],
)
def test_a_landing_override_with_no_stated_reason_is_refused_by_name(
    merge_client: TestClient, override: dict[str, object]
) -> None:
    """ADR-0032, on the act that changes what is already serving. The same named refusal both
    acts raise, because both build the same type from the same shape."""
    unit_id = _register_ready_unit(merge_client, f"reasonless-landing-{len(override)}-{override}")

    response = merge_client.post(
        f"/api/v1/work-units/{unit_id}/pr-merge",
        headers=SYSTEM,
        json={
            "idempotency_key": f"reasonless-landing-{unit_id}",
            "expected_version": 2,
            "change_window_override": override,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "change_window_override_reason_required"
    assert FakeGateway.calls == []
