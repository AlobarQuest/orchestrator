"""Keep the carry's tests off the operator's own machine state.

The workability report reads `~/.portfolio/portfolio.json` and a GitHub credential
from the environment by default, which is right in production and wrong in a test:
a suite that reads the operator's home answers differently on a machine where the
nightly sweep has not run, and one that finds a credential would reach the network.
Both are pointed somewhere harmless here rather than in each test, so a test added
later cannot forget.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from work_carrier.declaration import Declaration

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
def workable_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An estate in which the fixture record's target repository is workable on all three.

    **REQUESTED, NEVER AUTOUSE, and that is the whole safety of it.** The carry now REFUSES a
    record whose repository does not answer yes three times, so without this every end-to-end
    test in `test_work_carrier.py` would hold its record and assert nothing about the carry. An
    ambient permissive default would do the same job and would also silently disarm the two tests
    in `test_workability.py` that reach the environment path on purpose -- the absent-credential
    one and the credential-never-printed one -- turning a fail-closed control into a pass. So the
    permissive estate is declared by whoever wants it.

    It supplies INPUTS rather than a stubbed judge: a real `portfolio.json` with every check the
    constraints read passing, and a declaration source that answers `true` without a network. The
    composition under test is therefore the real one, and it answers yes because the estate says
    yes. The refusals themselves are exercised in `test_workability.py`.
    """
    portfolio = tmp_path / "workable-portfolio.json"
    portfolio.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "infraops-mcp-server",
                        "path": "/anywhere/infraops-mcp-server",
                        "factory": [
                            {"id": check, "status": status, "details": [], "fix": None}
                            for check, status in (
                                ("runner.caller", "pass"),
                                ("factory.secrets", "pass"),
                                ("factory.landing_known", "pass"),
                                ("factory.pat_access", "pass"),
                                ("factory.pat_scope", "unknown"),
                            )
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORK_CARRIER_PORTFOLIO", str(portfolio))
    monkeypatch.setattr(
        "work_carrier.workability.from_environment",
        lambda **_: _Declares(
            Declaration(True, "factory-target.toml declares factory_target = true")
        ),
    )


class _Declares:
    """A declaration source that answers without a network, and closes like the real one."""

    def __init__(self, answer: Declaration) -> None:
        self._answer = answer

    def declaration(self, slug: str) -> Declaration:
        return self._answer

    def close(self) -> None:
        return None


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
