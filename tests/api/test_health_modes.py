from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_session
from orchestrator.config import ProductionDrillMode
from orchestrator.main import create_app
from orchestrator.services.production_drill_compatibility import (
    DRILL_REVISION,
    PRE_DRILL_REVISION,
)


@pytest.mark.parametrize(
    ("mode", "database_heads", "ready"),
    [
        (ProductionDrillMode.OFF, (PRE_DRILL_REVISION,), True),
        (ProductionDrillMode.STANDBY, (DRILL_REVISION,), True),
        (ProductionDrillMode.ENABLED, (DRILL_REVISION,), True),
        (ProductionDrillMode.OFF, (DRILL_REVISION,), False),
        (ProductionDrillMode.STANDBY, (PRE_DRILL_REVISION,), False),
        (ProductionDrillMode.ENABLED, (PRE_DRILL_REVISION,), False),
        (ProductionDrillMode.ENABLED, (PRE_DRILL_REVISION, DRILL_REVISION), False),
        (ProductionDrillMode.ENABLED, ("unknown_revision",), False),
    ],
)
def test_readiness_requires_the_database_head_for_the_activation_mode(
    monkeypatch: pytest.MonkeyPatch,
    mode: ProductionDrillMode,
    database_heads: tuple[str, ...],
    ready: bool,
) -> None:
    session = Mock(spec=Session)
    session.connection.return_value = Mock()
    application = create_app(production_drill_mode=mode)
    application.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(
        "orchestrator.api.health.MigrationContext.configure",
        lambda _connection: Mock(get_current_heads=lambda: database_heads),
    )
    monkeypatch.setattr(
        "orchestrator.api.health.ScriptDirectory.from_config",
        lambda _config: Mock(get_heads=lambda: [DRILL_REVISION]),
    )

    response = TestClient(application).get("/health/ready")

    if ready:
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    else:
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable", "reason": "migration_drift"}
