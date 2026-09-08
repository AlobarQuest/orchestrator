"""This lane is OUT OF PROCESS, and that is the whole shape of ADR-0002.

It reads reality and files what it saw. It must not be able to reach into the orchestrator's
internals, because a producer that shares a process with the thing it reports on is not an
independent reading of anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCES = sorted(Path("src/revision_watcher").rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_lane_has_source_files_to_scan() -> None:
    """Without this the two scans below pass over an empty list, which is the shape of a guard
    that has quietly stopped guarding."""
    assert len(SOURCES) >= 5


def test_nothing_here_imports_the_orchestrator() -> None:
    offenders = {
        str(path): sorted(m for m in _imports(path) if m.split(".")[0] == "orchestrator")
        for path in SOURCES
    }
    assert not {k: v for k, v in offenders.items() if v}


def test_nothing_here_imports_a_sibling_lane() -> None:
    """The lanes import one another for DOMAIN knowledge and never for plumbing. A lane that
    reached into a sibling for an HTTP client would make an unrelated lane's refactor able to
    break this one's schedule."""
    siblings = {
        "landing_ledger",
        "deploy_watcher",
        "pin_watcher",
        "activation_sweep",
        "estate_lander",
        "inert_lander",
        "work_carrier",
        "work_watcher",
        "bump_proposer",
        "change_proposer",
        "tool_installer",
    }
    offenders = {
        str(path): sorted(m for m in _imports(path) if m.split(".")[0] in siblings)
        for path in SOURCES
    }
    assert not {k: v for k, v in offenders.items() if v}
