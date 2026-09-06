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
    except urllib.error.URLError as error:
        raise Unresolvable(f"cannot reach GitHub to read {path}: {error.reason}") from error


def _budgets_from_assignment(source: str, name: str, where: str) -> dict[str, int]:
    """The integer budgets of a module-level `name = {...}` literal, parsed rather than executed.

    An AST parse, because importing intent-packages here would drag its whole dependency tree into
    this repository's gate to read one dict -- the same trade `check_brief_consumer_compatibility`
    makes, and for the same reason.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise Unresolvable(f"{where} does not parse as Python: {error}") from error

    for node in tree.body:
        # Both spellings, because the two sites differ: intent-packages annotates its constant
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
            found = ast.literal_eval(value)
        except ValueError as error:
            raise Unresolvable(f"{where}: {name} is not a literal dict: {error}") from error
        missing = [k for k in BUDGET_KEYS if not isinstance(found.get(k), int)]
        if missing:
            raise Unresolvable(f"{where}: {name} has no integer {', '.join(missing)}")
        return {k: found[k] for k in BUDGET_KEYS}

    raise Unresolvable(f"{where}: no module-level {name} assignment found")


def pattern_budgets() -> dict[str, int]:
    """The known-good pattern's declared ceilings, from the artifact production reads."""
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
    row = matches[0]
    missing = [k for k in BUDGET_KEYS if not isinstance(row.get(k), int)]
    if missing:
        raise Unresolvable(f"the pattern declares no integer {', '.join(missing)}")
    return {k: row[k] for k in BUDGET_KEYS}


def test_constant_budgets() -> dict[str, int]:
    """`PROFILE_BUDGETS`, what the local suite tests against."""
    return _budgets_from_assignment(
        TEST_PATH.read_text(encoding="utf-8"), "PROFILE_BUDGETS", TEST_PATH.name
    )


def profile_budgets(source: str) -> dict[str, int]:
    """`BUDGETS`, what intent-packages actually stamps into every unit envelope."""
    return _budgets_from_assignment(source, "BUDGETS", PROFILE_PATH)


def main() -> int:
    try:
        pattern = pattern_budgets()
        constant = test_constant_budgets()
        profile = profile_budgets(fetch(INTENT_PACKAGES_REPO, PROFILE_PATH, PROFILE_REF))
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"pattern : factory-policy.toml {PATTERN_NAME!r} -> {pattern}")
    print(f"constant: {TEST_PATH.name} PROFILE_BUDGETS -> {constant}")
    print(f"profile : {INTENT_PACKAGES_REPO}@{PROFILE_REF} BUDGETS -> {profile}")

    if pattern == constant == profile:
        print(f"\nPASS: all three agree on {profile}.")
        return 0

    print(
        f"\nFAIL: the known-good pattern, PROFILE_BUDGETS and the profile must be EQUAL.\n"
        f"  pattern  {pattern}\n  constant {constant}\n  profile  {profile}\n\n"
        "Below the profile, the pattern recognises nothing and every uv pin bump silently takes "
        "the human gate ADR-0011 lifted -- fail-closed and invisible, which is how this went "
        "unnoticed for eighteen days. Above it, the pattern recognises an envelope no profile "
        "emits, which widens the recognised shape and is a decision the pattern's own rationale "
        "says must be a deliberate edit to that file.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
