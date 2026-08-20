"""`scripts/run-work-carrier.sh` orchestrates two programs (ADR-0029). This tests the shell.

**THE TWO THINGS THIS FILE EXISTS FOR ARE BOTH INVISIBLE TO EVERY PYTHON TEST**, and both are the
reason the change was made:

1. **The retirement runs BEFORE the carry.** Reversed, the carry reads an approved listing that
   still contains a finished record, re-registers its revision, and draws the 409 this whole
   change exists to remove -- then watches the record be retired a second later. The pass would
   report a finding on the morning the defect was fixed. Nothing in either program can see the
   order; only the script decides it.
2. **The exit-code fold.** The `for rc in 1 3 2` form these launchers have used lets any code
   outside {0,1,2,3} fall through to `exit 0`, so a scheduled job that never ran reports a clean
   pass. 127 -- a missing binary -- is the one that actually happens.

The REAL script is executed against STUB programs. `REPO_ROOT` resolves off `BASH_SOURCE`, so
copying the script into a temporary tree whose `.venv/bin` holds two shell stubs makes the script
invoke them exactly as it would invoke the real ones. Every credential the script would otherwise
fetch is pre-set in the environment, which is the same `${VAR:-...}` escape hatch the script
already offers -- so no BWS call is made and no secret is involved.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-work-carrier.sh")

# Every override the script honours, so it fetches nothing. Values are placeholders and are never
# sent anywhere: the stubs below do not make requests.
CREDENTIALS = {
    "BWS_ACCESS_TOKEN_BROAD": "broad-identity",
    "BWS_ACCESS_TOKEN_SDS": "sds-identity",
    "CHANGE_MANAGER_TOKEN": "read-bearer",
    "WORK_CARRIER_ORCHESTRATOR_TOKEN": "system-bearer",
    "WORK_WATCHER_CHANGE_MANAGER_TOKEN": "propose-bearer",
}

_STUB = """#!/usr/bin/env bash
echo "{name} $*" >> "$INVOCATION_LOG"
exit {code}
"""


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A tree shaped the way the script expects to find itself in one."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(LAUNCHER, tmp_path / "scripts" / LAUNCHER.name)
    return tmp_path


def _stub(tree: Path, name: str, code: int) -> None:
    path = tree / ".venv" / "bin" / name
    path.write_text(_STUB.format(name=name, code=code))
    path.chmod(0o755)


def _no_bws(tree: Path) -> str:
    """A PATH whose `bws` always fails, so no test can reach the real secret manager.

    Needed for the skipped-watcher cases below: they leave the orchestrator bearer unset, and the
    script's own fallback would otherwise read this machine's Keychain and make a LIVE BWS call --
    which would fetch a real credential and stop the branch under test from ever firing. Making
    the fetch fail reproduces "this machine cannot read that project" without touching either.
    """
    stub_bin = tree / "stubbin"
    stub_bin.mkdir(exist_ok=True)
    bws = stub_bin / "bws"
    bws.write_text("#!/usr/bin/env bash\nexit 1\n")
    bws.chmod(0o755)
    return f"{stub_bin}{os.pathsep}{os.environ['PATH']}"


def _run(tree: Path, *args: str, watcher: int = 0, carrier: int = 0, **env_overrides):
    _stub(tree, "work-watcher", watcher)
    _stub(tree, "work-carrier", carrier)
    log = tree / "invocations.log"
    env = {
        **os.environ,
        **CREDENTIALS,
        "PATH": _no_bws(tree),
        **env_overrides,
        "INVOCATION_LOG": str(log),
    }
    result = subprocess.run(
        ["bash", str(tree / "scripts" / LAUNCHER.name), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    # `rstrip`, because the stub's `$*` is empty on a bare invocation and leaves a trailing
    # space. That is an artefact of the stub, not of the script.
    invocations = [ln.rstrip() for ln in log.read_text().splitlines()] if log.exists() else []
    return result, invocations


# --- the order ---------------------------------------------------------------------------------


def test_the_retirement_runs_before_the_carry(tree: Path) -> None:
    """THE acceptance test for the ordering, asserted on what ran and in which order."""
    _, invocations = _run(tree, "--register")

    assert [line.split()[0] for line in invocations] == ["work-watcher", "work-carrier"]


def test_an_acting_pass_asks_the_watcher_to_retire_and_the_carry_to_register(tree: Path) -> None:
    """The flags are translated, not forwarded. `--register` is the carry's word for acting;
    `--retire` is the watcher's, and a pass that acts must do both."""
    _, invocations = _run(tree, "--register")

    assert invocations[0] == "work-watcher --retire"
    assert invocations[1] == "work-carrier --register"


def test_a_bare_pass_asks_neither_to_act(tree: Path) -> None:
    """A bare invocation writes nothing to either system, which is the mode the lane is
    inspected in. Both programs still RUN -- reporting is the point."""
    _, invocations = _run(tree)

    assert invocations == ["work-watcher", "work-carrier"]


# --- the exit-code fold ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("watcher", "carrier", "expected"),
    [
        (0, 0, 0),
        (0, 3, 3),
        (3, 0, 3),
        (3, 3, 3),
        # A tool failure outranks a finding in either position: something could not be measured,
        # so the findings that were reported are not the whole answer.
        (1, 3, 1),
        (3, 1, 1),
        # Unusable input outranks a tool failure -- the pass could not even start properly.
        (2, 1, 2),
        (1, 2, 2),
    ],
)
def test_the_worst_outcome_of_the_two_phases_wins(
    tree: Path, watcher: int, carrier: int, expected: int
) -> None:
    result, _ = _run(tree, "--register", watcher=watcher, carrier=carrier)

    assert result.returncode == expected


@pytest.mark.parametrize("position", ["watcher", "carrier"])
def test_an_unrecognised_code_is_preserved_and_dominates(tree: Path, position: str) -> None:
    """THE defect the ranking exists to prevent, in both positions.

    127 is a missing binary. Under the `for rc in 1 3 2` fold it falls through to `exit 0`, so a
    scheduled job that never ran reports a clean pass -- the permanently-quiet failure, which is
    worse than a permanently-red one because nothing ever asks about it.
    """
    codes = {"watcher": {"watcher": 127, "carrier": 3}, "carrier": {"watcher": 3, "carrier": 127}}
    result, _ = _run(tree, "--register", **codes[position])

    assert result.returncode == 127


# --- the reporting pass on a machine that cannot read the orchestrator --------------------------


def test_a_reporting_pass_without_an_orchestrator_credential_skips_the_watcher(
    tree: Path,
) -> None:
    """Inspecting what would happen must not require the right to make it happen.

    The watcher needs a READ against the orchestrator to learn what is complete, and a reporting
    pass is deliberately not given that credential. Skipping the phase keeps this file's own
    header promise -- that a bare invocation works anywhere -- where folding the watcher's
    `unusable` would report the whole pass broken for a phase it was never entitled to run.
    """
    result, invocations = _run(tree, WORK_CARRIER_ORCHESTRATOR_TOKEN="")

    assert [line.split()[0] for line in invocations] == ["work-carrier"]
    assert "[SKIPPED]" in result.stdout
    assert result.returncode == 0


def test_the_skipped_watcher_contributes_no_exit_code(tree: Path) -> None:
    """The control for the case above: the carry's own outcome still decides the pass, so the
    skip is genuinely silent rather than a swallowed failure."""
    result, _ = _run(tree, carrier=3, WORK_CARRIER_ORCHESTRATOR_TOKEN="")

    assert result.returncode == 3
