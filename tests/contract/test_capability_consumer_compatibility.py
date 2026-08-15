"""WS-P3.7 Increment 3: the offline half of the pull-request gate that refuses capability drift.

`scripts/check_capability_consumer_compatibility.py` reads the consumer's capability
vocabulary at TWO revisions -- the one this repo's caller workflow pins, and the one
factory-runner recommends to every caller -- and refuses a change declaring a name either
does not recognise. The reading is over the network, so it runs as its own step rather than
in the suite -- but everything it decides WITH is pure, and pure is what gets tested here:

- the source parser agrees with the shipped constant, for the vocabulary that is importable
  on this side;
- a moved, renamed, restructured or EMPTY vocabulary is loud rather than silently empty;
- the recommendation is refused unless it is an immutable revision;
- one revision falling behind is enough to refuse, even when the other is in step.

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
        'CAPABILITY_VOCABULARY = {"runner": ()}\n',
        'CAPABILITY_VOCABULARY = {"runner": (READ, EDIT)}\n',
    ],
    ids=[
        "renamed",
        "lane-renamed",
        "not-a-literal-sequence",
        "not-a-mapping-literal",
        "empty-sequence",
        "members-are-not-literals",
    ],
)
def test_a_vocabulary_the_parser_cannot_read_is_loud(source: str) -> None:
    """Never a silent pass: a parser that found nothing must not read as "nothing declared".

    An empty served set makes every difference empty, so the gate would report PASS on a
    vocabulary it never actually read -- which is the failure mode of the SHA-comparing
    conformance check this whole family of gates was built to replace.

    The last two cases arrived from review and are the ones the first four missed: a
    *well-formed* literal sequence that yields no strings. Both parse cleanly and both
    returned an empty set silently until the emptiness itself was made loud.
    """
    with pytest.raises(check.Unresolvable):
        check.declared_capabilities(source)


def test_one_revision_falling_behind_is_enough_to_refuse() -> None:
    """The reason two consumer revisions are read rather than one.

    Dispatch fires the caller workflow in the UNIT'S OWN target repository, so the runner
    that executes is that repository's pin -- which follows factory-runner's
    RECOMMENDED_CALLER_PIN, not this repository's caller workflow. A gate reading only this
    repository's pin vets the wrong caller for every unit that is not about this repository.
    """
    served = {"repo.read", "github.pr.merge"}
    in_step = {"repo.read", "github.pr.merge"}
    behind = {"repo.read"}

    assert check.revisions_that_cannot_parse(served, {"aaa": in_step, "bbb": in_step}) == {}
    assert check.revisions_that_cannot_parse(served, {"aaa": in_step, "bbb": behind}) == {
        "bbb": ["github.pr.merge"]
    }
    assert check.revisions_that_cannot_parse(served, {"aaa": behind, "bbb": in_step}) == {
        "aaa": ["github.pr.merge"]
    }


def test_a_revision_that_is_both_pinned_and_recommended_says_both() -> None:
    """The healthy steady state must not read as though only one revision was checked.

    A mapping keyed by revision silently keeps whichever label was written last when the two
    coincide, so the output claimed the recommendation was read and said nothing about the
    pin. Caught by running the PASS path rather than by reading it.
    """
    same = check._roles("a" * 40, "a" * 40)

    assert len(same) == 1
    assert "pinned by" in same["a" * 40] and "recommended" in same["a" * 40]

    apart = check._roles("a" * 40, "b" * 40)

    assert "pinned by" in apart["a" * 40] and "recommended" not in apart["a" * 40]
    assert "recommended" in apart["b" * 40] and "pinned by" not in apart["b" * 40]


def test_a_recommendation_that_is_not_an_immutable_revision_is_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOMMENDED_CALLER_PIN is read as a revision, so a branch name in it decides nothing.

    Same rule the caller pin already carries, applied to the file the whole estate follows:
    a mutable ref resolves to different code tomorrow, so a compatibility answer against it
    would expire.
    """
    monkeypatch.setattr(check, "fetch", lambda *_: "main\n")

    with pytest.raises(check.Unresolvable, match="full 40-character commit"):
        check.recommended_revision("AlobarQuest/factory-runner")


def test_the_recommendation_is_read_from_the_default_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberately mutable, unlike everything else this gate reads.

    The recommendation is the estate's current answer to "what should every caller be pinned
    to", so pinning THAT to a revision would freeze the question. Whitespace is stripped: the
    file carries a trailing newline.
    """
    calls: list[tuple[str, ...]] = []

    def record(*args: str) -> str:
        calls.append(args)
        return "f" * 40 + "\n"

    monkeypatch.setattr(check, "fetch", record)

    assert check.recommended_revision("AlobarQuest/factory-runner") == "f" * 40
    assert calls == [("AlobarQuest/factory-runner", "RECOMMENDED_CALLER_PIN", "HEAD")]


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
