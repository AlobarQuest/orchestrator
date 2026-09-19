"""The offline half of the gate that refuses change-record vocabulary drift.

`scripts/check_change_record_field_compatibility.py` compares three local declarations against
what change-manager declares and serves. The far-side reading is over the network, so it runs as
a CI step -- but everything it DECIDES with is pure, and pure is what gets tested here:

- both parsers agree with what they claim to read, and are loud rather than guessing;
- the two premises the comparison rests on are asserted rather than assumed;
- the comparison actually reports a name the far side does not know, in BOTH directions it can
  fail in -- a producer sending an undeclared field, and the carry reading an unserved key.

The last one matters most. A guard built to catch a failure is worth nothing until it has been
shown to fire, and this one replaces an ordering rule that was prose: change-manager ships first,
because both its proposal schemas forbid extra keys.
"""

from __future__ import annotations

import http.client
from pathlib import Path

import pytest

from bump_proposer import cli as bump_proposer_cli
from change_proposer import cli as change_proposer_cli
from orchestrator.api.schemas import RunnerBriefResponse
from scripts import check_change_record_field_compatibility as check
from work_carrier import change_manager as work_carrier_change_manager

SCRIPT = Path("scripts/check_change_record_field_compatibility.py")

# A stand-in for change-manager's two proposal schemas, carrying exactly the names the two local
# declarations send plus one optional extra -- because the comparison is a SUBSET test and an
# extra on the far side must not fail it.
SCHEMAS = """
class DeployChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_repository: str
    pull_request_number: int
    change_class: str
    risk: str
    reasoning: str
    acceptance_criteria: list[str]
    rollback_plan: RollbackPlanIn
    actor: str
    note: str | None = None
    originating_observation_id: str | None = None


class WorkChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    package_revision: int
    package_source_repository: str
    risk: str
    reasoning: str
    actor: str
    note: str | None = None
    originating_observation_id: str | None = None
"""

# A stand-in for the row serializer and the listing route that hands it the rows.
API = """
def _item_dict(it: ChangeItem) -> dict:
    return {
        "id": it.id,
        "source": it.source,
        "status": it.status,
        "decided_by": it.decided_by,
        "reasoning": it.reasoning,
        "package_id": it.package_id,
        "package_revision": it.package_revision,
        "package_source_repository": it.package_source_repository,
        "originating_observation_id": it.originating_observation_id,
        "unrelated_key_other_callers_read": it.note,
    }


@router.get("/items")
def list_items(db: Session = Depends(get_db)) -> list[dict]:
    return [_item_dict(it) for it in db.scalars(select(ChangeItem)).all()]
"""


CONTRACT_LINE = "    originating_observation_id: str | None = None\n"


def _schemas_missing_from(class_name: str) -> str:
    """The fixture with the contract field removed from ONE named class.

    Targeted rather than `SCHEMAS.replace(..., 1)`, which removes it from whichever class the
    fixture happens to declare first and therefore tests whichever lane happens to come first.
    That is not a hypothetical: the first draft of this module did exactly that, stripped
    `DeployChangeIn` -- the lane that deliberately does not send the field -- and the check
    correctly reported no divergence, so the test asserting the refusal failed for a reason that
    had nothing to do with the code under test.
    """
    declaration = f"class {class_name}(BaseModel):"
    head, found, tail = SCHEMAS.partition(declaration)
    assert found, f"the fixture declares no {class_name}"
    return head + found + tail.replace(CONTRACT_LINE, "", 1)


def _fetch(schemas: str = SCHEMAS, api: str = API):
    def fake(repo: str, path: str, ref: str) -> str:
        if path == check.SCHEMAS_PATH:
            return schemas
        if path == check.API_PATH:
            return api
        raise AssertionError(f"unexpected fetch: {path}")

    return fake


def test_the_field_parser_agrees_with_pydantic() -> None:
    """The fidelity pin: source-reading must mean the same thing as introspection.

    change-manager's models are read from source because they live in another repository and
    installing it would drag its dependency tree into this repository's pull-request gate. That
    trade is only sound while the parse is exact, so it is checked here against a real pydantic
    model on this side -- one that inherits `BaseModel` directly, since the parse reads a class
    body and an inherited field would be invisible to it.
    """
    source = Path("src/orchestrator/api/schemas.py").read_text(encoding="utf-8")

    assert check.declared_fields(source, "RunnerBriefResponse", "schemas.py") == set(
        RunnerBriefResponse.model_fields
    )


def test_the_field_parser_reads_a_pydantic_model_the_way_pydantic_would() -> None:
    """`model_config` is not a field, and the `model_` prefix is reserved, not declarable."""
    source = (
        "class WorkChangeIn(BaseModel):\n"
        '    model_config = ConfigDict(extra="forbid")\n'
        "    package_id: str\n"
        "    note: str | None = None\n"
    )

    assert check.declared_fields(source, "WorkChangeIn") == {"package_id", "note"}


def test_a_far_side_class_with_a_shared_base_is_loud_rather_than_under_reported() -> None:
    """The premise, asserted instead of assumed, and the failure it prevents is a FALSE finding.

    The parse reads a class BODY. Were change-manager to move a field onto a shared base, that
    field would be invisible here and the check would report a divergence that does not exist --
    telling a producer to stop sending something change-manager accepts perfectly well.
    """
    with pytest.raises(check.Unresolvable, match="inherits"):
        check.declared_fields(
            "class WorkChangeIn(ProposalBase):\n    package_id: str\n", "WorkChangeIn"
        )


def test_a_renamed_or_moved_far_side_class_is_loud() -> None:
    """Never a silent pass: a parser that found nothing must not read as "nothing declared"."""
    with pytest.raises(check.Unresolvable, match="no class"):
        check.declared_fields("class SomethingElse(BaseModel):\n    x: int\n", "WorkChangeIn")


def test_the_serializer_parser_reads_exactly_the_keys_the_function_returns() -> None:
    """The second fidelity pin, and it cannot borrow the first one's technique.

    The reading side is compared against a dict literal in a plain function, not a model, so
    there is no `model_fields` to check the parse against. What IS available is the function
    itself: build it, call it, and compare the executed keys to the parsed ones. That
    discriminates a parser that drops a key and one that picks up a constant that is not a key.
    """
    source = (
        "def _item_dict(it):\n"
        "    return {\n"
        '        "id": it.id,\n'
        '        "source": it.source,\n'
        '        "originating_observation_id": it.originating_observation_id,\n'
        "    }\n"
    )
    namespace: dict[str, object] = {}
    exec(compile(source, "<fixture>", "exec"), namespace)  # noqa: S102 - the fixture IS the pin
    executed = set(namespace["_item_dict"](_Row()))  # type: ignore[operator]

    assert check.served_keys(source) == executed


class _Row:
    """Whatever the fixture serializer reads off a row. Values are irrelevant; keys are the pin."""

    id = 1
    source = "work"
    originating_observation_id = None


@pytest.mark.parametrize(
    "source",
    [
        "def other():\n    return {}\n",
        "def _item_dict(it):\n    return [1, 2]\n",
        "def _item_dict(it):\n    if it:\n        return {}\n    return {}\n",
        'def _item_dict(it):\n    return {**base(it), "id": it.id}\n',
        "def _item_dict(it):\n    return {KEY: it.id}\n",
        "def _item_dict(it):\n    return {1: it.id}\n",
    ],
    ids=[
        "absent",
        "not-a-dict",
        "two-returns",
        "spread",
        "computed-key",
        "non-string-key",
    ],
)
def test_the_serializer_parser_is_loud_rather_than_guessing(source: str) -> None:
    """A partial key set would be a confident under-count, reported as a real divergence."""
    with pytest.raises(check.Unresolvable):
        check.served_keys(source)


def test_a_listing_that_stopped_serialising_through_the_reader_is_loud() -> None:
    """The other premise. The carry reads what the LISTING returns, so a serializer swap would
    leave this check vetting a function nothing serves -- a wrong answer that still reads green.
    """
    moved = (
        "def _item_dict(it):\n    return {}\n\n\n"
        "@router.get('/items')\n"
        "def list_items(db=None):\n    return [_row_projection(it) for it in db]\n"
    )

    with pytest.raises(check.Unresolvable, match="no longer serialises"):
        check.assert_the_listing_serves_through(moved)


def test_an_absent_listing_route_is_loud() -> None:
    with pytest.raises(check.Unresolvable, match="no list_items"):
        check.assert_the_listing_serves_through("def _item_dict(it):\n    return {}\n")


def test_the_listing_premise_holds_for_a_route_that_does_serialise_through_it() -> None:
    """The positive control: without it the assertion above passes on a checker that always
    raises."""
    check.assert_the_listing_serves_through(API)


def test_none_of_the_three_local_declarations_is_empty() -> None:
    """A subset comparison against an empty local set passes on everything.

    The three declarations are pinned to their own payloads by their own suites; this is the
    narrower property THIS check depends on, and it is the one that would make every comparison
    here vacuous while leaving the gate green.
    """
    for name, declared in (
        ("bump_proposer.cli.PROPOSAL_FIELDS", bump_proposer_cli.PROPOSAL_FIELDS),
        ("change_proposer.cli.PROPOSAL_FIELDS", change_proposer_cli.PROPOSAL_FIELDS),
        ("work_carrier.change_manager.RECORD_FIELDS", work_carrier_change_manager.RECORD_FIELDS),
    ):
        assert declared, f"{name} is empty, so its comparison would pass on anything"


def test_the_comparison_passes_when_every_local_name_is_known(monkeypatch, capsys) -> None:
    """The positive control, and it is not vacuous: the far side here declares `note` and serves
    a key no local declaration mentions, so this also pins the SUBSET direction."""
    monkeypatch.setattr(check, "fetch", _fetch())

    assert check.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_a_producer_sending_an_undeclared_field_is_refused(monkeypatch, capsys) -> None:
    """The 422 shape, reproduced: this is what shipping ahead of change-manager looks like."""
    monkeypatch.setattr(check, "fetch", _fetch(schemas=_schemas_missing_from("WorkChangeIn")))

    assert check.main() == 1
    captured = capsys.readouterr()
    assert "originating_observation_id" in captured.err
    assert "WorkChangeIn" in captured.err


def test_the_carry_reading_an_unserved_key_is_refused(monkeypatch, capsys) -> None:
    """The other direction, which a check written against the proposal schemas alone cannot see:
    the carry reads a RESPONSE row, and four of the keys it needs exist only there."""
    without = API.replace(
        '        "originating_observation_id": it.originating_observation_id,\n', "", 1
    )
    monkeypatch.setattr(check, "fetch", _fetch(api=without))

    assert check.main() == 1
    assert "RECORD_FIELDS" in capsys.readouterr().err


def test_every_comparison_is_reported_even_when_an_earlier_one_fails(monkeypatch, capsys) -> None:
    """A first divergence that hid a second would make the second discoverable only after the
    first was fixed -- two round trips for one reading."""
    without = API.replace(
        '        "originating_observation_id": it.originating_observation_id,\n', "", 1
    )
    monkeypatch.setattr(
        check,
        "fetch",
        _fetch(schemas=_schemas_missing_from("WorkChangeIn"), api=without),
    )

    assert check.main() == 1
    captured = capsys.readouterr()
    assert "2 of 3 comparisons" in captured.err
    for local in ("bump_proposer.cli.PROPOSAL_FIELDS", "work_carrier.change_manager.RECORD_FIELDS"):
        assert local in captured.err
    # Every comparison is reported on stdout whatever the verdict, including the one that fits.
    assert captured.out.count("::") == 3


def test_the_report_names_which_producers_declare_a_cause(monkeypatch, capsys) -> None:
    """Derived, never asserted, and deliberately NOT a verdict.

    The contract's own design records that the second lane does not conform -- it posts no
    observation, so it has no cause to name -- and says the check will see it from the day it
    ships. Seeing it is this line; failing on it would red every pull request over a
    non-conformance that was decided elsewhere.
    """
    monkeypatch.setattr(check, "fetch", _fetch())

    assert check.main() == 0
    out = capsys.readouterr().out
    assert "not named by ['change_proposer']" in out
    assert "named by ['bump_proposer']" in out


def test_an_unreadable_far_side_is_unresolvable_rather_than_agreement(monkeypatch) -> None:
    """A network that cannot answer is not two vocabularies agreeing."""

    def boom(repo: str, path: str, ref: str) -> str:
        raise check.Unresolvable("simulated")

    monkeypatch.setattr(check, "fetch", boom)

    assert check.main() == 1


def test_the_gate_runs_the_script_this_module_tests() -> None:
    """A check nothing invokes is the defect it guards against, wearing a different hat."""
    invocations = [
        path
        for path in Path(".github/workflows").glob("*.yml")
        if SCRIPT.name in path.read_text(encoding="utf-8")
    ]

    assert [path.name for path in invocations] == ["quality.yml"]


class _Response:
    """A stand-in response: `read` either fails part-way or returns bytes for `.decode()`.

    The second mode is load-bearing. Production raises `UnicodeDecodeError` from `.decode()`, not
    from `read()`, so a case that injects it at `read()` stays green if `.decode()` is ever hoisted
    out of the `try` -- a plausible readability refactor that reopens the escape. Returning real
    non-UTF-8 bytes makes the control fail when it should.
    """

    def __init__(self, error: Exception | bytes) -> None:
        self._error = error

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self._error, bytes):
            return self._error
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"half"),
        TimeoutError("the read timed out"),
        b"\xff\xfe not utf-8",
    ],
    ids=["incomplete-read", "read-timeout", "not-utf-8"],
)
def test_a_failure_during_the_body_read_is_unresolvable(
    error: Exception | bytes, monkeypatch
) -> None:
    """None of these is wrapped by urllib, and `IncompleteRead` is not even an `OSError` -- so a
    clause naming only `HTTPError`/`URLError` lets all three escape as a bare traceback out of a
    step that is part of a required status check."""
    monkeypatch.setattr(check.urllib.request, "urlopen", lambda *a, **k: _Response(error))

    with pytest.raises(check.Unresolvable):
        check.fetch(check.CHANGE_MANAGER_REPO, check.SCHEMAS_PATH, check.REF)


def test_the_ref_this_check_sends_is_a_literal_and_needs_no_encoding() -> None:
    """Named rather than fixed. `#` and `&` are legal in a branch name and truncate a query
    string, so an unquoted ref CAN compare the wrong branch's bytes -- but this check's ref and
    both its paths are module constants, so nothing caller-supplied reaches the URL.
    """
    assert check.REF == "main"
    assert "{" not in check.SCHEMAS_PATH
    assert "{" not in check.API_PATH
