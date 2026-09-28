"""Boot-time authentication configuration: the outcome for every environment shape, pinned.

`load_auth_config` runs at import of `orchestrator.main`, so what it does with a given
environment IS what the deployed container does at start. This table was written and run
green against the version that read the ten variables with `os.environ.get`, before they
were folded into `Settings`, and it is unchanged since — the same outcomes on both sides are
the evidence that the fold changed where the values are read and nothing else.

Each outcome is compared exactly: `None`, or the exception's type, message and cause type, or
every field of the returned configuration.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.api.dependencies import AuthConfig
from orchestrator.identity.auth import M2MCredential
from orchestrator.kernel.states import ActorRole
from orchestrator.main import load_auth_config

BUNDLE = str(Path("tests/fixtures/registry-bundle.json").resolve())
MESSAGE = "invalid runtime authentication configuration"

AUTH_VARIABLES = (
    "ORCHESTRATOR_REGISTRY_BUNDLE",
    "ORCHESTRATOR_M2M_CREDENTIALS",
    "ORCHESTRATOR_M2M_ROLES",
    "ORCHESTRATOR_TRUSTED_PROXY_IPS",
    "ORCHESTRATOR_PROXY_MARKER",
    "ORCHESTRATOR_PROXY_MARKER_HEADER",
    "ORCHESTRATOR_EMAIL_HEADER",
    "ORCHESTRATOR_EMAIL_TO_ACTOR",
    "ORCHESTRATOR_CREDENTIAL_KEY_HEADER",
    "ORCHESTRATOR_CSRF_SECRET",
)

VALID: dict[str, str] = {
    "ORCHESTRATOR_REGISTRY_BUNDLE": BUNDLE,
    "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps(
        {"worker-key": {"agent_id": "worker", "token_hash": "a" * 64}}
    ),
    "ORCHESTRATOR_TRUSTED_PROXY_IPS": '["127.0.0.1"]',
    "ORCHESTRATOR_PROXY_MARKER": "trusted-marker",
    "ORCHESTRATOR_EMAIL_TO_ACTOR": '{"devon@example.invalid":"devon"}',
    "ORCHESTRATOR_CSRF_SECRET": "x" * 32,
}

GARBAGE = {name: "{not json" for name in AUTH_VARIABLES}

UNSET = None


def _ok(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_revision": "0123456789abcdef0123456789abcdef01234567",
        "m2m_credentials": {"worker-key": M2MCredential(agent_id="worker", token_hash="a" * 64)},
        "trusted_proxy_ips": frozenset({"127.0.0.1"}),
        "proxy_marker_header": "X-Alobar-Proxy",
        "proxy_marker": "trusted-marker",
        "email_header": "X-Alobar-Email",
        "email_to_actor": {"devon@example.invalid": "devon"},
        "m2m_roles": {},
        "credential_key_header": "X-Credential-Key-Id",
        "csrf_secret": b"x" * 32,
    }
    fields.update(overrides)
    return {"ok": fields}


def _refused(cause: str | None) -> dict[str, Any]:
    return {"raises": ("RuntimeError", MESSAGE, cause)}


CASES: list[tuple[str, dict[str, str | None], dict[str, Any]]] = [
    (
        "bundle_unset_ignores_every_sibling",
        {**GARBAGE, "ORCHESTRATOR_REGISTRY_BUNDLE": UNSET},
        {"none": True},
    ),
    (
        "bundle_empty_ignores_every_sibling",
        {**GARBAGE, "ORCHESTRATOR_REGISTRY_BUNDLE": ""},
        {"none": True},
    ),
    ("valid_with_defaults", VALID, _ok()),
    (
        "header_overrides",
        {
            **VALID,
            "ORCHESTRATOR_PROXY_MARKER_HEADER": "X-P",
            "ORCHESTRATOR_EMAIL_HEADER": "X-E",
            "ORCHESTRATOR_CREDENTIAL_KEY_HEADER": "X-K",
        },
        _ok(proxy_marker_header="X-P", email_header="X-E", credential_key_header="X-K"),
    ),
    (
        "headers_set_but_empty_are_empty_not_defaulted",
        {
            **VALID,
            "ORCHESTRATOR_PROXY_MARKER_HEADER": "",
            "ORCHESTRATOR_EMAIL_HEADER": "",
            "ORCHESTRATOR_CREDENTIAL_KEY_HEADER": "",
        },
        _ok(proxy_marker_header="", email_header="", credential_key_header=""),
    ),
    (
        "role_for_a_known_credential",
        {**VALID, "ORCHESTRATOR_M2M_ROLES": '{"worker-key":"system"}'},
        _ok(m2m_roles={"worker-key": ActorRole.SYSTEM}),
    ),
    ("roles_set_but_empty", {**VALID, "ORCHESTRATOR_M2M_ROLES": ""}, _refused("JSONDecodeError")),
    ("roles_not_an_object", {**VALID, "ORCHESTRATOR_M2M_ROLES": "[]"}, _refused(None)),
    (
        "role_for_an_unknown_credential",
        {**VALID, "ORCHESTRATOR_M2M_ROLES": '{"missing-key":"system"}'},
        _refused(None),
    ),
    ("human_role", {**VALID, "ORCHESTRATOR_M2M_ROLES": '{"worker-key":"human"}'}, _refused(None)),
    (
        "unknown_role",
        {**VALID, "ORCHESTRATOR_M2M_ROLES": '{"worker-key":"bogus"}'},
        _refused("ValueError"),
    ),
    (
        "credentials_unset",
        {**VALID, "ORCHESTRATOR_M2M_CREDENTIALS": UNSET},
        _refused("JSONDecodeError"),
    ),
    (
        "credentials_malformed",
        {**VALID, "ORCHESTRATOR_M2M_CREDENTIALS": "{"},
        _refused("JSONDecodeError"),
    ),
    ("credentials_empty_object", {**VALID, "ORCHESTRATOR_M2M_CREDENTIALS": "{}"}, _refused(None)),
    (
        "credential_for_a_human_actor",
        {
            **VALID,
            "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps(
                {"k": {"agent_id": "devon", "token_hash": "a" * 64}}
            ),
        },
        _refused(None),
    ),
    (
        "credential_for_an_unknown_actor",
        {
            **VALID,
            "ORCHESTRATOR_M2M_CREDENTIALS": json.dumps(
                {"k": {"agent_id": "nobody", "token_hash": "a" * 64}}
            ),
        },
        _refused("RegistryValidationError"),
    ),
    ("csrf_unset", {**VALID, "ORCHESTRATOR_CSRF_SECRET": UNSET}, _refused(None)),
    ("csrf_empty", {**VALID, "ORCHESTRATOR_CSRF_SECRET": ""}, _refused(None)),
    ("csrf_too_short", {**VALID, "ORCHESTRATOR_CSRF_SECRET": "x" * 31}, _refused("ValueError")),
    ("marker_unset", {**VALID, "ORCHESTRATOR_PROXY_MARKER": UNSET}, _refused(None)),
    ("marker_empty", {**VALID, "ORCHESTRATOR_PROXY_MARKER": ""}, _refused(None)),
    ("proxy_ips_unset", {**VALID, "ORCHESTRATOR_TRUSTED_PROXY_IPS": UNSET}, _refused(None)),
    (
        "proxy_ips_not_strings",
        {**VALID, "ORCHESTRATOR_TRUSTED_PROXY_IPS": '["a", 1]'},
        _refused(None),
    ),
    ("proxy_ips_not_a_list", {**VALID, "ORCHESTRATOR_TRUSTED_PROXY_IPS": "{}"}, _refused(None)),
    (
        "proxy_ips_malformed",
        {**VALID, "ORCHESTRATOR_TRUSTED_PROXY_IPS": "["},
        _refused("JSONDecodeError"),
    ),
    (
        "email_map_unset",
        {**VALID, "ORCHESTRATOR_EMAIL_TO_ACTOR": UNSET},
        _refused("JSONDecodeError"),
    ),
    (
        "email_map_to_a_machine_actor",
        {**VALID, "ORCHESTRATOR_EMAIL_TO_ACTOR": '{"w@example.invalid":"worker"}'},
        _refused(None),
    ),
    (
        "email_map_not_normalized",
        {**VALID, "ORCHESTRATOR_EMAIL_TO_ACTOR": '{"Devon@example.invalid":"devon"}'},
        _refused(None),
    ),
    (
        "bundle_path_missing",
        {**VALID, "ORCHESTRATOR_REGISTRY_BUNDLE": "/nonexistent/registry-bundle.json"},
        _refused("RegistryValidationError"),
    ),
]


def _outcome() -> dict[str, Any]:
    try:
        config = load_auth_config()
    except Exception as error:  # the outcome under test IS the exception
        cause = error.__cause__
        message = str(error) if isinstance(error, RuntimeError) else None
        return {
            "raises": (
                type(error).__name__,
                message,
                type(cause).__name__ if cause is not None else None,
            )
        }
    if config is None:
        return {"none": True}
    assert isinstance(config, AuthConfig)
    return {
        "ok": {
            "source_revision": config.registry.source_revision,
            "m2m_credentials": dict(config.m2m_credentials),
            "trusted_proxy_ips": config.trusted_proxy_ips,
            "proxy_marker_header": config.proxy_marker_header,
            "proxy_marker": config.proxy_marker,
            "email_header": config.email_header,
            "email_to_actor": dict(config.email_to_actor),
            "m2m_roles": dict(config.m2m_roles) if config.m2m_roles is not None else None,
            "credential_key_header": config.credential_key_header,
            "csrf_secret": config.csrf_secret,
        }
    }


@pytest.mark.parametrize(
    ("environment", "expected"),
    [pytest.param(env, expected, id=name) for name, env, expected in CASES],
)
def test_boot_outcome_for_each_environment(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str | None],
    expected: dict[str, Any],
) -> None:
    for name in AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        if value is not None:
            monkeypatch.setenv(name, value)

    assert _outcome() == expected
