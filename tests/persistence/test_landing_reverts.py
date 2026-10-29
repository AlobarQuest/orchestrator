import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert

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
