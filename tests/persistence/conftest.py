import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Event, WorkPackage, WorkPackageRevision, WorkUnit
from tests.conftest import TEST_DATABASE_URL

AUTHORITY = {"capabilities": {"repo.edit": "allowed"}, "budgets": {}, "unknown_fields": []}


def alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def migrated_session(migrated_engine: Engine) -> Iterator[Session]:
    with Session(migrated_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def work_unit_and_event(migrated_session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """A committed `WorkUnit` + `Event`, returning `(work_unit_id, event_id)`.

    Smallest factory shared by reconciliation-condition tests -- mirrors
    `tests/persistence/test_constraints.py::_revision`.
    """
    package = WorkPackage(package_id="pkg-tracker-recon", source_repository="owner/repo")
    revision = WorkPackageRevision(
        work_package=package,
        revision=1,
        content_hash="hash",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at="2026-07-05T12:00:00+00:00",
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={},
        authority_fingerprint="authority",
        registry_version=1,
        registered_by="human-1",
    )
    migrated_session.add(revision)
    migrated_session.flush()

    unit = WorkUnit(
        unit_key="unit-tracker-recon",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        required_capability="repo.edit",
        authority=AUTHORITY,
        authority_fingerprint="authority",
    )
    migrated_session.add(unit)
    migrated_session.flush()

    event = Event(
        actor_id="system",
        action="reconciliation.required",
        subject_type="reconciliation_condition",
        subject_id=uuid.uuid4(),
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"tracker-recon:{uuid.uuid4()}",
    )
    migrated_session.add(event)
    migrated_session.commit()

    return unit.id, event.id
