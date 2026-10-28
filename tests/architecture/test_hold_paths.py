"""Every merge path and every propose path is classified for holds (bridge layer Part 4).

A hold is a version ceiling the member record places on one dependency. It is only as good as the
paths that ask it: one merge path that never reads the record lands the held version, whatever
every other path does. So each path that lands or publishes a change (every entry of
`MERGE_EXEMPT_PATHS`, the merge guard's list of files that merge) and each path that proposes a
bump is classified here, once, as one of:

- `Honours`: it applies the shared matcher itself, in the named function of the named module;
- `CoveredBy`: it never decides, and whatever it starts can only land through the named gates,
  each of which honours;
- `NotABump`: what it lands or publishes is not a dependency bump, for the stated reason.

A new entry in `MERGE_EXEMPT_PATHS` fails here until it is classified. The propose paths have no
such list to read, so a new propose path is added here by hand.

**How the `Honours` check reads, and how it can be fooled.** It parses the named module, finds the
named function, and requires its body to reference a matcher name (`factory_breaches`,
`exceeded`, or `version_ceiling_term`, which itself is held to call `exceeded`), the module to
import from a matcher module, the function to be called somewhere in its module or in the path's
file, and the path's file to import the module when they differ. It reads names, not behaviour:
a function that calls the matcher and ignores the answer, calls it behind a branch that never
runs, or passes it no holds would pass; so would a local function that shadows a matcher name in
a module that also imports the real one. The behaviour is pinned by each path's own tests; this
pins that no path has stopped asking.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.architecture.test_wsp21_invariant_scan import MERGE_EXEMPT_PATHS

SRC = Path("src")

# The shared matcher's entry points: the two copies of `holds.py` (held equal by
# `tests/services/test_hold_agreement.py`), and the landing term that wraps one of them.
MATCHER_NAMES = frozenset({"factory_breaches", "exceeded", "version_ceiling_term"})
MATCHER_MODULES = frozenset(
    {
        "orchestrator.services.membership.holds",
        "sds_members.holds",
        "orchestrator.services.landing.terms",
    }
)
TERM_MODULE = SRC / "orchestrator/services/landing/terms.py"


@dataclass(frozen=True)
class Honours:
    module: Path
    function: str


@dataclass(frozen=True)
class CoveredBy:
    gates: tuple[Path, ...]
    reason: str


@dataclass(frozen=True)
class NotABump:
    reason: str


LANDING = SRC / "orchestrator/services/landing"
FACTORY_MERGE = LANDING / "pr_merge.py"
ESTATE_MERGE = LANDING / "estate_pr_merge.py"
INERT_MERGE = LANDING / "inert_pr_merge.py"
CARRIER = SRC / "work_carrier/cli.py"

HOLD_PATHS: dict[Path, Honours | CoveredBy | NotABump] = {
    # Merge paths: every entry of MERGE_EXEMPT_PATHS.
    FACTORY_MERGE: Honours(LANDING / "pr_merge_admission.py", "_hold_term"),
    ESTATE_MERGE: Honours(LANDING / "estate_landing_admission.py", "_remote_terms"),
    INERT_MERGE: Honours(LANDING / "inert_landing_admission.py", "_remote_terms"),
    SRC / "bump_proposer/standing.py": NotABump(
        "publishes a commit of a standing package revision to intent-packages: the revision "
        "describes a bump, and the bump itself lands only through the factory merge"
    ),
    SRC / "tool_installer/plugin.py": NotABump(
        "moves one `version` line of one plugin in Devon's marketplace manifest to what the "
        "reviewed fork's serving clone declares; no dependency of a member changes"
    ),
    SRC / "fork_sync/acknowledge.py": NotABump(
        "an operator-run admin merge of its own pull request, whose diff is the fork's "
        "acknowledgements file alone"
    ),
    SRC / "fork_sync/onboarding.py": NotABump(
        "an operator-run merge of the three pull requests onboarding one fork opens itself: the "
        "fork's scaffold, factory-runner's registry entry and change-manager's policy version"
    ),
    # Propose paths: no list names them, so each is named here.
    SRC / "bump_proposer/cli.py": Honours(SRC / "bump_proposer/cli.py", "_held"),
    CARRIER: Honours(SRC / "work_carrier/prepare.py", "held_refusal"),
    SRC / "change_proposer/cli.py": CoveredBy(
        (ESTATE_MERGE,),
        "proposes deploy change records for Dependabot pull requests in deploying repositories; "
        "the estate admission refuses a held one (`landing_version_ceiling_exceeded`) and the "
        "estate lander's close act (`services/landing/held_close.py`) closes it",
    ),
    SRC / "orchestrator/services/intake/package_intake.py": CoveredBy(
        (CARRIER, FACTORY_MERGE),
        "registers the work a change record caused: the carrier refuses a held record before it "
        "registers, and a unit registered any other way lands only through the factory merge",
    ),
    SRC / "orchestrator/services/execution/dispatch.py": CoveredBy(
        (FACTORY_MERGE,),
        "starts a unit's run, whose pull request lands only through the factory merge",
    ),
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _imported_modules(tree: ast.Module) -> set[str]:
    """Every module a file imports from, `from a import b` also counted as importing `a.b`."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    return next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name),
        None,
    )


def _referenced_names(node: ast.AST) -> set[str]:
    return {
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
    }


def _called(tree: ast.AST, name: str, outside: ast.AST | None = None) -> bool:
    """Whether `name` is called anywhere in `tree` other than inside `outside`."""
    skipped = set() if outside is None else {id(child) for child in ast.walk(outside)}
    return any(
        isinstance(node, ast.Call)
        and id(node) not in skipped
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(tree)
    )


def _honours_failures(path: Path, honours: Honours) -> list[str]:
    """Why `path` does not honour holds as its entry claims; empty when it does."""
    if not honours.module.exists():
        return [f"{honours.module} does not exist"]
    tree = _tree(honours.module)
    function = _function(tree, honours.function)
    if function is None:
        return [f"{honours.module} defines no top-level function {honours.function}"]
    failures = []
    if not _referenced_names(function) & MATCHER_NAMES:
        failures.append(
            f"{honours.module}::{honours.function} references none of {sorted(MATCHER_NAMES)}"
        )
    if not _imported_modules(tree) & MATCHER_MODULES:
        failures.append(f"{honours.module} imports from none of {sorted(MATCHER_MODULES)}")
    path_tree = tree if path == honours.module else _tree(path)
    if not (_called(tree, honours.function, function) or _called(path_tree, honours.function)):
        failures.append(f"nothing in {honours.module} or {path} calls {honours.function}")
    if path != honours.module and _dotted(honours.module) not in _imported_modules(path_tree):
        failures.append(f"{path} does not import {_dotted(honours.module)}")
    return failures


def test_every_merge_path_is_classified() -> None:
    unclassified = sorted(str(path) for path in MERGE_EXEMPT_PATHS - HOLD_PATHS.keys())
    assert not unclassified, (
        f"{unclassified} merge but are not classified for holds. Add each to HOLD_PATHS in "
        "tests/architecture/test_hold_paths.py: Honours (the function applying the shared "
        "matcher), CoveredBy (the honouring gates everything it starts lands through) or "
        "NotABump (why what it lands is not a dependency bump)."
    )


def test_every_classified_path_exists() -> None:
    missing = sorted(str(path) for path in HOLD_PATHS if not path.exists())
    assert not missing, f"HOLD_PATHS names files that no longer exist: {missing}"


@pytest.mark.parametrize(
    "path",
    [path for path, entry in HOLD_PATHS.items() if isinstance(entry, Honours)],
    ids=str,
)
def test_an_honouring_path_asks_the_shared_matcher(path: Path) -> None:
    entry = HOLD_PATHS[path]
    assert isinstance(entry, Honours)
    failures = _honours_failures(path, entry)
    assert not failures, f"{path} is classified Honours, but: {'; '.join(failures)}"


@pytest.mark.parametrize(
    "path",
    [path for path, entry in HOLD_PATHS.items() if isinstance(entry, CoveredBy)],
    ids=str,
)
def test_a_covered_path_is_covered_by_honouring_gates(path: Path) -> None:
    entry = HOLD_PATHS[path]
    assert isinstance(entry, CoveredBy)
    assert entry.gates, f"{path} is CoveredBy no gate"
    not_honouring = [
        str(gate) for gate in entry.gates if not isinstance(HOLD_PATHS.get(gate), Honours)
    ]
    assert not not_honouring, f"{path} is covered by gates that are not Honours: {not_honouring}"


def test_every_classification_states_its_reason() -> None:
    blank = [
        str(path)
        for path, entry in HOLD_PATHS.items()
        if isinstance(entry, (CoveredBy, NotABump)) and not entry.reason.strip()
    ]
    assert not blank, f"classified with no reason: {blank}"


def test_the_landing_term_asks_the_matcher() -> None:
    """`version_ceiling_term` counts as the matcher only because it calls `exceeded`."""
    tree = _tree(TERM_MODULE)
    function = _function(tree, "version_ceiling_term")
    assert function is not None, f"{TERM_MODULE} no longer defines version_ceiling_term"
    assert _called(function, "exceeded"), "version_ceiling_term no longer calls exceeded"
    assert "orchestrator.services.membership.holds.exceeded" in _imported_modules(tree)


def test_the_honours_check_fires_on_a_path_that_stopped_asking(tmp_path: Path) -> None:
    """Control: a function that names no matcher, in a module that imports none, fails."""
    module = tmp_path / "admission.py"
    module.write_text(
        "from x import release\n\n"
        "def _hold_term(holds):\n    return release(holds)\n\n"
        "def admit(holds):\n    return _hold_term(holds)\n",
        encoding="utf-8",
    )
    failures = _honours_failures(module, Honours(module, "_hold_term"))
    assert any("references none of" in failure for failure in failures)
    assert any("imports from none of" in failure for failure in failures)


def test_the_honours_check_passes_a_path_that_asks(tmp_path: Path) -> None:
    """Control: the same module calling the matcher, imported from a matcher module, passes."""
    module = tmp_path / "admission.py"
    module.write_text(
        "from sds_members import holds as hold_matcher\n\n"
        "def _hold_term(holds, bump):\n"
        "    return hold_matcher.factory_breaches(holds, 'r', bump)\n\n"
        "def admit(holds, bump):\n    return _hold_term(holds, bump)\n",
        encoding="utf-8",
    )
    assert _honours_failures(module, Honours(module, "_hold_term")) == []
