"""The Coolify API token adapter, driven through the contract against a simulated Coolify.

ADR-0055 amendment 33 and D-82. A person mints and stages (a human act), so each test stages the
new value by hand, as Devon pastes it, then runs the machine units. The simulated Coolify answers
as amendment 33 items 2 to 4 read Coolify's source at `v4.0.0-beta.473`; the dev Coolify
measurement (D-82 item 14) is what proves those answers. Every token is drawn at run time.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from rotation_worker.adapters import ADAPTERS, coolify_api_token
from rotation_worker.config import VM_ONLY
from rotation_worker.contract import ContractError, Rotation, ScopeChecked, Step, UnitRun
from rotation_worker.evidence import StepEvidence
from rotation_worker.inbox import ConfirmsIdentity
from rotation_worker.plans import derive
from rotation_worker.types import CLASS_TYPES, TYPES
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

WORK = "work-project"
COOLIFY_URL = "http://100.64.0.1:8000"
CANARY = "canaryapp0000000000000001"
PROJECT = "5f0c6a2e-8d1b-4c3a-9e7f-0a1b2c3d4e5f"
TEAM = 0
EVERY = ["read", "write", "deploy"]


def _token() -> str:
    return f"{secrets.randbelow(10**4) + 1}|{secrets.token_urlsafe(30)}"


@dataclass
class Token:
    abilities: frozenset[str]
    team: int = TEAM


@dataclass
class Coolify:
    """Coolify's ability middleware and the five handlers the adapter reaches."""

    tokens: dict[str, Token] = field(default_factory=dict)
    canary_envs: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"key": "SDS_CANARY", "is_preview": False},
            {"key": "SDS_CANARY", "is_preview": True},
        ]
    )
    requests: list[httpx.Request] = field(default_factory=list)
    down: bool = False
    canary_missing: bool = False

    def add(self, *abilities: str, team: int = TEAM) -> Value:
        value = _token()
        self.tokens[value] = Token(frozenset(abilities), team)
        return Value(value)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no route", request=request)
        token = self.tokens.get(request.headers["Authorization"].removeprefix("Bearer "))
        if token is None:
            return httpx.Response(401, json={"message": "Unauthenticated."})
        path, method = request.url.path, request.method
        ability = {
            ("PATCH", f"/api/v1/projects/{PROJECT}"): "write",
            ("GET", "/api/v1/deploy"): "deploy",
        }.get((method, path), "read")
        if "root" not in token.abilities and ability not in token.abilities:
            return httpx.Response(403, json={"message": f"Missing required permissions: {ability}"})
        if path == "/api/v1/version":
            return httpx.Response(200, text="4.0.0-beta.473")
        if path == "/api/v1/teams/current":
            return httpx.Response(200, json={"id": token.team, "name": "Root Team"})
        if method == "PATCH":
            assert json.loads(request.content) == coolify_api_token.WRITE_PROBE_BODY
            assert request.headers["content-type"] == "application/json"
            return httpx.Response(422, json={"message": "Validation failed."})
        if path == "/api/v1/deploy":
            assert not request.url.query
            return httpx.Response(400, json={"message": "You must provide uuid or tag."})
        if self.canary_missing:
            return httpx.Response(404, json={"message": "Application not found"})
        if not isinstance(self.canary_envs, list):
            return httpx.Response(200, json=self.canary_envs)
        sensitive = "root" in token.abilities or "read:sensitive" in token.abilities
        envs = [
            {**env, "value": "visible", "real_value": "visible"} if sensitive else env
            for env in self.canary_envs
        ]
        return httpx.Response(200, json=envs)


def _provider(
    coolify: Coolify, abilities: list[str] | None = None, canary: str | None = CANARY
) -> coolify_api_token.CoolifyApiTokenProvider:
    return coolify_api_token.build_provider(
        credential={"abilities": EVERY if abilities is None else abilities},
        coolify_url=COOLIFY_URL,
        canary_application=canary,
        transport=httpx.MockTransport(coolify.handle),
        absent_project=PROJECT,
    )


class Rotating:
    """One rotation of a token, with the old value in its keeper."""

    def __init__(self, coolify: Coolify, abilities: list[str], old: Value) -> None:
        self.store = MemoryStore()
        self.keeper = self.store.put(WORK, "coolify token keeper", old)
        self.rotation = Rotation(
            "coolify-change-window-token",
            "2026-11-27-requested",
            self.keeper,
            WORK,
            (f'bws-secret uuid="{self.keeper}"', 'provider name="coolify"'),
        )
        self.provider = _provider(coolify, abilities)
        self.filed: list[tuple[Step, StepEvidence]] = []

    def stage(self, new: Value) -> None:
        self.store.put(WORK, self.rotation.staging_key, new)

    def run(self, unit: str) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=lambda: None,
            filed=frozenset(step.kind for step, _ in self.filed),
        ).run(coolify_api_token.PLAN.unit(unit))


def test_the_type_is_bitbuckets_shape_a_person_mints_and_revokes() -> None:
    assert CLASS_TYPES["coolify-api-token"] == coolify_api_token.NAME
    assert TYPES[coolify_api_token.NAME].capabilities == TYPES["bitbucket"].capabilities
    assert coolify_api_token.PLAN == derive(TYPES["bitbucket"].capabilities, (), "", "bws-secret")
    assert [segment.name for segment in coolify_api_token.PLAN.segments] == [
        "mint",
        "store",
        "verify",
        "revoke",
        "confirm",
    ]
    adapter = ADAPTERS[coolify_api_token.NAME]
    assert adapter.provider_name == "coolify"
    assert adapter.revoke_may_come_first
    assert adapter.check_row is coolify_api_token.check_row


def test_a_rotation_proves_stores_and_waits_for_the_person_s_revoke() -> None:
    coolify = Coolify()
    old, new = coolify.add(*EVERY), coolify.add(*EVERY)
    rotating = Rotating(coolify, EVERY, old)
    rotating.stage(new)
    rotating.run("store")
    [(_, verified)] = [(s, e) for s, e in rotating.filed if s.kind == "verify_new"]
    assert verified.note == (
        "scope: write held (422), deploy held (400), "
        "read:sensitive absent (the canary's env list carries no value field)"
    )
    rotating.run("verify")
    # Before the person revokes, confirm-dead finds the old token alive and stops.
    with pytest.raises(ContractError, match="probed live"):
        rotating.run("confirm")
    del coolify.tokens[old.reveal()]
    rotating.run("confirm")
    assert rotating.store.secrets.keys() == {rotating.keeper}
    assert rotating.store.read(rotating.keeper).same_as(new)


def test_no_evidence_or_refusal_carries_a_value() -> None:
    coolify = Coolify()
    old, new = coolify.add(*EVERY), coolify.add(*EVERY)
    rotating = Rotating(coolify, EVERY, old)
    rotating.stage(new)
    rotating.run("store")
    for _, evidence in rotating.filed:
        text = repr(evidence)
        assert old.reveal() not in text
        assert new.reveal() not in text
        assert "visible" not in text


@pytest.mark.parametrize(
    ("declared", "minted", "refusal"),
    [
        (
            EVERY,
            ["read", "deploy"],
            "declares write, so its probe must answer 422; it answered HTTP 403",
        ),
        (
            EVERY,
            ["read", "write"],
            "declares deploy, so its probe must answer 400; it answered HTTP 403",
        ),
        (["read"], ["read", "write"], "doesn't declare write, so its probe must answer 403"),
        (["read"], ["read", "deploy"], "doesn't declare deploy, so its probe must answer 403"),
        (["read"], ["read", "read:sensitive"], "reads sensitive values"),
        (EVERY, [*EVERY, "read:sensitive"], "reads sensitive values"),
        (EVERY, ["root"], "reads sensitive values"),
    ],
)
def test_verify_new_refuses_a_mint_whose_scope_isn_t_the_row_s(
    declared: list[str], minted: list[str], refusal: str
) -> None:
    coolify = Coolify()
    old, new = coolify.add(*declared), coolify.add(*minted)
    rotating = Rotating(coolify, declared, old)
    rotating.stage(new)
    with pytest.raises(ContractError, match=refusal):
        rotating.run("store")
    # Nothing reached the keeper.
    assert rotating.store.read(rotating.keeper).same_as(old)


def test_the_read_only_token_passes_with_both_probes_refused() -> None:
    coolify = Coolify()
    new = coolify.add("read")
    note = _provider(coolify, ["read"]).check_scope(new)
    assert note.startswith("scope: write absent (403), deploy absent (403)")


def test_scope_is_checked_only_after_the_value_answers_live() -> None:
    coolify = Coolify()
    old = coolify.add(*EVERY)
    rotating = Rotating(coolify, EVERY, old)
    rotating.stage(Value(_token()))
    with pytest.raises(ContractError, match="probed dead"):
        rotating.run("store")
    paths = {request.url.path for request in coolify.requests}
    assert paths == {"/api/v1/version"}


def test_with_no_canary_configured_verify_new_refuses_by_name() -> None:
    coolify = Coolify()
    new = coolify.add(*EVERY)
    provider = _provider(coolify, canary=None)
    with pytest.raises(ContractError, match="no coolify_token_canary_application is configured"):
        provider.check_scope(new)
    # The rest of the provider still works: the inbox and confirm-dead need no canary.
    assert provider.probe(new) == 200


@pytest.mark.parametrize(
    ("envs", "refusal"),
    [
        ([], "is empty"),
        ({"key": "SDS_CANARY"}, "isn't a list"),
        (["SDS_CANARY"], "isn't an object"),
    ],
)
def test_a_canary_list_that_can_t_discriminate_is_refused(envs: Any, refusal: str) -> None:
    coolify = Coolify(canary_envs=envs)
    with pytest.raises(ContractError, match=refusal):
        _provider(coolify).check_scope(coolify.add(*EVERY))


def test_a_canary_that_isn_t_there_is_refused_with_its_status() -> None:
    coolify = Coolify(canary_missing=True)
    with pytest.raises(ContractError, match="answered HTTP 404"):
        _provider(coolify).check_scope(coolify.add(*EVERY))


def test_a_scope_probe_that_gets_no_answer_fails_by_type_alone() -> None:
    coolify = Coolify()
    new = coolify.add(*EVERY)
    provider = _provider(coolify)
    coolify.down = True
    with pytest.raises(ContractError, match=r"the write probe got no answer \(ConnectError\)"):
        provider.check_scope(new)
    assert provider.probe(new) is None


def test_the_control_has_the_token_shape_and_is_refused() -> None:
    coolify = Coolify()
    provider = _provider(coolify)
    control = provider.known_bad()
    identifier, _, secret = control.reveal().partition("|")
    assert identifier.isdigit() and len(secret) >= 40
    assert provider.probe(control) == 401
    assert provider.known_bad().reveal() != control.reveal()


def test_identity_is_the_team_and_both_values_must_answer() -> None:
    coolify = Coolify()
    provider = _provider(coolify)
    assert isinstance(provider, ConfirmsIdentity)
    old, new = coolify.add(*EVERY), coolify.add("read")
    assert provider.same_identity(new, old)
    assert not provider.same_identity(coolify.add(*EVERY, team=7), old)
    # After an early revoke the old value no longer answers, so the value is staged by hand.
    del coolify.tokens[old.reveal()]
    assert not provider.same_identity(new, old)


def test_the_client_permits_only_the_five_requests() -> None:
    coolify = Coolify()
    provider = _provider(coolify)
    value = coolify.add(*EVERY)
    for method, path in [
        ("GET", "/api/v1/applications"),
        ("GET", "/api/v1/applications/other/envs"),
        ("PATCH", "/api/v1/projects/another"),
        ("POST", "/api/v1/deploy"),
        ("GET", "/api/v1/security/keys"),
    ]:
        with pytest.raises(ContractError, match="the probe may not"):
            provider._send(value, method, path)
    assert coolify.requests == []


def test_the_bearer_goes_only_to_https_or_the_tailnet() -> None:
    with pytest.raises(ContractError, match="tailnet"):
        coolify_api_token.build_provider(
            credential={"abilities": EVERY},
            coolify_url="http://coolify.example:8000",
            canary_application=CANARY,
        )
    with pytest.raises(ContractError, match="needs coolify_url"):
        coolify_api_token.build_provider(
            credential={"abilities": EVERY}, coolify_url=None, canary_application=CANARY
        )


def test_a_malformed_canary_uuid_is_refused_before_it_enters_a_path() -> None:
    with pytest.raises(ContractError, match="isn't a Coolify uuid"):
        coolify_api_token.build_provider(
            credential={"abilities": EVERY},
            coolify_url=COOLIFY_URL,
            canary_application="../../servers",
        )


@pytest.mark.parametrize(
    ("abilities", "refusal"),
    [
        (None, "declares its abilities"),
        ("read", "declares its abilities"),
        ([], "include read"),
        (["write", "deploy"], "include read"),
        (["read", "read"], "repeat an ability"),
        (["read", "read:sensitive"], "may not declare read:sensitive"),
        (["read", "root"], "may not declare root"),
    ],
)
def test_check_row_refuses_a_row_without_its_declared_abilities(
    abilities: Any, refusal: str
) -> None:
    credential = {} if abilities is None else {"abilities": abilities}
    with pytest.raises(ContractError, match=refusal):
        coolify_api_token.check_row(credential)


def test_mint_host_consumer_loaded_and_revoke_are_a_person_s() -> None:
    coolify = Coolify()
    provider = _provider(coolify)
    value = coolify.add(*EVERY)
    with pytest.raises(ContractError, match="person mints"):
        provider.mint()
    with pytest.raises(ContractError, match="no hosted service"):
        provider.host(value, value)
    with pytest.raises(ContractError, match="no hosted consumer"):
        provider.consumer_loaded({}, value)
    with pytest.raises(ContractError, match="person revokes"):
        provider.revoke(value)
    assert isinstance(provider, ScopeChecked)
    assert not provider.hosts_destinations


def test_the_canary_setting_is_the_vm_s_alone() -> None:
    assert "coolify_token_canary_application" in VM_ONLY
