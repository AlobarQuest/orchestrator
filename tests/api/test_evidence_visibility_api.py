import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.config import ProductionDrillMode, get_settings
from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN
from tests.api.test_status_ledger_api import _register_ready_unit
from tests.services.test_production_drill_resources import (
    mark_work_unit_as_production_drill_resource,
)


@pytest.mark.parametrize(
    "mode",
    (ProductionDrillMode.STANDBY, ProductionDrillMode.ENABLED),
)
def test_evidence_list_hides_production_drill_work(
    db_client: TestClient,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_PRODUCTION_DRILL_MODE", mode.value)
    get_settings.cache_clear()
    unit_id = _register_ready_unit(db_client, f"evidence-{mode.value}")
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        mark_work_unit_as_production_drill_resource(session, unit)

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence", headers=HUMAN)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "work_unit_not_found"
