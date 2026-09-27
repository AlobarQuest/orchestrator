"""Invocation tests for the CLI commands no other test invoked by name.

Each command is driven through the real Typer entrypoint (`CliRunner().invoke(app, [...])`),
with HTTP answered by a mock transport that records what the command sent. Three things are
asserted per command: it exits as its contract says, it prints what the API answered, and the
request it made is one the API actually serves -- the recorded path is folded back into its
route template and looked up in the application's own OpenAPI document, so a command pointed at
a path the server no longer carries fails here rather than as a 404 in an operator's shell.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.conformance_claim import ScannerUnavailableError
from orchestrator.main import app as api_app

UNIT = "11111111-1111-4111-8111-111111111111"
CANNED = {"answered": "by the mock"}


class Recorder:
    def __init__(self, answer: Any = CANNED) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.answer, request=request)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Recorder]:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://testserver")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "t")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)

    def install(answer: Any = CANNED) -> Recorder:
        recorder = Recorder(answer)
        monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", httpx.MockTransport(recorder))
        return recorder

    return install


def _served(method: str, path: str) -> bool:
    template = path.replace(UNIT, "{unit_id}").replace("/attempts/3/", "/attempts/{attempt}/")
    operations = api_app.openapi()["paths"].get(template, {})
    return method.lower() in operations


# (argv, method, path, request body or None)
HTTP_COMMANDS = [
    (["readiness", UNIT], "GET", f"/api/v1/work-units/{UNIT}/readiness", None),
    (["list-evidence", UNIT], "GET", f"/api/v1/work-units/{UNIT}/evidence", None),
    (
        ["reconcile-detect", "--idempotency-key", "k1"],
        "POST",
        "/api/v1/reconciliation/detect",
        {"idempotency_key": "k1", "expected_version": 0},
    ),
    (
        ["recover-evidence", UNIT, "--attempt", "3", "--data", '{"idempotency_key": "k2"}'],
        "POST",
        f"/api/v1/work-units/{UNIT}/attempts/3/recover-evidence",
        {"idempotency_key": "k2"},
    ),
    (["dead-letter"], "GET", "/api/v1/dead-letter", None),
    (["check-consistency"], "GET", "/api/v1/consistency-check", None),
]


@pytest.mark.parametrize(
    ("argv", "method", "path", "body"), HTTP_COMMANDS, ids=[row[0][0] for row in HTTP_COMMANDS]
)
def test_the_command_calls_a_served_route_and_prints_its_answer(
    transport: Callable[[Any], Recorder],
    argv: list[str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    recorder = transport(CANNED)

    result = CliRunner().invoke(app, [*argv, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == CANNED
    assert len(recorder.requests) == 1
    sent = recorder.requests[0]
    assert (sent.method, sent.url.path) == (method, path)
    assert (json.loads(sent.content) if sent.content else None) == body
    assert _served(method, path), f"{method} {path} is not a route the API serves"


def test_check_consistency_exits_one_when_the_report_is_divergent(
    transport: Callable[[Any], Recorder],
) -> None:
    transport({"divergent": True, "findings": []})

    result = CliRunner().invoke(app, ["check-consistency", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"divergent": True, "findings": []}


def test_an_api_error_exits_one_with_the_error_body(
    monkeypatch: pytest.MonkeyPatch, transport: Callable[[Any], Recorder]
) -> None:
    transport(CANNED)  # installs the environment; the transport is replaced below

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": {"code": "work_unit_not_found", "message": "no"}}, request=request
        )

    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", httpx.MockTransport(refuse))

    result = CliRunner().invoke(app, ["readiness", UNIT, "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "work_unit_not_found"


class _Claim:
    def as_authority_conformance(self) -> dict[str, Any]:
        return {"standards_touched": ["project"], "accepted_standards": [], "status": "green"}


def test_conformance_claim_prints_the_derived_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Path] = []

    def compute(repo: Path) -> _Claim:
        seen.append(repo)
        return _Claim()

    monkeypatch.setattr("orchestrator.cli.compute_conformance_claim", compute)

    result = CliRunner().invoke(app, ["conformance-claim", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _Claim().as_authority_conformance()
    assert seen == [tmp_path]


def test_conformance_claim_fails_closed_and_legibly_without_the_scanner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def compute(repo: Path) -> _Claim:
        raise ScannerUnavailableError("portfolio.compliance is not importable")

    monkeypatch.setattr("orchestrator.cli.compute_conformance_claim", compute)

    result = CliRunner().invoke(app, ["conformance-claim", str(tmp_path), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": {
            "code": "scanner_unavailable",
            "message": "portfolio.compliance is not importable",
        }
    }
