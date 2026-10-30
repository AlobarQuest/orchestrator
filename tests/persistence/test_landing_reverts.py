from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert
from tests._support.database import alembic_config

BUMP = {
    "ecosystem": "npm",
    "directory": "/",
    "dependency": "left-pad",
    "from_version": None,
    "to_version": "2.0.0",
}


def _revert(**overrides: object) -> LandingRevert:
    fields: dict[str, object] = {
        "repository": "owner/repo",
        "pr_number": 7,
        "merge_commit_sha": "a" * 40,
        "parent_sha": "b" * 40,
        "failed_job_names": ["Quality"],
        "failed_run_id": 1,
        "status": "rerun_requested",
        "bumps": [BUMP],
    }
    fields.update(overrides)
    return LandingRevert(**fields)


def test_one_row_per_landing(migrated_session: Session, migrated_engine) -> None:
    migrated_session.add(_revert())
    migrated_session.commit()
    with Session(migrated_engine) as other:
        other.add(_revert(merge_commit_sha="c" * 40))
        with pytest.raises(IntegrityError):
            other.commit()
    with Session(migrated_engine) as reader:
        rows = reader.query(LandingRevert).all()
        assert len(rows) == 1
        assert rows[0].bumps == [BUMP]
        assert rows[0].revert_pr_number is None


def test_the_status_check_refuses_an_unknown_value(migrated_session: Session) -> None:
    migrated_session.add(_revert(status="exploded"))
    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_run_ids_above_two_to_the_thirty_one_are_stored(
    migrated_session: Session, migrated_engine
) -> None:
    big = 2**31 + 12345
    migrated_session.add(_revert(failed_run_id=big))
    migrated_session.commit()
    with Session(migrated_engine) as reader:
        assert reader.query(LandingRevert).one().failed_run_id == big


def test_rerun_attempts_hold_each_reruns_run_and_attempt(
    migrated_session: Session, migrated_engine
) -> None:
    """JSON keys are strings, so the run id is stored as its decimal text; run ids exceed 2^31."""
    attempts = {str(2**31 + 1): 1, "42": 2}
    migrated_session.add(_revert(rerun_attempts=attempts))
    migrated_session.add(_revert(pr_number=8))
    migrated_session.commit()
    with Session(migrated_engine) as reader:
        rows = {row.pr_number: row for row in reader.query(LandingRevert).all()}
        assert rows[7].rerun_attempts == attempts
        assert rows[8].rerun_attempts is None


def test_the_image_steps_are_stored_and_read_back(
    migrated_session: Session, migrated_engine
) -> None:
    pinned = {"app-uuid-1": {"tag": "sha-" + "b" * 40, "digest": "sha256:" + "c" * 64}}
    restored = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    migrated_session.add(_revert(status="restored", pinned_images=pinned, restored_at=restored))
    migrated_session.add(_revert(pr_number=8, status="repointed"))
    migrated_session.commit()
    with Session(migrated_engine) as reader:
        rows = {row.pr_number: row for row in reader.query(LandingRevert).all()}
        assert rows[7].pinned_images == pinned
        assert rows[7].restored_at == restored
        assert rows[8].pinned_images is None
        assert rows[8].restored_at is None


def test_the_status_check_admits_the_image_statuses_and_still_refuses_others(
    migrated_session: Session,
) -> None:
    for number, status in enumerate(("repointed", "restored"), start=20):
        migrated_session.add(_revert(pr_number=number, status=status))
    migrated_session.commit()
    migrated_session.add(_revert(pr_number=30, status="exploded"))
    with pytest.raises(IntegrityError):
        migrated_session.commit()


@pytest.mark.schema_rebuild
def test_0045_downgrade_refuses_rows_holding_an_image_status_and_otherwise_reverts(
    migrated_engine,
) -> None:
    config = alembic_config()
    with Session(migrated_engine) as session:
        session.add(_revert(status="repointed"))
        session.commit()
    with pytest.raises(Exception, match="repointed|restored|landing_reverts"):
        command.downgrade(config, "0044_landing_reverts")
    with Session(migrated_engine) as session:
        session.query(LandingRevert).delete()
        session.commit()
    command.downgrade(config, "0044_landing_reverts")
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("landing_reverts")}
    assert "pinned_images" not in columns
    assert "restored_at" not in columns
    command.upgrade(config, "head")
    assert "pinned_images" in {
        c["name"] for c in inspect(migrated_engine).get_columns("landing_reverts")
    }
