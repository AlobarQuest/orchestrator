"""The OpenRouter adapter, against a local fake of OpenRouter: no test reaches the network.

The fake answers as increment 2 measured OpenRouter answering (`docs/superpowers/plans/
2026-10-09-rotation-increment-2-measurement-log.md`): 401 `User not found.` for a key it doesn't
hold, 400 for an `expires_at` in the `+00:00` form, a key's hash equal to sha256 of its value, and
key-info still answering 200 for a deleted key while inference refuses it at once.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from rotation_worker.adapters import ADAPTERS, AdapterContext
from rotation_worker.adapters import openrouter as adapter
from rotation_worker.contract import ContractError, Rotation, Step, UnitRun
from rotation_worker.evidence import StepEvidence
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

WORK = "work-project"
ADMIN = "admin-project"
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
FREE = {"prompt": "0", "completion": "0", "request": "0"}
PAID = {"prompt": "0.000001", "completion": "0.000002", "request": "0"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _new_key() -> str:
    return adapter.KEY_PREFIX + secrets.token_hex(32)


class FakeOpenRouter:
    """Keys by hash, each with its label, limit and expiry, and a log of every request."""

    def __init__(self) -> None:
        self.management = _new_key()
        self.keys: dict[str, dict[str, Any]] = {}
        # Deleted keys whose key-info still answers 200: OpenRouter's measured lag.
        self.lagging: dict[str, dict[str, Any]] = {}
        self.models: list[dict[str, Any]] = [
            {"id": "paid/model", "pricing": PAID},
            {"id": "busy/one:free", "pricing": FREE},
            {"id": "steady/two:free", "pricing": FREE},
        ]
        self.busy = {"busy/one:free"}
        self.requests: list[tuple[str, str, str]] = []
        self.asked_models: list[str] = []
        self.drop_limit = False
        self.stored_expiry: str | None = "as sent"
        self.mint_prefix = adapter.KEY_PREFIX
        self.delete_status = 200
        self.create_status = 201
        self.stored_limit: object = "as sent"

    def add(self, label: str = "sk-or-v1-abc...xyz") -> Value:
        key = _new_key()
        self.keys[_hash(key)] = {"label": label, "limit": None, "expires_at": None}
        return Value(key)

    def live(self, value: Value) -> bool:
        return _hash(value.reveal()) in self.keys

    def handle(self, request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        path = request.url.path.removeprefix("/api/v1")
        self.requests.append((request.method, path, _hash(bearer) if bearer else ""))
        if request.method == "GET" and path == "/models":
            return httpx.Response(200, json={"data": self.models})
        if request.method == "GET" and path == "/key":
            row = self.keys.get(_hash(bearer)) or self.lagging.get(_hash(bearer))
            if row is None:
                return _not_found()
            return httpx.Response(200, json={"data": {**row, "usage": 0}})
        if request.method == "POST" and path == "/chat/completions":
            model = json.loads(request.content)["model"]
            self.asked_models.append(model)
            if _hash(bearer) not in self.keys:
                return _not_found()
            if model in self.busy:
                return httpx.Response(429, json={"error": {"message": "rate limited"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        if bearer != self.management:
            return _not_found()
        if request.method == "POST" and path == "/keys":
            return self._create(json.loads(request.content))
        hashed = path.removeprefix("/keys/")
        if hashed not in self.keys:
            return httpx.Response(404, json={"error": {"message": "API key not found"}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": {**self.keys[hashed], "hash": hashed}})
        if request.method == "DELETE":
            if self.delete_status != 200:
                return httpx.Response(self.delete_status)
            self.lagging[hashed] = self.keys.pop(hashed)
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(405)

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        expires = body.get("expires_at")
        if not isinstance(expires, str) or not expires.endswith("Z"):
            return httpx.Response(400, json={"error": {"message": "Invalid request field"}})
        key = self.mint_prefix + secrets.token_hex(32)
        row = {
            "label": key[:12] + "..." + key[-3:],
            "limit": (
                None
                if self.drop_limit
                else body.get("limit")
                if self.stored_limit == "as sent"
                else self.stored_limit
            ),
            "expires_at": (
                expires.replace("Z", ".000Z")
                if self.stored_expiry == "as sent"
                else self.stored_expiry
            ),
            "name": body.get("name"),
        }
        self.keys[_hash(key)] = row
        return httpx.Response(
            self.create_status, json={"data": {**row, "hash": _hash(key)}, "key": key}
        )


def _not_found() -> httpx.Response:
    return httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}})


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def read(self, secret_id: str) -> Value:
        self.reads.append(secret_id)
        return super().read(secret_id)


class Rig:
    def __init__(self) -> None:
        self.server = FakeOpenRouter()
        self.store = CountingStore()
        self.management_id = self.store.put(ADMIN, "management", Value(self.server.management))
        self.provider = self.build()

    def build(self, transport: httpx.BaseTransport | None = None) -> adapter.OpenRouterProvider:
        return adapter.build(
            self.store,
            self.management_id,
            transport=transport or httpx.MockTransport(self.server.handle),
            clock=lambda: NOW,
        )

    def management_reads(self) -> int:
        return self.store.reads.count(self.management_id)


@pytest.fixture
def rig() -> Rig:
    return Rig()


# ---- the plan, end to end through the contract ------------------------------------------------


def test_the_openrouter_plan_rotates_end_to_end_while_key_info_lags(rig: Rig) -> None:
    original = rig.server.add()
    keeper = rig.store.put(WORK, "keeper", original)
    rotation = Rotation(
        "openrouter-generic",
        "2026-10-12-requested",
        keeper,
        WORK,
        (f'bws-secret uuid="{keeper}"', 'provider name="openrouter"'),
    )
    filed: list[tuple[Step, StepEvidence]] = []

    def run(unit: str) -> None:
        UnitRun(
            rotation=rotation,
            store=rig.store,
            provider=rig.build(),
            attestations=(),
            record=lambda step, evidence: filed.append((step, evidence)),
            before_step=lambda: None,
            filed=frozenset(step.kind for step, _ in filed),
        ).run(adapter.PLAN.unit(unit))

    run("mint")
    new = rig.store.read(keeper)
    assert not new.same_as(original) and rig.server.live(new) and rig.server.live(original)
    run("retire")

    assert not rig.server.live(original) and rig.server.live(new)
    # Key-info still answers 200 for the deleted key, yet confirm-dead proved it dead: it asks
    # inference, which refuses at once.
    assert _hash(original.reveal()) in rig.server.lagging
    assert rig.store.secrets.keys() == {keeper, rig.management_id}
    by_kind = {evidence.step: evidence for _, evidence in filed}
    retire = by_kind["retire_old"]
    assert [(p.target, p.subject, p.outcome) for p in retire.probes] == [
        ("openrouter.ai key-info", "known_bad", "dead"),
        ("openrouter.ai key-info", "old", "live"),
    ]
    assert retire.note is not None and "confirmed the id derived from it" in retire.note
    confirm = by_kind["confirm_dead"]
    assert {p.target for p in confirm.probes} == {adapter.OpenRouterProvider.probe_target}
    assert [(p.subject, p.outcome) for p in confirm.probes] == [
        ("known_bad", "dead"),
        ("old", "dead"),
        ("known_bad", "dead"),
        ("new", "live"),
    ]


# ---- mint --------------------------------------------------------------------------------------


def test_mint_asks_for_a_limit_and_a_z_form_expiry_400_days_out(rig: Rig) -> None:
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/keys") and request.method == "POST":
            sent.append(json.loads(request.content))
        return rig.server.handle(request)

    value = rig.build(httpx.MockTransport(capture)).mint()
    assert rig.server.live(value)
    assert sent == [
        {
            "name": "rotation-executor 2026-10-12",
            "limit": 5,
            "expires_at": "2027-11-16T15:00:00Z",
        }
    ]
    # Only the management key may mint.
    assert rig.server.requests[-1] == ("POST", "/keys", _hash(rig.server.management))


def test_a_minted_key_without_its_limit_is_deleted_and_the_mint_refused(rig: Rig) -> None:
    rig.server.drop_limit = True
    with pytest.raises(ContractError, match="lacked its credit limit or expiry"):
        rig.provider.mint()
    assert rig.server.keys == {}
    assert rig.server.requests[-1][0] == "DELETE"


@pytest.mark.parametrize("stored", [None, "2026-11-11T15:00:00.000Z"])
def test_a_minted_key_without_its_expiry_is_deleted_and_the_mint_refused(
    rig: Rig, stored: str | None
) -> None:
    rig.server.stored_expiry = stored
    with pytest.raises(ContractError, match="lacked its credit limit or expiry"):
        rig.provider.mint()
    assert rig.server.keys == {}


def test_a_failed_delete_of_a_too_strong_key_is_named_in_the_refusal(rig: Rig) -> None:
    rig.server.drop_limit = True
    rig.server.delete_status = 500
    with pytest.raises(ContractError, match="deleting it answered 500"):
        rig.provider.mint()


@pytest.mark.parametrize(("status", "limit"), [(200, "as sent"), (201, "5"), (201, 5.0)])
def test_a_mint_answered_200_or_with_a_string_limit_is_accepted(
    rig: Rig, status: int, limit: object
) -> None:
    rig.server.create_status = status
    rig.server.stored_limit = limit
    assert rig.server.live(rig.provider.mint())


def test_an_expiry_stored_without_a_zone_is_read_as_utc(rig: Rig) -> None:
    rig.server.stored_expiry = "2027-11-16T15:00:00"
    assert rig.server.live(rig.provider.mint())


def test_a_minted_key_of_another_shape_is_refused(rig: Rig) -> None:
    rig.server.mint_prefix = "sk-other-"
    with pytest.raises(ContractError, match="no key of the expected shape"):
        rig.provider.mint()


def test_a_mint_the_provider_refuses_is_a_refusal(rig: Rig) -> None:
    rig.server.management = _new_key()
    with pytest.raises(ContractError, match="minting a key answered 401"):
        rig.provider.mint()


# ---- probes ------------------------------------------------------------------------------------


def test_a_busy_free_model_is_skipped_and_a_paid_one_never_asked(rig: Rig) -> None:
    value = rig.server.add()
    assert rig.provider.probe(value) == 200
    asked = [r for r in rig.server.requests if r[1] == "/chat/completions"]
    assert len(asked) == 2
    assert rig.provider.probe(rig.provider.known_bad()) == 401


def test_only_a_free_suffixed_model_priced_at_zero_is_asked(rig: Rig) -> None:
    rig.server.models = [
        {"id": "zero/priced", "pricing": FREE},
        {"id": "priced/anyway:free", "pricing": PAID},
        {"id": "steady/two:free", "pricing": FREE},
    ]
    rig.server.busy = set()
    assert rig.provider.probe(rig.server.add()) == 200
    assert rig.server.asked_models == ["steady/two:free"]


def test_a_model_that_states_no_prompt_price_is_not_asked(rig: Rig) -> None:
    rig.server.models = [
        {"id": "unpriced/one:free", "pricing": {"completion": "0"}},
        {"id": "steady/two:free", "pricing": {"prompt": "0", "completion": "0"}},
    ]
    rig.server.busy = set()
    assert rig.provider.probe(rig.server.add()) == 200
    assert rig.server.asked_models == ["steady/two:free"]


def test_a_probe_with_no_determinate_answer_is_indeterminate_and_bounded(rig: Rig) -> None:
    rig.server.models = [{"id": f"m{i}:free", "pricing": FREE} for i in range(6)]
    rig.server.busy = {model["id"] for model in rig.server.models}
    assert rig.provider.probe(rig.server.add()) is None
    asked = [r for r in rig.server.requests if r[1] == "/chat/completions"]
    assert len(asked) == adapter.MAX_MODELS_TRIED


def test_a_probe_that_gets_no_answer_is_indeterminate(rig: Rig) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    provider = rig.build(httpx.MockTransport(down))
    assert provider.probe(rig.server.add()) is None
    assert provider.probe_before_revoke(rig.server.add()) is None


def test_the_probe_before_revoke_asks_key_info(rig: Rig) -> None:
    value = rig.server.add()
    assert rig.provider.probe_before_revoke(value) == 200
    assert rig.server.requests[-1] == ("GET", "/key", _hash(value.reveal()))
    assert rig.provider.probe_before_revoke(rig.provider.known_bad()) == 401


# ---- revoke ------------------------------------------------------------------------------------


def test_revoke_deletes_the_key_whose_derived_id_the_provider_confirms(rig: Rig) -> None:
    other = rig.server.add()
    old = rig.server.add()
    rig.provider.revoke(old)
    assert not rig.server.live(old) and rig.server.live(other)
    assert [r[:2] for r in rig.server.requests] == [
        ("GET", "/key"),
        ("GET", f"/keys/{_hash(old.reveal())}"),
        ("DELETE", f"/keys/{_hash(old.reveal())}"),
    ]


def test_revoke_refuses_when_key_info_does_not_answer_200(rig: Rig) -> None:
    with pytest.raises(ContractError, match="key-info for the old value answered 401"):
        rig.provider.revoke(Value(_new_key()))
    assert not any(r[0] == "DELETE" for r in rig.server.requests)


def test_revoke_refuses_when_a_live_key_has_no_key_at_its_derived_id(rig: Rig) -> None:
    old = rig.server.add()
    original = rig.server.handle

    def missing(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith(_hash(old.reveal())):
            return httpx.Response(404, json={"error": {"message": "API key not found"}})
        return original(request)

    with pytest.raises(ContractError, match="no key with the derived id"):
        rig.build(httpx.MockTransport(missing)).revoke(old)
    assert rig.server.live(old)
    assert not any(r[0] == "DELETE" for r in rig.server.requests)


def test_a_second_revoke_inside_key_info_s_lag_files_already_revoked(rig: Rig) -> None:
    old = rig.server.add()
    rig.provider.revoke(old)
    assert rig.provider.probe_before_revoke(old) == 200
    note = rig.provider.revoke(old)
    assert "already revoked" in note
    assert [r[0] for r in rig.server.requests].count("DELETE") == 1


def test_already_revoked_needs_inference_to_refuse_its_control_too(rig: Rig) -> None:
    old = rig.server.add()
    rig.provider.revoke(old)
    original = rig.server.handle

    def accepts_the_malformed_key(request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("Authorization", "")
        if request.url.path.endswith("/chat/completions") and adapter.MALFORMED_KEY in bearer:
            return httpx.Response(200, json={"choices": []})
        return original(request)

    with pytest.raises(ContractError, match="no key with the derived id"):
        rig.build(httpx.MockTransport(accepts_the_malformed_key)).revoke(old)


def test_revoke_reads_the_management_key_once(rig: Rig) -> None:
    rig.provider.revoke(rig.server.add())
    assert rig.management_reads() == 1


def test_revoke_refuses_when_the_derived_id_s_label_differs(rig: Rig) -> None:
    old = rig.server.add(label="sk-or-v1-aaa...aaa")
    original = rig.server.handle

    def relabel(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.method == "GET" and request.url.path.endswith(_hash(old.reveal())):
            payload = response.json()
            payload["data"]["label"] = "sk-or-v1-bbb...bbb"
            return httpx.Response(200, json=payload)
        return response

    with pytest.raises(ContractError, match="isn't the old value's key"):
        rig.build(httpx.MockTransport(relabel)).revoke(old)
    assert rig.server.live(old)


def test_revoke_refuses_when_the_delete_is_not_accepted(rig: Rig) -> None:
    old = rig.server.add()
    rig.server.delete_status = 500
    with pytest.raises(ContractError, match="deleting the old key answered 500"):
        rig.provider.revoke(old)


def test_a_revoke_that_gets_no_answer_is_a_refusal_naming_only_the_error_type(rig: Rig) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"down {request.headers['Authorization']}")

    old = rig.server.add()
    with pytest.raises(ContractError) as refused:
        rig.build(httpx.MockTransport(down)).revoke(old)
    assert str(refused.value) == "reading key-info got no answer (ConnectError)"


# ---- the management key and the client's reach ------------------------------------------------


def test_the_management_key_is_read_only_when_a_step_mints_or_revokes(rig: Rig) -> None:
    value = rig.server.add()
    rig.provider.probe(value)
    rig.provider.probe_before_revoke(value)
    assert rig.management_reads() == 0
    rig.provider.mint()
    assert rig.management_reads() == 1


def test_the_adapter_refuses_to_build_without_its_management_key_id(tmp_path: Path) -> None:
    context = AdapterContext(tmp_path, MemoryStore(), admin_secret_id=None)
    with pytest.raises(ContractError, match="management key's id"):
        ADAPTERS["openrouter"].build(context)


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/key", True),
        ("GET", "/models", True),
        ("POST", "/chat/completions", True),
        ("POST", "/keys", True),
        ("GET", "/keys/" + "a" * 64, True),
        ("DELETE", "/keys/" + "a" * 64, True),
        ("GET", "/keys", False),
        ("PATCH", "/keys/" + "a" * 64, False),
        ("DELETE", "/keys/" + "a" * 63, False),
        ("DELETE", "/keys/" + "A" * 64, False),
        ("GET", "/credits", False),
        ("POST", "/key", False),
    ],
)
def test_the_client_may_send_only_what_the_adapter_needs(
    method: str, path: str, allowed: bool
) -> None:
    assert adapter.permits(method, path) is allowed
