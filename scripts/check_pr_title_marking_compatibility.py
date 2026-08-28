#!/usr/bin/env python3
"""Refuse a state in which the pull-request title the factory stamps is one we cannot read.

The third surface of the runner consumer gate, and the first one that points the OTHER WAY.
`check_brief_consumer_compatibility.py` and `check_capability_consumer_compatibility.py` both
refuse a change HERE that the pinned consumer could not use. This one refuses a divergence in a
convention the consumer PRODUCES and this repository CONSUMES: `change_proposer` recognises a
factory pull request by its title, and that title's format lives in factory-runner.

If that format moves, `factory_unit_id` silently stops matching, every factory pull request is
passed over as human-authored, no deploy change record is created, and both the ADR-0020 landing
lane and ADR-0022's unit-scoped observation die with nothing red. That is the failure `brain`'s
`ci.yml` blob produced one week earlier -- the producer refused every pull request for a day
while reporting `underivable` and nobody read it -- and a comment saying "keep this in step" is
not a pin.

## Why a compatibility check rather than a shared fixture

The estate's other cross-repo shapes are pinned as a byte-identical fixture in BOTH repositories
under one `CONTRACT_SHA256`. That is the stronger mechanism and it is unavailable here: a fixture
pin requires a test in factory-runner, and factory-runner is deliberately untouched by this
change. Reading the consumer's source at a pinned revision is the estate's other established
mechanism and is entirely consumer-side, which is what this can be.

## When it actually runs, stated plainly

The marking is produced there and consumed here, so this pin cannot fire the moment factory-runner
changes. It fires when something re-runs it: on every pull request opened in THIS repository, and
on the daily scheduled `Quality` run at 10:23 UTC, which fires this job because it carries no
event condition. So the drift window is at most twenty-four hours, and the answer arrives without
anyone having to remember to look.

Residual, stated rather than papered over, and it is the same one the capability check carries: a
target repository whose caller pin has drifted off `RECOMMENDED_CALLER_PIN` runs a revision this
check never read. The conformance kit's `runner.caller` is what sees that, per repository.

## What it does

1. read the pinned revision from this repo's caller workflow -- that SHA is the consumer
   revision, because the workflow at X installs the CLI at X;
2. assert that premise still holds at that revision, rather than trusting it;
3. read the estate's recommended caller revision from the consumer's default branch;
4. at BOTH revisions, read the literal title expression the consumer passes to `gh pr create`,
   render it with a specimen work unit, and require this repository's recogniser to read that
   specimen back out of it.

Keyed on RECOGNITION rather than on byte equality of the expression, because recognition is the
property that matters: a change to the part of the title after the identifier is harmless and
must not red a pull request here, while any change to the marking itself must. Keyed
additionally on WHICH VALUE is interpolated first, because that is not a nicety -- a title
interpolating some other identifier of the same shape (a package id is also a UUID) would render
and match a specimen while every real title carried the wrong identifier, which is silent and
severe. An expression this cannot recognise as the work unit's id is loud rather than assumed.

Usage:
    python3 -m scripts.check_pr_title_marking_compatibility

Exit 0: at both consumer revisions, the title the factory stamps is one this repository reads.
Exit 1: at least one of them is not, or a revision could not be resolved.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from change_proposer.factory_marking import factory_unit_id
from scripts.check_brief_consumer_compatibility import (
    CONSUMER_WORKFLOW_PATH,
    Unresolvable,
    assert_the_workflow_installs_its_own_commit,
    fetch,
    pinned_consumer,
)
from scripts.check_capability_consumer_compatibility import recommended_revision

REPO_ROOT = Path(__file__).resolve().parent.parent
CALLER_WORKFLOW = REPO_ROOT / ".github/workflows/factory-runner-pilot.yml"
CONSUMER_SOURCE_PATH = "src/factory_runner/cli.py"

# The command-line flag whose value IS the pull request title. Located by the flag rather than by
# searching for an f-string that looks right: the flag is what gives the expression its meaning,
# and a title assembled some other way must be loud rather than guessed at.
TITLE_FLAG = "--title"

# How the consumer spells the work unit's identifier. Two accepted spellings rather than one, so a
# straightforward rename does not red a pull request here for a format that has not moved -- and
# not an open-ended match, for the reason in the module docstring.
UNIT_ID_EXPRESSIONS = ("work_unit.id", "work_unit_id")

# A specimen, never a real unit. Any well-formed identifier does: what is being asked is whether
# rendering the consumer's own template and reading it back returns what was put in.
SPECIMEN_UNIT_ID = "0f1e2d3c-4b5a-4968-8776-655443332211"
SPECIMEN_TEXT = "a work unit title"


def title_expression(source: str) -> ast.expr:
    """The expression the consumer passes as `--title`, from its module source.

    Source rather than import, for the reason both sibling checks give: the consumer's module
    lives at an arbitrary revision of another repository, and installing it would drag that
    repository's whole dependency tree into this repository's pull-request gate to read one
    string. `tests/contract/test_pr_title_marking_compatibility.py` pins this parser against the
    consumer's real spelling, so a parser that stopped agreeing is caught before it can vet
    anything wrongly.
    """
    found = [
        sequence.elts[index + 1]
        for sequence in ast.walk(ast.parse(source))
        if isinstance(sequence, (ast.List, ast.Tuple))
        for index, element in enumerate(sequence.elts[:-1])
        if isinstance(element, ast.Constant) and element.value == TITLE_FLAG
    ]
    if len(found) != 1:
        raise Unresolvable(
            f"expected exactly one {TITLE_FLAG!r} argument in {CONSUMER_SOURCE_PATH}, found "
            f"{len(found)} -- the pull request title is no longer built where this can read it"
        )
    return found[0]


def rendered_title(expression: ast.expr) -> str:
    """The consumer's title template, rendered with a specimen work unit.

    An f-string, or nothing. A title that became a plain constant carries no identifier at all,
    and one that became a variable or a call cannot be rendered without executing the consumer --
    both are refused rather than approximated.
    """
    if not isinstance(expression, ast.JoinedStr):
        raise Unresolvable(
            f"the {TITLE_FLAG!r} argument is {type(expression).__name__}, not an f-string, so the "
            "title it produces cannot be rendered from source"
        )
    parts: list[str] = []
    identifiers = 0
    for part in expression.values:
        if isinstance(part, ast.Constant):
            parts.append(str(part.value))
        elif isinstance(part, ast.FormattedValue):
            if _is_the_work_unit_id(ast.unparse(part.value)):
                identifiers += 1
                parts.append(SPECIMEN_UNIT_ID)
            else:
                parts.append(SPECIMEN_TEXT)
        else:  # pragma: no cover - an f-string holds only these two node types
            raise Unresolvable(f"unreadable f-string part {type(part).__name__}")
    if identifiers != 1:
        raise Unresolvable(
            f"the title interpolates {identifiers} value(s) this recognises as the work unit's "
            f"identifier; exactly one is required. Accepted spellings: {list(UNIT_ID_EXPRESSIONS)}"
        )
    return "".join(parts)


def _is_the_work_unit_id(expression: str) -> bool:
    collapsed = "".join(expression.split())
    return any(collapsed.endswith(spelling) for spelling in UNIT_ID_EXPRESSIONS)


def unreadable_revisions(accepted: dict[str, str]) -> dict[str, str]:
    """Per consumer revision, the rendered title this repository cannot read -- empty when it can.

    Extracted from `main` so the load-bearing claim is assertable without the network: ONE
    revision moving the format is enough to refuse, even when the other is in step.
    """
    return {
        revision: title
        for revision, title in accepted.items()
        if factory_unit_id(title) != SPECIMEN_UNIT_ID
    }


def _roles(pinned: str, recommended: str) -> dict[str, str]:
    """What each revision IS, for the human reading the output. Both, when they coincide."""
    if pinned == recommended:
        return {pinned: f"pinned by {CALLER_WORKFLOW.name}, and recommended to every caller"}
    return {pinned: f"pinned by {CALLER_WORKFLOW.name}", recommended: "recommended to every caller"}


def main() -> int:
    try:
        repo, _workflow, ref = pinned_consumer(CALLER_WORKFLOW.read_text())
        assert_the_workflow_installs_its_own_commit(fetch(repo, CONSUMER_WORKFLOW_PATH, ref), ref)
        recommended = recommended_revision(repo)
        titles = {
            revision: rendered_title(title_expression(fetch(repo, CONSUMER_SOURCE_PATH, revision)))
            for revision in dict.fromkeys((ref, recommended))
        }
    except Unresolvable as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    role = _roles(ref, recommended)
    for revision, title in titles.items():
        print(f"consumer:  {repo}@{revision[:8]} ({role[revision]}) -- stamps {title!r}")

    unreadable = unreadable_revisions(titles)
    if not unreadable:
        print(
            "\nPASS: at both consumer revisions the factory's pull request title carries a "
            "marking this repository reads."
        )
        return 0

    for revision, title in unreadable.items():
        print(
            f"\nFAIL: {repo}@{revision[:8]} ({role[revision]}) stamps {title!r}, from which "
            f"`change_proposer.factory_marking.factory_unit_id` reads "
            f"{factory_unit_id(title)!r} rather than the work unit.",
            file=sys.stderr,
        )
    print(
        "\nThe producer recognises a factory pull request by that marking and by nothing else, so "
        "a format it cannot\nread is not a degraded answer -- every factory pull request is passed "
        "over as human-authored, no deploy change\nrecord is created, and both the ADR-0020 "
        "landing lane and ADR-0022's unit-scoped observation stop with\nnothing red. Bring "
        "`factory_marking` into step with the consumer, or bring the consumer back.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
