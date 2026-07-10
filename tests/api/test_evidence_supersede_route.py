"""The evidence route dispatches to append vs supersede on the `supersede` flag.

Both service functions are covered in tests/services/test_evidence.py; this pins the
routing decision, which is the seam a retrying factory-runner depends on: a later attempt
must be able to supersede the evidence a partial-success earlier attempt left behind.
"""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import orchestrator.api.routes as routes
from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.main import create_app

_WORKER = {
    "Authorization": "Bearer fixture-token",
    "X-Credential-Key-Id": "worker-key",
}


def _evidence_body(*, supersede: bool | None) -> dict[str, object]:
    body: dict[str, object] = {
        "idempotency_key": "route-test",
        "expected_version": 1,
        "work_package_revision_id": "3f242a84-deaf-4cbd-bb66-1870235c6411",
        "ac_id": "AC-001",
        "attempt": 1,
        "lease_token": "lease",
        "evidence_type": "runner.pr.opened",
        "source_revision": "cd1f0659",
        "payload": {"pr_url": "https://example.invalid/pr/1"},
        "stable_ref": "https://example.invalid/pr/1",
    }
    if supersede is not None:
        body["supersede"] = supersede
    return body


@pytest.fixture
def routing_client(
    auth_config: AuthConfig, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list]]:
    called: list[str] = []

    def _sentinel(name: str):
        def _fn(_session, **_kwargs):
            called.append(name)
            return {"routed": name}

        return _fn

    monkeypatch.setattr(routes, "append_evidence", _sentinel("append"))
    monkeypatch.setattr(routes, "supersede_evidence", _sentinel("supersede"))
    # _raise_error passes a plain mapping straight through.
    monkeypatch.setattr(routes, "_raise_error", lambda value: value)

    app = create_app(auth_config)

    def _session() -> Iterator[Session]:
        yield Mock(spec=Session)

    app.dependency_overrides[get_session] = _session
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c, called


def test_supersede_false_routes_to_append(routing_client) -> None:
    client, called = routing_client
    r = client.post(
        "/api/v1/work-units/00000000-0000-4000-8000-000000000000/evidence",
        json=_evidence_body(supersede=False),
        headers=_WORKER,
    )
    # The sentinel's return shape does not satisfy EvidenceResponse, so the response-model
    # layer 500s after the handler runs. The routing decision is upstream of that and is
    # what this test pins.
    assert r.status_code != 401, "worker auth rejected"
    assert called == ["append"]


def test_supersede_true_routes_to_supersede(routing_client) -> None:
    client, called = routing_client
    r = client.post(
        "/api/v1/work-units/00000000-0000-4000-8000-000000000000/evidence",
        json=_evidence_body(supersede=True),
        headers=_WORKER,
    )
    assert r.status_code != 401, "worker auth rejected"
    assert called == ["supersede"]


def test_supersede_defaults_to_append_when_omitted(routing_client) -> None:
    """Existing callers that never send the field keep first-write behavior."""
    client, called = routing_client
    r = client.post(
        "/api/v1/work-units/00000000-0000-4000-8000-000000000000/evidence",
        json=_evidence_body(supersede=None),
        headers=_WORKER,
    )
    assert r.status_code != 401, "worker auth rejected"
    assert called == ["append"]
