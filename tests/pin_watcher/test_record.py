"""What a row asserts, and the two properties that stop the producer wedging itself."""

from __future__ import annotations

import json

import pytest

from orchestrator.services.observations import (
    MAX_FACT_BYTES,
    MAX_SUMMARY,
    SECRET_KEY_PARTS,
)
from pin_watcher.compare import Caller
from pin_watcher.record import pin_observation, reference_for, summary_of

RECOMMENDED = "a" * 40
RECOMMENDED_AT = "2026-09-01T10:00:00Z"


def _caller(**overrides: object) -> Caller:
    fields: dict[str, object] = {
        "repository": "AlobarQuest/brain",
        "pin": "b" * 40,
        "state": "behind",
        "behind_by": 23,
        "ahead_by": 0,
        "pinned_at": "2026-08-20T09:00:00Z",
    }
    fields.update(overrides)
    return Caller(**fields)  # type: ignore[arg-type]


def _observe(caller: Caller) -> dict:
    return pin_observation(caller, RECOMMENDED, RECOMMENDED_AT)


def test_observed_at_is_the_pinned_revisions_clock_and_never_the_passs() -> None:
    """A wall clock here makes the SECOND pass over unchanged reality an observation_conflict."""
    assert _observe(_caller())["observed_at"] == "2026-08-20T09:00:00Z"


def test_a_caller_with_no_resolvable_pin_falls_back_to_the_recommendations_clock() -> None:
    """Also fact-derived: it moves only when the recommendation moves, which is what is required."""
    unpinned = _caller(state="unpinned", pin="main", behind_by=None, ahead_by=None, pinned_at=None)
    assert _observe(unpinned)["observed_at"] == RECOMMENDED_AT


def test_no_wall_clock_is_reachable_when_neither_date_exists() -> None:
    """Raising beats inventing a clock: the invented one wedges the producer permanently."""
    with pytest.raises(ValueError, match="wedge"):
        pin_observation(_caller(pinned_at=None), RECOMMENDED, None)


def test_re_running_over_unchanged_reality_produces_the_identical_row() -> None:
    assert _observe(_caller()) == _observe(_caller())


def test_a_caller_that_advanced_produces_a_different_reference_so_the_row_appends() -> None:
    moved = _observe(_caller(pin="c" * 40, behind_by=4, pinned_at="2026-08-29T09:00:00Z"))
    assert moved["source_reference"] != _observe(_caller())["source_reference"]


def test_the_same_pin_with_a_changed_state_also_appends() -> None:
    """A pin that stopped being behind because the RECOMMENDATION moved is a new fact."""
    same_pin_now_current = _caller(state="current", behind_by=0)
    assert (
        _observe(same_pin_now_current)["source_reference"]
        != _observe(_caller())["source_reference"]
    )


def test_the_digest_covers_the_whole_record_rather_than_facts_alone(monkeypatch) -> None:
    """THE control for the defect the activation sweep paid to find.

    The reference is also the idempotency key, so the server's first lookup is by that key and on
    a hit it compares the ENTIRE stored command -- `summary` included. Digesting `facts` alone
    would make rewording one clause of `summary_of` an `idempotency_conflict` for every caller
    that had not moved, which for a healthy chain is all of them.
    """
    before = _observe(_caller())["source_reference"]
    monkeypatch.setattr("pin_watcher.record.summary_of", lambda caller, recommended: "reworded")
    assert _observe(_caller())["source_reference"] != before


def test_the_reference_and_the_idempotency_key_are_one_string() -> None:
    row = _observe(_caller())
    assert row["idempotency_key"] == row["source_reference"]
    assert row["source_reference"].startswith("caller-pin:AlobarQuest/brain@")


def test_a_current_caller_passes_and_any_other_state_is_degraded() -> None:
    assert _observe(_caller(state="current", behind_by=0))["status"] == "passed"
    for state in ("behind", "ahead", "diverged", "unpinned", "unresolvable"):
        assert _observe(_caller(state=state))["status"] == "degraded", state


def test_only_a_current_caller_is_described_as_current() -> None:
    """Structural rather than a clause that remembers to check."""
    for state in ("behind", "ahead", "diverged", "unpinned", "unresolvable"):
        sentence = summary_of(_caller(state=state), RECOMMENDED)
        assert "the revision the estate chose" not in sentence, state


def test_every_state_produces_a_summary_within_the_orchestrators_bound() -> None:
    for state in ("current", "behind", "ahead", "diverged", "unpinned", "unresolvable"):
        assert len(summary_of(_caller(state=state), RECOMMENDED)) <= MAX_SUMMARY, state


def test_no_fact_key_is_one_the_secret_detector_reads_as_metadata() -> None:
    """The detector matches nine substrings against key NAMES, whatever the value is."""

    def keys(node: object) -> list[str]:
        if isinstance(node, dict):
            return [k for key, value in node.items() for k in [key, *keys(value)]]
        return []

    for key in keys(_observe(_caller())["facts"]):
        assert not any(part in key.lower() for part in SECRET_KEY_PARTS), key


def test_the_record_fits_the_orchestrators_fact_bound_by_shape() -> None:
    """No variable-length member, so it fits without trimming -- asserted rather than assumed."""
    encoded = json.dumps(_observe(_caller())["facts"]).encode("utf-8")
    assert len(encoded) <= MAX_FACT_BYTES


def test_a_comparison_that_never_happened_reports_no_counts_rather_than_zeroes() -> None:
    """Absence is a statement; a zero would read as 'measured, and no distance'."""
    unpinned = _caller(state="unpinned", pin="main", behind_by=None, ahead_by=None, pinned_at=None)
    assert "measured" not in _observe(unpinned)["facts"]
    assert _observe(_caller())["facts"]["measured"] == {"behind_by": 23, "ahead_by": 0}


def test_the_reference_is_stable_across_two_independent_compositions() -> None:
    """Guards the canonical-json assumption: key order must not reach the digest."""
    row = _observe(_caller())
    body = {k: v for k, v in row.items() if k not in {"idempotency_key", "source_reference"}}
    assert reference_for(_caller(), dict(reversed(list(body.items())))) == row["source_reference"]


def test_the_lanes_vocabulary_is_one_the_orchestrator_actually_accepts() -> None:
    """THE test this lane was missing, and the live run is what found that out.

    `source_system` and `observation_type` are closed vocabularies pinned by DB CHECK
    constraints. The first live pass filed nothing: every row came back
    `409 observation_invalid: source_system is unsupported`, because the producer had chosen a
    name the orchestrator had never been taught. Every unit test passed throughout -- they compose
    a row and assert its shape, and a shape is not a vocabulary.

    This is the estate's recurring cross-boundary class, and the rule it keeps writing down is the
    one that applies: before building on a field that crosses a boundary, grep for it on both
    sides. Asserted here instead, so the next member is checked by CI rather than by a live pass.
    """
    from orchestrator.persistence.models import (
        OBSERVATION_SOURCE_SYSTEMS,
        OBSERVATION_SUBJECT_TYPES,
        OBSERVATION_TRUST_CLASSIFICATIONS,
        OBSERVATION_TYPES,
    )

    row = _observe(_caller())
    assert row["source_system"] in OBSERVATION_SOURCE_SYSTEMS
    assert row["observation_type"] in OBSERVATION_TYPES
    assert row["subject_type"] in OBSERVATION_SUBJECT_TYPES
    assert row["trust_classification"] in OBSERVATION_TRUST_CLASSIFICATIONS


def test_every_status_and_severity_the_lane_emits_is_one_the_orchestrator_accepts() -> None:
    """The same boundary, one field over -- and both are reachable from a real pass."""
    from orchestrator.persistence.models import (
        OBSERVATION_SEVERITIES,
        OBSERVATION_STATUSES,
    )

    for state in ("current", "behind", "ahead", "diverged", "unpinned", "unresolvable"):
        row = _observe(_caller(state=state))
        assert row["status"] in OBSERVATION_STATUSES, state
        assert row["severity"] in OBSERVATION_SEVERITIES, state
