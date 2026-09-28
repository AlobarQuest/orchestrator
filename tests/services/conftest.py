import pytest
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from tests.services.test_dependencies import register_unit


@pytest.fixture
def ready_unit(migrated_session: Session):
    unit = register_unit(migrated_session, "lifecycle")
    unit.state = WorkUnitState.READY
    migrated_session.commit()
    return unit


from tests.api.conftest import auth_config, db_client  # noqa: E402
from tests.services.test_reconciliation_detect_pass import (  # noqa: E402
    deployed_binding,
    unreported_binding,
)

__all__ = [
    "auth_config",
    "db_client",
    "deployed_binding",
    "ready_unit",
    "unreported_binding",
]
