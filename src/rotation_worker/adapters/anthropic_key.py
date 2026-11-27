"""The Anthropic API-key adapter: a person mints and deletes, the executor proves (ADR-0055
amendment 36, D-85).

Anthropic has no API that creates or deletes an API key, and the executor holds no Admin API key
(D-85 10 A): a person creates a key in the Claude Console, scoped to the row's one workspace, and
pastes it into the inbox; the executor proves and stores it, waits unclaimed until every scheduled
reader has run since the keeper's revision, and, after the person deletes the old key in the
Console, proves the old key refused.

* **Probe** is `GET /v1/models` on `https://api.anthropic.com` with the key as `x-api-key` (the
  form Anthropic's API examples use for a regular key; `Authorization: Bearer` is for OAuth tokens)
  and an `anthropic-version` header, and nothing else: the client permits that one request (D-85
  7 A). Live is exactly 200. The known-bad control is a random value of the
  key's shape, the documented regular-key prefix and random characters of the old key's length
  from the key's alphabet, made in memory and discarded. Anthropic documents 401 for a key that is
  "malformed, revoked, or expired", so `dead_statuses` is `{401}`; the first real rotation measures
  a deleted key's status (D-85 11 A), and any other answer fails the step with its status.
* **Identity**: a 200 counts only when the response's `anthropic-workspace-id` header equals the
  row's `workspace_id`. A 200 without it, or naming another workspace, refuses by name in the
  probe itself, so verify-new refuses a key staged by hand as the inbox's `same_identity` does. The
  probe sends no `anthropic-workspace-id` request header, so a key not scoped to a workspace never
  binds whatever status it gets.
* **Mint, host, consumer_loaded and revoke** are never the executor's.
* **The one-off probe** (`probe_current`, `rotation-worker probe`) sends the same request with the
  key and with a control, once in each header form, so amendment 36's exit check 1 measures which
  form Anthropic accepts. It needs no declared workspace and builds no provider.

Nothing here logs, keeps or raises with a key or its header, and no response body is read: a
refusal names the request, the status, the workspace or the failure's type.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import ContractError, Rotation
from rotation_worker.plans import derive
from rotation_worker.secret_store import SecretStore
from rotation_worker.types import TYPES
from rotation_worker.values import Value

NAME: Final = "anthropic-key"
# The rows' `provider`: every registry row of this type names it.
PROVIDER: Final = "anthropic"
CLASS: Final = "anthropic-api-key"
API: Final = "https://api.anthropic.com"
MODELS_PATH: Final = "/v1/models"
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
HEADERS: Final = {"anthropic-version": "2023-06-01"}
# The two forms a key can be sent in. The rotation sends `x-api-key` alone; the one-off probe
# sends each in turn, never both in one request.
API_KEY_HEADER: Final = "x-api-key"
BEARER_HEADER: Final = "Authorization"
HEADER_FORMS: Final = (API_KEY_HEADER, BEARER_HEADER)
TIMEOUT_SECONDS: Final = 30.0
WORKSPACE_HEADER: Final = "anthropic-workspace-id"
WORKSPACE_FIELD: Final = "workspace_id"
WORKSPACE: Final = re.compile(r"^wrkspc_[A-Za-z0-9]{1,64}$")
# The documented regular-key prefix, which the control carries so it has the key's shape.
KEY_PREFIX: Final = "sk-ant-api03-"
KEY_ALPHABET: Final = string.ascii_letters + string.digits + "-_"
# The consumer kinds a row of this type may list (amendment 36 item 8).
KEEPER_KIND: Final = "bws-secret"
READER_KIND: Final = "runtime-fetch"
# This type has no packages from before derivation, so its one candidate is the derived plan of a
# credential with no copies to distribute.
PLAN: Final = derive(TYPES[NAME].capabilities, (), "", "bws-secret")


def declared_workspace(credential: Mapping[str, Any]) -> str:
    """The row's `workspace_id`, or a refusal."""
    workspace = credential.get(WORKSPACE_FIELD)
    if not isinstance(workspace, str) or not WORKSPACE.fullmatch(workspace):
        raise ContractError(f"a {CLASS} row declares its {WORKSPACE_FIELD}, a wrkspc_ id")
    return workspace


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row this adapter couldn't act on: one without its declared
    workspace, or whose consumers aren't one keeper and `runtime-fetch` readers, each read by its
    Healthchecks check."""
    declared_workspace(credential)
    consumers = credential.get("consumer")
    if not isinstance(consumers, list) or not all(isinstance(c, Mapping) for c in consumers):
        raise ContractError(f"a {CLASS} row lists its consumers")
    kinds = [consumer.get("kind") for consumer in consumers]
    if kinds.count(KEEPER_KIND) != 1:
        raise ContractError(f"a {CLASS} row has exactly one {KEEPER_KIND} keeper")
    others = sorted({str(kind) for kind in kinds} - {KEEPER_KIND, READER_KIND})
    if others:
        raise ContractError(f"a {CLASS} row may not list a {', '.join(others)} consumer")
    readers = [consumer for consumer in consumers if consumer.get("kind") == READER_KIND]
    if not readers:
        raise ContractError(f"a {CLASS} row lists at least one {READER_KIND} reader")
    for reader in readers:
        check = reader.get("healthchecks_check")
        if not isinstance(check, str) or not check:
            raise ContractError(f"each {READER_KIND} reader of a {CLASS} row names its check")


def confined_client(transport: httpx.BaseTransport | None = None) -> ConfinedClient:
    """The client every Anthropic request goes through: it permits `GET /v1/models` alone."""
    try:
        return ConfinedClient(
            base_url=API,
            user_agent=USER_AGENT,
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
            permits=lambda method, path: (method, path) == ("GET", MODELS_PATH),
            refuse=lambda method, path: ContractError(f"the probe may not {method} {path}"),
            transport=transport,
        )
    except TransportFailure as failure:
        raise ContractError(f"Anthropic's client couldn't be built: {failure.error_type}") from None


def control_like(key: Value) -> Value:
    """A key Anthropic never issued: the regular-key prefix, then random characters of the key's
    alphabet, as long as `key`."""
    length = max(len(key.reveal()) - len(KEY_PREFIX), 1)
    return Value(KEY_PREFIX + "".join(secrets.choice(KEY_ALPHABET) for _ in range(length)))


def models(
    client: ConfinedClient, value: Value, header: str = API_KEY_HEADER
) -> tuple[int, str | None]:
    """`GET /v1/models`'s status with `value` sent in the `header` form and, on 200, the workspace
    header it names. A request with no answer raises `TransportFailure`, which names its type."""
    if header not in HEADER_FORMS:
        raise ContractError(f"a key is sent as {' or '.join(HEADER_FORMS)}")
    sent = f"Bearer {value.reveal()}" if header == BEARER_HEADER else value.reveal()
    response = client.request("GET", MODELS_PATH, headers={header: sent})
    if response.status_code != 200:
        return response.status_code, None
    return 200, response.headers.get(WORKSPACE_HEADER)


def probe_current(key: Value, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """The one-off probe of a key already kept (amendment 36 exit check 1): `GET /v1/models` with
    the key and with a control of its shape, each once per header form. Statuses and the
    workspace header only; a request with no answer is `null` beside its failure's type."""
    client = confined_client(transport)
    control = control_like(key)
    found: dict[str, Any] = {}
    for subject, value in (("key", key), ("control", control)):
        for header in HEADER_FORMS:
            try:
                status, workspace = models(client, value, header)
                found[f"{subject}_{_slug(header)}"] = {
                    "status": status,
                    WORKSPACE_HEADER: None if workspace is None else _shown(workspace),
                }
            except TransportFailure as failure:
                found[f"{subject}_{_slug(header)}"] = {
                    "status": None,
                    "no_answer": failure.error_type,
                }
    return found


def _slug(header: str) -> str:
    return "bearer" if header == BEARER_HEADER else "x_api_key"


def build_provider(
    *,
    rotation: Rotation | None,
    credential: Mapping[str, Any] | None,
    store: SecretStore,
    transport: httpx.BaseTransport | None = None,
) -> AnthropicKeyProvider:
    if rotation is None or credential is None:
        raise ContractError("the anthropic-key adapter needs its rotation and registry row")
    workspace = declared_workspace(credential)

    def old() -> Value:
        # The value the new one replaces, read as the contract reads it for a rotation: the
        # quarantine once store has moved it there, the keeper before.
        quarantine = store.find(rotation.work_project_id, rotation.quarantine_key)
        return store.read(quarantine.id if quarantine is not None else rotation.keeper_id)

    return AnthropicKeyProvider(
        confined_client(transport),
        workspace=workspace,
        old=old,
        stored_on=lambda: store.revised(rotation.keeper_id),
    )


class AnthropicKeyProvider:
    name = PROVIDER
    probe_target = "Anthropic's GET /v1/models, with the key as x-api-key"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = False

    def __init__(
        self,
        client: ConfinedClient,
        *,
        workspace: str,
        old: Callable[[], Value],
        stored_on: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._old = old
        self._stored_on = stored_on

    # ---- the probe and the control ------------------------------------------------------------

    def mint(self) -> Value:
        raise ContractError("a person creates an Anthropic API key, in the Claude Console")

    def known_bad(self) -> Value:
        """A key Anthropic never issued, as long as the old key (`control_like`)."""
        return control_like(self._old())

    def probe(self, value: Value) -> int | None:
        status, workspace = self._models(value)
        if status == 200 and workspace != self._workspace:
            raise ContractError(
                f"GET {MODELS_PATH} answered 200 "
                + (
                    f"with no {WORKSPACE_HEADER} header"
                    if workspace is None
                    else f"for workspace {_shown(workspace)}"
                )
                + f", not the row's {self._workspace}"
            )
        return status

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def _models(self, value: Value) -> tuple[int | None, str | None]:
        """`models` with `value` as `x-api-key`; a request with no answer is a `None` status."""
        try:
            return models(self._client, value)
        except TransportFailure:
            return None, None

    # ---- identity ----------------------------------------------------------------------------

    def same_identity(self, new: Value, old: Value) -> bool:
        """Whether both keys answer `GET /v1/models` in the row's workspace (amendment 36 item 3);
        a 200 for another workspace, or naming none, refuses in the probe."""
        return self.probe(new) == 200 and self.probe(old) == 200

    # ---- the person's acts ----------------------------------------------------------------------

    def revoke_guide(self, new: Value) -> str:
        """The revoke alert's text (amendment 36 item 5), naming the day the new key was stored."""
        del new
        stored = self._stored_on().astimezone(UTC).date().isoformat()
        return (
            "Every reader has run with the new key. In the Claude Console, in workspace "
            f"{self._workspace}, Delete (not Disable: a disabled key can be re-enabled) every API "
            "key except the one created for this rotation, which the executor stored on "
            f"{stored} (UTC). The executor can't list the workspace's keys, so it can't see a key "
            "made by hand and never pasted: delete that too. There is nothing to record; the "
            "executor proves the old key refused."
        )

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("an Anthropic API key has no hosted service to host it")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("an Anthropic API key has no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person deletes an Anthropic API key, in the Claude Console")


def _shown(workspace: str) -> str:
    """A workspace id the response named, if it has a workspace id's shape: the header is the
    provider's text, never echoed otherwise."""
    return workspace if WORKSPACE.fullmatch(workspace) else "(a malformed id)"
