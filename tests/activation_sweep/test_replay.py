"""Acceptance 3, against the REAL ingestion service and the REAL CHECK constraints.

Everything else in this suite reasons about the record's bytes. This asks the orchestrator, on a
migrated database, whether those bytes actually replay -- which is the only place the conflict
branch, the idempotency branch and the widened vocabulary are exercised together. A test that
proved replay against a fake writer would prove that the fake replays.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from activation_sweep.checkout import read_checkout
from activation_sweep.record import activation_observation
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Observation
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation
from tests.activation_sweep.conftest import Estate

# The standing identity for every observe-and-report producer (ADR-0017, WS-P3.6 Increment 1).
OBSERVER = ActorContext("drift-reconciler", ActorRole.OBSERVER)


def _command(body: dict[str, Any]) -> ObservationCommand:
    fields = dict(body)
    fields["observed_at"] = datetime.fromisoformat(fields["observed_at"])
    return ObservationCommand(actor=OBSERVER, **fields)


def _record(session: Session, estate: Estate) -> Observation:
    outcome = record_observation(
        session, _command(activation_observation(read_checkout(estate.local)))
    )
    # `record_observation` RETURNS its DomainErrors rather than raising them, so an unnarrowed
    # test reaches for `.id` and fails with an AttributeError naming an attribute instead of
    # naming the conflict.
    assert not isinstance(outcome, DomainError), (outcome.code, outcome.message)
    return outcome


def _rows(session: Session) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(Observation)
        .where(Observation.source_system == "machine_activation")
    )
    assert count is not None
    return count


def test_the_widened_vocabulary_is_accepted_by_the_migrated_database(
    migrated_session: Session, estate: Estate
) -> None:
    """The CHECK lives in the database the sweep posts to, so the model edit alone proves nothing.
    This is the local half of the release's five-point verification."""
    row = _record(migrated_session, estate)

    assert row.source_system == "machine_activation"
    assert row.observation_type == "activation"
    assert row.subject_type == "repo"
    assert row.subject_reference == "AlobarQuest/example"
    assert row.status == "passed"


def test_a_second_sweep_over_unchanged_reality_replays(
    migrated_session: Session, estate: Estate
) -> None:
    first = _record(migrated_session, estate)
    second = _record(migrated_session, estate)

    assert second.id == first.id
    assert _rows(migrated_session) == 1


def test_a_tree_that_goes_dirty_and_clean_again_appends_then_replays(
    migrated_session: Session, estate: Estate
) -> None:
    """SECTION 5.2's whole point, proven where it would actually have failed.

    HEAD never moves across these three passes. Under a reference keyed on
    `(repository, head_sha)` the second would be one key carrying different facts -- an
    `observation_conflict` with no supersession model and no delete route -- and the producer
    would be wedged on every sweep from then on.
    """
    clean = _record(migrated_session, estate)
    estate.modify_tracked()
    dirty = _record(migrated_session, estate)
    estate.restore_tracked()
    again = _record(migrated_session, estate)

    assert dirty.id != clean.id
    assert again.id == clean.id
    assert _rows(migrated_session) == 2
    assert clean.facts["head"]["commit"] == dirty.facts["head"]["commit"]
    assert dirty.status == "degraded"
    assert dirty.severity == "warning"


def test_a_moved_head_appends_rather_than_conflicting(
    migrated_session: Session, estate: Estate
) -> None:
    before = _record(migrated_session, estate)
    estate.land_upstream("bump ruff from 0.16.2 to 0.16.3 (#76)")
    behind = _record(migrated_session, estate)

    assert behind.id != before.id
    assert behind.facts["conditions"] == ["behind"]
    assert _rows(migrated_session) == 2


def test_the_facts_survive_the_orchestrators_own_bounds_and_secret_detector(
    migrated_session: Session, estate: Estate
) -> None:
    """A refused write is an exit 3 somebody diagnoses at 07:10. The bounds are checked here,
    by the code that enforces them, rather than restated."""
    for index in range(15):
        estate.land_upstream(f"a long dependency bump subject number {index} " * 12)
    estate.modify_tracked()

    row = _record(migrated_session, estate)

    assert row.facts["conditions"] == ["behind", "dirty"]
    assert len(row.facts["missing"]) == 10
    assert row.facts["measured"]["behind_by"] == 15
