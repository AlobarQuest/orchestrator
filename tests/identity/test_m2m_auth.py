import hashlib

import pytest

from orchestrator.identity.auth import (
    AuthenticationError,
    M2MCredential,
    authenticate_m2m,
)
from orchestrator.identity.registry import RegistryAdapter

REVISION = "0123456789abcdef0123456789abcdef01234567"


def registry() -> RegistryAdapter:
    return RegistryAdapter(
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
                }
            ],
        }
    )


def credential(agent_id: str = "worker") -> M2MCredential:
    return M2MCredential(
        agent_id=agent_id,
        token_hash=hashlib.sha256(b"fixture-token").hexdigest(),
    )


def test_m2m_authentication_returns_registered_actor_context() -> None:
    context = authenticate_m2m(
        bearer_token="fixture-token",
        credential_key_id="worker-key",
        credentials={"worker-key": credential()},
        registry=registry(),
    )

    assert context.actor_id == "worker"
    assert context.role.value == "worker"
    assert context.authority_profile == "agent-queue-v1"
    assert context.credential_key_id == "worker-key"
    assert context.registry_version == 3


@pytest.mark.parametrize("token", ["wrong-token", "", "Bearer fixture-token"])
def test_m2m_rejects_invalid_bearer_tokens(token: str) -> None:
    with pytest.raises(AuthenticationError):
        authenticate_m2m(
            bearer_token=token,
            credential_key_id="worker-key",
            credentials={"worker-key": credential()},
            registry=registry(),
        )


def test_m2m_rejects_unknown_key() -> None:
    with pytest.raises(AuthenticationError):
        authenticate_m2m(
            bearer_token="fixture-token",
            credential_key_id="missing",
            credentials={"worker-key": credential()},
            registry=registry(),
        )


def test_m2m_configuration_must_map_agents_one_to_one() -> None:
    credentials = {
        "worker-key": credential(),
        "other-key": credential(),
    }

    with pytest.raises(AuthenticationError, match="one-to-one"):
        authenticate_m2m(
            bearer_token="fixture-token",
            credential_key_id="worker-key",
            credentials=credentials,
            registry=registry(),
        )


def test_m2m_context_never_contains_token() -> None:
    context = authenticate_m2m(
        bearer_token="fixture-token",
        credential_key_id="worker-key",
        credentials={"worker-key": credential()},
        registry=registry(),
    )

    assert "fixture-token" not in repr(context)
