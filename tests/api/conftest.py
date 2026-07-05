import hashlib
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.identity.auth import M2MCredential
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.main import create_app
from tests.persistence.conftest import migrated_engine, migrated_session

REVISION = "0123456789abcdef0123456789abcdef01234567"
__all__ = ["migrated_engine", "migrated_session"]


@pytest.fixture
def auth_config() -> AuthConfig:
    registry = RegistryAdapter(
        {
            "schema": "orchestrator-actor-bundle/v1",
            "source_revision": REVISION,
            "actors": [
                {
                    "agent_id": "worker",
                    "version": 3,
                    "status": "active",
                    "runtime": "runner",
                    "authority_profile": "agent-queue-v1",
                },
                {
                    "agent_id": "devon",
                    "version": 1,
                    "status": "active",
                    "runtime": "human",
                    "authority_profile": "human-operator-v1",
                },
                {
                    "agent_id": "system",
                    "version": 1,
                    "status": "active",
                    "runtime": "orchestrator",
                    "authority_profile": "system-v1",
                },
                {
                    "agent_id": "verifier",
                    "version": 1,
                    "status": "active",
                    "runtime": "verifier",
                    "authority_profile": "verifier-v1",
                },
            ],
        }
    )
    return AuthConfig(
        registry=registry,
        m2m_credentials={
            "worker-key": M2MCredential(
                agent_id="worker",
                token_hash=hashlib.sha256(b"fixture-token").hexdigest(),
            ),
            "system-key": M2MCredential(
                agent_id="system",
                token_hash=hashlib.sha256(b"system-token").hexdigest(),
            ),
            "verifier-key": M2MCredential(
                agent_id="verifier",
                token_hash=hashlib.sha256(b"verifier-token").hexdigest(),
            ),
        },
        trusted_proxy_ips=frozenset({"testclient"}),
        proxy_marker_header="X-Alobar-Proxy",
        proxy_marker="fixture-marker",
        email_header="X-Alobar-Email",
        email_to_actor={"devon@example.invalid": "devon"},
        m2m_roles={
            "system-key": ActorRole.SYSTEM,
            "verifier-key": ActorRole.VERIFIER,
        },
    )


@pytest.fixture
def client(auth_config: AuthConfig) -> Iterator[TestClient]:
    app = create_app(auth_config)

    def unavailable_session() -> Iterator[Session]:
        yield Mock(spec=Session)

    app.dependency_overrides[get_session] = unavailable_session
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def db_client(auth_config: AuthConfig, migrated_engine: Engine) -> Iterator[TestClient]:
    app = create_app(auth_config)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    app.dependency_overrides[get_session] = database_session
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
