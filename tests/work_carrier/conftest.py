"""Keep the carry's tests off the operator's own machine state.

The workability report reads `~/.portfolio/portfolio.json` and a GitHub credential
from the environment by default, which is right in production and wrong in a test:
a suite that reads the operator's home answers differently on a machine where the
nightly sweep has not run, and one that finds a credential would reach the network.
Both are pointed somewhere harmless here rather than in each test, so a test added
later cannot forget.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FIXTURE_PACKAGE = "ws32-approved-software"


@pytest.fixture(autouse=True)
def isolated_workability_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORK_CARRIER_PORTFOLIO", str(tmp_path / "no-portfolio.json"))
    monkeypatch.delenv("WORK_CARRIER_GITHUB_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def emitter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The carrier resolves `orchestrator` from PATH, which is a real deployment requirement.

    `scripts/run-work-carrier.sh` puts the repository venv's `bin` there; a bare `pytest`
    invocation has not. Putting the running interpreter's own directory on PATH is what makes
    the tests below exercise the REAL command rather than skipping to a "not on PATH" refusal,
    and it is the same directory the launcher exports.
    """
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture()
def checkout_root(tmp_path: Path) -> Path:
    """A checkout root laid out the way this machine lays one out.

    The real fixture package is COPIED in rather than symlinked, so the layout the carrier
    resolves (`<root>/<repo>/packages/<package_id>`) is exercised rather than assumed.
    """
    source = Path("tests/fixtures/intent-packages") / FIXTURE_PACKAGE
    target = tmp_path / "intent-packages" / "packages" / FIXTURE_PACKAGE
    target.parent.mkdir(parents=True)
    target.mkdir()
    for path in source.iterdir():
        (target / path.name).write_bytes(path.read_bytes())
    return tmp_path
