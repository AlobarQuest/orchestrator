#!/usr/bin/env python3
"""Refuse a pull request that lets the known-good pattern and the profile's budgets disagree.

ADR-0011 decided that a recognised uv pin bump does not need a human authority approval: gate the
novel, pre-authorize work once we know how to do it. The pattern that recognises one is declared in
`factory-policy.toml`, and its own rationale says it recognises "only budgets at or under the
profile's own defaults" -- a claim THIS repository makes about a constant in ANOTHER one,
`intent_packages.profiles.dependency_update.BUDGETS`.

Nothing compared them. The pattern read `max_llm_calls = 4`, which was the profile default on
2026-08-01 and has since been 120, 240 and 360. `_within` is `envelope <= ceiling`, so from the
moment the profile passed 4 the pattern recognised NOTHING: every uv pin bump drew
`authority_envelope_novel` and took the human gate ADR-0011 had lifted. It failed closed, so
nothing unsafe happened -- and nothing said so either, for eighteen days.

THE RELATION IS EQUALITY, and it is the opposite of the one governing the same-named constant in
intent-packages. There `max_llm_calls` is a FLOOR under the gate ordering and over-provisioning is
free. Here it decides what a human still looks at, so a ceiling ABOVE the profile's default would
recognise an envelope no profile emits -- widening the recognised shape, which the pattern's
rationale forbids without a deliberate edit. Below it, the mechanism silently stops existing.

TWO SITES, ONE NUMBER, and this check holds both to the profile:
  - `factory-policy.toml`'s pattern, which is what production actually matches against;
  - `tests/services/test_authority_known_good.py`'s `PROFILE_BUDGETS`, which is what the local
    suite tests against. It exists because this repository cannot import intent-packages, and
    unpinned it would be a second copy going stale exactly as the first one did.

AND THE CAPABILITIES, since 2026-09-27, by the same equality and for the same reason. The
pattern matches an envelope's capabilities as a SUBSET of its own, so a pattern declaring a
capability the profile never stamps widens what is recognised, and one declaring fewer recognises
nothing. Its sites are the pattern's `capabilities` table, `PROFILE_CAPABILITIES` beside
`PROFILE_BUDGETS`, and the profile's `CAPABILITIES`. Nothing compared them before: `uv_bump()`
took the specimen's capabilities while its docstring claimed the profile's, and nothing noticed
only because the two happened to agree. Both surfaces are evaluated and reported on every run.

Deliberately NOT pinned to `tests/fixtures/runner_authority_envelope.json`. That fixture is a
byte-identical cross-repo SPECIMEN shared with factory-runner, frozen at the 2026-08-01 shape; the
recognition test used to inherit its budgets, which is precisely why it was green while production
was refused.

Modeled on `scripts/check_brief_consumer_compatibility.py`: read this side's source of truth, fetch
the far side at a named revision, fail loudly on divergence or on anything leaving the comparison
unresolvable.

Exit 0: the pattern, PROFILE_BUDGETS and the profile agree. Exit 1: they do not, or the comparison
could not be resolved.
"""

from __future__ import annotations

import ast
import http.client
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

INTENT_PACKAGES_REPO = "AlobarQuest/intent-packages"
PROFILE_PATH = "src/intent_packages/profiles/dependency_update.py"
# `main`, not a pin: this repository declares no revision of intent-packages, and the claim the
# pattern makes is about the profile as it stands. A pin would need a second thing to keep current.
PROFILE_REF = "main"

POLICY_PATH = REPO_ROOT / "src" / "orchestrator" / "factory-policy.toml"
TEST_PATH = REPO_ROOT / "tests" / "services" / "test_authority_known_good.py"

PATTERN_NAME = "uv dependency pin bump into a named repository"
BUDGET_KEYS = ("max_attempts", "max_llm_calls")


class Unresolvable(RuntimeError):
    """The check could not establish what it needed to compare. Never a silent pass."""


def fetch(repo: str, path: str, ref: str) -> str:
    """The file at a revision, read from GitHub. Read-only, one GET."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}",
        headers={
            "Accept": "application/vnd.github.raw",
            "User-Agent": "orchestrator-profile-budget-agreement/1",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        raise Unresolvable(
            f"cannot read {path} at {repo}@{ref}: HTTP {error.code}. The repository must stay "
            "readable and the ref must name a reachable revision."
        ) from error
    # `URLError` FIRST: it is an `OSError` subclass, so a broad clause above it would swallow the
    # one that carries a `reason`. The two clauses below cover what urllib does NOT wrap -- a
    # failure part-way through `response.read()`, which arrives as a bare `TimeoutError` (an
    # OSError) or as `http.client.IncompleteRead` (which is not one), and a body that is not UTF-8,
    # which `.decode()` raises `UnicodeDecodeError` for. All three escaped as a bare traceback.
    except urllib.error.URLError as error:
        raise Unresolvable(f"cannot reach GitHub to read {path}: {error.reason}") from error
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as error:
        raise Unresolvable(
            f"the connection reading {path} at {repo}@{ref} failed: {error!r}"
        ) from error


def _dict_from_assignment(source: str, name: str, where: str) -> dict:
    """A module-level `name = {...}` literal, parsed rather than executed.

    An AST parse, because importing intent-packages here would drag its whole dependency tree into
    this repository's gate to read one dict -- the same trade `check_brief_consumer_compatibility`
    makes, and for the same reason.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise Unresolvable(f"{where} does not parse as Python: {error}") from error

    for node in tree.body:
        # Both spellings, because the two sites differ: intent-packages annotates its constants
        # (`BUDGETS: dict[str, int] = {...}`, an AnnAssign) and a bare assignment is an Assign.
        # Narrowed by isinstance rather than by a suppression, so the parser stays honest about
        # which node shapes it actually handles.
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        if not isinstance(value, ast.Dict):
            raise Unresolvable(f"{where}: {name} is not a dict literal")
        try:
            return ast.literal_eval(value)
        except ValueError as error:
            raise Unresolvable(f"{where}: {name} is not a literal dict: {error}") from error

    raise Unresolvable(f"{where}: no module-level {name} assignment found")


def _budgets_from_assignment(source: str, name: str, where: str) -> dict[str, int]:
    """The integer budgets of a module-level dict literal."""
    found = _dict_from_assignment(source, name, where)
    missing = [k for k in BUDGET_KEYS if not isinstance(found.get(k), int)]
    if missing:
        raise Unresolvable(f"{where}: {name} has no integer {', '.join(missing)}")
    return {k: found[k] for k in BUDGET_KEYS}


def _capabilities_from_assignment(source: str, name: str, where: str) -> dict[str, str]:
    """A module-level capability-to-level dict literal, every key and value a non-empty string."""
    return _checked_capabilities(_dict_from_assignment(source, name, where), f"{where}: {name}")


def _checked_capabilities(found: object, where: str) -> dict[str, str]:
    if not isinstance(found, dict) or not found:
        raise Unresolvable(f"{where} is not a non-empty capability table")
    if not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in found.items()):
        raise Unresolvable(f"{where} maps something other than a capability name to a level")
    return dict(found)


def _pattern_row() -> dict:
    """The known-good pattern row, from the artifact production reads."""
    try:
        document = tomllib.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise Unresolvable(f"cannot read {POLICY_PATH.name}: {error}") from error

    rows = (((document.get("reach") or {}).get("source_repository")) or {}).get("known_good") or []
    matches = [row for row in rows if row.get("name") == PATTERN_NAME]
    if len(matches) != 1:
        raise Unresolvable(
            f"{POLICY_PATH.name} declares {len(matches)} patterns named {PATTERN_NAME!r}; "
            "this check compares exactly one"
        )
    return matches[0]


def pattern_budgets() -> dict[str, int]:
    """The known-good pattern's declared ceilings."""
    row = _pattern_row()
    missing = [k for k in BUDGET_KEYS if not isinstance(row.get(k), int)]
    if missing:
        raise Unresolvable(f"the pattern declares no integer {', '.join(missing)}")
    return {k: row[k] for k in BUDGET_KEYS}


def pattern_capabilities() -> dict[str, str]:
    """The known-good pattern's declared capabilities."""
    return _checked_capabilities(_pattern_row().get("capabilities"), "the pattern's capabilities")


def test_constant_budgets() -> dict[str, int]:
    """`PROFILE_BUDGETS`, what the local suite tests against."""
    return _budgets_from_assignment(
        TEST_PATH.read_text(encoding="utf-8"), "PROFILE_BUDGETS", TEST_PATH.name
    )


def test_constant_capabilities() -> dict[str, str]:
    """`PROFILE_CAPABILITIES`, what the local suite tests against."""
    return _capabilities_from_assignment(
        TEST_PATH.read_text(encoding="utf-8"), "PROFILE_CAPABILITIES", TEST_PATH.name
    )


def profile_budgets(source: str) -> dict[str, int]:
    """`BUDGETS`, what intent-packages actually stamps into every unit envelope."""
    return _budgets_from_assignment(source, "BUDGETS", PROFILE_PATH)


def profile_capabilities(source: str) -> dict[str, str]:
    """`CAPABILITIES`, what intent-packages actually stamps into every unit envelope."""
    return _capabilities_from_assignment(source, "CAPABILITIES", PROFILE_PATH)


def _compare(label: str, pattern, constant, profile, constant_name: str, profile_name: str) -> bool:
    print(f"{label}:")
    print(f"  pattern : factory-policy.toml {PATTERN_NAME!r} -> {pattern}")
    print(f"  constant: {TEST_PATH.name} {constant_name} -> {constant}")
    print(f"  profile : {INTENT_PACKAGES_REPO}@{PROFILE_REF} {profile_name} -> {profile}")
    if pattern == constant == profile:
        print("  PASS: all three agree.")
        return True
    print(
        f"FAIL: the known-good pattern's {label}, {constant_name} and the profile's "
        f"{profile_name} must be EQUAL.\n"
        f"  pattern  {pattern}\n  constant {constant}\n  profile  {profile}\n\n"
        "Short of the profile, the pattern recognises nothing and every uv pin bump silently takes "
        "the human gate ADR-0011 lifted -- fail-closed and invisible, which is how the budgets "
        "went unnoticed for eighteen days. Beyond it, the pattern recognises an envelope no "
        "profile emits, which widens the recognised shape and is a decision the pattern's own "
        "rationale says must be a deliberate edit to that file.",
        file=sys.stderr,
    )
    return False


def main() -> int:
    """Both surfaces are evaluated and reported on every run: a budget divergence must not hide a
    capability one, or the second is only discoverable after the first is fixed."""
    try:
        source = fetch(INTENT_PACKAGES_REPO, PROFILE_PATH, PROFILE_REF)
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    arms = (
        (
            "budgets",
            pattern_budgets,
            test_constant_budgets,
            profile_budgets,
            "PROFILE_BUDGETS",
            "BUDGETS",
        ),
        (
            "capabilities",
            pattern_capabilities,
            test_constant_capabilities,
            profile_capabilities,
            "PROFILE_CAPABILITIES",
            "CAPABILITIES",
        ),
    )
    agreed = True
    for label, read_pattern, read_constant, read_profile, constant_name, profile_name in arms:
        try:
            values = (read_pattern(), read_constant(), read_profile(source))
        except Unresolvable as error:
            print(f"FAIL: {label}: {error}", file=sys.stderr)
            agreed = False
            continue
        agreed = _compare(label, *values, constant_name, profile_name) and agreed

    if agreed:
        print("\nPASS: the pattern, the local constants and the profile agree on both surfaces.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
