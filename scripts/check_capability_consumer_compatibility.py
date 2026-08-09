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

What it does, reusing the brief check's pin reader, fetcher and premise assertion rather
than keeping a second copy of them (two readers of one pin could vet two different
revisions):

1. read the pinned revision from this repo's caller workflow -- that SHA is the consumer
   revision, because the workflow at X installs the CLI at X;
2. assert that premise still holds at that revision, rather than trusting it;
3. read the consumer's capability vocabulary at that revision, from source;
4. require every name this repo's runner vocabulary declares to be one of them.

One direction only, deliberately. A name the CONSUMER knows and this repo does not is
harmless -- unit ingress refuses it here, so no envelope can carry it. The asymmetry is the
same one `capability_vocabulary.py` documents: the orchestrator's accepted set is a
superset that includes work no runner performs, and only the runner-executable subset has
to be mutually understood.

Usage:
    python3 -m scripts.check_capability_consumer_compatibility

Exit 0: the pinned consumer recognises every runner capability this repo declares.
Exit 1: at least one is unknown to it, or the pin could not be resolved.
"""

from __future__ import annotations

import ast
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
            return {
                item.value
                for item in names.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
        raise Unresolvable(f"{VOCABULARY} declares no {key!r} lane")
    raise Unresolvable(f"no {VOCABULARY} found -- the vocabulary has moved or been renamed")


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def main() -> int:
    try:
        repo, workflow, ref = pinned_consumer(CALLER_WORKFLOW.read_text())
        assert_the_workflow_installs_its_own_commit(fetch(repo, CONSUMER_WORKFLOW_PATH, ref), ref)
        accepted = declared_capabilities(fetch(repo, CONSUMER_VOCABULARY_PATH, ref))
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    served = declared_capabilities(SERVED_VOCABULARY_SOURCE.read_text())
    unknown = sorted(served - accepted)

    print(f"consumer:  {repo}/{workflow}@{ref[:8]} -- recognises {len(accepted)} capabilities")
    print(f"served:    {SERVED_VOCABULARY_SOURCE.name} declares {len(served)}")
    if not unknown:
        print(f"\nPASS: every runner capability this repo declares is known at {repo}@{ref[:8]}.")
        return 0

    print(
        f"\nFAIL: the runner vocabulary names {len(unknown)} capability(s) the pinned consumer "
        f"does not recognise: {unknown}\n\n"
        f"The consumer at {repo}@{ref[:8]} raises on an unknown capability and forbids extra "
        "envelope fields, so an\nenvelope carrying one of these does not merely go unused -- it "
        "kills that unit's run at envelope parse,\nwith the attempt spent and nothing between "
        "here and there to notice.\n"
        f"Merge the capability into the consumer first, then advance the pin in "
        f"{CALLER_WORKFLOW.name}\nand in every other caller, then re-run.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
