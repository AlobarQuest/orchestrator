"""The durable row, and the two properties that decide whether this producer can run twice.

A producer whose record moves over unchanged reality wedges permanently: the orchestrator refuses
a second observation at the same reference with different facts, there is no supersession model
and no delete route, so every subsequent pass fails. ADR-0022's first draft reached that state by
copying the landing ledger's reference shape onto a subject that re-runs. These pin the answer.
"""

from __future__ import annotations

import pytest

from revision_watcher.census import BEHIND, CURRENT, DIVERGED, UNSTAMPED, Reading
from revision_watcher.record import (
    record_digest,
    reference_for,
    revision_observation,
    summary_of,
)
from revision_watcher.subjects import Subject
from tests.revision_watcher.conftest import OLD, TIP, WHEN_OLD, WHEN_TIP

SUBJECT = Subject("app-brain", "https://app-brain.example/api/health", "AlobarQuest/brain")


def _reading(
    state: str = CURRENT, served: str | None = TIP, when: str | None = WHEN_TIP
) -> Reading:
    return Reading(SUBJECT, state, served=served, expected=TIP, observed_at=when)


def test_two_passes_over_unchanged_reality_compose_an_IDENTICAL_row() -> None:
    """The replay property. Anything that moves here -- a wall clock most of all -- turns the
    second pass into a permanent `observation_conflict`."""
    assert revision_observation(_reading()) == revision_observation(_reading())


def test_a_moved_revision_composes_a_DIFFERENT_reference_rather_than_conflicting() -> None:
    """What an application serves is expected to move, several times a day. Keying on the
    application alone would make the pass after any deploy a conflict, permanently."""
    current = revision_observation(_reading())
    moved = revision_observation(_reading(state=BEHIND, served=OLD, when=WHEN_OLD))

    assert current["source_reference"] != moved["source_reference"]


def test_the_digest_covers_the_WHOLE_record_rather_than_the_facts_alone() -> None:
    """The reference is also the idempotency key, so the server's first lookup is by that key and
    on a hit it compares the entire stored command. Digesting `facts` alone would make rewording
    one clause of `summary_of` an `idempotency_conflict` for every application that had not moved
    -- which for a healthy estate is all of them."""
    record = {"facts": {"a": 1}, "summary": "one", "status": "passed"}
    reworded = {**record, "summary": "two"}

    assert record_digest(record) != record_digest(reworded)


def test_the_reference_and_the_idempotency_key_are_ONE_string() -> None:
    """Spelling two strings for one concept would be a second copy of it."""
    row = revision_observation(_reading())

    assert row["idempotency_key"] == row["source_reference"]
    assert row["source_reference"] == reference_for(
        _reading(),
        {k: v for k, v in row.items() if k not in {"idempotency_key", "source_reference"}},
    )


def test_a_reading_with_no_fact_derived_clock_is_REFUSED_rather_than_given_the_wall() -> None:
    with pytest.raises(ValueError, match="wedge the producer"):
        revision_observation(_reading(when=None))


@pytest.mark.parametrize(
    ("state", "status", "severity"),
    [
        (CURRENT, "passed", "info"),
        (BEHIND, "degraded", "warning"),
        (DIVERGED, "degraded", "warning"),
        # An application that never claimed to be askable has not failed a check; it declined to
        # take one. Recording it `degraded` would put six applications permanently in that state.
        (UNSTAMPED, "passed", "info"),
    ],
)
def test_the_state_decides_the_status_and_severity(state, status, severity) -> None:
    row = revision_observation(_reading(state=state, served=None if state == UNSTAMPED else TIP))

    assert (row["status"], row["severity"]) == (status, severity)


def test_an_unstamped_row_carries_serving_as_NULL_rather_than_omitting_it() -> None:
    """A consumer must tell "this application does not say" from "this application is current",
    and an absent key says the first to a reader who is looking and the second to one calling
    `.get()`."""
    row = revision_observation(_reading(state=UNSTAMPED, served=None))

    assert "serving" in row["facts"]
    assert row["facts"]["serving"] is None


def test_nothing_in_the_row_asserts_a_CAUSE() -> None:
    """Cause-independence is the property that makes this lane worth having: a failed deployment,
    an image that never built, a rotated secret and a manual swap nobody performed all end in the
    same measurable state, and this lane cannot tell them apart. A record that guessed would trade
    that away for a claim it never checked."""
    row = revision_observation(_reading(state=BEHIND, served=OLD, when=WHEN_OLD))
    prose = (row["summary"] + " " + str(row["facts"])).lower()

    for cause in ("failed", "rollback", "rolled back", "coolify", "secret", "broken"):
        assert cause not in prose


def test_the_summary_names_both_commits_because_a_reader_needs_the_difference() -> None:
    line = summary_of(_reading(state=BEHIND, served=OLD, when=WHEN_OLD))

    assert OLD[:12] in line
    assert TIP[:12] in line


# ---------------------------------------------------------------------------------------------
# THE VOCABULARY THIS PAYLOAD MUST SATISFY LIVES IN ANOTHER PROGRAM, and until 2026-09-08 nothing
# checked it. The lane shipped, measured six applications correctly, and filed NOTHING: every
# field below is a closed vocabulary backed by a CHECK constraint, `source_system` and
# `observation_type` had no member for this producer, and the server refused each row with
# `observation_invalid: source_system is unsupported`.
#
# It survived a 5197-test gate because every test that composes this payload also mocks the client
# that would have refused it -- the estate's documented cross-program boundary, where the request
# model is a second rule set on top of the service's and no unit test sees it.
#
# The orchestrator's tuples are IMPORTABLE here, so this needs no HTTP client and cannot drift.
# ---------------------------------------------------------------------------------------------


def test_every_vocabulary_field_this_payload_sets_is_one_the_orchestrator_accepts() -> None:
    from orchestrator.persistence.models import (
        OBSERVATION_SEVERITIES,
        OBSERVATION_SOURCE_SYSTEMS,
        OBSERVATION_STATUSES,
        OBSERVATION_SUBJECT_TYPES,
        OBSERVATION_TRUST_CLASSIFICATIONS,
        OBSERVATION_TYPES,
    )

    for state in (CURRENT, BEHIND, DIVERGED, UNSTAMPED):
        row = revision_observation(
            _reading(state=state, served=None if state == UNSTAMPED else TIP)
        )
        assert row["source_system"] in OBSERVATION_SOURCE_SYSTEMS
        assert row["observation_type"] in OBSERVATION_TYPES
        assert row["subject_type"] in OBSERVATION_SUBJECT_TYPES
        assert row["trust_classification"] in OBSERVATION_TRUST_CLASSIFICATIONS
        assert row["status"] in OBSERVATION_STATUSES
        assert row["severity"] in OBSERVATION_SEVERITIES


def test_the_payload_satisfies_the_ROUTES_OWN_REQUEST_MODEL() -> None:
    """The second rule set. FastAPI answers before any service code runs, so a field the request
    model rejects is an HTTP 422 that no named error and no service test can reach -- the shape
    that refused twelve candidate rows in the binding lane before this one."""
    from orchestrator.api.schemas import ObservationCommandModel

    ObservationCommandModel.model_validate(revision_observation(_reading()))
