"""Which way the landing package's imports point, and that no name is reached by re-export.

`services/landing/` holds two lanes that land Dependabot pull requests -- the estate lane (landing
changes something already serving) and the inert lane (it does not) -- plus the unit-bound lane
(`pr_merge`) and the sources all three read. The two Dependabot lanes speak one vocabulary, and
until Tier 2 wave 2 that vocabulary lived inside the estate lane: the inert admission imported the
estate admission for its refusal codes and terms, and imported the estate ACT for two merge-method
constants. `interfaces` (types, protocols, constants) and `terms` (the shared term builders) now
hold it, and this module says which way everything else may point.

Four rules, each keyed on a classification that must cover the whole package -- a new module that
nobody classified fails `test_every_landing_module_is_classified` rather than escaping all four:

* an inert module imports from the estate lane only the one name ADR-0020's merge exemption keeps
  there, the real GitHub gateway the inert act subclasses;
* no admission module imports an act module -- an admission answers, an act does, and a constant
  both need belongs in `interfaces`;
* `interfaces` imports nothing from the package and no HTTP client, so every party can reach it;
* `terms` imports only `interfaces` from the package.

`estate_landing` is NOT in the estate lane despite its name: it is the client for the estate's own
answer about what landing on a repository does, which all three lanes read.

The fifth test is what makes the first four worth having. A name moved out of a module stays
bound in it wherever that module still imports it for its own use, so `from old import name` keeps
working and a stale importer is invisible -- to the rules above, and to `test_unreachable_guards`,
which drops an edge through a re-export. So every import of a landing name must name the module
that DEFINES it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.import_scan import file_import_names
from tests.architecture.test_wsp21_invariant_scan import HTTP_CLIENTS

PACKAGE = "orchestrator.services.landing"
LANDING = Path("src/orchestrator/services/landing")

ESTATE_LANE = frozenset({"estate_landing_admission", "estate_pr_merge", "estate_pr_branch_update"})
INERT_LANE = frozenset(
    {"inert_landing_admission", "inert_landing_policy", "inert_pr_merge", "inert_pr_branch_update"}
)
SHARED = frozenset(
    {
        "interfaces",
        "terms",
        "branch_update_serialization",
        "change_record",
        "estate_landing",
        "pr_merge",
        "pr_merge_admission",
    }
)
ADMISSIONS = frozenset(
    {"estate_landing_admission", "inert_landing_admission", "pr_merge_admission"}
)
ACTS = frozenset(
    {
        "estate_pr_merge",
        "estate_pr_branch_update",
        "inert_pr_merge",
        "inert_pr_branch_update",
        "pr_merge",
    }
)

# ADR-0020: each lane's remote call stays in that lane's module, and the inert act's gateway is the
# estate gateway with the landing renamed (`GitHubInertPullRequests(GitHubEstatePullRequests)`).
# Equality rather than a subset, so an exemption nothing uses any more fails too.
INERT_MAY_IMPORT_FROM_ESTATE = frozenset(
    {("inert_pr_merge", "estate_pr_merge", "GitHubEstatePullRequests")}
)


def _modules() -> set[str]:
    return {path.stem for path in LANDING.glob("*.py") if path.stem != "__init__"}


def _landing_names(path: Path) -> set[tuple[str, str]]:
    """Every (landing module, name) a file imports; importing the module itself is `<module>`.

    All three spellings count, so none is a way past the rules: `from <package>.m import name`,
    `from <package> import m`, and `import <package>.m`.
    """
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.ImportFrom) and node.module == PACKAGE:
            found |= {(alias.name, "<module>") for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(f"{PACKAGE}."):
            target = (node.module or "").removeprefix(f"{PACKAGE}.")
            found |= {(target, alias.name) for alias in node.names}
        elif isinstance(node, ast.Import):
            found |= {
                (alias.name.removeprefix(f"{PACKAGE}."), "<module>")
                for alias in node.names
                if alias.name.startswith(f"{PACKAGE}.")
            }
    return found


def _landing_modules_imported(stem: str) -> set[str]:
    return {target for target, _ in _landing_names(LANDING / f"{stem}.py")}


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound to a landing MODULE object, so `alias.attr` can be read as an import."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == PACKAGE:
            aliases |= {alias.asname or alias.name: alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            aliases |= {
                alias.asname: alias.name.removeprefix(f"{PACKAGE}.")
                for alias in node.names
                if alias.asname and alias.name.startswith(f"{PACKAGE}.")
            }
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
    lanes = ESTATE_LANE | INERT_LANE | SHARED
    assert len(lanes) == len(ESTATE_LANE) + len(INERT_LANE) + len(SHARED), "a module is in two sets"
    present = _modules()
    assert lanes == present, (
        f"unclassified: {sorted(present - lanes)}; no longer present: {sorted(lanes - present)}"
    )
    assert _modules() >= ADMISSIONS | ACTS


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


def test_no_admission_module_imports_an_act_module() -> None:
    offenders = {
        stem: sorted(_landing_modules_imported(stem) & ACTS)
        for stem in ADMISSIONS
        if _landing_modules_imported(stem) & ACTS
    }
    assert not offenders, offenders


def test_interfaces_imports_nothing_from_the_package_and_no_http_client() -> None:
    imported = file_import_names(LANDING / "interfaces.py")
    assert not {module for module in imported if module.startswith(f"{PACKAGE}.")}
    assert not imported & HTTP_CLIENTS
    assert not {module.split(".")[0] for module in imported} & HTTP_CLIENTS


def test_terms_imports_only_interfaces_from_the_package() -> None:
    assert _landing_modules_imported("terms") == {"interfaces"}


def test_every_import_of_a_landing_name_names_the_module_that_defines_it() -> None:
    defined = {stem: _defined(LANDING / f"{stem}.py") for stem in _modules()}
    package_names = set().union(*defined.values())
    sources = [
        *Path("src").rglob("*.py"),
        *Path("tests").rglob("*.py"),
        *Path("scripts").glob("*.py"),
    ]
    stale = sorted(
        f"{path}: {name} from {target}"
        for path in sources
        for target, name in _landing_names(path)
        if name != "<module>" and target in defined and name not in defined[target]
    )
    # The same read through a module object. Only a name the package defines ELSEWHERE counts,
    # because `estate_pr_merge.httpx` is a test patching what that module calls, not a re-export.
    for path in sources:
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _module_aliases(tree)
        stale += sorted(
            f"{path}:{node.lineno}: {node.attr} through {aliases[node.value.id]}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and aliases.get(node.value.id) in defined
            and node.attr in package_names
            and node.attr not in defined[aliases[node.value.id]]
        )
    assert not stale, stale
