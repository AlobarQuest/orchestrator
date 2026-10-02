"""Fixtures for tests that drive a unit through the public protocol."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from orchestrator.api.dependencies import AuthConfig
from tests._support.protocol import protocol_auth_config, protocol_client


@pytest.fixture
def auth_config() -> AuthConfig:
    return protocol_auth_config()


@pytest.fixture
def db_client(auth_config: AuthConfig, migrated_engine: Engine) -> Iterator[TestClient]:
    with protocol_client(auth_config, migrated_engine) as test_client:
        yield test_client
