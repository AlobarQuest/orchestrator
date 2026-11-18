"""`scripts/sds-cred-registries.sh`'s `sds_infraops_session`: a person's infraops command in the
rotation proposer's lane container, between passes (docs/operations/deploy.md).

The pass's own use of the helper is held by `test_run_rotation_proposer_lane.py`. Here `git`, `node`
and `id` are stubs on PATH, and the session runs under `env -i` with what a `docker exec` into the
lane's container inherits from its compose: the lane's token and `SECURITY_DRIFT_STATE_DIR`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.scripts.test_run_rotation_proposer_lane import _GIT, REGISTRIES, _executable

HELPER = Path("scripts/sds-cred-registries.sh").resolve()

# node: logs its argv, the two infraops settings, whether the lane's token reached it, and the list.
_NODE = """#!/bin/bash
{
  echo "node $*"
  echo "config=$INFRADRIFT_CONFIG_DIR state=$SECURITY_DRIFT_STATE_DIR \
token=${BWS_ACCESS_TOKEN:+set}"
  sed 's/^/list /' "$INFRADRIFT_CONFIG_DIR/cred-consumers.list"
} >> "$NODE_LOG"
exit "${FAKE_NODE_RC:-0}"
"""


def _session(tmp_path: Path, *args: str, uid: str = "1000", **fake: str | None) -> tuple:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "git", _GIT)
    _executable(stubs / "node", _NODE)
    _executable(stubs / "id", f"#!/bin/sh\necho {uid}\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "cred-rotation-state.json").write_text("{}\n")
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("node", "git")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "TMPDIR": str(scratch),
        "BWS_ACCESS_TOKEN": "a-lane-token",
        "SECURITY_DRIFT_STATE_DIR": str(state),
        "NODE_LOG": str(logs["node"]),
        "GIT_LOG": str(logs["git"]),
        **fake,
    }
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            *(f"{k}={v}" for k, v in env.items() if v is not None),
            "/bin/bash",
            "-c",
            f'source "{HELPER}" && sds_infraops_session "$@"',
            "_",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = {name: log.read_text().splitlines() for name, log in logs.items()}
    return result, lines, scratch, state


def test_the_session_runs_infraops_from_its_own_clones_and_list(tmp_path: Path) -> None:
    result, logs, scratch, state = _session(
        tmp_path, "resolve-exposure", "--cred", "a-cred", "--exposure", "e-1"
    )
    assert result.returncode == 0, result.stderr
    node, settings, *listed = logs["node"]
    root = node.split()[1].removesuffix("/infraops-mcp-server/dist/cli/security-drift-cli.js")
    assert node == (
        f"node {root}/infraops-mcp-server/dist/cli/security-drift-cli.js "
        "resolve-exposure --cred a-cred --exposure e-1"
    )
    assert Path(root).parent == scratch
    assert settings == f"config={root}/infra-drift state={state} token="
    assert listed == [f"list {root}/{repository}/.cred-consumers.toml" for repository in REGISTRIES]
    assert [line.split()[-1] for line in logs["git"] if " | " in line] == [
        "lane_token=" for _ in REGISTRIES
    ]
    # Its clones and list are discarded after.
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("rc", [0, 1, 2])
def test_the_session_exits_with_infraops_code(tmp_path: Path, rc: int) -> None:
    result, *_ = _session(tmp_path, "record-rotation", FAKE_NODE_RC=str(rc))
    assert result.returncode == rc


def test_the_session_refuses_root(tmp_path: Path) -> None:
    result, logs, *_ = _session(tmp_path, "resolve-exposure", uid="0")
    assert result.returncode == 1
    assert "FATAL: run this as the lane's user" in result.stderr
    assert logs["git"] == [] and logs["node"] == []


@pytest.mark.parametrize("given", [None, ""], ids=["unset", "empty"])
def test_the_session_refuses_without_the_state_directory(tmp_path: Path, given: str | None) -> None:
    result, logs, *_ = _session(tmp_path, "resolve-exposure", SECURITY_DRIFT_STATE_DIR=given)
    assert result.returncode == 2
    assert "FATAL: SECURITY_DRIFT_STATE_DIR is not set" in result.stderr
    assert logs["git"] == [] and logs["node"] == []


def test_the_session_refuses_a_missing_state_file(tmp_path: Path) -> None:
    """infraops would start a new, empty state file, without the resolved exposures and recorded
    rotations the cut-over copy carries."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result, logs, *_ = _session(tmp_path, "record-rotation", SECURITY_DRIFT_STATE_DIR=str(empty))
    assert result.returncode == 2
    assert "FATAL: no cred-rotation-state.json" in result.stderr
    assert logs["git"] == [] and logs["node"] == []


def test_a_clone_without_its_registry_file_stops_the_session(tmp_path: Path) -> None:
    result, logs, scratch, _ = _session(
        tmp_path, "record-rotation", FAKE_NO_REGISTRY="change-manager"
    )
    assert result.returncode == 2
    assert "FATAL: no readable .cred-consumers.toml at main in AlobarQuest/change-manager" in (
        result.stderr
    )
    assert logs["node"] == []
    assert list(scratch.iterdir()) == []


def test_a_failed_clone_stops_the_session(tmp_path: Path) -> None:
    result, logs, scratch, _ = _session(
        tmp_path, "record-rotation", FAKE_CLONE_FAILS="orchestrator"
    )
    assert result.returncode == 1
    assert "FATAL: could not clone https://github.com/AlobarQuest/orchestrator.git" in result.stderr
    assert logs["node"] == []
    assert list(scratch.iterdir()) == []
