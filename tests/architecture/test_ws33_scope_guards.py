from __future__ import annotations

import ast
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import orchestrator.package_sources as package_sources
from orchestrator.api.routes import router as api_router
from orchestrator.package_sources import (
    PackageSourceError,
    VerifiedApproval,
    load_package_intake_payload,
    load_protocol_fixture_intake_payload,
)
from orchestrator.persistence.models import INTAKE_SOURCES
from orchestrator.services import package_intake

RUNTIME_ROOT = Path("src/orchestrator")
WORKFLOW_ROOT = Path(".github/workflows")
PACKAGE_FIXTURE = Path("tests/fixtures/intent-packages/ws32-approved-software")
TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
AUTOMATIC_MERGE_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("gh", "pr", "merge"),
    ("git", "push", "origin", "main"),
    ("merge", "pull", "request"),
    ("auto", "merge"),
    ("automerge",),
    ("merges",),
)


@dataclass(frozen=True)
class SourceMatch:
    path: Path
    value: str


def _python_sources() -> list[Path]:
    return sorted(RUNTIME_ROOT.rglob("*.py"))


def _workflow_sources() -> list[Path]:
    return sorted(path for path in WORKFLOW_ROOT.glob("*") if path.is_file())


def _read_lower(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token for token in TOKEN_SPLIT.split(value.lower()) if token)


def _contains_sequence(tokens: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    size = len(sequence)
    return any(tokens[index : index + size] == sequence for index in range(len(tokens) - size + 1))


def _automatic_merge_sequence(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    for sequence in AUTOMATIC_MERGE_SEQUENCES:
        if _contains_sequence(tokens, sequence):
            return sequence
    return None


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _python_automatic_merge_matches(path: Path) -> list[SourceMatch]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matches: list[SourceMatch] = []
    for node in ast.walk(tree):
        tokens: tuple[str, ...] = ()
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens = _tokens(node.value)
        elif isinstance(node, (ast.Name, ast.Attribute)):
            value = node.id if isinstance(node, ast.Name) else node.attr
            tokens = _tokens(value)
        elif isinstance(node, (ast.List, ast.Tuple)):
            values = [
                item.value
                for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            tokens = tuple(token for value in values for token in _tokens(value))
        sequence = _automatic_merge_sequence(tokens)
        if sequence is not None:
            matches.append(SourceMatch(path, " ".join(sequence)))
    return matches


def _text_automatic_merge_matches(path: Path) -> list[SourceMatch]:
    sequence = _automatic_merge_sequence(_tokens(path.read_text(encoding="utf-8")))
    if sequence is None:
        return []
    return [SourceMatch(path, " ".join(sequence))]


def _closed_fixture(tmp_path: Path) -> Path:
    package_dir = tmp_path / "ws33-closed-software"
    shutil.copytree(PACKAGE_FIXTURE, package_dir)
    package_path = package_dir / "package.yaml"
    lineage_path = package_dir / "lineage.yaml"
    package_path.write_text(
        package_path.read_text(encoding="utf-8").replace("status: approved", "status: closed"),
        encoding="utf-8",
    )
    lineage_path.write_text(
        lineage_path.read_text(encoding="utf-8").replace(
            "current_state: approved",
            "current_state: closed",
        ),
        encoding="utf-8",
    )
    return package_dir


def _verified_approval() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def test_no_workflow_dispatch_or_factory_runner_dispatch_code_exists() -> None:
    forbidden = ("workflow_dispatch", "factory-runner", "factory_runner")
    # factory-runner-pilot.yml legitimately dispatches the factory runner; release-image.yml
    # is a separate, unrelated workflow_dispatch trigger (manual image build+push, no factory
    # runner involvement) — both are deliberate human-triggered exceptions to this guard.
    # attest-exit-criteria.yml is a third and weakest exception: read-only (one unauthenticated
    # GET of production's public OpenAPI document), carrying workflow_dispatch only so the
    # scorecard guard can be re-run on demand after a production image swap. attest-wave-exit.yml
    # (WS-P2.39) is a fourth of exactly that kind: same read, same reason, for the wave exit bars.
    manual_dispatch_workflows = {
        "attest-exit-criteria.yml",
        "attest-wave-exit.yml",
        "factory-runner-pilot.yml",
        "release-image.yml",
    }
    workflow_paths = [
        path for path in _workflow_sources() if path.name not in manual_dispatch_workflows
    ]
    python_paths = [
        path
        for path in _python_sources()
        if path
        not in {
            Path("src/orchestrator/services/dispatch.py"),
            Path("src/orchestrator/api/routes.py"),
            Path("src/orchestrator/api/schemas.py"),
            Path("src/orchestrator/config.py"),
        }
    ]
    matches = [
        SourceMatch(path, value)
        for path in [*python_paths, *workflow_paths]
        for value in forbidden
        if value in _read_lower(path)
    ]

    assert not matches, "Forbidden dispatch code found:\n" + "\n".join(
        f"- {match.path}: {match.value}" for match in matches
    )


def test_runtime_has_no_external_factory_events_publisher_import() -> None:
    matches = [
        SourceMatch(path, module)
        for path in _python_sources()
        for module in _imported_modules(path)
        if module == "factory_events.store" or module.startswith("factory_events.store.")
    ]

    assert not matches, "Forbidden factory_events store imports found:\n" + "\n".join(
        f"- {match.path}: {match.value}" for match in matches
    )


def test_status_ledger_exposes_no_mutation_routes() -> None:
    ledger_operations: dict[str, set[str]] = {}
    for route in api_router.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/status-ledger"):
            continue
        ledger_operations.setdefault(route.path, set()).update(
            method.lower() for method in route.methods or set()
        )

    assert ledger_operations == {"/api/v1/status-ledger": {"get"}}


def test_no_automatic_merge_path_exists() -> None:
    matches = [
        *[match for path in _python_sources() for match in _python_automatic_merge_matches(path)],
        *[match for path in _workflow_sources() for match in _text_automatic_merge_matches(path)],
    ]

    assert not matches, "Forbidden automatic merge path found:\n" + "\n".join(
        f"- {match.path}: {match.value}" for match in matches
    )


def test_automatic_merge_scanner_catches_split_command_arguments() -> None:
    tree = ast.parse(
        'subprocess.run(["gh", "pr", "merge", "1"])\n'
        'subprocess.run(["git", "push", "origin", "main"])\n'
    )
    path = Path("sample.py")
    matches: list[SourceMatch] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        values = [
            item.value
            for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        sequence = _automatic_merge_sequence(
            tuple(token for value in values for token in _tokens(value))
        )
        if sequence is not None:
            matches.append(SourceMatch(path, " ".join(sequence)))

    assert [match.value for match in matches] == ["gh pr merge", "git push origin main"]


def test_executable_package_intake_still_rejects_closed_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )

    with pytest.raises(PackageSourceError, match="not approved for intake"):
        load_package_intake_payload(
            _closed_fixture(tmp_path),
            source_repository="AlobarQuest/intent-packages",
        )


def test_protocol_fixture_path_is_named_and_guarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    payload = load_protocol_fixture_intake_payload(
        _closed_fixture(tmp_path),
        source_repository="AlobarQuest/intent-packages",
    )

    assert payload["intake_purpose"] == "protocol_fixture"
    assert payload["status_at_intake"] == "closed"
    limitations = payload["verification_limitations"]
    assert isinstance(limitations, dict)
    assert limitations["protocol_fixture_only"] is True
    assert "protocol_fixture" in INTAKE_SOURCES
    assert package_intake._PROTOCOL_FIXTURE_SOURCE == "protocol_fixture"
