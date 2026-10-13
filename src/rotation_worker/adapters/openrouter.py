"""The OpenRouter adapter: the executor mints, verifies and revokes OpenRouter API keys.

ADR-0055 increment 5. The provider facts are amendment 1's, measured in increment 2:

* **Mint** with the management key (`POST /keys`). Every key carries a credit `limit` and
  an `expires_at` (decision 4), the expiry written in the `…Z` form: OpenRouter answers the
  `+00:00` form with 400. A key that comes back without either is deleted at once, and the mint
  refuses.
* **Probe** with a zero-cost chat completion on a `:free` model chosen at probe time. Live is
  exactly 200, and a malformed key, the known-bad control, answers 401 `User not found.`. A model
  that answers anything else (rate-limited, unavailable) is skipped for the next, up to
  `MAX_MODELS_TRIED`; with no determinate answer the probe is indeterminate and the rotation stops.
* **Probe before revoke** with key-info (`GET /key`). Key-info lags a revoke by up to about
  a minute, so it can prove the old key live before the revoke, which is the evidence amendment 1
  keeps, and never that it is dead after.
* **Revoke** by an id derived from the quarantined value and confirmed by the provider: sha256 of
  the value, then `GET /keys/{that hash}` with the management key must answer 200 with the
  same `label` key-info gives for the value. Only then `DELETE`. The id is never taken from
  evidence or from the create response, and a label alone never picks a key (T8).

The management key is account-wide (a provider limit). It lives in `Rotation / Admin`, which only
the executor's root reads, and is read from BWS by UUID only when a step mints or revokes.
Paths are relative to `BASE_URL`, OpenRouter's API root.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import (
    Actor,
    ContractError,
    Plan,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.secret_store import SecretStore
from rotation_worker.values import Value

NAME: Final = "openrouter"
BASE_URL: Final = "https://openrouter.ai/api/v1"
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
KEY_PREFIX: Final = "sk-or-v1-"
# A key of the right shape that no account holds: OpenRouter answers it 401 `User not found.`.
MALFORMED_KEY: Final = KEY_PREFIX + "0" * 64
# Decision 4 and Devon's 2026-10-12 answer: $5 of credit, and an expiry 400 days out, which
# outlasts the class's 365-day rotation age with about five weeks' grace.
CREDIT_LIMIT: Final = 5
LIFETIME: Final = timedelta(days=400)
MAX_MODELS_TRIED: Final = 3
DETERMINATE: Final = frozenset({200, 401})

KEY_INFO: Final = "/key"
MODELS: Final = "/models"
COMPLETIONS: Final = "/chat/completions"
KEYS: Final = "/keys"
_KEY_BY_HASH: Final = re.compile(r"^/keys/[0-9a-f]{64}$")

M = Actor.MACHINE
# Two units with Devon's completion click between them, so nothing is revoked until he has seen
# the new key stored. With no attested consumer there is no human act.
PLAN: Final = Plan(
    (
        UnitPlan(
            "mint",
            (
                Step(StepKind.MINT, M, "AC-001"),
                Step(StepKind.STAGE, M, "AC-002"),
                Step(StepKind.VERIFY_NEW, M, "AC-003"),
                Step(StepKind.STORE, M, "AC-004"),
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-005"),
            ),
        ),
        UnitPlan(
            "retire",
            (
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-006"),
                Step(StepKind.RETIRE_OLD, M, "AC-007"),
                Step(StepKind.CONFIRM_DEAD, M, "AC-008"),
            ),
        ),
    )
)
validate_plan(PLAN)


def permits(method: str, path: str) -> bool:
    if method == "GET":
        return path in (KEY_INFO, MODELS) or bool(_KEY_BY_HASH.match(path))
    if method == "POST":
        return path in (COMPLETIONS, KEYS)
    return method == "DELETE" and bool(_KEY_BY_HASH.match(path))


def _refuse(method: str, path: str) -> ContractError:
    # The path is left out: a key's path carries its hash, a 64-character run.
    return ContractError(f"the OpenRouter adapter may not send that {method}")


def build(
    store: SecretStore,
    management_key_id: str | None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> OpenRouterProvider:
    if management_key_id is None:
        raise ContractError("the openrouter adapter needs its management key's id in admin_secrets")
    client = ConfinedClient(
        base_url=BASE_URL,
        user_agent=USER_AGENT,
        headers={},
        timeout=TIMEOUT_SECONDS,
        permits=permits,
        refuse=_refuse,
        transport=transport,
    )
    return OpenRouterProvider(client, store, management_key_id, clock)


def _key_hash(value: Value) -> str:
    return hashlib.sha256(value.reveal().encode()).hexdigest()


def _bearer(value: Value) -> dict[str, str]:
    return {"Authorization": f"Bearer {value.reveal()}"}


class OpenRouterProvider:
    name = NAME
    probe_target = "openrouter.ai chat completion on a :free model"
    revoke_probe_target = "openrouter.ai key-info"
    dead_statuses = frozenset({401})

    def __init__(
        self,
        client: ConfinedClient,
        store: SecretStore,
        management_key_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._store = store
        self._management_key_id = management_key_id
        self._clock = clock
        self._free_models: list[str] | None = None

    # ---- the contract's provider ------------------------------------------------------------

    def mint(self) -> Value:
        now = self._clock().astimezone(UTC)
        expires_at = (now + LIFETIME).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "name": f"rotation-executor {now:%Y-%m-%d}",
            "limit": CREDIT_LIMIT,
            "expires_at": expires_at,
        }
        response = self._send("POST", KEYS, self._management(), "minting", json=body)
        if response.status_code not in (200, 201):
            raise ContractError(f"minting a key answered {response.status_code}")
        payload = _json(response)
        key, data = payload.get("key"), payload.get("data")
        if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
            raise ContractError("minting a key answered with no key of the expected shape")
        value = Value(key)
        if not isinstance(data, dict) or not _carries_limit_and_expiry(data, expires_at):
            # The key exists and is stronger than decision 4 allows: delete it before refusing.
            path = f"{KEYS}/{_key_hash(value)}"
            deleted = self._send("DELETE", path, self._management(), "deleting the minted key")
            raise ContractError(
                "the minted key lacked its credit limit or expiry; deleting it answered "
                f"{deleted.status_code}"
            )
        return value

    def probe(self, value: Value) -> int | None:
        for model in self._models()[:MAX_MODELS_TRIED]:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 1,
            }
            status = self._status("POST", COMPLETIONS, value, json=body)
            if status in DETERMINATE:
                return status
        return None

    def probe_before_revoke(self, value: Value) -> int | None:
        return self._status("GET", KEY_INFO, value)

    def known_bad(self) -> Value:
        return Value(MALFORMED_KEY)

    def revoke(self, value: Value) -> str:
        info = self._send("GET", KEY_INFO, value, "reading key-info")
        if info.status_code != 200:
            raise ContractError(f"key-info for the old value answered {info.status_code}")
        label = _label(_json(info))
        path = f"{KEYS}/{_key_hash(value)}"
        management = self._management()
        found = self._send("GET", path, management, "looking up the derived id")
        if found.status_code == 404 and self._refused_at_inference(value):
            # An earlier attempt deleted it, and key-info still lags the delete.
            return (
                "key-info answered 200 for the old value, but the provider holds no key with the "
                "id derived from it and inference refuses it beside a refused malformed key: an "
                "earlier attempt already revoked it"
            )
        if found.status_code != 200:
            raise ContractError(
                f"the provider holds no key with the derived id ({found.status_code})"
            )
        if not hmac.compare_digest(_label(_json(found)).encode(), label.encode()):
            raise ContractError("the key with the derived id isn't the old value's key")
        deleted = self._send("DELETE", path, management, "deleting")
        if deleted.status_code != 200:
            raise ContractError(f"deleting the old key answered {deleted.status_code}")
        return (
            "key-info answered 200 for the old value; the provider confirmed the id derived "
            "from it (same label) and deleted that key"
        )

    # ---- requests ---------------------------------------------------------------------------

    def _refused_at_inference(self, value: Value) -> bool:
        return self.probe(self.known_bad()) == 401 and self.probe(value) == 401

    def _management(self) -> Value:
        return self._store.read(self._management_key_id)

    def _models(self) -> list[str]:
        if self._free_models is None:
            try:
                response = self._client.request("GET", MODELS)
            except TransportFailure:
                return []
            if response.status_code != 200:
                return []
            models = _json(response).get("data")
            self._free_models = [
                str(model["id"])
                for model in (models if isinstance(models, list) else [])
                if isinstance(model, dict) and _is_free(model)
            ]
        return self._free_models

    def _status(self, method: str, path: str, value: Value, **kwargs: Any) -> int | None:
        try:
            return self._client.request(method, path, headers=_bearer(value), **kwargs).status_code
        except TransportFailure:
            return None

    def _send(
        self, method: str, path: str, value: Value, doing: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            return self._client.request(method, path, headers=_bearer(value), **kwargs)
        except TransportFailure as failure:
            raise ContractError(f"{doing} got no answer ({failure.error_type})") from None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _label(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    label = data.get("label") if isinstance(data, dict) else None
    if not isinstance(label, str) or not label:
        raise ContractError("the provider's answer carries no key label")
    return label


def _is_free(model: dict[str, Any]) -> bool:
    """A `:free` model whose prompt and completion are stated and zero; a per-request price may
    be absent."""
    pricing = model.get("pricing")
    if not str(model.get("id", "")).endswith(":free") or not isinstance(pricing, dict):
        return False
    stated = (pricing.get("prompt"), pricing.get("completion"), pricing.get("request", "0"))
    return all(_number(price) == 0 for price in stated)


def _number(text: object) -> float | None:
    try:
        return float(str(text))
    except ValueError:
        return None


def _carries_limit_and_expiry(data: dict[str, Any], expires_at: str) -> bool:
    try:
        stored = datetime.fromisoformat(str(data.get("expires_at")))
    except ValueError:
        return False
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    limit = _number(data.get("limit"))
    return limit == CREDIT_LIMIT and stored == datetime.fromisoformat(expires_at)
