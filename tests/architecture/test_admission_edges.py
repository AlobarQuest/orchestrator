"""Architecture guard: one test states what the Admission layer reaches.

* Its source builds no client and imports no HTTP library (`OUTBOUND_MODULES`).
* The policy file is the one file it reads, and it runs nothing (`read_sites`).
* The compliance read lives only on the operator's command line (`CLI_ONLY`).
* Each vocabulary kept in more than one place names every copy and what holds them equal
  (`VOCABULARIES`).

The scan is of each module's own code, so a call into another layer's module isn't seen; that
module's own edge test states it. The population comes from `layers.toml`, so a new admission
module is scanned without being listed. A copy in a sibling repository is checked to still exist
where the table says when a checkout is on disk (`~/Projects/<repository>`), and CI, which has
none, skips only that half. Agreement of the values is held by each vocabulary's holder, not here.
"""

from __future__ import annotations

import ast
import json
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from orchestrator import factory_policy
from scripts.layers import load
from tests.architecture.import_scan import file_imports

SOURCE = Path("src/orchestrator")
POLICY_SOURCE = SOURCE / "factory_policy.py"
CONFORMANCE_SOURCE = SOURCE / "conformance_claim.py"
CLI_SOURCE = SOURCE / "cli.py"
THIS_REPOSITORY = "orchestrator"

# Top-level modules that can leave the process, or start something that does. `estate_clients` and
# `alert_hub_client` are this estate's own ways out.
OUTBOUND_MODULES = frozenset(
    {
        "aiohttp",
        "alert_hub_client",
        "estate_clients",
        "http",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib",
        "urllib3",
    }
)
# Calls that read or write a file, or run a program, by the name they are called through.
FILE_CALLS = frozenset(
    {"open", "read_bytes", "read_text", "write_bytes", "write_text", "system", "popen"}
)
# The one file read: (module, function, call).
READ_SITES = {(POLICY_SOURCE, "load_factory_policy", "read_text")}
# Modules that may reach another local tool's code, and the only module that may import them.
CLI_ONLY = {CONFORMANCE_SOURCE: CLI_SOURCE}
DYNAMIC_IMPORTS = {CONFORMANCE_SOURCE: {"portfolio.compliance", "portfolio.manifest"}}


def admission_sources() -> list[Path]:
    return sorted(
        path
        for pattern in load()["admission"].paths
        if pattern.startswith("src/") and pattern.endswith((".py", "/**"))
        for path in Path().glob(
            pattern.removesuffix("**") + "**/*.py" if "**" in pattern else pattern
        )
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def outbound_imports(path: Path) -> set[str]:
    return {found.module for found in file_imports(path) if found.top in OUTBOUND_MODULES}


def read_sites(path: Path) -> set[tuple[Path, str, str]]:
    """Each file call in `path`, with the top-level function holding it."""
    found: set[tuple[Path, str, str]] = set()
    tree = _tree(path)
    for function in tree.body:
        name = function.name if isinstance(function, ast.FunctionDef) else "<module>"
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _callee(node) in FILE_CALLS:
                found.add((path, name, str(_callee(node))))
    return found


def dynamic_imports(path: Path) -> set[str]:
    return {
        str(node.args[0].value)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and _callee(node) in {"import_module", "__import__"}
        and isinstance(node.args[0], ast.Constant)
    }


def test_the_population_is_the_layers_source() -> None:
    sources = admission_sources()
    assert POLICY_SOURCE in sources
    assert SOURCE / "services/intake/decomposition.py" in sources
    assert {
        SOURCE / "reach_vocabulary.py",
        SOURCE / "capability_vocabulary.py",
        SOURCE / "change_window_override.py",
        CONFORMANCE_SOURCE,
    } <= set(sources)


@pytest.mark.parametrize("path", admission_sources(), ids=str)
def test_no_admission_module_can_leave_the_process(path: Path) -> None:
    assert outbound_imports(path) == set()
    assert not [
        node
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and _callee(node) == "ConfinedClient"
    ]


def test_control_an_http_import_is_seen(tmp_path: Path) -> None:
    written = tmp_path / "module.py"
    written.write_text("def f():\n    import httpx\n    from subprocess import run\n")

    assert outbound_imports(written) == {"httpx", "subprocess"}


def test_the_policy_file_is_the_one_file_read() -> None:
    found = {site for path in admission_sources() for site in read_sites(path)}
    assert found == READ_SITES


def test_control_a_second_read_is_seen(tmp_path: Path) -> None:
    written = tmp_path / "module.py"
    written.write_text("def other(path):\n    return path.read_text()\n")

    assert read_sites(written) == {(written, "other", "read_text")}


def test_the_read_defaults_to_the_packaged_artifact() -> None:
    assert factory_policy.PACKAGED_ARTIFACT.name == "factory-policy.toml"
    assert factory_policy.load_factory_policy.__defaults__ == (factory_policy.PACKAGED_ARTIFACT,)


def test_every_production_load_reads_the_packaged_artifact() -> None:
    """A caller passing a path would make a second file the policy."""
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and _callee(node) == "load_factory_policy":
                assert not node.args
                assert not node.keywords


def test_only_the_compliance_read_imports_another_tool() -> None:
    found = {path: names for path in admission_sources() if (names := dynamic_imports(path))}
    assert found == DYNAMIC_IMPORTS


@pytest.mark.parametrize(("module", "only_importer"), CLI_ONLY.items(), ids=str)
def test_the_compliance_read_is_reached_only_from_the_command_line(
    module: Path, only_importer: Path
) -> None:
    dotted = ".".join(module.with_suffix("").parts[1:])
    importers = {
        path
        for path in Path("src").rglob("*.py")
        if dotted in {found.module for found in file_imports(path)}
    }
    assert importers == {only_importer}


# ---- Vocabulary copies --------------------------------------------------------------------------


@dataclass(frozen=True)
class Copy:
    """Where one copy lives: a name in a file, in this repository or a sibling's."""

    path: str
    name: str
    repository: str = THIS_REPOSITORY


@dataclass(frozen=True)
class Vocabulary:
    copies: tuple[Copy, ...]
    # The tests that fail when the copies part, as (file, function).
    held_by: tuple[tuple[str, str], ...]


_ENVELOPE_TEST = "tests/contract/test_runner_envelope_contract.py"
_BUDGET_TEST = "tests/contract/test_profile_budget_agreement.py"
_CAPABILITY_TEST = "tests/contract/test_capability_consumer_compatibility.py"
_POLICY_TEST = "tests/services/test_factory_policy.py"
_POLICY_TOML = "src/orchestrator/factory-policy.toml"
_ENVELOPE_FIXTURE = "tests/fixtures/runner_envelope_contract.json"
_DEPENDENCY_PROFILE = "src/intent_packages/profiles/dependency_update.py"

VOCABULARIES: dict[str, Vocabulary] = {
    "capabilities": Vocabulary(
        (
            Copy("src/orchestrator/capability_vocabulary.py", "CAPABILITY_VOCABULARY"),
            Copy(_ENVELOPE_FIXTURE, "capabilities"),
            Copy(
                "src/factory_runner/capability_vocabulary.py",
                "CAPABILITY_VOCABULARY",
                "factory-runner",
            ),
            Copy(_DEPENDENCY_PROFILE, "CAPABILITIES", "intent-packages"),
        ),
        (
            (_ENVELOPE_TEST, "test_capability_vocabulary_is_derived_from_the_pinned_contract"),
            (_CAPABILITY_TEST, "test_the_vocabulary_parser_agrees_with_the_shipped_constant"),
            (_BUDGET_TEST, "test_the_capability_sites_agree_today"),
        ),
    ),
    "capability levels": Vocabulary(
        (
            Copy("src/orchestrator/kernel/runner_authority.py", "RUNNER_CAPABILITY_LEVELS"),
            Copy(_ENVELOPE_FIXTURE, "levels"),
            Copy("src/factory_runner/authority.py", "SUPPORTED_LEVELS", "factory-runner"),
        ),
        ((_ENVELOPE_TEST, "test_capability_levels_are_derived_from_the_pinned_level_contract"),),
    ),
    "envelope fields": Vocabulary(
        (
            Copy("src/orchestrator/kernel/runner_authority.py", "RUNNER_ENVELOPE_FIELDS"),
            Copy(_ENVELOPE_FIXTURE, "envelope_fields"),
        ),
        ((_ENVELOPE_TEST, "test_envelope_field_set_is_derived_from_the_pinned_contract"),),
    ),
    "reach": Vocabulary(
        (
            Copy("src/orchestrator/reach_vocabulary.py", "REACH_VOCABULARY"),
            Copy("src/orchestrator/reach_vocabulary.py", "LIVE_ESTATE"),
            Copy(_POLICY_TOML, "reach"),
        ),
        (
            (_POLICY_TEST, "test_the_shipped_artifact_loads_and_covers_every_reach_member"),
            (
                "tests/services/test_reach_estate.py",
                "test_the_named_member_and_the_vocabulary_it_belongs_to_stay_in_agreement",
            ),
        ),
    ),
    "change classes": Vocabulary(
        (
            Copy(_POLICY_TOML, "change_classes"),
            Copy("src/orchestrator/services/execution/target_grant.py", "DEPENDENCY_UPDATE"),
            Copy(_DEPENDENCY_PROFILE, "change_class", "intent-packages"),
        ),
        (
            (
                _POLICY_TEST,
                "test_the_shipped_posture_is_the_one_production_enforced_from_its_settings",
            ),
            ("tests/services/test_target_grant.py", "test_the_lane_and_admission_mappings_agree"),
        ),
    ),
    "dependency-update budget": Vocabulary(
        (
            Copy(_POLICY_TOML, "max_llm_calls"),
            Copy("tests/services/test_authority_known_good.py", "PROFILE_BUDGETS"),
            Copy(_DEPENDENCY_PROFILE, "BUDGETS", "intent-packages"),
        ),
        (
            ("scripts/check_profile_budget_agreement.py", "main"),
            (_BUDGET_TEST, "test_the_three_sites_agree_today"),
        ),
    ),
}
REQUIRED = {"capabilities", "reach", "change classes", "dependency-update budget"}


def _keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def defined_names(path: Path) -> set[str]:
    """What a file defines, not what it mentions: keys, or assigned and keyword-argument names."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return set(_keys(json.loads(text)))
    if path.suffix == ".toml":
        return set(_keys(tomllib.loads(text)))
    names: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign):
            names |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def copy_problems(vocabulary: Vocabulary, root: Path) -> list[str]:
    """Each copy this repository holds that isn't where the table says, and each missing holder."""
    found: list[str] = []
    for copy in vocabulary.copies:
        if copy.repository != THIS_REPOSITORY:
            continue
        file = root / copy.path
        if not file.is_file() or copy.name not in defined_names(file):
            found.append(f"{copy.path}: no {copy.name}")
    for path, function in vocabulary.held_by:
        file = root / path
        names = (
            {node.name for node in ast.walk(_tree(file)) if isinstance(node, ast.FunctionDef)}
            if file.is_file()
            else set()
        )
        if function not in names:
            found.append(f"{path}: no {function}")
    return found


def test_the_table_covers_the_stated_vocabularies() -> None:
    assert REQUIRED <= set(VOCABULARIES)


@pytest.mark.parametrize("name", sorted(VOCABULARIES))
def test_each_copy_is_where_the_table_says_and_something_holds_it(name: str) -> None:
    vocabulary = VOCABULARIES[name]
    assert copy_problems(vocabulary, Path()) == []
    assert len(vocabulary.copies) > 1
    assert vocabulary.held_by


@pytest.mark.parametrize(
    ("name", "copy"),
    [
        (name, copy)
        for name, vocabulary in sorted(VOCABULARIES.items())
        for copy in vocabulary.copies
        if copy.repository != THIS_REPOSITORY
    ],
    ids=lambda value: value if isinstance(value, str) else value.repository,
)
def test_each_sibling_copy_is_where_the_table_says(name: str, copy: Copy) -> None:
    checkout = Path.home() / "Projects" / copy.repository
    if not checkout.is_dir():
        pytest.skip(f"no {copy.repository} checkout on disk: {name}'s far side was not checked")
    assert copy.name in defined_names(checkout / copy.path)


def test_control_a_moved_copy_and_a_deleted_holder_are_named(tmp_path: Path) -> None:
    vocabulary = Vocabulary((Copy("a.py", "NAME"),), (("tests/b.py", "test_b"),))
    (tmp_path / "a.py").write_text("OTHER = 1\n")

    assert copy_problems(vocabulary, tmp_path) == ["a.py: no NAME", "tests/b.py: no test_b"]
