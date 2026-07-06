import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


def test_propose_decomposition_reads_json_file_and_posts_to_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    payload_path = tmp_path / "proposal.json"
    payload_path.write_text(
        json.dumps(
            {
                "idempotency_key": "proposal-1",
                "expected_version": 0,
                "rationale": "Split by delivery path.",
                "proposed_units": [
                    {
                        "unit_key": "unit-1",
                        "title": "Implement service",
                        "outcome": "Service persists proposals.",
                        "required_capability": "repository_write",
                        "authority": {
                            "capabilities": {"repository_write": "allowed"},
                            "budgets": {"max_attempts": 3, "max_llm_calls": 4},
                        },
                        "max_attempts": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"id": "proposal-1"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app,
        [
            "propose-decomposition",
            "revision-1",
            "--data",
            f"@{payload_path}",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "method": "POST",
        "path": "/api/v1/package-intakes/revision-1/decomposition-proposals",
        "payload": {
            "idempotency_key": "proposal-1",
            "expected_version": 0,
            "rationale": "Split by delivery path.",
            "proposed_units": [
                {
                    "unit_key": "unit-1",
                    "title": "Implement service",
                    "outcome": "Service persists proposals.",
                    "required_capability": "repository_write",
                    "authority": {
                        "capabilities": {"repository_write": "allowed"},
                        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
                    },
                    "max_attempts": 3,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("arguments", "path"),
    [
        (
            ["list-decomposition-proposals", "revision-1"],
            "/api/v1/package-intakes/revision-1/decomposition-proposals",
        ),
        (
            ["show-decomposition-proposal", "proposal-1"],
            "/api/v1/decomposition-proposals/proposal-1",
        ),
    ],
)
def test_list_and_show_decomposition_commands_forward_get_requests(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    path: str,
) -> None:
    observed: dict[str, object] = {}

    def fake_request(method: str, actual_path: str, payload=None):
        observed.update(method=method, path=actual_path, payload=payload)
        return {"id": "result-1"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(app, [*arguments, "--json"])

    assert result.exit_code == 0
    assert observed == {"method": "GET", "path": path, "payload": None}


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ("approve-decomposition", "/api/v1/decomposition-proposals/proposal-1/approve"),
        ("reject-decomposition", "/api/v1/decomposition-proposals/proposal-1/reject"),
        (
            "require-decomposition-revision",
            "/api/v1/decomposition-proposals/proposal-1/require-revision",
        ),
    ],
)
def test_decomposition_decision_commands_post_reason_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    path: str,
) -> None:
    observed: dict[str, object] = {}

    def fake_request(method: str, actual_path: str, payload=None):
        observed.update(method=method, path=actual_path, payload=payload)
        return {"id": "proposal-1", "state": "proposed"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)
    result = CliRunner().invoke(
        app,
        [
            command,
            "proposal-1",
            "--idempotency-key",
            "decision-1",
            "--reason",
            "Needs a clearer dependency split.",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "method": "POST",
        "path": path,
        "payload": {
            "idempotency_key": "decision-1",
            "expected_version": 0,
            "reason": "Needs a clearer dependency split.",
        },
    }
