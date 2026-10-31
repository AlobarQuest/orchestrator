"""Which way the landing package's imports point, and that no name is reached by re-export.

`services/landing/` holds two lanes that land Dependabot pull requests -- the estate lane (landing
changes something already serving) and the inert lane (it does not) -- plus the unit-bound lane
(`pr_merge`) and the sources all three read. The two Dependabot lanes speak one vocabulary, and
until Tier 2 wave 2 that vocabulary lived inside the estate lane: the inert admission imported the
estate admission for its refusal codes and terms, and imported the estate ACT for two merge-method
constants. `interfaces` (types, protocols, constants) and `terms` (the shared term builders) now
hold it, and this module says which way everything else may point.

The rules, each keyed on two classifications that must each cover the whole package -- which lane
a module belongs to, and which role it plays -- so a module nobody classified fails
`test_every_landing_module_is_classified` rather than escaping the rules:

* an inert module imports from the estate lane only the one name ADR-0038 part 2 keeps there,
  the real GitHub gateway the inert act subclasses; an estate module imports nothing inert;
* no admission module imports an act module -- an admission answers, an act does, and a constant
  both need belongs in `interfaces`;
* `interfaces` imports nothing from the package and no HTTP client, so every party can reach it;
* `terms` imports only `interfaces` from the package;
* `lane_act`, which holds the steps both lanes' acts share, imports no lane module and names no
  landing call -- each lane makes its own, in its own module, which is the file the repository's
  guard against unattended landings lists for that lane's recorded reason.

`estate_landing` is NOT in the estate lane despite its name: it is the client for the estate's own
answer about what landing on a repository does, which all three lanes read.

The last test is what makes the others worth having. A name moved out of a module stays bound in
it wherever that module still imports it for its own use, so `from old import name` keeps working
and a stale importer is invisible -- to the rules above, and to `test_unreachable_guards`, which
drops an edge through a re-export. So every import of a landing name, including a read through a
module object, must name the module that DEFINES it.

Every spelling of an import counts: `from <package>.m import name`, `from <package> import m`,
`import <package>.m` and a relative import (which `test_layering` also refuses outright). Importing
the package object itself is refused here, because `landing.m.name` is a read no scan can attribute.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.import_scan import file_import_names
from tests.architecture.test_wsp21_invariant_scan import HTTP_CLIENTS

PACKAGE = "orchestrator.services.landing"
PARENT, _, LEAF = PACKAGE.rpartition(".")
SRC = Path("src")
LANDING = SRC / "orchestrator/services/landing"

# Which lane a module belongs to.
ESTATE_LANE = frozenset({"estate_landing_admission", "estate_pr_merge", "estate_pr_branch_update"})
INERT_LANE = frozenset(
    {
        "inert_landing_admission",
        "inert_landing_policy",
        "inert_pr_merge",
        "inert_pr_branch_update",
        # Landing Part 3a: watching this lane's landings and acting on a bad one.
        "revert_act",
    }
)
SHARED = frozenset(
    {
        # Bridge layer Part 4: one close act for both Dependabot lanes, bounded by each lane's own
        # admission, which it imports and re-composes.
        "held_close",
        "interfaces",
        "terms",
        "branch_update_serialization",
        "lane_act",
        "change_record",
        "estate_landing",
        "pr_merge",
        "pr_merge_admission",
        # Landing Part 3a: judging a landed bump and building its revert each reach GitHub only
        # through their own gateway protocol.
        "revert_build",
        "revert_record",
        "revert_watch",
        # Landing Part 3b: the revert subject's terms, which both lanes' admissions ask, and the
        # hook both lanes' merge paths call when a revert lands.
        "revert_subject",
        "revert_landed",
    }
)

# Which role it plays.
ADMISSIONS = frozenset(
    {"estate_landing_admission", "inert_landing_admission", "pr_merge_admission"}
)
ACTS = frozenset(
    {
        "held_close",
        "estate_pr_merge",
        "estate_pr_branch_update",
        "inert_pr_merge",
        "inert_pr_branch_update",
        "pr_merge",
        "revert_act",
    }
)
NEITHER = frozenset(
    {
        "interfaces",
        "terms",
        "branch_update_serialization",
        "lane_act",
        "change_record",
        "estate_landing",
        "inert_landing_policy",
        "revert_build",
        "revert_record",
        "revert_watch",
        "revert_subject",
        "revert_landed",
    }
)

# ADR-0038 part 2: the inert act's gateway is the estate gateway with the landing renamed
# (`GitHubInertPullRequests(GitHubEstatePullRequests)`), and each lane's remote call stays in that
# lane's own module, which `MERGE_EXEMPT_PATHS` records. Equality rather than a subset, so an
# exemption nothing uses any more fails too.
INERT_MAY_IMPORT_FROM_ESTATE = frozenset(
    {("inert_pr_merge", "estate_pr_merge", "GitHubEstatePullRequests")}
)


def _modules() -> set[str]:
    return {path.stem for path in LANDING.glob("*.py") if path.stem != "__init__"}


def _absolute(node: ast.ImportFrom, path: Path) -> str:
    """The module an ImportFrom names, with a relative one resolved against its file's package."""
    if node.level == 0:
        return node.module or ""
    package = list(path.relative_to(SRC).with_suffix("").parts[:-1])
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _landing_names(path: Path) -> set[tuple[str, str]]:
    """Every (landing module, name) a file imports; importing the module itself is `<module>`.

    The package object is recorded as `(<package>, <leaf>)` so the last test can refuse it.
    """
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.ImportFrom):
            module = _absolute(node, path)
            if module == PACKAGE:
                found |= {(alias.name, "<module>") for alias in node.names}
            elif module.startswith(f"{PACKAGE}."):
                target = module.removeprefix(f"{PACKAGE}.")
                found |= {(target, alias.name) for alias in node.names}
            elif module == PARENT and any(alias.name == LEAF for alias in node.names):
                found.add(("<package>", LEAF))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    found.add((alias.name.removeprefix(f"{PACKAGE}."), "<module>"))
                elif alias.name == PACKAGE:
                    found.add(("<package>", LEAF))
    return found


def _landing_modules_imported(stem: str) -> set[str]:
    return {target for target, _ in _landing_names(LANDING / f"{stem}.py")}


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Dotted expressions that evaluate to a landing MODULE object, mapped to the module.

    `import <package>.m` binds nothing new, but `<package>.m` then evaluates to the module, so
    the full dotted spelling is an alias too.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == PACKAGE:
            aliases |= {alias.asname or alias.name: alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    module = alias.name.removeprefix(f"{PACKAGE}.")
                    aliases[alias.asname or alias.name] = module
    return aliases


def _defined(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(), filename=str(path)).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names |= {target.id for target in node.targets if isinstance(target, ast.Name)}
    return names


def test_every_landing_module_is_classified() -> None:
    present = _modules()
    for sets, what in (
        ((ESTATE_LANE, INERT_LANE, SHARED), "lane"),
        ((ADMISSIONS, ACTS, NEITHER), "role"),
    ):
        union = frozenset().union(*sets)
        assert sum(map(len, sets)) == len(union), f"a module has two {what}s"
        assert union == present, (
            f"no {what}: {sorted(present - union)}; no longer present: {sorted(union - present)}"
        )
    subpackages = [p.name for p in LANDING.iterdir() if p.is_dir() and p.name != "__pycache__"]
    assert not subpackages, f"classify {subpackages} here before nesting modules the rules skip"


def test_an_inert_module_imports_from_the_estate_lane_only_the_real_gateway() -> None:
    reached = {
        (stem, target, name)
        for stem in INERT_LANE
        for target, name in _landing_names(LANDING / f"{stem}.py")
        if target in ESTATE_LANE
    }
    assert reached == INERT_MAY_IMPORT_FROM_ESTATE, (
        "a shared type, constant or term belongs in `interfaces` or `terms`, never in a lane"
    )


def test_an_estate_module_imports_nothing_from_the_inert_lane() -> None:
    offenders = {
        stem: sorted(_landing_modules_imported(stem) & INERT_LANE)
        for stem in ESTATE_LANE
        if _landing_modules_imported(stem) & INERT_LANE
    }
    assert not offenders, offenders


def test_no_admission_module_imports_an_act_module() -> None:
    offenders = {
        stem: sorted(_landing_modules_imported(stem) & ACTS)
        for stem in ADMISSIONS
        if _landing_modules_imported(stem) & ACTS
    }
    assert not offenders, offenders


def test_interfaces_imports_nothing_from_the_package_and_no_http_client() -> None:
    assert not _landing_modules_imported("interfaces")
    imported = file_import_names(LANDING / "interfaces.py")
    assert not imported & HTTP_CLIENTS
    assert not {module.split(".")[0] for module in imported} & HTTP_CLIENTS


def test_terms_imports_only_interfaces_from_the_package() -> None:
    assert _landing_modules_imported("terms") == {"interfaces"}


def test_every_import_of_a_landing_name_names_the_module_that_defines_it() -> None:
    defined = {stem: _defined(LANDING / f"{stem}.py") for stem in _modules()}
    package_names = set().union(*defined.values())
    sources = [
        *SRC.rglob("*.py"),
        *Path("tests").rglob("*.py"),
        *Path("scripts").glob("*.py"),
    ]
    stale: list[str] = []
    for path in sources:
        names = _landing_names(path)
        stale += sorted(
            f"{path}: {name} from {target}"
            for target, name in names
            if target == "<package>"
            or (name != "<module>" and target in defined and name not in defined[target])
        )
        # The same read through a module object. Only a name the package defines ELSEWHERE counts,
        # because `estate_pr_merge.httpx` is a test patching what that module calls, not a
        # re-export.
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _module_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            module = aliases.get(_dotted(node.value) or "")
            if (
                module in defined
                and node.attr in package_names
                and node.attr not in defined[module]
            ):
                stale.append(f"{path}:{node.lineno}: {node.attr} through {module}")
    assert not stale, stale


def test_the_shared_act_steps_name_no_lane_and_no_landing_call() -> None:
    """The remote landing call stays in each lane's own module, where the merge guard's exemption
    for that lane is recorded. A shared module that made it -- under either lane's spelling --
    would put a landing in a file no exemption names, and one the text scan cannot see, since
    `submit_merge` contains neither the REST path nor a `.merge(` call."""
    assert not _landing_modules_imported("lane_act") & (ESTATE_LANE | INERT_LANE)
    tree = ast.parse((LANDING / "lane_act.py").read_text(), filename="lane_act.py")
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attributes & {"merge", "submit_merge"}


def _landing_row_writes(tree: ast.Module, model: str) -> set[str]:
    """Each function here that builds, updates or deletes a landing row, by name.

    A row reaches a function as a parameter annotated `model`, or is built by calling it.
    """
    writers: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        rows = {
            parameter.arg
            for parameter in [*function.args.args, *function.args.kwonlyargs]
            if parameter.annotation is not None and model in ast.unparse(parameter.annotation)
        }
        for node in ast.walk(function):
            written = False
            if isinstance(node, ast.Call):
                written = (
                    isinstance(node.func, ast.Name)
                    and (
                        node.func.id == model
                        or node.func.id in {"update", "delete"}
                        and model in ast.unparse(node)
                    )
                ) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "delete"
                    and any(isinstance(arg, ast.Name) and arg.id in rows for arg in node.args)
                )
            elif isinstance(node, ast.Assign):
                written = any(
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in rows
                    for target in node.targets
                )
            if written:
                writers.add(function.name)
    return writers


def test_only_lane_act_may_write_a_landing_row_and_only_resettle_changes_one() -> None:
    """`_record` writes the row after an act. `_resettle` alone changes or removes one, because a
    row is what bars a second landing."""
    found = {
        (path.name, name)
        for path in sorted((SRC / "orchestrator").rglob("*.py"))
        for name in _landing_row_writes(ast.parse(path.read_text()), "EstatePrMerge")
    }

    assert found == {("lane_act.py", "_record"), ("lane_act.py", "_resettle")}


def test_only_pr_merge_may_write_a_factory_row_and_only_resettle_changes_one() -> None:
    found = {
        (path.name, name)
        for path in sorted((SRC / "orchestrator").rglob("*.py"))
        for name in _landing_row_writes(ast.parse(path.read_text()), "UnitPrMerge")
    }

    assert found == {("pr_merge.py", "_record"), ("pr_merge.py", "_resettle")}
