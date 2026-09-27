#!/usr/bin/env python3
"""Refuse a pull request whose derived rollout criteria change-manager's policy has not ratified.

A change record for a redeploying repository carries two fields this repository DERIVES and
change-manager COMPARES BYTE FOR BYTE: `acceptance_criteria`, from
`change_proposer.criteria.acceptance_criteria` over the transcription in
`deploy_watcher.workflows`, and `rollback_plan`, from `change_proposer.criteria._ROLLBACKS`. On the
far side `app/deploy_policy.py::objections` holds a record to the tuple and the mapping its
CURRENT version ratifies, and a mismatch leaves the record `pending`, never landed.

NOTHING COMPARED THE TWO. Each side pinned a literal of its own and each pin's docstring said the
other side asserted "the same literal". On 2026-09-27 they did not: this repository pinned policy
VERSION 3's brain criteria (blob `c5c0887`) while change-manager pinned VERSION 8's (blob
`7cf6ca2d`). Both suites were green. The failure is SILENT -- a pending record is invisible to the
estate lander, which asks only about approved records.

WHAT IS READ, AND WHY IT IS DATA. change-manager commits `contracts/ratified_rollout_policy.json`,
generated from `current()` and held to it by its own test, so the file cannot drift from the code
that decides. This check fetches that file over the contents API and `json.loads` it. It never
imports or executes anything it fetches: fetched content is data, and a check that ran the far
repository's code would make that repository's `main` a code-execution path into this one's
required CI job. An earlier draft of this check did exactly that, behind an import allowlist that
the builtins make bypassable; it was replaced before merging.

WHAT IS COMPARED. For every repository the current version ratifies:
  - its pinned rollout revision must be TRANSCRIBED here -- `attestation_for` must know it, or
    nothing here can derive criteria for it at all;
  - what this repository derives at that revision must EQUAL the ratified list;
  - this repository's rollback plan for it must EQUAL the ratified plan.

Keyed on change-manager's PIN, not on this repository's newest transcription. The transcription
registry is additive -- a revision is transcribed BEFORE a policy version ratifies it, because the
ratified text is derived from it -- so vetting the pinned revision keeps this check green across
that ordering. It goes red only when the two disagree about the revision production is held to.

EQUALITY, never a subset: `objections` is `tuple(criteria) != ratified`.

RESIDUALS. It vets change-manager's `main`, not what production serves; change-manager redeploys on
a push to `main`, so they converge in minutes. It vets only the CURRENT version. And it trusts
change-manager's test to keep the file equal to `current()` -- a file edited by hand on that side
with the test deleted would be believed here.

Exit 0: every ratified repository's criteria and rollback plan equal what this repository derives.
Exit 1: they do not, or the comparison could not be resolved. `--ref` names another revision of
change-manager to read (a pull request's branch, to prove a change before it merges).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from change_proposer.criteria import CriteriaUnavailable, acceptance_criteria, rollback_for
from deploy_watcher.workflows import attestation_for
from scripts.check_profile_budget_agreement import Unresolvable, fetch

CHANGE_MANAGER_REPO = "AlobarQuest/change-manager"
POLICY_PATH = "contracts/ratified_rollout_policy.json"
# `main`: this repository declares no revision of change-manager, and a record is held to the
# policy change-manager is running, which is its `main` within minutes.
POLICY_REF = "main"
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


@dataclass(frozen=True)
class Ratified:
    repository: str
    blob_sha: str
    criteria: tuple[str, ...]
    rollback: dict[str, Any]


@dataclass(frozen=True)
class Verdict:
    repository: str
    blob_sha: str
    divergences: tuple[str, ...]


def _strings(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise Unresolvable(f"{where} is not a non-empty list of strings")
    return value


def parse(text: str) -> tuple[int, list[Ratified]]:
    """The policy version and its ratified entries, validated. Data in, data out -- never code."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise Unresolvable(f"{POLICY_PATH} is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise Unresolvable(f"{POLICY_PATH} is not a JSON object")
    schema = document.get("schema_version")
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise Unresolvable(
            f"{POLICY_PATH} declares schema_version {schema!r}; this check reads "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}. A new schema is taught here first."
        )
    version = document.get("policy_version")
    repositories = document.get("repositories")
    if not isinstance(version, int) or isinstance(version, bool):
        raise Unresolvable(f"{POLICY_PATH} has no integer policy_version")
    if not isinstance(repositories, dict) or not repositories:
        raise Unresolvable(f"{POLICY_PATH} ratifies no repository")

    entries: list[Ratified] = []
    for repository, entry in sorted(repositories.items()):
        where = f"{POLICY_PATH} {repository}"
        if not isinstance(entry, dict):
            raise Unresolvable(f"{where} is not an object")
        workflow = entry.get("rollout_workflow")
        blob = workflow.get("blob_sha") if isinstance(workflow, dict) else None
        if not isinstance(blob, str) or not blob:
            raise Unresolvable(f"{where} pins no rollout workflow blob")
        rollback = entry.get("rollback_plan")
        if not isinstance(rollback, dict) or not isinstance(rollback.get("target"), str):
            raise Unresolvable(f"{where} has no rollback plan with a target")
        _strings(rollback.get("steps"), f"{where} rollback_plan.steps")
        criteria = _strings(entry.get("acceptance_criteria"), f"{where} acceptance_criteria")
        entries.append(Ratified(repository, blob, tuple(criteria), rollback))
    return version, entries


def judge(entries: list[Ratified]) -> list[Verdict]:
    """One verdict per ratified repository. Pure, so it is what gets tested."""
    verdicts: list[Verdict] = []
    for ratified in entries:
        divergences: list[str] = []
        attestation = attestation_for(ratified.blob_sha)
        if attestation is None:
            divergences.append(
                f"rollout revision {ratified.blob_sha} is not transcribed in "
                "deploy_watcher.workflows, so nothing here can derive criteria for it"
            )
        else:
            derived = acceptance_criteria(ratified.repository, attestation)
            if tuple(derived) != ratified.criteria:
                divergences.append(
                    "acceptance criteria differ:\n"
                    f"    derived  {list(derived)!r}\n    ratified {list(ratified.criteria)!r}"
                )
        try:
            plan = rollback_for(ratified.repository)
        except CriteriaUnavailable as error:
            divergences.append(str(error))
        else:
            ours = {"steps": list(plan.steps), "target": plan.target}
            if ours != ratified.rollback:
                divergences.append(
                    f"rollback plan differs:\n    derived  {ours!r}\n"
                    f"    ratified {ratified.rollback!r}"
                )
        verdicts.append(Verdict(ratified.repository, ratified.blob_sha, tuple(divergences)))
    return verdicts


def main(argv: list[str] | None = None, fetcher=fetch) -> int:
    parser = argparse.ArgumentParser(
        description="Hold derived rollout criteria to change-manager's ratified policy."
    )
    parser.add_argument("--ref", default=POLICY_REF, help="change-manager revision to read")
    ref = parser.parse_args(argv).ref
    try:
        version, entries = parse(fetcher(CHANGE_MANAGER_REPO, POLICY_PATH, ref))
        verdicts = judge(entries)
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"{CHANGE_MANAGER_REPO}@{ref} {POLICY_PATH}: policy version {version}")
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
