import ast
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.cli import app
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.main import create_app
from orchestrator.services.lifecycle import ActorContext, TransitionResult

LIFECYCLE_COMMANDS = (
    ("ready", "ready"),
    ("start", "executing"),
    ("block", "blocked"),
    ("request-approval", "awaiting_approval"),
    ("approve", "ready"),
    ("submit", "submitted"),
    ("verify", "verifying"),
    ("review", "awaiting_review"),
    ("complete", "completed"),
    ("fail", "failed"),
    ("retry", "ready"),
    ("cancel", "cancelled"),
)


@pytest.mark.parametrize(("command", "state"), LIFECYCLE_COMMANDS)
def test_lifecycle_commands_preserve_api_result(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    state: str,
) -> None:
    unit_id = UUID("00000000-0000-0000-0000-000000000001")
    event_id = UUID("00000000-0000-0000-0000-000000000002")
    application = create_app()
    application.dependency_overrides[get_actor] = lambda: ActorContext("worker", ActorRole.WORKER)
    application.dependency_overrides[get_session] = lambda: Mock(spec=Session)
    monkeypatch.setattr(
        "orchestrator.api.routes.transition_unit",
        lambda *_args, **_kwargs: TransitionResult(unit_id, WorkUnitState(state), 7, event_id),
    )
    api_response = TestClient(application).post(
        f"/api/v1/work-units/{unit_id}/commands/{command}",
        json={"idempotency_key": f"{command}-1", "expected_version": 6},
    )
    assert api_response.status_code == 200
    expected = api_response.json()
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload: dict[str, object] | None = None):
        observed.update(method=method, path=path, payload=payload)
        return expected

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app,
        [
            command,
            str(unit_id),
            "--idempotency-key",
            f"{command}-1",
            "--expected-version",
            "6",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == expected
    assert observed == {
        "method": "POST",
        "path": f"/api/v1/work-units/{unit_id}/commands/{command}",
        "payload": {
            "idempotency_key": f"{command}-1",
            "expected_version": 6,
            "attempt": None,
            "lease_token": None,
        },
    }


def test_human_transition_output_contains_contract_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orchestrator.cli.request",
        lambda *_args, **_kwargs: {
            "unit_id": "unit-1",
            "state": "executing",
            "version": 4,
            "event_id": "event-1",
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "start",
            "unit-1",
            "--idempotency-key",
            "start-1",
            "--expected-version",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "unit=unit-1 state=executing version=4 event=event-1"


@pytest.mark.parametrize(
    ("arguments", "method", "path"),
    [
        (["register-revision"], "POST", "/api/v1/revisions"),
        (["register-unit", "revision-1"], "POST", "/api/v1/revisions/revision-1/work-units"),
        (["adjudicate", "unit-1"], "POST", "/api/v1/work-units/unit-1/adjudications"),
        (
            ["authorize-retry", "unit-1"],
            "POST",
            "/api/v1/work-units/unit-1/retry-authorization",
        ),
        (["add-dependency", "unit-1"], "POST", "/api/v1/work-units/unit-1/dependencies"),
        (
            ["resolve-dependency", "dependency-1"],
            "POST",
            "/api/v1/dependencies/dependency-1/resolve",
        ),
        (["append-evidence", "unit-1"], "POST", "/api/v1/work-units/unit-1/evidence"),
    ],
)
def test_mutation_nouns_forward_json_payload(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    method: str,
    path: str,
) -> None:
    observed: dict[str, object] = {}

    def fake_request(actual_method: str, actual_path: str, payload=None):
        observed.update(method=actual_method, path=actual_path, payload=payload)
        return {"id": "result-1"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(app, [*arguments, "--data", '{"expected_version":2}', "--json"])

    assert result.exit_code == 0
    assert observed == {
        "method": method,
        "path": path,
        "payload": {"expected_version": 2},
    }


def test_record_approval_has_explicit_validated_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"id": "approval-1"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app,
        [
            "record-approval",
            "unit-1",
            "--idempotency-key",
            "approval-1",
            "--expected-version",
            "2",
            "--subject-type",
            "authority",
            "--reason",
            "approved",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed["payload"] == {
        "idempotency_key": "approval-1",
        "expected_version": 2,
        "subject_type": "authority",
        "reason": "approved",
    }

    invalid = CliRunner().invoke(
        app,
        [
            "record-approval",
            "unit-1",
            "--idempotency-key",
            "approval-2",
            "--expected-version",
            "2",
            "--subject-type",
            "worker",
            "--reason",
            "invalid",
        ],
    )
    assert invalid.exit_code == 2


def test_cli_source_has_no_forbidden_domain_or_database_imports() -> None:
    source = Path("src/orchestrator/cli.py").read_text()
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    )

    assert not any(
        name == "sqlalchemy"
        or name.startswith("sqlalchemy.")
        or name == "orchestrator.kernel"
        or name.startswith("orchestrator.kernel.")
        or name == "orchestrator.persistence"
        or name.startswith("orchestrator.persistence.")
        or name == "orchestrator.services"
        or name.startswith("orchestrator.services.")
        for name in imports
    )
