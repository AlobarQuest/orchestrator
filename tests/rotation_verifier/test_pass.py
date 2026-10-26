"""The rotation verifier's pass, against a fake orchestrator."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from rotation_verifier.cli import exit_code, main, run_pass
from rotation_verifier.client import ForbiddenEndpointError, VerifierClient, permits

ROTATION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
MOVED = "33333333-3333-4333-8333-333333333333"
HELD = "44444444-4444-4444-8444-444444444444"


class Orchestrator:
    """Answers the three routes the lane may reach, and records each verify it was sent."""

    def __init__(self, units: dict[str, dict[str, Any]], answers: dict[str, httpx.Response]):
        self.units = units
        self.answers = answers
        self.verified: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/status-ledger":
            state = request.url.params["state"]
            rows = [
                {"unit_id": i, "unit_key": u["unit_key"]}
                for i, u in self.units.items()
                if u.get("state", "submitted") == state
            ]
            return httpx.Response(200, json=rows)
        unit_id = path.split("/")[4]
        if path.endswith("/verify"):
            self.verified.append((unit_id, json.loads(request.content)))
            return self.answers[unit_id]
        return httpx.Response(200, json=self.units[unit_id])


def _unit(key: str, version: int = 5, rotation: str | None = "retire") -> dict[str, Any]:
    constraints = {"rotation_unit": rotation} if rotation else {"target_repository": "o/r"}
    return {"unit_key": key, "version": version, "authority": {"constraints": constraints}}


def _client(orchestrator: Orchestrator) -> VerifierClient:
    return VerifierClient(
        base_url="https://sds.example",
        credential_key_id="orchestrator-verifier",
        token="verifier-token",
        transport=httpx.MockTransport(orchestrator),
    )


def test_only_rotation_units_are_verified() -> None:
    orchestrator = Orchestrator(
        {ROTATION: _unit("cm-full-bearer-retire"), OTHER: _unit("repo-change", rotation=None)},
        {ROTATION: httpx.Response(200, json={"state": "completed"})},
    )

    outcomes = run_pass(_client(orchestrator))

    assert [(o.unit_key, o.result) for o in outcomes] == [("cm-full-bearer-retire", "completed")]
    assert orchestrator.verified == [
        (ROTATION, {"expected_version": 5, "idempotency_key": f"rotation-verifier-{ROTATION}-v5"})
    ]


def test_completed_and_awaiting_review_are_reported() -> None:
    orchestrator = Orchestrator(
        {ROTATION: _unit("a-retire"), HELD: _unit("b-store")},
        {
            ROTATION: httpx.Response(200, json={"state": "completed"}),
            HELD: httpx.Response(200, json={"state": "awaiting_review"}),
        },
    )

    outcomes = run_pass(_client(orchestrator))

    assert sorted((o.unit_key, o.result) for o in outcomes) == [
        ("a-retire", "completed"),
        ("b-store", "awaiting_review"),
    ]
    assert exit_code(outcomes) == 2


def test_version_conflict_skips_unit() -> None:
    conflict = {"error": {"code": "version_conflict", "message": "work unit version has changed"}}
    orchestrator = Orchestrator(
        {MOVED: _unit("a-mint"), ROTATION: _unit("b-retire")},
        {
            MOVED: httpx.Response(409, json=conflict),
            ROTATION: httpx.Response(200, json={"state": "completed"}),
        },
    )

    outcomes = run_pass(_client(orchestrator))

    assert sorted((o.unit_key, o.result) for o in outcomes) == [
        ("a-mint", "refused"),
        ("b-retire", "completed"),
    ]
    assert "409 (version_conflict)" in next(o.detail for o in outcomes if o.result == "refused")
    assert exit_code(outcomes) == 3


def test_a_unit_left_verifying_is_verified_again() -> None:
    orchestrator = Orchestrator(
        {ROTATION: {**_unit("a-retire"), "state": "verifying"}},
        {ROTATION: httpx.Response(200, json={"state": "completed"})},
    )

    outcomes = run_pass(_client(orchestrator))

    assert [(o.unit_key, o.result) for o in outcomes] == [("a-retire", "completed")]


def test_a_dry_run_verifies_nothing() -> None:
    orchestrator = Orchestrator({ROTATION: _unit("a-retire")}, {})

    outcomes = run_pass(_client(orchestrator), dry_run=True)

    assert [(o.unit_key, o.result) for o in outcomes] == [("a-retire", "would-verify")]
    assert orchestrator.verified == []
    assert exit_code(outcomes) == 0


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/status-ledger"),
        ("GET", f"/api/v1/work-units/{ROTATION}/evidence"),
        ("POST", f"/api/v1/work-units/{ROTATION}/claim"),
        ("POST", f"/api/v1/work-units/{ROTATION}/verify/"),
        ("GET", "/api/v1/status-ledger/"),
    ],
)
def test_client_refuses_unpermitted_path(method: str, path: str) -> None:
    assert not permits(method, path)


def test_a_malformed_unit_id_never_leaves_the_process() -> None:
    orchestrator = Orchestrator({}, {})
    with pytest.raises(ForbiddenEndpointError):
        _client(orchestrator).unit("../dead-letter")


def test_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    assert exit_code([]) == 0
    assert main([]) == 1  # no credential in the environment
