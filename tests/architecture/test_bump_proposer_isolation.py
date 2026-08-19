"""`bump_proposer` is a separate program that happens to live in this repository.

Same shape as the watcher's, the ledger's, the lander's and the carry's isolation tests, and
for the same reason: hosting an out-of-process program here is a packaging choice, and the
moment it can import the orchestrator it stops being one.

**IT MATTERS PARTICULARLY HERE, because this program's whole judgment is a REPRODUCTION.** It
decides which bumps the auto-merge cascade refuses, and it does so from `landing_ledger.rules`
-- the hand-transcribed registry of what each revision of that gate actually said. A program
that could import the orchestrator could reach the admission module that answers an adjacent
question about the same pull requests, and the two answers would drift into each other.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROPOSER = Path("src/bump_proposer")
ORCHESTRATOR = Path("src/orchestrator")

# Everything the program may import at the top level. `httpx` and the standard library, plus
# `landing_ledger` -- reused rather than re-implemented, because the transcribed gate registry
# and the update-metadata reader are exactly what this producer needs and a second copy of
# either would be a second answer to "what did the gate say".
ALLOWED_TOP_LEVEL = {
    "__future__",
    "argparse",
    "bump_proposer",
    "dataclasses",
    "httpx",
    "json",
    "landing_ledger",
    "os",
    "pathlib",
    "re",
    "subprocess",
    "sys",
    "typing",
}


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
    return names


def test_the_producer_imports_nothing_from_the_orchestrator() -> None:
    offenders = {name for name in _imports(PROPOSER) if name.split(".")[0] == "orchestrator"}
    assert offenders == set()


def test_the_orchestrator_imports_nothing_from_the_producer() -> None:
    offenders = {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "bump_proposer"}
    assert offenders == set()


def test_the_producers_third_party_deps_are_confined() -> None:
    offenders = {name.split(".")[0] for name in _imports(PROPOSER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_the_producer_cannot_reach_the_orchestrators_api_at_all() -> None:
    """It writes to change-manager and to a checkout, and to nothing else.

    The orchestrator learns about this work through the carry, from an APPROVED record -- so a
    producer that could register an intake itself would be the machine approving its own
    proposal, which ADR-0026 deliberately did not decide.
    """
    text = "\n".join(path.read_text() for path in sorted(PROPOSER.rglob("*.py")))
    for forbidden in ("/api/v1/", "sds.alobar.net", "package-intakes"):
        assert forbidden not in text, forbidden
