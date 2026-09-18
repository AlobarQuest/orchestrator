"""The deploy producer's declared field set, pinned to what it actually emits.

**THIS LANE IS DELIBERATELY NOT MADE TO CONFORM to the signal->work contract**, and the
declaration exists anyway. The cross-repo field check vets BOTH of change-manager's proposal
schemas, so a declaration for the work lane alone would leave half of it with nothing on this side
to hold the other to -- and the alternative, retyping the names into the check script, would be a
second copy of this vocabulary, which is the defect this repository has now re-learned in three
others.

So `originating_observation_id` is absent here, on purpose: this producer posts no observation and
carries no cause. The contract's own document names that non-conformance rather than leaving it to
be discovered, and the check will see it from the day it ships.
"""

from __future__ import annotations

from dataclasses import dataclass

from change_proposer.cli import PROPOSAL_FIELDS, _proposal


@dataclass(frozen=True)
class _Rollback:
    steps: tuple[str, ...]
    target: str


def _emitted(*, work_unit_id: str | None = None) -> dict[str, object]:
    return _proposal(
        "AlobarQuest/change-manager",
        {"number": 51},
        ("the rollout concluded green",),
        _Rollback(steps=("redeploy the previous image",), target="change-mgr.alobar.net"),
        work_unit_id=work_unit_id,
    )


def test_the_declared_field_set_is_EXACTLY_what_the_proposal_emits() -> None:
    assert set(_emitted()) == set(PROPOSAL_FIELDS)
    assert len(PROPOSAL_FIELDS) == len(set(PROPOSAL_FIELDS))


def test_both_shapes_emit_the_same_fields() -> None:
    """A factory pull request and an update bot's differ in `change_class` and in nothing else,
    so one declaration covers both. A shape that carried an extra key would make the check's
    answer depend on which of the two it happened to be shown."""
    assert set(_emitted()) == set(_emitted(work_unit_id="7a81c2c2-0835-5bba-a308-36e868719b62"))


def test_this_lane_declares_NO_originating_observation() -> None:
    """Named rather than left as an absence, so a reader does not take it for an oversight and
    quietly 'fix' it here -- which would be this producer asserting a cause it never observed."""
    assert "originating_observation_id" not in PROPOSAL_FIELDS
