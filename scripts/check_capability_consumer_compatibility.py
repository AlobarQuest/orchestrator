#!/usr/bin/env python3
"""Refuse a pull request that names a runner capability the pinned consumer cannot parse.

The twin of `check_brief_consumer_compatibility.py`, for the other cross-repo vocabulary.
WS-P2.23 built that one for the runner BRIEF after the orchestrator served an `enrichment`
key against a consumer that had never heard of it and every dispatch in the estate died
for a day. Nobody generalised it, and capabilities are the sharper of the two surfaces:
the consumer's envelope model is `extra="forbid"` and its `_validate_capabilities` raises
on any key outside its own vocabulary, so a name the pinned revision does not know is not
a field that goes unused -- it kills the run at envelope parse, with the unit's ordinal
spent. That is the WS-P2.33 shape, where this repository admits what the consumer refuses
and nothing sees the disagreement until a real run dies.

The ordering rule -- merge the consumer, advance every caller's pin, and only then say the
name -- has been prose in a handoff. This is the enforcement, and it belongs in the
pull-request gate because the pull request that adds the name is the thing that must not
merge.

THE REVISION CHECKED IS THE RECOMMENDATION, and that is what dispatch makes load-bearing.
Dispatch fires the caller workflow **in the unit's own target repository**
(`services/dispatch.py`, from `authority.constraints.target_repository`), so the revision
that actually runs is that repository's caller pin -- never a pin this repository holds.

The estate's answer to "what should every caller be pinned to" already exists:
factory-runner's `RECOMMENDED_CALLER_PIN`, which the conformance kit's `runner.caller`
check compares each repository's `uses:` line against by exact equality, as an ADMISSION
check rather than an advisory one. So this gate vets the RECOMMENDATION, and `runner.caller`
holds each target repository to it. Neither alone is sufficient and the composition is:

    runner.caller  : every target repository is AT the recommended revision
    this gate      : the recommended revision recognises every name we declare

Until ADR-0015's amendment of 2026-09-11 it read a SECOND revision as well -- the pin in
this repository's own caller workflow -- and deliberately did not require the two to be
equal. That file is gone: this repository declares `factory_target = false` and is not
dispatched to, so a pin of its own would have described a run that cannot happen. Nothing
about the answer moved, because the recommendation was already the load-bearing half and
the two agreed at the moment of the change.

Residual, stated rather than papered over: a target repository whose caller has drifted off
the recommendation is invisible here. `runner.caller` is what sees that, and it runs per
repository in the conformance kit rather than in this pull-request gate.

What it does, reusing the brief check's revision reader, fetcher and premise assertion
rather than keeping a second copy of them (two readers of one value could vet two different
revisions):

1. read the estate's recommended caller revision from the consumer's default branch;
2. assert that the workflow at that revision installs its own commit, so the revision named
   is the CLI revision a run would execute, rather than trusting it;
3. read the consumer's capability vocabulary at that revision, from source;
4. require every name this repo's runner vocabulary declares to be known there.

One direction only, deliberately. A name the CONSUMER knows and this repo does not is
harmless -- unit ingress refuses it here, so no envelope can carry it. The asymmetry is the
same one `capability_vocabulary.py` documents: the orchestrator's accepted set is a
superset that includes work no runner performs, and only the runner-executable subset has
to be mutually understood.

Usage:
    python3 -m scripts.check_capability_consumer_compatibility

Exit 0: the recommended consumer revision recognises every runner capability this repo
declares. Exit 1: it does not, or the revision could not be resolved.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from scripts.check_brief_consumer_compatibility import (
    CONSUMER_REPOSITORY,
    CONSUMER_WORKFLOW_PATH,
    Unresolvable,
    assert_the_workflow_installs_its_own_commit,
    fetch,
    recommended_revision,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVED_VOCABULARY_SOURCE = REPO_ROOT / "src/orchestrator/capability_vocabulary.py"
CONSUMER_VOCABULARY_PATH = "src/factory_runner/capability_vocabulary.py"
VOCABULARY = "CAPABILITY_VOCABULARY"
RUNNER_KEY = "runner"


def declared_capabilities(source: str, key: str = RUNNER_KEY) -> set[str]:
    """The capability names `CAPABILITY_VOCABULARY[key]` declares, read from module source.

    Source rather than import because the consumer's module lives at an arbitrary revision
    of another repository, and installing it would drag that repository's whole dependency
    tree into this repository's pull-request gate to read one tuple. Both repositories spell
    the vocabulary the same way -- a module-level mapping from lane to a tuple of string
    literals -- so one parser reads both, and
    `tests/contract/test_capability_consumer_compatibility.py` pins it against the shipped
    `RUNNER_CAPABILITIES` on the side where both readings are available. A parser that
    stopped agreeing is caught before it can vet anything wrongly.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or _assigned_name(node) != VOCABULARY:
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            raise Unresolvable(f"{VOCABULARY} is no longer a mapping literal")
        for lane, names in zip(value.keys, value.values, strict=True):
            if not (isinstance(lane, ast.Constant) and lane.value == key):
                continue
            if not isinstance(names, (ast.Tuple, ast.List)):
                raise Unresolvable(f"{VOCABULARY}[{key!r}] is no longer a literal sequence")
            declared = {
                item.value
                for item in names.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            # An empty answer is never legitimate and would be the WORST outcome: on the
            # served side it makes every difference empty, so the gate reports PASS having
            # read nothing -- the exact failure mode this parser's loudness exists to
            # prevent. Reached when the sequence is empty, or when its members are named
            # constants rather than literals.
            if not declared:
                raise Unresolvable(
                    f"{VOCABULARY}[{key!r}] yielded no capability names -- it is empty, or "
                    "its members are no longer string literals"
                )
            return declared
        raise Unresolvable(f"{VOCABULARY} declares no {key!r} lane")
    raise Unresolvable(f"no {VOCABULARY} found -- the vocabulary has moved or been renamed")


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def revisions_that_cannot_parse(
    served: set[str], accepted: dict[str, set[str]]
) -> dict[str, list[str]]:
    """Per consumer revision, the names it does not recognise -- empty when all of them do.

    Extracted from `main` so the load-bearing claim is assertable without the network: ONE
    revision falling behind is enough to refuse. It stays keyed by revision although only
    one is read today, because the refusal message names the revision that cannot parse and
    a shape that can carry more than one costs nothing to keep.
    """
    return {
        revision: sorted(served - names) for revision, names in accepted.items() if served - names
    }


def main() -> int:
    repo = CONSUMER_REPOSITORY
    try:
        recommended = recommended_revision(repo)
        assert_the_workflow_installs_its_own_commit(
            fetch(repo, CONSUMER_WORKFLOW_PATH, recommended), recommended
        )
        accepted = {
            recommended: declared_capabilities(fetch(repo, CONSUMER_VOCABULARY_PATH, recommended))
        }
        served = declared_capabilities(SERVED_VOCABULARY_SOURCE.read_text())
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    for revision, names in accepted.items():
        print(
            f"consumer:  {repo}@{revision[:8]} (recommended to every caller) -- "
            f"recognises {len(names)}"
        )
    print(f"served:    {SERVED_VOCABULARY_SOURCE.name} declares {len(served)}")

    unknown = revisions_that_cannot_parse(served, accepted)
    if not unknown:
        print(
            "\nPASS: every runner capability this repo declares is known at the revision "
            "recommended to every caller."
        )
        return 0

    for revision, names in unknown.items():
        print(
            f"\nFAIL: {repo}@{revision[:8]} (recommended to every caller) does not recognise "
            f"{len(names)} capability(s) this repo declares: {names}",
            file=sys.stderr,
        )
    print(
        "\nThe consumer raises on an unknown capability and forbids extra envelope fields, so an "
        "envelope carrying\none of these does not merely go unused -- it kills that unit's run at "
        "envelope parse, with the attempt\nspent and nothing between here and there to notice. "
        "And dispatch fires the caller workflow in the UNIT'S\nOWN TARGET REPOSITORY, so the "
        "revision that runs is that repository's pin, which follows the\nrecommendation this "
        "gate reads.\n"
        "Merge the capability into the consumer first, then advance RECOMMENDED_CALLER_PIN and "
        "every caller, then re-run.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
