"""WS-P2.18 Increment 2 -- the policy artifact, its loader, and its guarantees.

Every proof here runs in both directions. A guard that cannot fail is this programme's recurring
defect, so each restrictive claim is paired with the control that shows the same predicate saying
yes on the input it is meant to accept.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import types
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from orchestrator import factory_policy
from orchestrator.errors import DomainError
from orchestrator.factory_policy import (
    PACKAGED_ARTIFACT,
    REACH_NOT_IN_POLICY,
    REACH_UNDECLARED,
    REACH_UNRECOGNISED,
    FactoryPolicy,
    ReachPolicy,
    load_factory_policy,
)
from orchestrator.reach_vocabulary import REACH_VOCABULARY

SOURCE_ROOT = Path("src")
MODULE_PATH = "src/orchestrator/factory_policy.py"

RATIONALE = 'rationale = "repository only"'
DECIDED = 'decided = "2026-08-01"'

VALID = f"""
version = 3

[reach.source_repository]
{RATIONALE}
{DECIDED}

[reach.live_estate]
rationale = "something already serving"
{DECIDED}

[reach.external_system]
rationale = "outside the estate"
{DECIDED}

[reach.operator_machine]
rationale = "the operator's own machine"
{DECIDED}
"""

ROWS_ARE_NOT_TABLES = """
version = 3

[reach]
source_repository = 1
live_estate = 1
external_system = 1
operator_machine = 1
"""

MALFORMED: tuple[tuple[str, str], ...] = (
    ("not toml at all", "this is not = = toml"),
    ("no version", VALID.replace("version = 3\n", "")),
    ("version is a string", VALID.replace("version = 3", 'version = "3"')),
    ("version is a boolean", VALID.replace("version = 3", "version = true")),
    ("an unknown top-level key", VALID + '\nlease = "15m"\n'),
    ("no reach table", "version = 3\n"),
    ("a missing row", VALID.replace("[reach.external_system]", "[reach.spare]")),
    ("an unknown row", VALID + f'\n[reach.invented]\nrationale = "x"\n{DECIDED}\n'),
    ("a row that is not a table", ROWS_ARE_NOT_TABLES),
    ("a row with an extra field", VALID.replace(RATIONALE, 'note = "x"')),
    # Schema 2 gave rows one OPTIONAL field, so "exactly these" became "these, plus at most those".
    (
        "a row with a field beside the optional one",
        VALID.replace(RATIONALE, RATIONALE + '\nnote = "x"'),
    ),
    ("a row missing a field", VALID.replace(f"{DECIDED}\n\n[reach.live", "\n[reach.live")),
    ("an empty rationale", VALID.replace(RATIONALE, 'rationale = "  "')),
    ("a rationale that is not a string", VALID.replace(RATIONALE, "rationale = 7")),
    ("an unquoted TOML date", VALID.replace(DECIDED, "decided = 2026-08-01", 1)),
    ("a decided value that is not a date", VALID.replace(DECIDED, 'decided = "soon"', 1)),
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "factory-policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# The shipped artifact
# ---------------------------------------------------------------------------------------------


def test_the_shipped_artifact_loads_and_covers_every_reach_member() -> None:
    policy = load_factory_policy()

    assert policy.version == 3
    assert set(policy.rows) == set(REACH_VOCABULARY)
    assert all(row.rationale and row.decided for row in policy.rows.values())


def test_the_artifact_ships_beside_the_module_that_reads_it() -> None:
    # The `templates/` precedent: resolved from the module's own directory, so it is found whether
    # the package is imported from `src/` or from an installed location.
    assert PACKAGED_ARTIFACT.is_file()
    assert PACKAGED_ARTIFACT.parent == Path(factory_policy.__file__).parent


# ---------------------------------------------------------------------------------------------
# A malformed artifact fails loudly and NAMED; a valid one loads
# ---------------------------------------------------------------------------------------------


def test_a_valid_artifact_loads_the_control_for_every_malformation(tmp_path: Path) -> None:
    policy = load_factory_policy(write(tmp_path, VALID))

    assert policy.version == 3
    assert sorted(policy.rows) == sorted(REACH_VOCABULARY)


@pytest.mark.parametrize(("label", "text"), MALFORMED, ids=[case[0] for case in MALFORMED])
def test_a_malformed_artifact_is_a_named_loud_failure(
    label: str, text: str, tmp_path: Path
) -> None:
    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, text))

    assert raised.value.code == "factory_policy_invalid", label
    assert raised.value.recovery is not None, label


def test_an_absent_artifact_is_a_named_failure_not_an_empty_policy(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as raised:
        load_factory_policy(tmp_path / "nothing-here.toml")

    assert raised.value.code == "factory_policy_invalid"


# ---------------------------------------------------------------------------------------------
# An unknown version fails closed
# ---------------------------------------------------------------------------------------------


def test_an_unknown_schema_version_fails_closed(tmp_path: Path) -> None:
    assert load_factory_policy(write(tmp_path, VALID)).version == 3  # control

    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, VALID.replace("version = 3", "version = 4")))

    assert raised.value.code == "factory_policy_version_unsupported"


def test_a_version_below_the_supported_one_fails_closed_too(tmp_path: Path) -> None:
    # Compatibility is an exact set, not a floor: an artifact at an OLDER schema is as unreadable
    # as a newer one, because this loader was not written against its shape either.
    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, VALID.replace("version = 3", "version = 2")))

    assert raised.value.code == "factory_policy_version_unsupported"


# ---------------------------------------------------------------------------------------------
# Absent reach, unrecognised reach, absent policy row: each resolves most restrictively
# ---------------------------------------------------------------------------------------------


def test_a_fully_declared_reach_draws_no_refusal_the_control_for_the_three_below() -> None:
    policy = load_factory_policy()

    for size in range(1, len(REACH_VOCABULARY) + 1):
        for members in combinations(sorted(REACH_VOCABULARY), size):
            assert policy.refusals_for(members) == (), members


def test_an_absent_reach_resolves_restrictively() -> None:
    policy = load_factory_policy()

    assert policy.refusals_for(None) == (REACH_UNDECLARED,)
    assert policy.refusals_for(()) == (REACH_UNDECLARED,)


def test_an_unrecognised_reach_member_resolves_restrictively() -> None:
    policy = load_factory_policy()

    assert policy.refusals_for(("invented",)) == (REACH_UNRECOGNISED,)
    # And it is not laundered by a recognised member standing beside it.
    assert REACH_UNRECOGNISED in policy.refusals_for(("source_repository", "invented"))


def test_a_reach_member_with_no_policy_row_resolves_restrictively() -> None:
    # The loader refuses to build a policy with a row missing, so this is the defence behind that:
    # a resolver asked about an uncovered member must refuse rather than raise KeyError.
    complete = load_factory_policy()
    stripped = dataclasses.replace(
        complete,
        rows={m: row for m, row in complete.rows.items() if m != "operator_machine"},
    )

    assert complete.refusals_for(("operator_machine",)) == ()  # control
    assert stripped.refusals_for(("operator_machine",)) == (REACH_NOT_IN_POLICY,)


def test_composition_over_a_reach_set_can_only_narrow() -> None:
    # ADR-0009's intersection-of-permission, as a union of refusals: adding a member never removes
    # an objection. Checked over every subset/superset pair rather than on an example.
    complete = load_factory_policy()
    policy = dataclasses.replace(
        complete,
        rows={m: row for m, row in complete.rows.items() if m != "external_system"},
    )
    members = [*sorted(REACH_VOCABULARY), "invented"]

    for size in range(1, len(members)):
        for smaller in combinations(members, size):
            for extra in members:
                if extra in smaller:
                    continue
                assert set(policy.refusals_for(smaller)) <= set(
                    policy.refusals_for((*smaller, extra))
                )


# ---------------------------------------------------------------------------------------------
# The hard off-switch outranks the artifact, structurally
# ---------------------------------------------------------------------------------------------


def _return_annotation(value: object) -> str:
    try:
        return str(inspect.signature(value).return_annotation)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""


def _permission_shaped(subject: object) -> list[str]:
    """Public surfaces of `subject` whose annotation could express a permission.

    The artifact answers only in refusals. A boolean anywhere in its public shape would be the one
    thing that lets a policy row read as "allowed" -- and a permission policy can grant is a
    permission policy can grant while the hard off-switch says no.
    """
    found: list[str] = []
    for name, value in vars(subject).items():
        if name.startswith("_"):
            continue
        if isinstance(value, type) and dataclasses.is_dataclass(value):
            found += [
                f"{name}.{field.name}"
                for field in dataclasses.fields(value)
                if "bool" in str(field.type)
            ]
            found += [
                f"{name}.{attribute}"
                for attribute, member in vars(value).items()
                if not attribute.startswith("_")
                and inspect.isfunction(member)
                and "bool" in _return_annotation(member)
            ]
        elif inspect.isfunction(value) and "bool" in _return_annotation(value):
            found.append(name)
    return sorted(found)


def test_the_artifact_cannot_express_a_permission() -> None:
    assert _permission_shaped(factory_policy) == []


def test_the_permission_detector_flags_a_permission_when_there_is_one() -> None:
    # The control. Without it, the assertion above is satisfied by a detector that finds nothing.
    @dataclasses.dataclass(frozen=True)
    class Permissive:
        allowed: bool

        def permits(self) -> bool:
            return self.allowed

    def is_open() -> bool:
        return True

    stub = types.SimpleNamespace(Permissive=Permissive, is_open=is_open)

    assert _permission_shaped(stub) == ["Permissive.allowed", "Permissive.permits", "is_open"]


def _settings_dependencies(source: str) -> list[str]:
    """Imports and references through which `source` could reach the process settings.

    Parsed, not grepped. A docstring that NAMES `orchestrator.config` in order to promise it is not
    imported is not a dependency on it, and a predicate that cannot tell those apart would have to
    be silenced rather than satisfied -- which is how a guard stops guarding.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("orchestrator.conf"):
            found.append(f"from {node.module}")
        elif isinstance(node, ast.Import):
            found += [
                f"import {a.name}" for a in node.names if a.name.startswith("orchestrator.co")
            ]
        elif isinstance(node, ast.Name) and node.id in {"Settings", "get_settings"}:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in {"dispatch_enabled", "get_settings"}:
            found.append(node.attr)
    return sorted(set(found))


def test_the_artifact_cannot_read_the_hard_off_switch() -> None:
    # Precedence by structure rather than by check ordering: this module cannot see the setting, so
    # neither an artifact nor a later refactor of one can overrule the switch.
    source = Path(factory_policy.__file__).read_text(encoding="utf-8")

    assert _settings_dependencies(source) == []


def test_the_settings_dependency_detector_fires_on_a_module_that_does_read_them() -> None:
    # The control, on a real reader: `api/routes.py` imports the settings the off-switch lives in,
    # so a predicate that finds nothing there finds nothing anywhere.
    reader = Path("src/orchestrator/api/routes.py").read_text(encoding="utf-8")

    assert _settings_dependencies(reader) != []


def test_the_most_permissive_artifact_expressible_cannot_empty_an_admission_refusal() -> None:
    # Compose the way admission composes: policy contributes to a refusal list it can only add to.
    # With the switch's own refusal already in that list, no reach empties it.
    policy = load_factory_policy()
    switch_off = ("dispatch_disabled",)
    members = [*sorted(REACH_VOCABULARY), "invented"]

    for size in range(len(members) + 1):
        for reach in combinations(members, size):
            composed = switch_off + policy.refusals_for(reach or None)
            assert "dispatch_disabled" in composed, reach


# ---------------------------------------------------------------------------------------------
# Single source
# ---------------------------------------------------------------------------------------------


def test_no_second_copy_of_the_artifact_values_exists_in_the_source_tree() -> None:
    policy = load_factory_policy()
    sources = {path: path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.rglob("*.py"))}

    for row in policy.rows.values():
        fingerprint = " ".join(row.rationale.split()[:8])
        holders = [str(path) for path, text in sources.items() if fingerprint in text]
        assert holders == [], f"{row.member}'s rationale also lives in {holders}"

    readers = [str(path) for path, text in sources.items() if PACKAGED_ARTIFACT.name in text]
    assert readers == [MODULE_PATH]


def test_the_artifact_is_pinned_to_the_reach_vocabulary_not_a_second_copy_of_it(
    tmp_path: Path,
) -> None:
    # A new vocabulary member with no row does not fall back to something lenient -- the document
    # stops loading, and a document that does not load permits nothing.
    row = f'[reach.operator_machine]\nrationale = "the operator\'s own machine"\n{DECIDED}\n'

    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, VALID.replace(row, "")))

    assert raised.value.code == "factory_policy_invalid"
    assert "operator_machine" in raised.value.message


# ---------------------------------------------------------------------------------------------
# The report the production caller serves
# ---------------------------------------------------------------------------------------------


def test_the_report_names_the_version_the_source_and_every_row() -> None:
    report: dict[str, Any] = load_factory_policy().report()

    assert report["version"] == 3
    assert report["source"] == PACKAGED_ARTIFACT.name
    assert [row["member"] for row in report["reach"]] == sorted(REACH_VOCABULARY)
    assert all(isinstance(row["rationale"], str) and row["rationale"] for row in report["reach"])


def test_a_rationale_reaches_the_report_as_one_line() -> None:
    # Wrapped in the file so the artifact reviews well; unwrapped on the wire so it reads well.
    assert all("\n" not in row.rationale for row in load_factory_policy().rows.values())


def test_reach_policy_rows_are_immutable() -> None:
    row = load_factory_policy().rows["live_estate"]
    assert isinstance(row, ReachPolicy)

    with pytest.raises(dataclasses.FrozenInstanceError):
        row.rationale = "something else"  # type: ignore[misc]


def test_a_policy_is_immutable() -> None:
    policy = load_factory_policy()
    assert isinstance(policy, FactoryPolicy)

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.version = 99  # type: ignore[misc]
