import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orchestrator.package_sources as package_sources
from orchestrator.cli import app
from orchestrator.package_sources import VerifiedApproval

_FIXTURE = "tests/fixtures/intent-packages/ws32-approved-software"
_BASE_ARGS = [
    _FIXTURE,
    "--source-repository",
    "AlobarQuest/intent-packages",
    "--idempotency-key",
    "package-intake-1",
]


def _pass_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: VerifiedApproval(
            approved_by="devon",
            approved_at="2026-07-05T00:02:00Z",
            approval_event_id="22222222-2222-2222-2222-222222222222",
            approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")


def test_emit_payload_matches_intake_package_post_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_verification(monkeypatch)
    posted: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        posted.update(payload=payload)
        return {"id": "revision-1", "revision": 1}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    intake = CliRunner().invoke(app, ["intake-package", *_BASE_ARGS, "--json"])
    assert intake.exit_code == 0
    emit = CliRunner().invoke(app, ["emit-intake-payload", *_BASE_ARGS, "--json"])
    assert emit.exit_code == 0
    assert json.loads(emit.stdout) == posted["payload"]


def test_emit_payload_writes_out_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _pass_verification(monkeypatch)
    out = tmp_path / "intake.json"
    result = CliRunner().invoke(
        app, ["emit-intake-payload", *_BASE_ARGS, "--out", str(out), "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"written": str(out)}
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["package_id"] == "ws32-approved-software"
    assert body["idempotency_key"] == "package-intake-1"
    assert body["expected_version"] == 0
    assert body["verification_mode"] == "caller_attested_cli_verified"


def test_emit_payload_fails_on_unapproved_package(tmp_path: Path) -> None:
    out = tmp_path / "intake.json"
    result = CliRunner().invoke(
        app,
        [
            "emit-intake-payload",
            "tests/fixtures/intent-packages/ws32-draft-software",
            "--source-repository",
            "AlobarQuest/intent-packages",
            "--idempotency-key",
            "package-intake-1",
            "--out",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "package_source_error"
    assert not out.exists()


def test_emit_payload_carries_a_declared_reach_into_the_enforcement_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WS-P2.18: the snapshot is what the orchestrator holds from intake onwards, so a declaration
    # that does not reach it is a declaration nothing can ever be keyed on.
    _pass_verification(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "emit-intake-payload",
            "tests/fixtures/intent-packages/ws32-approved-reach",
            "--source-repository",
            "AlobarQuest/intent-packages",
            "--idempotency-key",
            "package-intake-reach",
            "--json",
        ],
    )

    assert result.exit_code == 0
    snapshot = json.loads(result.stdout)["enforcement_snapshot"]
    assert snapshot["reach"] == ["live_estate", "source_repository"]


def test_emit_payload_omits_reach_entirely_when_the_package_declares_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A `reach: null` key would read as "reaches nothing", the most permissive claim available,
    # for exactly the packages nobody has ever classified.
    _pass_verification(monkeypatch)

    result = CliRunner().invoke(app, ["emit-intake-payload", *_BASE_ARGS, "--json"])

    assert result.exit_code == 0
    assert "reach" not in json.loads(result.stdout)["enforcement_snapshot"]


def test_emit_payload_reports_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pass_verification(monkeypatch)
    out = tmp_path / "missing-dir" / "intake.json"
    result = CliRunner().invoke(
        app, ["emit-intake-payload", *_BASE_ARGS, "--out", str(out), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "output_write_failed"
    assert not out.exists()
