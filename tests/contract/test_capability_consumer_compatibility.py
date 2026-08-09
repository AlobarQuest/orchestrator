"""WS-P3.7 Increment 3: the offline half of the pull-request gate that refuses capability drift.

`scripts/check_capability_consumer_compatibility.py` reads the consumer's capability
vocabulary at the revision this repo's caller workflow pins and refuses a change that would
declare a name that revision does not recognise. The reading is over the network, so it runs
as its own step rather than in the suite -- but everything it decides WITH is pure, and pure
is what gets tested here:

- the source parser agrees with the shipped constant, for the vocabulary that is importable
  on this side;
- a moved, renamed or restructured vocabulary is loud rather than empty;
- the comparison actually reports a declared-but-unrecognised name.

The last one matters most. The brief twin exists because a served-but-undeclared field cost
the estate a day of dead dispatches; a declared-but-unrecognised CAPABILITY is worse in
kind, because the consumer refuses the whole envelope rather than ignoring the extra. Until
this gate the rule against it was prose in a handoff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES
from scripts import check_capability_consumer_compatibility as check

SCRIPT = Path("scripts/check_capability_consumer_compatibility.py")


def test_the_vocabulary_parser_agrees_with_the_shipped_constant() -> None:
    """The fidelity pin: source-reading must mean the same thing as importing.

    The consumer's vocabulary is read from source because it lives at an arbitrary revision
    of another repository and installing it would drag that repository's dependency tree
    into this repository's pull-request gate. That trade is only sound while the parse is
    exact, and this is the one place both readings are available for the same lane.
    """
    parsed = check.declared_capabilities(check.SERVED_VOCABULARY_SOURCE.read_text())

    assert parsed == set(RUNNER_CAPABILITIES)


def test_the_parser_reads_the_consumer_spelling_too() -> None:
    """The consumer annotates the mapping as a bare `Final` and this repo parameterises it.

    One parser reads both, so it must not be keyed on either spelling of the annotation --
    nor on the lane it is not asked about.
    """
    source = (
        "CAPABILITY_VOCABULARY: Final = {\n"
        '    "runner": (\n'
        '        "repo.read",\n'
        '        "github.pr.merge",\n'
        "    ),\n"
        '    "other": ("ignored",),\n'
        "}\n"
    )

    assert check.declared_capabilities(source) == {"repo.read", "github.pr.merge"}
    assert check.declared_capabilities(source, key="other") == {"ignored"}


@pytest.mark.parametrize(
    "source",
    [
        'SOMETHING_ELSE = {"runner": ("repo.read",)}\n',
        'CAPABILITY_VOCABULARY = {"worker": ("repo.read",)}\n',
        'CAPABILITY_VOCABULARY = {"runner": frozenset({"repo.read"})}\n',
        'CAPABILITY_VOCABULARY = dict(runner=("repo.read",))\n',
    ],
    ids=["renamed", "lane-renamed", "not-a-literal-sequence", "not-a-mapping-literal"],
)
def test_a_vocabulary_the_parser_cannot_read_is_loud(source: str) -> None:
    """Never a silent pass: a parser that found nothing must not read as "nothing declared".

    An empty served set makes every difference empty, so the gate would report PASS on a
    vocabulary it never actually read -- which is the failure mode of the SHA-comparing
    conformance check this whole family of gates was built to replace.
    """
    with pytest.raises(check.Unresolvable):
        check.declared_capabilities(source)


def test_the_comparison_names_every_capability_the_pinned_consumer_cannot_parse() -> None:
    """The guard shown firing, on the exact shape of the WS-P3.7 ordering hazard.

    This repo declares a capability; the pinned consumer does not recognise it. Shipped in
    that order, every dispatch of a unit whose envelope names it dies at envelope parse.
    Here it is a set difference with a name on it.
    """
    served = 'CAPABILITY_VOCABULARY = {"runner": ("repo.read", "github.pr.merge")}\n'
    behind = 'CAPABILITY_VOCABULARY = {"runner": ("repo.read",)}\n'
    in_step = 'CAPABILITY_VOCABULARY = {"runner": ("repo.read", "github.pr.merge")}\n'

    assert check.declared_capabilities(served) - check.declared_capabilities(behind) == {
        "github.pr.merge"
    }
    assert not check.declared_capabilities(served) - check.declared_capabilities(in_step)


def test_a_capability_the_consumer_knows_and_this_repo_does_not_is_not_a_failure() -> None:
    """The direction that is deliberately NOT checked, asserted so it stays deliberate.

    Unit ingress refuses a name this repo does not declare, so no envelope can carry one --
    and the two sets are not meant to be equal in the first place. Failing on this direction
    would make every consumer-side addition a red pull request here for no gain.
    """
    served = 'CAPABILITY_VOCABULARY = {"runner": ("repo.read",)}\n'
    ahead = 'CAPABILITY_VOCABULARY = {"runner": ("repo.read", "something.new")}\n'

    assert not check.declared_capabilities(served) - check.declared_capabilities(ahead)


def test_the_gate_runs_the_script_this_module_tests() -> None:
    """A check nothing invokes is the defect it guards against, wearing a different hat."""
    invocations = [
        path for path in Path(".github/workflows").glob("*.yml") if SCRIPT.stem in path.read_text()
    ]

    assert [path.name for path in invocations] == ["quality.yml"]
