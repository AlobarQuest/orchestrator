#!/usr/bin/env python3
"""Refuse a pull request whose derived deploy criteria change-manager's policy has not ratified.

A change record for a deploying repository carries two fields this repository DERIVES and
change-manager COMPARES BYTE FOR BYTE: `acceptance_criteria`, from
`change_proposer.criteria.acceptance_criteria` over the transcription in
`deploy_watcher.workflows`, and `rollback_plan`, from `change_proposer.criteria._ROLLBACKS`. On the
far side `app/deploy_policy.py::objections` holds a record to the tuple and the mapping its
CURRENT version ratifies, and a mismatch is `acceptance_criteria_not_ratified` /
`rollback_plan_not_ratified`: the record stays `pending` and is never landed.

NOTHING COMPARED THE TWO. Each side pinned a literal of its own and each pin's docstring said the
other side asserted "the same literal". On 2026-09-27 they did not: this repository pinned deploy
policy VERSION 3's brain criteria (blob `c5c0887`) while change-manager pinned VERSION 8's (blob
`7cf6ca2d`). Both tests were green, because each held its own copy to itself. And the failure they
exist to prevent is SILENT -- a pending record is invisible to the estate lander, whose
`_ASK_ABOUT` is `{"approved"}`, which is how brain's queue sat for six days in September.

WHAT IS COMPARED. For every repository the current version ratifies:
  - its pinned rollout revision (`landing.rollout_workflows[repo].blob_sha`) must be TRANSCRIBED
    here -- `attestation_for` must know it, or nothing here can derive criteria for it at all;
  - what this repository derives at that revision must EQUAL the ratified tuple;
  - this repository's rollback plan for it must EQUAL the ratified plan's stored shape.

Keyed on change-manager's PIN, not on this repository's newest transcription. The transcription
registry is additive -- a new revision is transcribed BEFORE a policy version ratifies it, because
the ratified text is derived from it -- so vetting the pinned revision keeps this check green
across that ordering. It goes red only when the two genuinely disagree about the revision
production is held to.

EQUALITY, never a subset. `objections` is `tuple(criteria) != ratified`, so anything short of
equality is a record that does not conform.

HOW THE FAR SIDE IS READ, AND THE TRADE IT MAKES. Every sibling check here AST-parses the far
file. This one cannot do so honestly: `current()` is a registry of frozen dataclasses built from
constants, other versions' attributes (`V7.rollback_plans`, `_V5_LANDING.rollout_workflows[...]`)
and string concatenation, and an AST reading would be a second interpreter for exactly the
expressions that decide the answer. So the module is EXECUTED, in an isolated namespace, after
two guards: its imports must be a subset of `ALLOWED_IMPORTS` (the file's own docstring says it is
data-in-code with no loader; a new import is a reason to re-read this trade, and it reds as
Unresolvable), and `GITHUB_TOKEN` is removed from the environment before execution. The code run
is change-manager's `main`, which lands only through that repository's protected branch.

`GET /api/deploy-policy` is NOT an alternative: it does not serve `acceptance_criteria`.

RESIDUALS. It vets change-manager's `main`, not what production serves; change-manager redeploys
on a push to `main`, so they converge in minutes. And it vets only the CURRENT version -- a record
approved under an older version is re-evaluated against that version by change-manager, and this
repository no longer derives anything for it.

Exit 0: every ratified repository's criteria and rollback plan equal what this repository
derives. Exit 1: they do not, or the comparison could not be resolved.
"""

from __future__ import annotations

import ast
import os
import sys
import types
from dataclasses import dataclass
from typing import Any

from change_proposer.criteria import CriteriaUnavailable, acceptance_criteria, rollback_for
from deploy_watcher.workflows import attestation_for
from scripts.check_profile_budget_agreement import Unresolvable, fetch

CHANGE_MANAGER_REPO = "AlobarQuest/change-manager"
POLICY_PATH = "app/deploy_policy.py"
# `main`: this repository declares no revision of change-manager, and a record is held to the
# policy change-manager is running, which is its `main` within minutes.
POLICY_REF = "main"

# What the far module imports today. Anything else changes what executing it means.
MODULE_NAME = "change_manager_deploy_policy"

ALLOWED_IMPORTS = frozenset({"__future__", "collections.abc", "dataclasses", "typing"})


@dataclass(frozen=True)
class Verdict:
    repository: str
    blob_sha: str
    divergences: tuple[str, ...]


def _imports_of(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add("." * node.level + (node.module or ""))
    return found


def load_policy_module(source: str) -> types.ModuleType:
    """change-manager's `deploy_policy`, executed in isolation after its imports are vetted."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise Unresolvable(f"{POLICY_PATH} does not parse as Python: {error}") from error
    unexpected = sorted(_imports_of(tree) - ALLOWED_IMPORTS)
    if unexpected:
        raise Unresolvable(
            f"{POLICY_PATH} now imports {unexpected}; this check executes that module and allows "
            f"only {sorted(ALLOWED_IMPORTS)}. Re-read the trade in this script's docstring before "
            "widening the allowlist."
        )
    module = types.ModuleType(MODULE_NAME)
    # `@dataclass` resolves its class's module through `sys.modules`, so the namespace must be
    # registered while it executes -- and removed afterwards, so nothing else can import it.
    sys.modules[MODULE_NAME] = module
    try:
        exec(compile(tree, POLICY_PATH, "exec"), module.__dict__)
    except Exception as error:  # the far module failing to build is not a verdict
        raise Unresolvable(f"{POLICY_PATH} raised while loading: {error!r}") from error
    finally:
        sys.modules.pop(MODULE_NAME, None)
    if not callable(getattr(module, "current", None)):
        raise Unresolvable(f"{POLICY_PATH} has no callable current()")
    return module


def judge(policy: Any) -> list[Verdict]:
    """One verdict per repository the policy ratifies. Pure, so it is what gets tested."""
    try:
        ratified_criteria = dict(policy.acceptance_criteria)
        ratified_rollbacks = dict(policy.rollback_plans)
        pins = dict(policy.landing.rollout_workflows)
    except (AttributeError, TypeError) as error:
        raise Unresolvable(
            f"the current policy does not have the expected shape: {error}"
        ) from error
    if not ratified_criteria:
        raise Unresolvable("the current policy ratifies criteria for no repository")

    verdicts: list[Verdict] = []
    for repository, ratified in sorted(ratified_criteria.items()):
        pin = pins.get(repository)
        if pin is None:
            raise Unresolvable(f"the current policy pins no rollout workflow for {repository}")
        blob_sha = pin.blob_sha
        divergences: list[str] = []

        attestation = attestation_for(blob_sha)
        if attestation is None:
            divergences.append(
                f"rollout revision {blob_sha} is not transcribed in deploy_watcher.workflows, so "
                "nothing here can derive criteria for it"
            )
        else:
            derived = acceptance_criteria(repository, attestation)
            if tuple(derived) != tuple(ratified):
                divergences.append(
                    "acceptance criteria differ:\n"
                    f"    derived  {list(derived)!r}\n    ratified {list(ratified)!r}"
                )

        rollback = ratified_rollbacks.get(repository)
        try:
            plan = rollback_for(repository)
        except CriteriaUnavailable as error:
            divergences.append(str(error))
        else:
            ours = {"steps": list(plan.steps), "target": plan.target}
            theirs = rollback.as_stored() if rollback is not None else None
            if ours != theirs:
                divergences.append(
                    f"rollback plan differs:\n    derived  {ours!r}\n    ratified {theirs!r}"
                )
        verdicts.append(Verdict(repository, blob_sha, tuple(divergences)))
    return verdicts


def main(fetcher=fetch) -> int:
    try:
        source = fetcher(CHANGE_MANAGER_REPO, POLICY_PATH, POLICY_REF)
        # The token is withheld from the environment for as long as far code can run -- loading
        # the module and calling `current()` -- and put back afterwards, so a caller importing
        # this module (the tests) does not lose it as a side effect.
        token = os.environ.pop("GITHUB_TOKEN", None)
        try:
            module = load_policy_module(source)
            try:
                policy = module.current()
            except Exception as error:  # the far module failing to answer is not a verdict
                raise Unresolvable(f"{POLICY_PATH} current() raised: {error!r}") from error
        finally:
            if token is not None:
                os.environ["GITHUB_TOKEN"] = token
        verdicts = judge(policy)
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"{CHANGE_MANAGER_REPO}@{POLICY_REF} deploy policy version {policy.version}")
    failed = False
    for verdict in verdicts:
        if verdict.divergences:
            failed = True
            print(f"  {verdict.repository} @ {verdict.blob_sha}: DIVERGES", file=sys.stderr)
            for line in verdict.divergences:
                print(f"    {line}", file=sys.stderr)
        else:
            print(f"  {verdict.repository} @ {verdict.blob_sha[:12]}: criteria and rollback agree")

    if failed:
        print(
            "\nFAIL: change-manager compares these byte for byte, so every record for a diverging "
            "repository stays pending and is never landed -- silently, because the lander asks "
            "only about approved records. Transcribe the revision here first, then ratify the "
            "derived text as a new policy version there.",
            file=sys.stderr,
        )
        return 1
    print(f"\nPASS: {len(verdicts)} ratified repositories agree with what this repository derives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
