"""The offline half of the gate that refuses drift in the factory's pull request marking.

`scripts/check_pr_title_marking_compatibility.py` reads factory-runner's own source at the pinned
and recommended revisions and asks whether the title it stamps is still one `change_proposer` can
read. The reading is over the network, so it runs as its own CI step -- but everything it decides
WITH is pure, and pure is what gets tested here:

- the parser reads the consumer's real spelling, and the rendered title is recognised;
- a moved format is refused, in every shape a move can take;
- a title interpolating some OTHER identifier is LOUD rather than silently rendering a specimen
  the recogniser happens to match -- a package id is a UUID too, so this is the one drift that
  could pass while every real title carried the wrong value;
- one revision moving the format is enough to refuse, even when the other is in step.

The specimen below is a byte copy of factory-runner's call. It exists to pin the PARSER, exactly
as the brief twin pins its own against `RunnerBriefResponse` -- the live comparison against the
real revision is the CI step's job, and the check is proven against the real remote in the build
report rather than here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from change_proposer.factory_marking import factory_unit_id
from scripts import check_pr_title_marking_compatibility as check
from scripts.check_brief_consumer_compatibility import Unresolvable

SCRIPT = Path("scripts/check_pr_title_marking_compatibility.py")

# Byte-copied from `factory-runner/src/factory_runner/cli.py`, the `gh pr create` call.
CONSUMER_SPELLING = """
def _open(brief, verification_summaries):
    pr_url = _run_command(
        [
            "gh",
            "pr",
            "create",
            "--title",
            f"SDS {brief.work_unit.id}: {brief.work_unit.title}",
            "--body",
            _pr_body(brief, verification_summaries, []),
        ]
    ).strip()
"""


def _rendered(source: str) -> str:
    return check.rendered_title(check.title_expression(source))


def test_the_parser_reads_the_consumer_s_real_spelling() -> None:
    """The fidelity pin. Source-reading must mean what running the consumer would mean."""
    rendered = _rendered(CONSUMER_SPELLING)

    assert rendered == f"SDS {check.SPECIMEN_UNIT_ID}: {check.SPECIMEN_TEXT}"
    assert factory_unit_id(rendered) == check.SPECIMEN_UNIT_ID
    assert not check.unreadable_revisions({"abc": rendered})


@pytest.mark.parametrize(
    ("moved", "reason"),
    [
        ('f"SDS-{brief.work_unit.id}: {brief.work_unit.title}"', "the separator moved"),
        ('f"SDS {brief.work_unit.id} - {brief.work_unit.title}"', "the colon went"),
        ('f"[SDS] {brief.work_unit.id}: {brief.work_unit.title}"', "the prefix is decorated"),
        ('f"{brief.work_unit.title} (SDS {brief.work_unit.id})"', "the order reversed"),
    ],
)
def test_a_moved_title_format_is_refused(moved: str, reason: str) -> None:
    """Keyed on RECOGNITION, so every move that breaks reading the unit reds -- and only those."""
    rendered = _rendered(CONSUMER_SPELLING.replace(_TITLE_LITERAL, moved))

    assert factory_unit_id(rendered) is None, reason
    assert check.unreadable_revisions({"abc": rendered}) == {"abc": rendered}


def test_a_change_after_the_identifier_is_not_a_move() -> None:
    """The half that must NOT red: the title's prose is not the marking, and a check that
    reddened on it would train its reader to ignore it."""
    rendered = _rendered(
        CONSUMER_SPELLING.replace(
            _TITLE_LITERAL, 'f"SDS {brief.work_unit.id}: [{brief.package.id}] {brief.title}"'
        )
    )

    assert factory_unit_id(rendered) == check.SPECIMEN_UNIT_ID


def test_a_title_carrying_SOME_OTHER_identifier_is_loud_rather_than_rendered() -> None:
    """THE silent drift, and the reason the parser reads which value is interpolated.

    A package id is a UUID as well. If the title interpolated one, a position-keyed renderer would
    substitute the specimen there, the rendered string would be recognised, and the gate would pass
    while every real title named the wrong thing. Refusing to guess is the only safe answer.
    """
    with pytest.raises(Unresolvable, match="work unit"):
        _rendered(
            CONSUMER_SPELLING.replace(
                _TITLE_LITERAL, 'f"SDS {brief.package.id}: {brief.work_unit.title}"'
            )
        )


def test_a_renamed_but_unambiguous_identifier_is_still_read() -> None:
    """Two accepted spellings, so a straightforward rename does not red a format that has not
    moved. Anything broader would be the silent case above."""
    rendered = _rendered(
        CONSUMER_SPELLING.replace(_TITLE_LITERAL, 'f"SDS {work_unit_id}: {brief.title}"')
    )

    assert factory_unit_id(rendered) == check.SPECIMEN_UNIT_ID


def test_a_title_that_is_no_longer_an_f_string_is_loud() -> None:
    """A constant carries no identifier; a variable or a call cannot be rendered without running
    the consumer. Neither is approximated."""
    with pytest.raises(Unresolvable, match="f-string"):
        _rendered(CONSUMER_SPELLING.replace(_TITLE_LITERAL, '"SDS: a pull request"'))
    with pytest.raises(Unresolvable, match="f-string"):
        _rendered(CONSUMER_SPELLING.replace(_TITLE_LITERAL, "_title(brief)"))


@pytest.mark.parametrize(
    "source",
    [
        CONSUMER_SPELLING.replace('"--title"', '"--head"'),
        CONSUMER_SPELLING + CONSUMER_SPELLING.replace("_open", "_open_again"),
        # The flag as the LAST element: there is no value after it to read.
        'x = ["gh", "pr", "create", "--title"]',
    ],
)
def test_a_title_this_cannot_locate_is_loud(source: str) -> None:
    """Zero, two, or a flag with nothing after it. Each is refused rather than guessed at -- and
    the two-call case matters most, because taking the first would vet a call that may not be the
    one that opens a pull request."""
    with pytest.raises(Unresolvable, match="--title"):
        check.title_expression(source)


def test_the_parser_is_loud_when_the_f_string_interpolates_nothing() -> None:
    with pytest.raises(Unresolvable, match="work unit"):
        _rendered(CONSUMER_SPELLING.replace(_TITLE_LITERAL, 'f"SDS a pull request {1 + 1}"'))


def test_one_revision_moving_the_format_is_enough_to_refuse() -> None:
    """The reason two revisions are read at all: dispatch fires the caller workflow in the unit's
    own target repository, so the recommended revision is the one that will actually run."""
    good = f"SDS {check.SPECIMEN_UNIT_ID}: {check.SPECIMEN_TEXT}"
    bad = f"SDS-{check.SPECIMEN_UNIT_ID}: {check.SPECIMEN_TEXT}"

    assert check.unreadable_revisions({"pinned": good, "recommended": bad}) == {"recommended": bad}


def test_the_specimen_is_a_valid_work_unit_identifier() -> None:
    """Otherwise every comparison above compares None with None and proves nothing."""
    assert factory_unit_id(f"SDS {check.SPECIMEN_UNIT_ID}: x") == check.SPECIMEN_UNIT_ID


def test_the_check_is_wired_into_the_pull_request_gate() -> None:
    """A check nothing runs is a comment. It shares the job its two siblings sit in, because that
    job's NAME is the protected status context and a new one would report without blocking."""
    workflow = Path(".github/workflows/quality.yml").read_text()

    assert "scripts.check_pr_title_marking_compatibility" in workflow
    assert SCRIPT.exists()


def _title_literal() -> str:
    """The exact source text of the specimen's title expression, so the mutations above replace
    it rather than a string that merely looks like it."""
    for node in ast.walk(ast.parse(CONSUMER_SPELLING)):
        if isinstance(node, ast.JoinedStr):
            return ast.get_source_segment(CONSUMER_SPELLING, node) or ""
    raise AssertionError("the specimen carries no f-string")


_TITLE_LITERAL = _title_literal()
