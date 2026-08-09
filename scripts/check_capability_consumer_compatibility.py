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

TWO revisions are checked, and the second one is the load-bearing one. Dispatch fires the
caller workflow **in the unit's own target repository** (`services/dispatch.py`, from
`authority.constraints.target_repository`), so the revision that actually runs is that
repository's caller pin -- not this one's. Checking only this repository's pin, as the brief
twin does, vets the wrong caller for every unit that is not about this repository.

The estate's answer to "what should every caller be pinned to" already exists:
factory-runner's `RECOMMENDED_CALLER_PIN`, which the conformance kit's `runner.caller`
check compares each repository's `uses:` line against by exact equality, as an ADMISSION
check rather than an advisory one. So this gate vets the RECOMMENDATION, and `runner.caller`
holds each target repository to it. Neither alone is sufficient and the composition is:

    runner.caller  : every target repository is AT the recommended revision
    this gate      : the recommended revision recognises every name we declare

It deliberately does NOT assert that this repository's pin EQUALS the recommendation. That
would red every open pull request here the moment factory-runner advanced its recommendation,
punishing changes that have nothing to do with it -- and equality is a proxy for the property
that matters, which is whether the revision can parse what we serve. Checking both revisions
answers that directly and is silent on an unrelated move.

Residual, stated rather than papered over: a target repository whose caller has drifted off
the recommendation is invisible here. `runner.caller` is what sees that, and it runs per
repository in the conformance kit rather than in this pull-request gate.

What it does, reusing the brief check's pin reader, fetcher and premise assertion rather
than keeping a second copy of them (two readers of one pin could vet two different
revisions):

1. read the pinned revision from this repo's caller workflow -- that SHA is the consumer
   revision, because the workflow at X installs the CLI at X;
2. assert that premise still holds at that revision, rather than trusting it;
3. read the estate's recommended caller revision from the consumer's default branch;
4. read the consumer's capability vocabulary at BOTH revisions, from source;
5. require every name this repo's runner vocabulary declares to be known at both.

One direction only, deliberately. A name the CONSUMER knows and this repo does not is
harmless -- unit ingress refuses it here, so no envelope can carry it. The asymmetry is the
same one `capability_vocabulary.py` documents: the orchestrator's accepted set is a
superset that includes work no runner performs, and only the runner-executable subset has
to be mutually understood.

Usage:
    python3 -m scripts.check_capability_consumer_compatibility

Exit 0: both the pinned and the recommended consumer revision recognise every runner
capability this repo declares. Exit 1: one of them does not, or a revision could not be
resolved.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from scripts.check_brief_consumer_compatibility import (
    CONSUMER_WORKFLOW_PATH,
    Unresolvable,
    assert_the_workflow_installs_its_own_commit,
    fetch,
    pinned_consumer,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CALLER_WORKFLOW = REPO_ROOT / ".github/workflows/factory-runner-pilot.yml"
SERVED_VOCABULARY_SOURCE = REPO_ROOT / "src/orchestrator/capability_vocabulary.py"
CONSUMER_VOCABULARY_PATH = "src/factory_runner/capability_vocabulary.py"
RECOMMENDED_PIN_PATH = "RECOMMENDED_CALLER_PIN"
VOCABULARY = "CAPABILITY_VOCABULARY"
RUNNER_KEY = "runner"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


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


def recommended_revision(repo: str) -> str:
    """The revision every caller in the estate is expected to be pinned to."""
    ref = fetch(repo, RECOMMENDED_PIN_PATH, "HEAD").strip()
    if not FULL_SHA.match(ref):
        raise Unresolvable(
            f"{repo}'s {RECOMMENDED_PIN_PATH} reads {ref!r}, which is not a full 40-character "
            "commit, so there is no revision to check the estate's callers against."
        )
    return ref


def revisions_that_cannot_parse(
    served: set[str], accepted: dict[str, set[str]]
) -> dict[str, list[str]]:
    """Per consumer revision, the names it does not recognise -- empty when all of them do.

    Extracted from `main` so the load-bearing claim is assertable without the network: ONE
    revision falling behind is enough to refuse, so a recommendation the estate follows can
    red a pull request whose own pin is perfectly in step. That is the whole reason two
    revisions are read.
    """
    return {
        revision: sorted(served - names) for revision, names in accepted.items() if served - names
    }


def main() -> int:
    try:
        repo, workflow, ref = pinned_consumer(CALLER_WORKFLOW.read_text())
        assert_the_workflow_installs_its_own_commit(fetch(repo, CONSUMER_WORKFLOW_PATH, ref), ref)
        recommended = recommended_revision(repo)
        # `dict` rather than a set of revisions: order matters in the report, and the two are
        # usually the same commit, which should read as one line rather than two.
        accepted = {
            revision: declared_capabilities(fetch(repo, CONSUMER_VOCABULARY_PATH, revision))
            for revision in dict.fromkeys((ref, recommended))
        }
        served = declared_capabilities(SERVED_VOCABULARY_SOURCE.read_text())
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    role = {ref: f"pinned by {CALLER_WORKFLOW.name}", recommended: "recommended to every caller"}
    for revision, names in accepted.items():
        print(f"consumer:  {repo}@{revision[:8]} ({role[revision]}) -- recognises {len(names)}")
    print(f"served:    {SERVED_VOCABULARY_SOURCE.name} declares {len(served)}")

    unknown = revisions_that_cannot_parse(served, accepted)
    if not unknown:
        print(
            f"\nPASS: every runner capability this repo declares is known at {workflow}'s "
            "pinned revision and at the one recommended to every caller."
        )
        return 0

    for revision, names in unknown.items():
        print(
            f"\nFAIL: {repo}@{revision[:8]} ({role[revision]}) does not recognise "
            f"{len(names)} capability(s) this repo declares: {names}",
            file=sys.stderr,
        )
    print(
        "\nThe consumer raises on an unknown capability and forbids extra envelope fields, so an "
        "envelope carrying\none of these does not merely go unused -- it kills that unit's run at "
        "envelope parse, with the attempt\nspent and nothing between here and there to notice. "
        "And dispatch fires the caller workflow in the UNIT'S\nOWN TARGET REPOSITORY, so the "
        "revision that runs is that repository's pin, which follows the\nrecommendation rather "
        "than this file.\n"
        f"Merge the capability into the consumer first, then advance {RECOMMENDED_PIN_PATH} and "
        f"every caller\nincluding {CALLER_WORKFLOW.name}, then re-run.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
