import uuid
from datetime import UTC, datetime, timedelta

import pytest

from orchestrator.errors import DomainError
from orchestrator.services.follow_ups import (
    FOLLOW_UP_CAPABILITY,
    SKIP_ALREADY_MINTED,
    SKIP_NO_COMPLETED_UNIT,
    SKIP_NOT_REQUIRED,
    SKIP_NOT_YET_DUE,
    SKIP_UNITS_IN_FLIGHT,
    SKIP_UNSETTLED_FAILED_UNIT,
    RevisionFacts,
    UnitFacts,
    evaluate_due,
    validate_follow_up,
)

VALID = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def test_a_valid_declaration_round_trips() -> None:
    assert validate_follow_up(VALID) == VALID


def test_absent_declaration_is_none_not_an_error() -> None:
    assert validate_follow_up(None) is None


def test_the_fully_degenerate_declaration_is_valid() -> None:
    degenerate = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    assert validate_follow_up(degenerate) == degenerate


@pytest.mark.parametrize(
    "value",
    [
        {"required": True, "revisit_when": None, "signals": []},
        {"required": True, "revisit_when": None, "signals": [], "owner": None, "extra": 1},
        {"required": "yes", "revisit_when": None, "signals": [], "owner": None},
        {"required": True, "revisit_when": 7, "signals": [], "owner": None},
        {"required": True, "revisit_when": None, "signals": "not-a-list", "owner": None},
        {"required": True, "revisit_when": None, "signals": [None], "owner": None},
        "not-a-mapping",
    ],
    ids=[
        "missing-key",
        "unknown-key",
        "required-not-bool",
        "revisit-when-not-str",
        "signals-not-list",
        "signal-item-not-str",
        "not-a-mapping",
    ],
)
def test_a_malformed_declaration_is_a_named_domain_error(value: object) -> None:
    with pytest.raises(DomainError) as caught:
        validate_follow_up(value)

    assert caught.value.code == "follow_up_invalid"


NOW = datetime(2026, 9, 1, tzinfo=UTC)
SETTLED = NOW - timedelta(days=40)
REQUIRED = {"required": True, "revisit_when": "Later.", "signals": [], "owner": None}


def facts(*units: UnitFacts, follow_up=REQUIRED) -> RevisionFacts:
    return RevisionFacts(
        revision_id=uuid.uuid4(),
        follow_up=follow_up,
        units=units,
    )


def completed(settled_at=SETTLED) -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="completed", settled_at=settled_at)


def cancelled(settled_at=SETTLED) -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="cancelled", settled_at=settled_at)


def in_flight() -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="executing", settled_at=None)


def failed() -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="failed", settled_at=None)


def test_a_settled_revision_past_the_window_is_due() -> None:
    decision = evaluate_due(facts(completed()), now=NOW, due_after_days=30)

    assert decision.skip_reason is None
    assert decision.due_at == SETTLED + timedelta(days=30)


def test_the_anchor_is_the_latest_settling_not_the_earliest() -> None:
    late = NOW - timedelta(days=31)
    decision = evaluate_due(facts(completed(), completed(late)), now=NOW, due_after_days=30)

    assert decision.due_at == late + timedelta(days=30)


def test_a_declaration_that_does_not_require_follow_up_is_skipped() -> None:
    declaration = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    decision = evaluate_due(facts(completed(), follow_up=declaration), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_REQUIRED


def test_a_revision_with_no_declaration_is_skipped() -> None:
    decision = evaluate_due(facts(completed(), follow_up=None), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_REQUIRED


def test_a_revision_with_work_still_moving_is_skipped() -> None:
    decision = evaluate_due(facts(completed(), in_flight()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNITS_IN_FLIGHT


def test_a_lingering_failed_unit_blocks_with_its_own_reason() -> None:
    """FAILED is not terminal -- it can go back to READY or on to CANCELLED -- so a revision
    behind one has an undecided outcome. It must NOT read as units_in_flight: 'still working'
    and 'stopped, and nobody decided' are different operator actions."""
    decision = evaluate_due(facts(completed(), failed()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNSETTLED_FAILED_UNIT


def test_a_wholly_cancelled_revision_never_mints() -> None:
    """Nothing shipped, so there is no outcome to revisit."""
    decision = evaluate_due(facts(cancelled(), cancelled()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NO_COMPLETED_UNIT


def test_a_revision_with_no_units_at_all_never_mints() -> None:
    decision = evaluate_due(facts(), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NO_COMPLETED_UNIT


def test_a_lone_failed_unit_reports_unsettled_failed_not_no_completed_unit() -> None:
    """FAILED must win over no_completed_unit's own absence-of-completion check -- the clause
    order matters, and this pins it against the more generic reason swallowing the specific
    one."""
    decision = evaluate_due(facts(failed()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNSETTLED_FAILED_UNIT


def test_a_lone_in_flight_unit_reports_units_in_flight_not_no_completed_unit() -> None:
    decision = evaluate_due(facts(in_flight()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNITS_IN_FLIGHT


def test_a_revision_inside_the_window_is_not_yet_due() -> None:
    recent = NOW - timedelta(days=5)
    decision = evaluate_due(facts(completed(recent)), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_YET_DUE
    assert decision.due_at == recent + timedelta(days=30)


def test_zero_days_makes_a_settled_revision_immediately_due() -> None:
    decision = evaluate_due(facts(completed(NOW)), now=NOW, due_after_days=0)

    assert decision.skip_reason is None


def test_an_existing_review_unit_stops_the_revision_from_minting_again() -> None:
    own = UnitFacts(
        required_capability=FOLLOW_UP_CAPABILITY, state="awaiting_review", settled_at=None
    )

    decision = evaluate_due(facts(completed(), own), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_ALREADY_MINTED


def test_a_completed_review_unit_still_stops_a_second_mint() -> None:
    """One review per revision, forever. Completing the review must not make the revision
    eligible again -- which is exactly what a predicate that merely filtered the unit out of
    the settled-set would do."""
    own = UnitFacts(required_capability=FOLLOW_UP_CAPABILITY, state="completed", settled_at=SETTLED)

    decision = evaluate_due(facts(completed(), own), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_ALREADY_MINTED
