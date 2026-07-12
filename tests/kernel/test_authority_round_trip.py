"""`normalized()` must be a FIXED POINT of `normalize_authority()`.

This test exists because of a failed acceptance criterion, and the way it failed is the whole
lesson.

WS-P2.15 found that every post-deploy unit was minted with a spurious unknown field, and "fixed"
it by removing an `"unknown_fields": []` key from the dict literal at the minting site. That is a
real fix, and it is not the bug. `normalized()` -- which is what actually gets STORED as the
envelope -- emits an `unknown_fields` key of its own. With that key absent from KNOWN_FIELDS,
re-normalizing any stored envelope reported `unknown_fields` as itself an unknown field. So:

  * `normalized()` was never a fixed point, for ANY unit -- not just post-deploy ones.
  * `dispatch.py` and the runner brief both re-normalize the stored column, and the fingerprint
    they re-derived DISAGREED with the one that was minted.
  * A future fail-closed unknown-fields gate would have rejected every unit in the system.

The landmine was moved one layer down, not removed.

AC-012 named the test that would have caught it -- "round-trips through normalisation unchanged"
-- and that test was never written. The fix was made and the outcome asserted. An independent
verifier caught it precisely because the falsifying test was missing.

A guard nobody calls is not protection. **A claim whose falsifying test is skipped is not
evidence.** That happened here, in the one package whose entire thesis is that it must not.

So this file asserts the property, not the fix -- for every envelope shape, not just the one that
was broken.
"""

from typing import Any

import pytest

from orchestrator.kernel.authority import (
    KNOWN_FIELDS,
    authority_fingerprint,
    normalize_authority,
)

ENVELOPES: list[dict[str, Any]] = [
    # the post-deploy verification unit -- the shape that surfaced this
    {"capabilities": {"post_deploy_verification": "allowed"}, "budgets": {"max_attempts": 1}},
    # the runner envelope (the cross-repo contract shape)
    {
        "capabilities": {"repo.read": "allowed", "repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {"target_repository": "AlobarQuest/orchestrator"},
        "change_class": "dependency-update",
        "conformance": {"status": "green"},
    },
    # the empty envelope
    {"capabilities": {}, "budgets": {}},
    # one carrying a genuinely unknown field -- it must SURVIVE the round trip, not be laundered
    {"capabilities": {}, "budgets": {}, "future_thing": 1},
]


@pytest.mark.parametrize("raw", ENVELOPES, ids=lambda e: ",".join(sorted(e)))
def test_normalized_is_a_fixed_point_of_normalize_authority(raw: dict[str, Any]) -> None:
    """Storing an envelope and reading it back must not change it.

    `normalized()` is what is persisted; `normalize_authority()` is what reads it back (dispatch
    and the runner brief both do). If the two disagree, the envelope drifts every time it is
    round-tripped, and the fingerprint re-derived from storage is not the fingerprint a human
    approved.
    """
    minted = normalize_authority(raw)
    stored = minted.normalized()
    reread = normalize_authority(stored)

    assert reread.normalized() == stored
    assert authority_fingerprint(reread) == authority_fingerprint(minted)


@pytest.mark.parametrize("raw", ENVELOPES, ids=lambda e: ",".join(sorted(e)))
def test_a_round_trip_invents_no_unknown_field(raw: dict[str, Any]) -> None:
    """The specific bug: re-reading a stored envelope must not manufacture an unknown field.

    Before the fix, this produced `{"unknown_fields"}` -- the key normalized() emits, reported as
    unknown by the function that reads it back. Self-referential, and true of every unit.
    """
    minted = normalize_authority(raw)
    reread = normalize_authority(minted.normalized())

    assert reread.unknown_fields == minted.unknown_fields


def test_a_genuinely_unknown_field_is_still_detected() -> None:
    """The fix must not blind the detector it repairs."""
    parsed = normalize_authority({"capabilities": {}, "budgets": {}, "future_thing": 1})

    assert parsed.unknown_fields == frozenset({"future_thing"})


def test_every_key_normalized_emits_is_a_known_field() -> None:
    """The general form of the bug, so no future field can reintroduce it.

    Any key `normalized()` emits but `KNOWN_FIELDS` does not contain will be reported as an
    unknown field the moment the stored envelope is read back -- for every unit in the system.
    """
    emitted = set(normalize_authority({"capabilities": {}, "budgets": {}}).normalized())

    assert emitted <= set(KNOWN_FIELDS), (
        f"normalized() emits {sorted(emitted - set(KNOWN_FIELDS))}, which KNOWN_FIELDS does not "
        "contain. Every stored envelope will grow that as an unknown field on re-read, and the "
        "fingerprint derived from storage will not match the one that was approved."
    )
