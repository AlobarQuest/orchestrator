import uuid

from sqlalchemy.orm import Session

from orchestrator.persistence.models import Evidence
from orchestrator.services.evidence_pack import evidence_pack_projection
from tests.services.test_slo_report import _build_unit


def test_projection_assembles_core_facts(migrated_session: Session) -> None:
    _revision, unit = _build_unit(migrated_session, "evidence-pack-projection")
    evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://result",
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-projection-evidence",
    )
    migrated_session.add(evidence)
    migrated_session.commit()

    pack = evidence_pack_projection(migrated_session, unit.id)

    assert set(pack) == {
        "unit",
        "authority",
        "authority_violation",
        "revision",
        "dependencies",
        "claims",
        "evidence",
        "current_evidence_ids",
        "adjudications",
        "current_adjudication_ids",
        "approvals",
        "events",
        "event_publications",
    }
    assert pack["unit"].id == unit.id
    assert [row.id for row in pack["evidence"]] == [evidence.id]
    assert pack["current_evidence_ids"] == {evidence.id}
