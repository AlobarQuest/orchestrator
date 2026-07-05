import pytest

from orchestrator.identity.auth import AuthenticationError, authenticate_human
from orchestrator.identity.registry import RegistryAdapter

REVISION = "0123456789abcdef0123456789abcdef01234567"


def registry(**overrides: object) -> RegistryAdapter:
    actor: dict[str, object] = {
        "agent_id": "devon",
        "version": 1,
        "status": "active",
        "runtime": "human",
        "authority_profile": "human-operator-v1",
    }
    actor.update(overrides)
    return RegistryAdapter(
        {
            "schema": "orchestrator-actor-bundle/v1",
            "source_revision": REVISION,
            "actors": [actor],
        }
    )


def trusted_headers() -> list[tuple[str, str]]:
    return [
        ("X-Alobar-Proxy", "fixture-marker"),
        ("X-Alobar-Email", "devon@example.invalid"),
    ]


def test_human_authentication_maps_trusted_email_to_devon() -> None:
    context = authenticate_human(
        headers=trusted_headers(),
        peer_ip="10.0.0.2",
        trusted_proxy_ips={"10.0.0.2"},
        proxy_marker_header="X-Alobar-Proxy",
        proxy_marker="fixture-marker",
        email_header="X-Alobar-Email",
        email_to_actor={"devon@example.invalid": "devon"},
        registry=registry(),
    )

    assert context.actor_id == "devon"
    assert context.role.value == "human"
    assert context.authority_profile == "human-operator-v1"
    assert context.credential_key_id is None
    assert context.registry_version == 1


def test_spoofed_forward_auth_from_untrusted_peer_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="untrusted"):
        authenticate_human(
            headers=trusted_headers(),
            peer_ip="203.0.113.9",
            trusted_proxy_ips={"10.0.0.2"},
            proxy_marker_header="X-Alobar-Proxy",
            proxy_marker="fixture-marker",
            email_header="X-Alobar-Email",
            email_to_actor={"devon@example.invalid": "devon"},
            registry=registry(),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "reserved"},
        {"runtime": "runner"},
        {"authority_profile": "interactive-dev-v1"},
    ],
)
def test_only_active_human_operator_resolves_as_human(overrides: dict[str, object]) -> None:
    with pytest.raises((AuthenticationError, ValueError)):
        authenticate_human(
            headers=trusted_headers(),
            peer_ip="10.0.0.2",
            trusted_proxy_ips={"10.0.0.2"},
            proxy_marker_header="X-Alobar-Proxy",
            proxy_marker="fixture-marker",
            email_header="X-Alobar-Email",
            email_to_actor={"devon@example.invalid": "devon"},
            registry=registry(**overrides),
        )


def test_human_auth_rejects_missing_marker_or_unknown_email() -> None:
    headers = trusted_headers()
    headers[0] = ("X-Alobar-Proxy", "wrong")
    with pytest.raises(AuthenticationError):
        authenticate_human(
            headers=headers,
            peer_ip="10.0.0.2",
            trusted_proxy_ips={"10.0.0.2"},
            proxy_marker_header="X-Alobar-Proxy",
            proxy_marker="fixture-marker",
            email_header="X-Alobar-Email",
            email_to_actor={"devon@example.invalid": "devon"},
            registry=registry(),
        )


@pytest.mark.parametrize(
    "duplicate",
    [
        ("x-alobar-proxy", "fixture-marker"),
        ("x-alobar-email", "devon@example.invalid"),
    ],
)
def test_human_auth_rejects_case_variant_duplicate_headers(
    duplicate: tuple[str, str],
) -> None:
    with pytest.raises(AuthenticationError, match="exactly one"):
        authenticate_human(
            headers=[*trusted_headers(), duplicate],
            peer_ip="10.0.0.2",
            trusted_proxy_ips={"10.0.0.2"},
            proxy_marker_header="X-Alobar-Proxy",
            proxy_marker="fixture-marker",
            email_header="X-Alobar-Email",
            email_to_actor={"devon@example.invalid": "devon"},
            registry=registry(),
        )
