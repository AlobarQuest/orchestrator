"""WS-P2.1 schema behavior: append-only conditions, mutable PR binding, single evidence head."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError
from sqlalchemy.orm import Session

HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _seed_unit(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (work_package_revision_id, work_unit_id) with the minimum required columns."""
    package_id, revision_id, unit_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO work_packages (id, package_id, source_repository) "
            "VALUES (:id, :pid, 'AlobarQuest/orchestrator')"
        ),
        {"id": package_id, "pid": f"pkg-{package_id}"},
    )
    session.execute(
        text(
            "INSERT INTO work_package_revisions (id, work_package_id, revision, content_hash,"
            " source_path, source_commit, approved_by, approved_at, approval_event_id,"
            " enforcement_snapshot, authority_fingerprint, registry_version, registered_by)"
            " VALUES (:id, :pkg, 1, :hash, '/pkg', 'abc123', 'devon', now(), 'evt-1',"
            " '{}'::jsonb, 'fp-1', 1, 'system')"
        ),
        {"id": revision_id, "pkg": package_id, "hash": f"hash-{revision_id}"},
    )
    session.execute(
        text(
            "INSERT INTO work_units (id, unit_key, work_package_revision_id, title, outcome,"
            " state, required_capability, authority, authority_fingerprint,"
            " decomposition_approved_by, decomposition_approved_at)"
            " VALUES (:id, 'u1', :rev, 't', 'o', 'ready', 'repo.edit', '{}'::jsonb, 'fp',"
            " 'devon', now())"
        ),
        {"id": unit_id, "rev": revision_id},
    )
    session.commit()
    return revision_id, unit_id


def _insert_evidence(
    session: Session,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    *,
    key: str,
    supersedes: uuid.UUID | None,
) -> uuid.UUID:
    evidence_id, event_id = uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO events (id, actor_id, action, subject_type, subject_id, payload,"
            " correlation_id, idempotency_key)"
            " VALUES (:id, 'worker', 'evidence.recorded', 'evidence', :subject, '{}'::jsonb,"
            " :corr, :key)"
        ),
        {"id": event_id, "subject": evidence_id, "corr": uuid.uuid4(), "key": f"{key}:event"},
    )
    session.execute(
        text(
            "INSERT INTO evidence (id, work_package_revision_id, work_unit_id, ac_id, attempt,"
            " evidence_type, stable_ref, source_revision, recorded_by, event_id,"
            " idempotency_key, supersedes_evidence_id)"
            " VALUES (:id, :rev, :unit, 'AC-001', 1, 'test', 'artifact://x', 'abc123',"
            " 'worker', :event, :key, :sup)"
        ),
        {
            "id": evidence_id,
            "rev": revision_id,
            "unit": unit_id,
            "event": event_id,
            "key": key,
            "sup": supersedes,
        },
    )
    session.flush()
    return evidence_id


def _seed_condition(session: Session, unit_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    condition_id, event_id = uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO events (id, actor_id, action, subject_type, subject_id, payload,"
            " correlation_id, idempotency_key)"
            " VALUES (:id, 'system', 'reconciliation.required', 'reconciliation_condition',"
            " :subject, '{}'::jsonb, :corr, 'reconcile:evt')"
        ),
        {"id": event_id, "subject": condition_id, "corr": uuid.uuid4()},
    )
    session.execute(
        text(
            "INSERT INTO reconciliation_conditions (id, work_unit_id, observation_kind,"
            " condition_type, lineage_hash, resolution_generation, normalized_divergence_hash,"
            " detail, event_id, idempotency_key)"
            " VALUES (:id, :unit, 'github_pr', 'external_merge_alarm', 'sha256:lineage', 0,"
            " 'sha256:divergence', 'merged outside the session', :event, 'reconcile:cond')"
        ),
        {"id": condition_id, "unit": unit_id, "event": event_id},
    )

    resolution_id, resolution_event = uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO events (id, actor_id, action, subject_type, subject_id, payload,"
            " correlation_id, idempotency_key)"
            " VALUES (:id, 'devon', 'reconciliation.resolved', 'reconciliation_resolution',"
            " :subject, '{}'::jsonb, :corr, 'reconcile:res-evt')"
        ),
        {"id": resolution_event, "subject": resolution_id, "corr": uuid.uuid4()},
    )
    session.execute(
        text(
            "INSERT INTO reconciliation_resolutions (id, condition_id, resolved_by, decision,"
            " rationale, event_id, idempotency_key)"
            " VALUES (:id, :cond, 'devon', 'accepted', 'acknowledged', :event, 'reconcile:res')"
        ),
        {"id": resolution_id, "cond": condition_id, "event": resolution_event},
    )
    session.commit()
    return condition_id, resolution_id


@pytest.mark.parametrize("table", ["reconciliation_conditions", "reconciliation_resolutions"])
def test_reconciliation_tables_reject_update_and_delete(
    migrated_session: Session, table: str
) -> None:
    _, unit_id = _seed_unit(migrated_session)
    condition_id, resolution_id = _seed_condition(migrated_session, unit_id)
    row_id = condition_id if table == "reconciliation_conditions" else resolution_id

    with pytest.raises((IntegrityError, InternalError)):
        migrated_session.execute(
            text(f"UPDATE {table} SET detail = 'mutated' WHERE id = :id")
            if table == "reconciliation_conditions"
            else text(f"UPDATE {table} SET rationale = 'mutated' WHERE id = :id"),
            {"id": row_id},
        )
    migrated_session.rollback()

    with pytest.raises((IntegrityError, InternalError)):
        migrated_session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
    migrated_session.rollback()


def test_unit_pr_binding_head_sha_is_mutable(migrated_session: Session) -> None:
    """Deliberately NOT append-only: a worker rebase/force-push before verification is normal."""
    _, unit_id = _seed_unit(migrated_session)
    migrated_session.execute(
        text(
            "INSERT INTO unit_pr_binding (work_unit_id, pr_number, head_sha)"
            " VALUES (:unit, 41, :sha)"
        ),
        {"unit": unit_id, "sha": HEAD_A},
    )
    migrated_session.commit()

    migrated_session.execute(
        text("UPDATE unit_pr_binding SET head_sha = :sha WHERE work_unit_id = :unit"),
        {"sha": HEAD_B, "unit": unit_id},
    )
    migrated_session.commit()

    assert (
        migrated_session.scalar(
            text("SELECT head_sha FROM unit_pr_binding WHERE work_unit_id = :unit"),
            {"unit": unit_id},
        )
        == HEAD_B
    )


def test_a_second_unsuperseded_evidence_head_is_structurally_rejected(
    migrated_session: Session,
) -> None:
    """THE wedge guard.

    Two unsuperseded heads make `_terminal` raise, so the AC can never be adjudicated and no
    further evidence can be written -- and `evidence` is append-only, so the row could never be
    repaired and the unit could never complete. One naive recovery call would wedge the unit
    permanently. The partial unique index makes that impossible.
    """
    revision_id, unit_id = _seed_unit(migrated_session)
    _insert_evidence(migrated_session, revision_id, unit_id, key="head-1", supersedes=None)
    migrated_session.commit()

    with pytest.raises(IntegrityError) as error:
        _insert_evidence(migrated_session, revision_id, unit_id, key="head-2", supersedes=None)
    assert "uq_evidence_unsuperseded_head" in str(error.value)
    migrated_session.rollback()


def test_a_superseding_evidence_row_is_allowed(migrated_session: Session) -> None:
    """The index must forbid a second HEAD without forbidding legitimate supersession."""
    revision_id, unit_id = _seed_unit(migrated_session)
    first = _insert_evidence(migrated_session, revision_id, unit_id, key="sup-1", supersedes=None)
    second = _insert_evidence(migrated_session, revision_id, unit_id, key="sup-2", supersedes=first)
    migrated_session.commit()

    heads = migrated_session.scalar(
        text(
            "SELECT count(*) FROM evidence WHERE work_unit_id = :unit"
            " AND supersedes_evidence_id IS NULL"
        ),
        {"unit": unit_id},
    )
    assert heads == 1
    assert second is not None
