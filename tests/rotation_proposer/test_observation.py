"""The fact filed before anything is acted on: its identity, its stability and its vocabulary."""

from __future__ import annotations

from dataclasses import replace

import pytest

from orchestrator.kernel.secret_metadata import secret_metadata_path
from orchestrator.persistence.models import (
    OBSERVATION_SEVERITIES,
    OBSERVATION_SOURCE_SYSTEMS,
    OBSERVATION_STATUSES,
    OBSERVATION_SUBJECT_TYPES,
    OBSERVATION_TRUST_CLASSIFICATIONS,
    OBSERVATION_TYPES,
)
from rotation_proposer.findings import Due
from rotation_proposer.observation import ObservationUncomposable, rotation_observation

DUE = Due("openrouter-generic", "openrouter-key", "requested", "2026-10-07", "2026-10-07")


def test_every_vocabulary_member_is_one_the_orchestrator_declares() -> None:
    """The static half; `tests/api/test_rotation_proposer_observation_ingest.py` is the half where
    the route, the CHECK constraints and the OBSERVER credential each have their say."""
    row = rotation_observation(DUE)
    assert row["source_system"] in OBSERVATION_SOURCE_SYSTEMS
    assert row["observation_type"] in OBSERVATION_TYPES
    assert row["subject_type"] in OBSERVATION_SUBJECT_TYPES
    assert row["trust_classification"] in OBSERVATION_TRUST_CLASSIFICATIONS
    assert row["status"] in OBSERVATION_STATUSES
    assert row["severity"] in OBSERVATION_SEVERITIES


def test_the_row_passes_the_secret_detector() -> None:
    """Kills: a fact key naming a credential. The detector refuses any key containing
    `credential`, so the registry id travels as `registry_entry`."""
    assert secret_metadata_path(rotation_observation(DUE)) is None


def test_the_same_rotation_is_the_same_row_on_every_pass() -> None:
    """Kills: anything that moves between passes -- a clock, the age in days -- in the command,
    which the orchestrator hashes whole. `observed_at` is the trigger's date, not the pass's."""
    first = rotation_observation(DUE)
    assert first == rotation_observation(replace(DUE))
    assert first["observed_at"] == "2026-10-07T00:00:00+00:00"
    assert (
        first["source_reference"] == "credential-rotation:openrouter-generic:requested-2026-10-07"
    )


def test_a_class_change_in_the_registry_does_not_move_the_row() -> None:
    """The class is a registry edit away from changing under a fixed reference, which would be
    `observation_conflict` forever; it is deliberately not a fact."""
    assert rotation_observation(DUE) == rotation_observation(replace(DUE, credential_class="x"))


def test_the_next_rotation_of_the_same_credential_is_a_different_row() -> None:
    later = replace(DUE, trigger="age", basis="2026-10-08", dated="2026-10-08")
    assert (
        rotation_observation(later)["source_reference"]
        != (rotation_observation(DUE)["source_reference"])
    )
    assert (
        rotation_observation(later)["idempotency_key"]
        != (rotation_observation(DUE)["idempotency_key"])
    )


def test_a_key_over_the_routes_limit_is_a_named_refusal() -> None:
    long = replace(DUE, credential_id="k" * 64, trigger="exposure", basis="e" * 64)
    with pytest.raises(ObservationUncomposable, match="over the 200"):
        rotation_observation(long)
