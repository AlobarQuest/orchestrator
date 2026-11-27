"""The Anthropic API-key adapter, driven through the contract against a simulated Anthropic.

ADR-0055 amendment 36 and D-85. A person creates a key in the Console and pastes it (a human act),
so each test stages the new key by hand, then runs the machine units. The simulated Anthropic
answers `GET /v1/models` 200 for a key it issued, with the key's workspace in the
`anthropic-workspace-id` response header, and 401 for any other key unless a test names another
status: Anthropic documents 401 for a key that is "malformed, revoked, or expired", and a deleted
key's status is measured at the first real rotation (D-85 11 A). An unscoped key gets 400, as
Anthropic documents for a request naming no workspace. Every key is built at run time from random
characters, and none is committed.
"""

from __future__ import annotations

import logging
import secrets
import tomllib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from rotation_worker import worker
from rotation_worker.adapters import ADAPTERS, anthropic_key
from rotation_worker.contract import ContractError, Provider, Rotation, ScopeChecked, UnitRun
from rotation_worker.inbox import ConfirmsIdentity, NamesItsCredential, bind
from rotation_worker.plans import derive
from rotation_worker.types import CLASS_TYPES, TYPES
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

WORK = "work-project"
CREDENTIAL = "anthropic-change-window-key"
KEEPER_ID = "0e9ebf2d-5b62-4f81-a731-b50a00e0df18"
# Workspace ids built at run time in the `wrkspc_` shape, as Anthropic's are.
WORKSPACE = f"wrkspc_{secrets.token_hex(12)}"
OTHER_WORKSPACE = f"wrkspc_{secrets.token_hex(12)}"
# The length of a real key's body is not stated anywhere; any length works, since the control
# copies the old key's.
BODY_LENGTH = 95


def _key() -> str:
    """A key of the regular shape, random at run time: Anthropic never issued it."""
    body = "".join(secrets.choice(anthropic_key.KEY_ALPHABET) for _ in range(BODY_LENGTH))
    return anthropic_key.KEY_PREFIX + body


def _row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": CREDENTIAL,
        "class": "anthropic-api-key",
        "provider": "anthropic",
        "workspace_id": WORKSPACE,
        "consumer": [
            {"kind": "bws-secret", "uuid": KEEPER_ID},
            {
                "kind": "runtime-fetch",
                "repo": "orchestrator",
                "ref": "scripts/run-change-window.sh",
                "healthchecks_check": "Change Manager OPs Check",
            },
        ],
    }
    row.update(changes)
    return row


@dataclass
class Anthropic:
    """Anthropic's `GET /v1/models`, answering by which workspace issued the bearer."""

    # Key -> the workspace it is scoped to, or `None` for a key scoped to none.
    keys: dict[str, str | None] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    # How Anthropic answers a key it doesn't hold: 401 is documented for a malformed key, and the
    # status of a deleted one is measured at the first real rotation (D-85 11 A).
    refused: int = 401
    down: bool = False
    # A 200 that names no workspace, which the documentation doesn't rule out for this route.
    no_header: bool = False
    # A status Anthropic answers whatever the key (429, 5xx).
    every: int | None = None

    def issue(self, workspace: str | None = WORKSPACE) -> str:
        key = _key()
        self.keys[key] = workspace
        return key

    def delete(self, key: str) -> None:
        del self.keys[key]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no route", request=request)
        if self.every is not None:
            return httpx.Response(self.every)
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if bearer not in self.keys:
            return httpx.Response(self.refused, json={"type": "error"})
        workspace = self.keys[bearer] or request.headers.get("anthropic-workspace-id")
        if workspace is None:
            return httpx.Response(400, json={"type": "error"})
        headers = {} if self.no_header else {"anthropic-workspace-id": workspace}
        return httpx.Response(200, headers=headers, json={"data": [], "has_more": False})


class Rotating:
    """One rotation of the change window's key, with the old key in its keeper."""

    def __init__(self, anthropic: Anthropic, old: str, row: dict[str, Any] | None = None) -> None:
        self.anthropic = anthropic
        self.store = MemoryStore()
        self.keeper = self.store.put(WORK, "change-window-anthropic-key", Value(old))
        self.rotation = Rotation(
            CREDENTIAL,
            "2026-11-27-requested",
            self.keeper,
            WORK,
            (f'bws-secret uuid="{self.keeper}"', 'provider name="anthropic"'),
        )
        self.provider = anthropic_key.build_provider(
            rotation=self.rotation,
            credential=row or _row(),
            store=self.store,
            transport=httpx.MockTransport(anthropic.handle),
        )
        self.filed: list[tuple[Any, Any]] = []
        # The keys Anthropic issued for this rotation, so the controls can be told apart.
        self.issued: set[str] = {old}

    def stage(self, new: str) -> None:
        self.store.put(WORK, self.rotation.staging_key, Value(new))

    def run(self, unit: str) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=lambda: None,
            filed=frozenset(step.kind for step, _ in self.filed),
        ).run(anthropic_key.PLAN.unit(unit))

    def evidence(self, kind: str) -> list[Any]:
        return [evidence for step, evidence in self.filed if step.kind == kind]

    def controls(self) -> list[str]:
        """Every bearer sent that Anthropic never issued: the known-bad controls."""
        sent = [r.headers["Authorization"].removeprefix("Bearer ") for r in self.anthropic.requests]
        return [bearer for bearer in sent if bearer not in self.issued]


def _rotating(anthropic: Anthropic | None = None) -> tuple[Rotating, str, str]:
    anthropic = anthropic or Anthropic()
    old, new = anthropic.issue(), anthropic.issue()
    rotating = Rotating(anthropic, old)
    rotating.issued.add(new)
    rotating.stage(new)
    return rotating, old, new


# ---- the type -------------------------------------------------------------------------------


def test_the_type_is_bitbuckets_shape_a_person_mints_and_deletes() -> None:
    assert CLASS_TYPES["anthropic-api-key"] == anthropic_key.NAME
    assert TYPES[anthropic_key.NAME].capabilities == TYPES["bitbucket"].capabilities
    assert TYPES[anthropic_key.NAME].store_kind == "bws-secret"
    assert anthropic_key.PLAN == derive(TYPES["bitbucket"].capabilities, (), "", "bws-secret")
    assert [segment.name for segment in anthropic_key.PLAN.segments] == [
        "mint",
        "store",
        "verify",
        "revoke",
        "confirm",
    ]
    adapter = ADAPTERS[anthropic_key.NAME]
    assert adapter.provider_name == "anthropic"
    assert adapter.revoke_may_come_first
    assert adapter.check_row is anthropic_key.check_row
    provider = _rotating()[0].provider
    assert isinstance(provider, ConfirmsIdentity)
    assert isinstance(provider, worker.GuidesTheRevoke)
    # The workspace check is in the probe; nothing else is probed, and the inbox binds only while
    # no other rotation waits on a mint.
    assert not isinstance(provider, ScopeChecked)
    assert not isinstance(provider, NamesItsCredential)


# ---- a rotation -----------------------------------------------------------------------------


def test_a_rotation_proves_stores_and_waits_for_the_person_s_deletion() -> None:
    rotating, old, new = _rotating()
    rotating.run("store")
    [verified] = rotating.evidence("verify_new")
    # D-85 11 A: the never-issued control's status is recorded before anything is stored.
    control, answer = verified.probes
    assert (control.subject, control.http_status, control.outcome) == ("known_bad", 401, "dead")
    assert (answer.http_status, answer.outcome) == (200, "live")
    rotating.run("verify")
    # Before the person deletes the old key, confirm-dead finds it alive and stops.
    with pytest.raises(ContractError, match="probed live"):
        rotating.run("confirm")
    rotating.anthropic.delete(old)
    rotating.run("confirm")
    # The deleted key's status is recorded at confirm.
    [*_, confirmed] = rotating.evidence("confirm_dead")
    assert [(p.subject, p.http_status) for p in confirmed.probes] == [
        ("known_bad", 401),
        ("old", 401),
        ("known_bad", 401),
        ("new", 200),
    ]
    assert rotating.store.secrets.keys() == {rotating.keeper}
    assert rotating.store.read(rotating.keeper).same_as(Value(new))


@pytest.mark.parametrize("status", [400, 403, 404])
def test_a_control_anthropic_doesn_t_refuse_with_401_fails_verify_new_by_its_status(
    status: int,
) -> None:
    rotating, old, _ = _rotating(Anthropic(refused=status))
    with pytest.raises(ContractError, match=f"didn't refuse a known-bad value \\({status}\\)"):
        rotating.run("store")
    assert rotating.store.read(rotating.keeper).same_as(Value(old))


@pytest.mark.parametrize("status", [403, 404])
def test_a_deleted_key_anthropic_doesn_t_refuse_with_401_fails_confirm_by_its_status(
    status: int,
) -> None:
    """D-85 11 A: confirm fails closed on anything but 401, naming the status, and nothing is
    revoked or deleted by machine."""
    rotating, old, _ = _rotating()
    rotating.run("store")
    rotating.run("verify")
    rotating.anthropic.delete(old)
    rotating.anthropic.refused = status
    with pytest.raises(ContractError, match=f"\\({status}\\)"):
        rotating.run("confirm")
    assert rotating.store.find(WORK, rotating.rotation.quarantine_key) is not None


def test_a_key_from_another_workspace_staged_by_hand_is_refused_by_name() -> None:
    anthropic = Anthropic()
    old, other = anthropic.issue(), anthropic.issue(OTHER_WORKSPACE)
    rotating = Rotating(anthropic, old)
    rotating.stage(other)
    with pytest.raises(
        ContractError,
        match=f"answered 200 for workspace {OTHER_WORKSPACE}, not the row's {WORKSPACE}$",
    ):
        rotating.run("store")
    assert rotating.store.read(rotating.keeper).same_as(Value(old))
    assert rotating.evidence("verify_new") == []


def test_a_200_naming_no_workspace_is_refused_and_nothing_is_stored() -> None:
    rotating, old, _ = _rotating()
    rotating.anthropic.no_header = True
    with pytest.raises(
        ContractError, match=f"with no anthropic-workspace-id header, not the row's {WORKSPACE}$"
    ):
        rotating.run("store")
    assert rotating.store.read(rotating.keeper).same_as(Value(old))


def test_a_malformed_workspace_header_is_never_echoed() -> None:
    rotating, _, new = _rotating()
    rotating.anthropic.keys[new] = "x" * 40
    with pytest.raises(ContractError, match="for workspace \\(a malformed id\\)") as refused:
        rotating.provider.probe(Value(new))
    assert "x" * 40 not in str(refused.value)


def test_an_unscoped_key_never_proves_live() -> None:
    """The probe names no workspace, so a key scoped to none (which could call the Admin API) gets
    400, which is indeterminate: the step fails and nothing is stored."""
    anthropic = Anthropic()
    old, unscoped = anthropic.issue(), anthropic.issue(None)
    rotating = Rotating(anthropic, old)
    rotating.stage(unscoped)
    with pytest.raises(ContractError, match="the new value probed indeterminate \\(400\\)"):
        rotating.run("store")
    assert all("anthropic-workspace-id" not in r.headers for r in anthropic.requests)
    assert rotating.store.read(rotating.keeper).same_as(Value(old))


@pytest.mark.parametrize("status", [429, 500, 529])
def test_a_rate_limit_or_an_outage_is_indeterminate_and_fails_the_step(status: int) -> None:
    rotating, old, new = _rotating()
    rotating.anthropic.every = status
    assert rotating.provider.probe(Value(new)) == status
    with pytest.raises(ContractError, match=f"didn't refuse a known-bad value \\({status}\\)"):
        rotating.run("store")
    assert rotating.store.read(rotating.keeper).same_as(Value(old))


def test_no_answer_is_indeterminate_and_fails_the_step() -> None:
    rotating, old, new = _rotating()
    assert rotating.provider.probe(Value(new)) == 200
    rotating.anthropic.down = True
    assert rotating.provider.probe(Value(new)) is None
    with pytest.raises(ContractError, match="didn't refuse a known-bad value \\(None\\)"):
        rotating.run("store")
    assert rotating.store.read(rotating.keeper).same_as(Value(old))


def test_no_evidence_refusal_or_log_line_carries_a_key_or_a_control(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rotating, old, new = _rotating()
    refusals = []
    rotating.run("store")
    rotating.run("verify")
    for change in ({"refused": 403}, {"no_header": True}, {"down": True}):
        for name, value in change.items():
            setattr(rotating.anthropic, name, value)
        try:
            rotating.run("confirm")
        except ContractError as error:
            refusals.append(str(error))
    assert len(refusals) == 3
    controls = rotating.controls()
    assert controls
    text = " ".join([*(repr(evidence) for _, evidence in rotating.filed), *refusals, caplog.text])
    for form in (old, new, *controls):
        assert form not in text
        assert form[len(anthropic_key.KEY_PREFIX) :][:16] not in text


# ---- the probe's request and the control ----------------------------------------------------


def test_the_probe_sends_the_key_as_a_bearer_with_a_version_and_nothing_else() -> None:
    rotating, _, new = _rotating()
    assert rotating.provider.probe(Value(new)) == 200
    [request] = rotating.anthropic.requests
    assert (request.method, str(request.url)) == ("GET", "https://api.anthropic.com/v1/models")
    assert request.headers["Authorization"] == f"Bearer {new}"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in request.headers
    assert "anthropic-workspace-id" not in request.headers
    assert request.content == b""


def test_the_client_permits_only_get_v1_models() -> None:
    """D-85 7 A: the executor sends the key to `GET https://api.anthropic.com/v1/models` alone."""
    rotating, _, _ = _rotating()
    client = rotating.provider._client
    for method, path in (
        ("POST", "/v1/models"),
        ("DELETE", "/v1/models"),
        ("GET", "/v1/messages"),
        ("POST", "/v1/messages"),
        ("GET", "/v1/organizations/api_keys"),
        ("POST", "/v1/organizations/api_keys/k"),
        ("GET", "/v1/models/claude"),
        ("GET", "/v1/models/"),
        ("GET", "/v1/models?limit=1"),
        ("GET", "https://evil.example/v1/models"),
        ("GET", "//evil.example/v1/models"),
    ):
        with pytest.raises(ContractError):
            client.request(method, path)
    assert rotating.anthropic.requests == []
    client.request("GET", "/v1/models")
    assert [r.url.host for r in rotating.anthropic.requests] == ["api.anthropic.com"]


def test_the_control_has_the_key_s_shape_and_is_new_each_time() -> None:
    rotating, old, _ = _rotating()
    first, second = rotating.provider.known_bad().reveal(), rotating.provider.known_bad().reveal()
    for control in (first, second):
        assert control.startswith(anthropic_key.KEY_PREFIX)
        assert len(control) == len(old)
        assert set(control[len(anthropic_key.KEY_PREFIX) :]) <= set(anthropic_key.KEY_ALPHABET)
        assert control not in rotating.anthropic.keys
    assert first != second


# ---- identity, the inbox and the revoke alert -----------------------------------------------


def test_identity_is_both_keys_answering_in_the_row_s_workspace() -> None:
    rotating, old, new = _rotating()
    assert rotating.provider.same_identity(Value(new), Value(old))
    other = rotating.anthropic.issue(OTHER_WORKSPACE)
    with pytest.raises(ContractError, match=f"for workspace {OTHER_WORKSPACE}"):
        rotating.provider.same_identity(Value(other), Value(old))
    # The old key no longer answering means the new one can't be told to be its successor.
    rotating.anthropic.delete(old)
    assert not rotating.provider.same_identity(Value(new), Value(old))


def _unstage(rotating: Rotating) -> None:
    staged = rotating.store.find(WORK, rotating.rotation.staging_key)
    assert staged is not None
    rotating.store.delete(staged.id)


def _bind(rotating: Rotating, value: str, others_waiting: list[str] | None = None) -> str | None:
    return bind(
        Value(value),
        rotation=rotating.rotation,
        store=rotating.store,
        provider=cast(Provider, rotating.provider),
        others_waiting=others_waiting or [],
        other_keepers=list,
    )


def test_the_inbox_binds_a_key_from_the_row_s_workspace() -> None:
    rotating, _, new = _rotating()
    _unstage(rotating)
    assert _bind(rotating, new) is None
    staged = rotating.store.find(WORK, rotating.rotation.staging_key)
    assert staged is not None and rotating.store.read(staged.id).same_as(Value(new))


def test_the_inbox_never_binds_a_key_from_another_workspace_or_an_unscoped_one() -> None:
    rotating, _, _ = _rotating()
    _unstage(rotating)
    with pytest.raises(ContractError, match=f"for workspace {OTHER_WORKSPACE}"):
        _bind(rotating, rotating.anthropic.issue(OTHER_WORKSPACE))
    assert _bind(rotating, rotating.anthropic.issue(None)) == (
        "the provider doesn't accept it beside a refused known-bad value"
    )
    assert rotating.store.find(WORK, rotating.rotation.staging_key) is None


def test_the_inbox_binds_nothing_while_another_rotation_waits_on_a_mint() -> None:
    rotating, _, new = _rotating()
    _unstage(rotating)
    reason = _bind(rotating, new, others_waiting=["coolify-store"])
    assert reason is not None and reason.startswith("another rotation waits on a person's mint")


def test_the_revoke_alert_says_delete_not_disable_and_names_the_day_stored() -> None:
    rotating, _, new = _rotating()
    rotating.store.revisions[rotating.keeper] = datetime(2026, 12, 3, 23, 59, tzinfo=UTC)
    guide = rotating.provider.revoke_guide(Value(new))
    assert f"in workspace {WORKSPACE}, Delete (not Disable" in guide
    assert "the executor stored on 2026-12-03 (UTC)" in guide
    assert "a key made by hand and never pasted: delete that too" in guide
    assert new not in guide


def test_the_worker_s_revoke_alert_is_the_adapter_s() -> None:
    rotating, _, new = _rotating()
    rotating.run("store")
    setup = cast(worker.Setup, SimpleNamespace(store=rotating.store))
    provider = cast(Provider, rotating.provider)
    guide = worker._revoke_guide(setup, provider, rotating.rotation, "u1")
    assert guide == rotating.provider.revoke_guide(Value(new))
    unreadable = replace(rotating.rotation, keeper_id="no-such-secret")
    fallback = worker._revoke_guide(setup, provider, unreadable, "u1")
    assert fallback.startswith(worker._REVOKE_GUIDE)


def test_mint_host_consumer_loaded_and_revoke_are_a_person_s() -> None:
    """D-85 10 A: no Admin API key, so the adapter never lists, disables or deletes a key."""
    provider = _rotating()[0].provider
    value = Value(_key())
    for call in (
        provider.mint,
        lambda: provider.host(value, value),
        lambda: provider.consumer_loaded({}, value),
        lambda: provider.revoke(value),
    ):
        with pytest.raises(ContractError):
            call()


# ---- the registry row -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"workspace_id": None}, "declares its workspace_id"),
        ({"workspace_id": "sds-change-window"}, "declares its workspace_id"),
        ({"workspace_id": "wrkspc_"}, "declares its workspace_id"),
        ({"workspace_id": "wrkspc_a b"}, "declares its workspace_id"),
        ({"consumer": []}, "exactly one bws-secret keeper"),
        (
            {"consumer": [{"kind": "bws-secret", "uuid": KEEPER_ID}]},
            "at least one runtime-fetch reader",
        ),
        (
            {
                "consumer": [
                    {"kind": "bws-secret", "uuid": KEEPER_ID},
                    {"kind": "runtime-fetch", "ref": "r"},
                ]
            },
            "names its check",
        ),
    ],
)
def test_check_row_refuses_a_row_the_adapter_couldn_t_act_on(
    changes: dict[str, Any], refusal: str
) -> None:
    with pytest.raises(ContractError, match=refusal):
        anthropic_key.check_row(_row(**changes))
    if "workspace_id" in changes:
        with pytest.raises(ContractError, match=refusal):
            anthropic_key.build_provider(
                rotation=_rotating()[0].rotation, credential=_row(**changes), store=MemoryStore()
            )


def test_check_row_refuses_factory_runner_s_shared_key_by_its_consumers() -> None:
    """Amendment 36 items 1 and 8: the type also covers factory-runner's shared row, whose
    consumers are Actions secrets, so `check_row` refuses it even with a workspace declared."""
    shared = _row(
        id="anthropic-api-key",
        consumer=[
            {"kind": "bws-secret", "uuid": KEEPER_ID},
            {"kind": "gh-actions-secret", "repo": "AlobarQuest/factory-runner", "name": "K"},
        ],
    )
    with pytest.raises(ContractError, match="may not list a gh-actions-secret consumer"):
        anthropic_key.check_row(shared)


def test_the_row_declares_no_workspace_yet_and_stays_out_of_the_sds_s_hands() -> None:
    """Exit check 1 reads the `workspace_id` from the current key, so until then the row is
    refused before any claim, and it stays on the manual checklist (D-85)."""
    registry = tomllib.loads(
        (Path(__file__).resolve().parents[2] / ".cred-consumers.toml").read_text()
    )
    [row] = [r for r in registry["credential"] if r.get("class") == "anthropic-api-key"]
    assert row["id"] == CREDENTIAL
    assert row.get("rotated_by_sds", False) is False
    assert "rotate_requested" not in row
    assert "workspace_id" not in row
    with pytest.raises(ContractError, match="declares its workspace_id"):
        anthropic_key.check_row(row)
    # Its consumers are the adapter's: with the workspace declared, nothing else refuses.
    anthropic_key.check_row({**row, "workspace_id": WORKSPACE})
