"""The generator is run as one read-only command, and what it answers is what is posted."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bump_driver.cli import _generator_environment
from bump_driver.decompose import (
    DecomposeRefused,
    Generator,
    factory_binary,
    tooling_of,
)
from bump_driver.mirror import Mirrors

REPOSITORY = "AlobarQuest/orchestrator"
REVISION = "f962475a-1964-46df-81ac-2b3029dec2a7"
INTAKE: dict[str, Any] = {
    "id": REVISION,
    "package_id": "orchestrator-uv-httpx2",
    "enforcement_snapshot": {
        "profile_fields": {
            "package": "httpx2",
            "from_version": "2.13.1",
            "to_version": "2.16.0",
            "target_repo": REPOSITORY,
        }
    },
}
ENV = {"PATH": "/p", "HOME": "/h", "CODE_BRAIN_KEY": "c"}


def _generator(run: Any) -> Generator:
    return Generator("/bin/factory", ENV, run=run)


def _writes(body: dict[str, Any]) -> tuple[list[list[str]], list[dict[str, Any]], Any]:
    calls: list[list[str]] = []
    keywords: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        keywords.append(kwargs)
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(body))
        return subprocess.CompletedProcess(argv, 0, "", "")

    return calls, keywords, run


def test_the_command_is_decompose_no_execute_with_the_revisions_fields(tmp_path: Path) -> None:
    calls, keywords, run = _writes({"proposed_units": [{}], "idempotency_key": "theirs"})
    body = _generator(run)(INTAKE, REPOSITORY, tmp_path)
    [argv] = calls
    out = argv[argv.index("--out") + 1]
    intake_file = argv[argv.index("--intake-file") + 1]
    assert argv == [
        "/bin/factory",
        "decompose",
        "--revision",
        REVISION,
        "--ac",
        "AC-001",
        "--target-repo",
        REPOSITORY,
        "--repo-path",
        str(tmp_path),
        "--tooling",
        "uv",
        "--package",
        "httpx2",
        "--from",
        "2.13.1",
        "--to",
        "2.16.0",
        "--out",
        out,
        "--intake-file",
        intake_file,
        "--no-execute",
        "--assume-checkout-current",
    ]
    assert "--submit" not in argv
    assert keywords[0]["env"] == ENV
    assert Path(keywords[0]["cwd"]) != tmp_path
    assert not str(keywords[0]["cwd"]).startswith(str(tmp_path))
    assert body["proposed_units"] == [{}]


def test_the_exact_environment_factory_gets_holds_no_credential(
    tmp_path: Path, monkeypatch
) -> None:
    """Its conformance claim runs `code-standards` in the mirror with this whole environment."""
    poisoned = {
        "PATH": "/p",
        "HOME": "/h",
        "FACTORY_PYTHONPATH": "/standards",
        "CODE_BRAIN_KEY": "code",
        "INFRA_BRAIN_KEY": "infra",
        "ORCHESTRATOR_API_TOKEN": "SECRET-SYSTEM",
        "ORCHESTRATOR_SYSTEM_TOKEN": "SECRET-SYSTEM",
        "CHANGE_MANAGER_TOKEN": "SECRET-CM",
        "BUMP_DRIVER_GITHUB_TOKEN": "SECRET-GITHUB",
        "GITHUB_TOKEN": "SECRET-GITHUB",
        "GH_TOKEN": "SECRET-GITHUB",
        "BWS_ACCESS_TOKEN": "SECRET-BWS",
        "COOLIFY_TOKEN": "SECRET-COOLIFY",
        "SDS_BRIDGE_ALERT_TOKEN": "SECRET-ALERT",
    }
    for name, value in poisoned.items():
        monkeypatch.setenv(name, value)
    mirrors = Mirrors(tmp_path, {}, token="SECRET-GITHUB")
    _, keywords, run = _writes({"proposed_units": [{}]})
    Generator("/bin/factory", _generator_environment(poisoned, mirrors), run=run)(
        INTAKE, REPOSITORY, tmp_path
    )
    environment = keywords[0]["env"]
    assert set(environment) == {
        "PATH",
        "HOME",
        "PYTHONPATH",
        "CODE_BRAIN_KEY",
        "INFRA_BRAIN_KEY",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    }
    assert environment["PYTHONPATH"] == "/standards"
    assert environment["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert not any("SECRET" in value for value in environment.values())


def test_the_intake_file_is_the_intake_the_lane_read(tmp_path: Path) -> None:
    read: list[Any] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        read.append(json.loads(Path(argv[argv.index("--intake-file") + 1]).read_text()))
        Path(argv[argv.index("--out") + 1]).write_text('{"proposed_units": [{}]}')
        return subprocess.CompletedProcess(argv, 0, "", "")

    _generator(run)(INTAKE, REPOSITORY, tmp_path)
    assert read == [INTAKE]


def test_the_generators_idempotency_key_is_dropped_for_the_lane_to_set(tmp_path: Path) -> None:
    _, _, run = _writes({"proposed_units": [{}], "idempotency_key": "theirs"})
    assert "idempotency_key" not in _generator(run)(INTAKE, REPOSITORY, tmp_path)


@pytest.mark.parametrize(
    ("package_id", "tooling"),
    [
        ("orchestrator-uv-httpx2", "uv"),
        ("orchestrator-pip-requests", "pip"),
        ("orchestrator-npm-left-pad", "npm"),
    ],
)
def test_tooling_is_the_generators_word_not_dependabots(package_id: str, tooling: str) -> None:
    assert tooling_of(package_id, REPOSITORY) == tooling


def test_a_package_id_naming_no_tooling_has_none() -> None:
    assert tooling_of("orchestrator-cargo-serde", REPOSITORY) is None


def test_a_non_zero_exit_is_refused_with_its_last_line(tmp_path: Path) -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "noise\ndecompose failed: no pin site\n")

    with pytest.raises(DecomposeRefused, match="no pin site"):
        _generator(run)(INTAKE, REPOSITORY, tmp_path)


def test_a_hang_is_refused(tmp_path: Path) -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 1)

    with pytest.raises(DecomposeRefused, match="timed out"):
        _generator(run)(INTAKE, REPOSITORY, tmp_path)


def test_a_missing_binary_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DecomposeRefused, match="factory"):
        Generator(None, ENV)(INTAKE, REPOSITORY, tmp_path)


def test_an_unstartable_binary_is_refused(tmp_path: Path) -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("no such file")

    with pytest.raises(DecomposeRefused, match="could not start"):
        _generator(run)(INTAKE, REPOSITORY, tmp_path)


@pytest.mark.parametrize("text", ["not json", "[]", '{"proposed_units": []}', "{}"])
def test_an_unreadable_proposal_is_refused(tmp_path: Path, text: str) -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        Path(argv[argv.index("--out") + 1]).write_text(text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(DecomposeRefused, match="proposal"):
        _generator(run)(INTAKE, REPOSITORY, tmp_path)


def test_a_field_that_is_missing_is_refused_before_the_command(tmp_path: Path) -> None:
    intake = {**INTAKE, "enforcement_snapshot": {"profile_fields": {"package": "httpx2"}}}

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("must not run")

    with pytest.raises(DecomposeRefused, match="profile_fields"):
        _generator(run)(intake, REPOSITORY, tmp_path)


def test_factory_is_found_on_path_then_in_local_bin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/on/path/{name}")
    assert factory_binary(tmp_path) == "/on/path/factory"
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert factory_binary(tmp_path) is None
    local = tmp_path / ".local/bin"
    local.mkdir(parents=True)
    (local / "factory").write_text("")
    assert factory_binary(tmp_path) == str(local / "factory")
