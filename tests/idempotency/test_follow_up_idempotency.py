"""The minting pass's duplicate-delivery story (matrix row: follow-up minting)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.config import Settings, get_settings
from orchestrator.main import create_app
from orchestrator.persistence.models import WorkPackageRevision
from tests.conftest import TEST_DATABASE_URL

SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}


@pytest.fixture
def mint_client(auth_config: AuthConfig, migrated_engine: Engine) -> Iterator[TestClient]:
    """`db_client` runs under the default 30-day follow-up window. `due_revision` settles its
    unit moments before the request, so under that default the revision would read as
    `not_yet_due` and nothing would ever mint -- making a duplicate-delivery assertion pass
    whether or not idempotency actually held. Overriding `follow_up_due_after_days` to 0 makes
    the revision genuinely due within the test, the same way the service's own unit tests do."""
    app = create_app(auth_config)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    def due_immediately_settings() -> Settings:
        return Settings(database_url=TEST_DATABASE_URL, follow_up_due_after_days=0)

    app.dependency_overrides[get_session] = database_session
    app.dependency_overrides[get_settings] = due_immediately_settings
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client


def test_a_duplicate_minting_pass_creates_one_unit(
    mint_client: TestClient,
    due_revision: WorkPackageRevision,
    migrated_session: Session,
) -> None:
    """Two passes under DIFFERENT keys still mint once: the unit id is content-addressed from
    the revision id, so idempotency does not depend on the caller reusing a key."""
    # `due_revision` only flushes inside `migrated_session`; each API request opens its OWN
    # session against the same engine, so the fixture's insert must be committed to be visible.
    migrated_session.commit()

    first = mint_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-pass-a", "expected_version": 0},
    )
    second = mint_client.post(
        "/api/v1/follow-ups/mint",
        headers=SYSTEM,
        json={"idempotency_key": "mint-pass-b", "expected_version": 0},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_minted = {
        row["work_package_revision_id"]: row["work_unit_id"] for row in first.json()["minted"]
    }
    # The fixture revision must actually have minted, or the assertions below are vacuous.
    assert str(due_revision.id) in first_minted

    second_minted_ids = {row["work_unit_id"] for row in second.json()["minted"]}
    assert second_minted_ids & set(first_minted.values()) == set()

    second_skip_reasons = {
        row["work_package_revision_id"]: row["reason"] for row in second.json()["skipped"]
    }
    assert second_skip_reasons[str(due_revision.id)] == "already_minted"
