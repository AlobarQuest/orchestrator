import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.services.test_package_registration import register_test_revision


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (
            "work_package_revisions",
            {
                "work_package_id": None,
                "revision": 1,
                "content_hash": "hash",
                "source_path": "intent.md",
                "source_commit": "abc123",
                "approved_by": "human-1",
                "approved_at": datetime.now(UTC),
                "approval_event_id": uuid.uuid4(),
                "enforcement_snapshot": "{}",
                "authority_fingerprint": "authority",
                "registry_version": 1,
                "registered_by": "human-1",
            },
        ),
        (
            "evidence",
            {
                "work_package_revision_id": None,
                "work_unit_id": None,
                "ac_id": "ac-1",
                "attempt": 1,
                "evidence_type": "test",
                "stable_ref": "artifact://result",
                "source_revision": "abc123",
                "recorded_by": "worker-1",
                "event_id": uuid.uuid4(),
                "idempotency_key": "evidence-1",
            },
        ),
        (
            "adjudications",
            {
                "work_package_revision_id": None,
                "work_unit_id": None,
                "ac_id": "ac-1",
                "outcome": "passed",
                "decided_by": "verifier-1",
                "rationale": "Verified",
                "event_id": uuid.uuid4(),
            },
        ),
        (
            "events",
            {
                "actor_id": "actor-1",
                "action": "created",
                "subject_type": "work_unit",
                "subject_id": uuid.uuid4(),
                "payload": "{}",
                "correlation_id": uuid.uuid4(),
                "idempotency_key": "event-1",
            },
        ),
    ],
)
def test_append_only_tables_reject_update_and_delete(
    migrated_session: Session, table: str, values: dict[str, object]
) -> None:
    values = values.copy()
    package_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    if table != "events":
        migrated_session.execute(
            text(
                "INSERT INTO work_packages (id, package_id, source_repository) "
                "VALUES (:id, 'pkg-1', 'owner/repo')"
            ),
            {"id": package_id},
        )
        if table != "work_package_revisions":
            migrated_session.execute(
                text(
                    "INSERT INTO work_package_revisions "
                    "(id, work_package_id, revision, content_hash, source_path, "
                    "source_commit, approved_by, approved_at, approval_event_id, "
                    "enforcement_snapshot, authority_fingerprint, registry_version, "
                    "registered_by) VALUES "
                    "(:id, :work_package_id, 1, 'hash', 'intent.md', 'abc123', "
                    "'human-1', now(), :approval_event_id, '{}', 'authority', 1, 'human-1')"
                ),
                {
                    "id": revision_id,
                    "work_package_id": package_id,
                    "approval_event_id": uuid.uuid4(),
                },
            )
            migrated_session.execute(
                text(
                    "INSERT INTO work_units "
                    "(id, unit_key, work_package_revision_id, title, outcome, state, "
                    "required_capability, authority_fingerprint) VALUES "
                    "(:id, 'unit-1', :revision_id, 'Title', 'Outcome', 'draft', "
                    "'python', 'authority')"
                ),
                {"id": unit_id, "revision_id": revision_id},
            )
        else:
            values["work_package_id"] = package_id
        if table != "work_package_revisions":
            values["work_package_revision_id"] = revision_id
            values["work_unit_id"] = unit_id

    columns = ", ".join(("id", *values))
    parameters = ", ".join((":id", *(f":{name}" for name in values)))
    row_id = uuid.uuid4()
    migrated_session.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({parameters})"),
        {"id": row_id, **values},
    )
    migrated_session.commit()

    for statement in (
        f"UPDATE {table} SET id = id WHERE id = :id",
        f"DELETE FROM {table} WHERE id = :id",
    ):
        with pytest.raises(IntegrityError):
            migrated_session.execute(text(statement), {"id": row_id})
            migrated_session.commit()
        migrated_session.rollback()


@pytest.mark.parametrize("table", ["package_acceptance_criteria"])
def test_ws32_projection_tables_are_append_only(
    migrated_session: Session, table: str
) -> None:
    revision = register_test_revision(migrated_session)
    criterion_id = uuid.uuid4()
    migrated_session.execute(
        text(
            f"insert into {table} "
            "(id, work_package_revision_id, ac_id, condition, evidence_type, evidence, approver) "
            "values (:id, :revision_id, 'AC-001', 'condition', 'automated_test', 'gate: test', 'policy')"
        ),
        {"id": criterion_id, "revision_id": revision.id},
    )
    migrated_session.commit()

    for statement in (
        f"update {table} set condition = 'changed' where id = :id",
        f"delete from {table} where id = :id",
    ):
        with pytest.raises(IntegrityError):
            migrated_session.execute(text(statement), {"id": criterion_id})
            migrated_session.commit()
        migrated_session.rollback()
