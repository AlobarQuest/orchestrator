"""The observation vocabularies live in TWO places, and only one of them is authoritative.

`persistence/models.py` declares the tuples the service validates against; each Alembic migration
inlines a FROZEN copy into a `CHECK` constraint, because a migration must not import a constant
that will keep moving. So the two can drift, and the drift is invisible until a producer posts a
member the service admits and the database refuses -- an `IntegrityError`, which reaches the wire
as an unhandled HTTP 500 rather than as a named `observation_invalid`.

These tests read the constraint back out of the migrated database and compare it to the tuple, so
any future member added to one place and not the other fails here instead of in production.
"""

import re

from sqlalchemy import Engine, text

from orchestrator.persistence.models import (
    OBSERVATION_SEVERITIES,
    OBSERVATION_SOURCE_SYSTEMS,
    OBSERVATION_STATUSES,
    OBSERVATION_SUBJECT_TYPES,
    OBSERVATION_TRUST_CLASSIFICATIONS,
    OBSERVATION_TYPES,
)

CHECKED_VOCABULARIES = {
    "ck_observations_source_system": OBSERVATION_SOURCE_SYSTEMS,
    "ck_observations_trust_classification": OBSERVATION_TRUST_CLASSIFICATIONS,
    "ck_observations_subject_type": OBSERVATION_SUBJECT_TYPES,
    "ck_observations_type": OBSERVATION_TYPES,
    "ck_observations_status": OBSERVATION_STATUSES,
    "ck_observations_severity": OBSERVATION_SEVERITIES,
}


def admitted_members(engine: Engine, constraint: str) -> set[str]:
    with engine.connect() as connection:
        definition = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name AND conrelid = 'observations'::regclass"
            ),
            {"name": constraint},
        )
    assert definition is not None, f"{constraint} is not on the observations table"
    return set(re.findall(r"'([^']+)'", definition))


def test_every_checked_observation_vocabulary_matches_the_database(migrated_engine: Engine) -> None:
    drift = {
        name: (set(members) ^ admitted_members(migrated_engine, name))
        for name, members in CHECKED_VOCABULARIES.items()
        if set(members) != admitted_members(migrated_engine, name)
    }
    assert drift == {}, f"model tuples and CHECK constraints disagree: {drift}"


def test_the_recovery_floor_members_reached_the_database(migrated_engine: Engine) -> None:
    """ADR-0021's members specifically -- so a migration that was written but never applied, or a
    downgrade that silently narrowed the constraint, is named rather than merely counted."""
    sources = admitted_members(migrated_engine, "ck_observations_source_system")
    types = admitted_members(migrated_engine, "ck_observations_type")
    assert "recovery_floor" in sources
    assert {"backup", "chain_integrity"} <= types
    # No new subject type was needed: `external_run` already described a scheduled run that
    # happens outside the orchestrator.
    assert "external_run" in admitted_members(migrated_engine, "ck_observations_subject_type")
