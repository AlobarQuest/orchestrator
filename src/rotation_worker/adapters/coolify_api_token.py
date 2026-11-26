"""The Coolify API token adapter: a person mints and revokes, the executor proves (ADR-0055
amendment 33, D-82).

Production Coolify (`4.0.0-beta.473`) has no API route that creates, lists or revokes a token: a
signed-in person does both in the web UI. So a person mints into the inbox, the executor proves
and stores the value, waits unclaimed until every scheduled reader has run since the keeper's
revision, and, after the person's revoke, proves the old value dead. Every request goes to the
executor's own `coolify_url`, the tailnet address, with the probed value as its bearer.

* **Probe** is `GET /api/v1/version` (`read`): live is exactly 200. The known-bad control has a
  Sanctum token's shape, a numeric id, `|` and a random secret, which Coolify refuses with 401
  before any handler runs.
* **Identity** is the account check only: both values answer and `GET /api/v1/teams/current`
  names the same team. Every row's token is in the one team, so the inbox's own rules tell two
  rows apart, not this.
* **Scope** is checked in verify_new (`contract.ScopeChecked`), because Coolify never reads a
  token's abilities back. Ability middleware answers before the handler, so each probe either
  reaches a handler that refuses it without writing, or gets 403:
  - `write`: `PATCH /api/v1/projects/<a random UUID>` with only a field the handler doesn't
    allow. With `write`, 422 before any project is looked up; without, 403.
  - `deploy`: `GET /api/v1/deploy` with no `uuid` and no `tag`. With `deploy`, 400 and nothing
    deployed; without, 403.
  - `read:sensitive` absent (D-82 item 13 C): `GET /api/v1/applications/<canary>/envs`, the
    env list of a canary application holding one non-secret variable. Without
    `read:sensitive`, no entry carries `value` or `real_value`. A `root` token passes the
    first two probes as `read` + `write` + `deploy` does, and only this one catches it. The
    answer is checked in memory and never logged or kept. With no canary configured, verify_new
    refuses by name.
  These answers are read from Coolify's source at the tag and unmeasured until the dev Coolify
  measurement (D-82 item 14) runs.
* **Mint, host, consumer_loaded and revoke** are never the executor's (D-82 item 12 A).
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import ContractError
from rotation_worker.coolify import USER_AGENT, url_problem
from rotation_worker.plans import derive
from rotation_worker.types import TYPES
from rotation_worker.values import Value

NAME: Final = "coolify-api-token"
# The rows' `provider`: every registry row of this type names it.
PROVIDER: Final = "coolify"
TIMEOUT_SECONDS: Final = 30.0
# The registry row's declared abilities (amendment 33 item 5).
ABILITIES_FIELD: Final = "abilities"
# not-a-vocabulary: Coolify's own ability names, checked only against registry rows.
DECLARABLE: Final = frozenset({"read", "write", "deploy"})
VERSION_PATH: Final = "/api/v1/version"
TEAM_PATH: Final = "/api/v1/teams/current"
DEPLOY_PATH: Final = "/api/v1/deploy"
# A field `update_project` doesn't allow, so it answers 422 before it looks up a project.
WRITE_PROBE_BODY: Final = {"sds_scope_probe": True}
# What each scope probe answers, by whether the token holds the ability.
WRITE_ANSWERS: Final = {True: 422, False: 403}
DEPLOY_ANSWERS: Final = {True: 400, False: 403}
SENSITIVE_FIELDS: Final = ("value", "real_value")
# A Coolify resource uuid: lowercase letters and digits.
CANARY_UUID: Final = re.compile(r"^[a-z0-9]{8,64}$")
NO_CANARY: Final = (
    "no coolify_token_canary_application is configured, so the new token's lack of "
    "read:sensitive can't be proved"
)
# This type has no packages from before derivation, so its one candidate is the derived plan of a
# credential with no copies to distribute.
PLAN: Final = derive(TYPES[NAME].capabilities, (), "", "bws-secret")


def declared_abilities(credential: Mapping[str, Any]) -> frozenset[str]:
    """The row's declared abilities, or a refusal: `read` always, and never `read:sensitive` or
    `root`, which no row of this type may hold."""
    declared = credential.get(ABILITIES_FIELD)
    if not isinstance(declared, list) or not all(isinstance(a, str) for a in declared):
        raise ContractError(f"a coolify-api-token row declares its {ABILITIES_FIELD}")
    abilities = frozenset(declared)
    if len(abilities) != len(declared):
        raise ContractError(f"a coolify-api-token row's {ABILITIES_FIELD} repeat an ability")
    if "read" not in abilities:
        raise ContractError(f"a coolify-api-token row's {ABILITIES_FIELD} include read")
    undeclarable = sorted(abilities - DECLARABLE)
    if undeclarable:
        raise ContractError(f"a coolify-api-token row may not declare {', '.join(undeclarable)}")
    return abilities


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row this adapter couldn't act on."""
    declared_abilities(credential)


def build_provider(
    *,
    credential: Mapping[str, Any] | None,
    coolify_url: str | None,
    canary_application: str | None,
    transport: httpx.BaseTransport | None = None,
    absent_project: str | None = None,
) -> CoolifyApiTokenProvider:
    """`absent_project` is the UUID the `write` probe names, drawn at random unless a test pins
    it; Coolify's own uuids aren't RFC UUIDs, so it names no project."""
    if credential is None:
        raise ContractError("the coolify-api-token adapter needs its registry row")
    abilities = declared_abilities(credential)
    if not coolify_url:
        raise ContractError("the coolify-api-token adapter needs coolify_url")
    problem = url_problem(coolify_url)
    if problem is not None:
        raise ContractError(f"the Coolify URL {problem}")
    if canary_application is not None and not CANARY_UUID.fullmatch(canary_application):
        raise ContractError("coolify_token_canary_application isn't a Coolify uuid")
    project_path = f"/api/v1/projects/{absent_project or uuid.uuid4()}"
    canary_path = (
        None if canary_application is None else f"/api/v1/applications/{canary_application}/envs"
    )
    permitted = {
        ("GET", VERSION_PATH),
        ("GET", TEAM_PATH),
        ("PATCH", project_path),
        ("GET", DEPLOY_PATH),
        *([("GET", canary_path)] if canary_path else []),
    }
    try:
        client = ConfinedClient(
            base_url=coolify_url,
            user_agent=USER_AGENT,
            headers={},
            timeout=TIMEOUT_SECONDS,
            permits=lambda method, path: (method, path) in permitted,
            refuse=lambda method, path: ContractError(f"the probe may not {method} {path}"),
            transport=transport,
        )
    except TransportFailure as failure:
        raise ContractError(f"the Coolify URL is unusable: {failure.error_type}") from None
    return CoolifyApiTokenProvider(client, abilities, project_path, canary_path)


class CoolifyApiTokenProvider:
    name = PROVIDER
    probe_target = "production Coolify's version route, with the token as its bearer"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = False

    def __init__(
        self,
        client: ConfinedClient,
        abilities: frozenset[str],
        project_path: str,
        canary_path: str | None,
    ) -> None:
        self._client = client
        self._abilities = abilities
        self._project_path = project_path
        self._canary_path = canary_path

    def _send(self, value: Value, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {value.reveal()}", "Accept": "application/json"}
        return self._client.request(method, path, headers=headers, **kwargs)

    def mint(self) -> Value:
        raise ContractError("a person mints a Coolify API token, in Coolify's UI")

    def known_bad(self) -> Value:
        return Value(f"{secrets.randbelow(10**6) + 1}|{secrets.token_urlsafe(30)}")

    def probe(self, value: Value) -> int | None:
        try:
            return self._send(value, "GET", VERSION_PATH).status_code
        except TransportFailure:
            return None

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def same_identity(self, new: Value, old: Value) -> bool:
        """Whether both values answer the current team's route, which needs `read`, with the same
        team. The account check only."""
        new_team = self._team(new)
        return new_team is not None and new_team == self._team(old)

    def _team(self, value: Value) -> int | None:
        try:
            response = self._send(value, "GET", TEAM_PATH)
        except TransportFailure:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        team = payload.get("id") if isinstance(payload, dict) else None
        return team if isinstance(team, int) and not isinstance(team, bool) else None

    def check_scope(self, new: Value) -> str:
        """Refuse a value whose answers don't match the row's declared abilities. Returns the
        note verify_new files, which names abilities and statuses only."""
        holds_write, holds_deploy = "write" in self._abilities, "deploy" in self._abilities
        write = self._scope_status("write", new, "PATCH", self._project_path, json=WRITE_PROBE_BODY)
        if write != WRITE_ANSWERS[holds_write]:
            raise ContractError(_mismatch("write", holds_write, write))
        deploy = self._scope_status("deploy", new, "GET", DEPLOY_PATH)
        if deploy != DEPLOY_ANSWERS[holds_deploy]:
            raise ContractError(_mismatch("deploy", holds_deploy, deploy))
        self._check_not_sensitive(new)
        return (
            f"scope: write {'held' if holds_write else 'absent'} ({write}), "
            f"deploy {'held' if holds_deploy else 'absent'} ({deploy}), "
            "read:sensitive absent (the canary's env list carries no value field)"
        )

    def _scope_status(self, ability: str, new: Value, method: str, path: str, **kwargs: Any) -> int:
        try:
            return self._send(new, method, path, **kwargs).status_code
        except TransportFailure as failure:
            raise ContractError(
                f"the {ability} probe got no answer ({failure.error_type})"
            ) from None

    def _check_not_sensitive(self, new: Value) -> None:
        if self._canary_path is None:
            raise ContractError(NO_CANARY)
        try:
            response = self._send(new, "GET", self._canary_path)
        except TransportFailure as failure:
            raise ContractError(
                f"the canary's env list got no answer ({failure.error_type})"
            ) from None
        if response.status_code != 200:
            raise ContractError(f"the canary's env list answered HTTP {response.status_code}")
        try:
            entries = response.json()
        except ValueError:
            raise ContractError("the canary's env list isn't JSON") from None
        problem = _sensitive_problem(entries)
        if problem is not None:
            raise ContractError(problem)

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a Coolify API token has no hosted service to host it")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("a Coolify API token has no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person revokes a Coolify API token, in Coolify's UI")


def _mismatch(ability: str, held: bool, status: int) -> str:
    expected = (WRITE_ANSWERS if ability == "write" else DEPLOY_ANSWERS)[held]
    declared = "declares" if held else "doesn't declare"
    return (
        f"the row {declared} {ability}, so its probe must answer {expected}; "
        f"it answered HTTP {status}"
    )


def _sensitive_problem(entries: object) -> str | None:
    """Why the canary's env list doesn't prove the token lacks `read:sensitive`, or `None`.

    An empty list proves nothing, since a `root` token's would be empty too, so the canary must
    hold a variable."""
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return "the canary's env list isn't a list"
    if not entries:
        return "the canary's env list is empty, so it can't show a sensitive field"
    if not all(isinstance(entry, Mapping) for entry in entries):
        return "the canary's env list holds an entry that isn't an object"
    if any(field in entry for entry in entries for field in SENSITIVE_FIELDS):
        return "the new token reads sensitive values: the canary's env list carries a value field"
    return None
