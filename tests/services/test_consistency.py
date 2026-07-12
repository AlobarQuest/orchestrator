"""WS-P2.1 Task 13: the projection-vs-source consistency check (AC-008).

It REPORTS. It never repairs, never transitions, and -- critically -- never crashes on the
corruption it exists to find.

That last point dictates the whole design. `_terminal` RAISES on a broken supersession chain, so a
check built on it would crash instead of report. And reusing the same set-difference the
projection uses would make the clean-fixture result a tautology: it would agree with itself. So
the check recomputes INDEPENDENTLY, in SQL.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import EVIDENCE_HEAD_BOOKKEEPING_AC_ID, Evidence, WorkUnit
from orchestrator.services.consistency import check_consistency
from orchestrator.services.evidence import current_evidence
from tests.services.test_dependencies import register_unit


def _seed_evidence(
    session: Session,
    unit: WorkUnit,
    *,
    ac_id: str,
    key: str,
    supersedes: uuid.UUID | None = None,
) -> uuid.UUID:
    evidence_id = uuid.uuid4()
    session.add(
        Evidence(
            id=evidence_id,
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id=ac_id,
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://x",
            payload=None,
            source_revision="abc123",
            recorded_by="worker",
            event_id=uuid.uuid4(),
            idempotency_key=key,
            supersedes_evidence_id=supersedes,
        )
    )
    session.flush()
    return evidence_id


def test_a_clean_fixture_reports_zero_divergence(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "consistency-clean")
    unit.state = WorkUnitState.SUBMITTED
    _seed_evidence(migrated_session, unit, ac_id="ac-1", key="clean-1")
    migrated_session.commit()

    report = check_consistency(migrated_session)

    assert report.divergent is False
    assert report.findings == ()


def test_a_zero_head_chain_is_REPORTED_not_raised(migrated_session: Session) -> None:
    """THE test. The partial index makes a two-headed chain structurally impossible, so the
    reachable corruption is a chain with NO head -- here, a row that supersedes itself.

    `current_evidence` RAISES on it. The check must REPORT it. That contrast is AC-008's evidence:
    a checker that crashes on corruption is not a checker.
    """
    unit = register_unit(migrated_session, "consistency-zero-head")
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()
    orphan = uuid.uuid4()
    # Inserted directly: `evidence` is append-only, so no service call can produce -- or repair --
    # this state. That is exactly why an out-of-band audit has to exist.
    migrated_session.execute(
        text(
            "INSERT INTO evidence (id, work_package_revision_id, work_unit_id, ac_id, attempt,"
            " evidence_type, stable_ref, source_revision, recorded_by, event_id,"
            " idempotency_key, supersedes_evidence_id)"
            " VALUES (:id, :rev, :unit, 'ac-1', 1, 'test', 'ref://orphan', 'abc123', 'worker',"
            " :event, 'consistency-orphan', :id)"
        ),
        {
            "id": orphan,
            "rev": unit.work_package_revision_id,
            "unit": unit.id,
            "event": uuid.uuid4(),
        },
    )
    migrated_session.commit()

    # The helper the check must NOT reuse blows up on this row:
    with pytest.raises(DomainError) as error:
        current_evidence(migrated_session, unit.work_package_revision_id, unit.id, "ac-1")
    assert error.value.code == "evidence_chain_invalid"

    report = check_consistency(migrated_session)

    assert report.divergent is True
    heads = [f for f in report.findings if f.check == "evidence_head_count"]
    assert len(heads) == 1
    assert heads[0].work_unit_id == unit.id
    assert heads[0].subject == "ac-1"
    assert (heads[0].observed, heads[0].expected) == ("0", "1")


def test_release_artifact_bookkeeping_rows_are_NOT_flagged(migrated_session: Session) -> None:
    """The false-positive this check would otherwise scream on every healthy multi-binding unit.

    `release_artifacts` writes ONE evidence row per binding under the constant
    ac_id 'release-artifact', all with supersedes_evidence_id NULL. A unit may legitimately carry
    several bindings, so that triple genuinely has MANY unsuperseded heads. Those rows are never
    superseded and never adjudicated. A head-count check that included them would flag every
    healthy unit with two bindings -- and a checker that cries wolf is worse than no checker.
    """
    unit = register_unit(migrated_session, "consistency-bookkeeping")
    unit.state = WorkUnitState.SUBMITTED
    _seed_evidence(migrated_session, unit, ac_id=EVIDENCE_HEAD_BOOKKEEPING_AC_ID, key="binding-1")
    _seed_evidence(migrated_session, unit, ac_id=EVIDENCE_HEAD_BOOKKEEPING_AC_ID, key="binding-2")
    _seed_evidence(migrated_session, unit, ac_id=EVIDENCE_HEAD_BOOKKEEPING_AC_ID, key="binding-3")
    migrated_session.commit()

    report = check_consistency(migrated_session)

    assert report.divergent is False
    assert report.findings == ()


def test_a_superseded_chain_with_exactly_one_head_is_clean(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "consistency-chain")
    unit.state = WorkUnitState.SUBMITTED
    first = _seed_evidence(migrated_session, unit, ac_id="ac-1", key="chain-1")
    _seed_evidence(migrated_session, unit, ac_id="ac-1", key="chain-2", supersedes=first)
    migrated_session.commit()

    report = check_consistency(migrated_session)

    assert report.divergent is False


def test_completion_integrity_flags_a_completed_unit_with_no_adjudication(
    migrated_session: Session,
) -> None:
    """A unit cannot legitimately be COMPLETED without its required ACs satisfied -- the
    lifecycle guard forbids it. If one is, the guard was bypassed and the operator must know."""
    unit = register_unit(migrated_session, "consistency-completion")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    report = check_consistency(migrated_session)

    findings = [f for f in report.findings if f.check == "completion_integrity"]
    assert findings
    assert findings[0].work_unit_id == unit.id


def test_the_check_never_repairs(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "consistency-readonly")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    before = (unit.state, unit.version)

    check_consistency(migrated_session)

    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == before
