import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Approval, WorkUnit
from tests.api.conftest import auth_config, db_client
from tests.persistence.conftest import migrated_engine
from tests.services.test_dependencies import register_unit

__all__ = ["auth_config", "db_client", "migrated_engine"]


@pytest.fixture
def review_unit(migrated_engine: Engine) -> WorkUnit:
    with Session(migrated_engine) as session:
        unit = register_unit(session, "review-unit")
        unit.state = WorkUnitState.AWAITING_REVIEW
        approval = Approval(
            subject_type="authority",
            subject_id=unit.id,
            subject_revision_or_fingerprint=unit.authority_fingerprint,
            decision="approved",
            approved_by="devon",
            reason="approved authority",
            event_id=uuid.uuid4(),
            idempotency_key=f"authority-{unit.id}",
        )
        session.add(approval)
        session.flush()
        unit.authority_approval_id = approval.id
        session.commit()
        session.refresh(unit)
        session.expunge(unit)
        return unit
