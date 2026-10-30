"""The Bitbucket adapter: an Atlassian API token, minted and revoked by a person.

ADR-0055 increment 7 and its per-class row: Atlassian has no revoke the executor has verified, and
no API mints an API token, so mint and stage are one human act and retire-old another. The
executor checks the staged value works, stores it, waits unclaimed until every reader has
switched, and, after the person's revoke, proves the old value dead.

* **Probe** is the legacy lane's: `GET /2.0/repositories/{workspace}?pagelen=1` with Basic auth
  (the account's email and the token). Live is exactly 200; a random token, the known-bad
  control, answers 401. The email and workspace are the registry entry's `probe_email` and
  `probe_workspace`.
* **The `verify` unit gates the person's revoke.** It holds only verify-consumers, and the
  worker leaves it unclaimed until every reader has run with the new value
  (`worker._gates_retire`), so its completion is the signal that the revoke is safe.
"""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Mapping
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import (
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.values import Value

NAME: Final = "bitbucket"
API: Final = "https://api.bitbucket.org"
USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
WORKSPACE: Final = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EMAIL: Final = re.compile(r"^[^@\s]+@[^@\s]+$")

M, H = Actor.MACHINE, Actor.HUMAN
PLAN: Final = Plan(
    (
        HumanAct("mint", (Step(StepKind.MINT, H), Step(StepKind.STAGE, H))),
        UnitPlan(
            "store",
            (Step(StepKind.VERIFY_NEW, M, "AC-001"), Step(StepKind.STORE, M, "AC-002")),
        ),
        UnitPlan("verify", (Step(StepKind.VERIFY_CONSUMERS, M, "AC-003"),)),
        HumanAct("revoke", (Step(StepKind.RETIRE_OLD, H),)),
        UnitPlan("confirm", (Step(StepKind.CONFIRM_DEAD, M, "AC-004"),)),
    )
)
validate_plan(PLAN)


def build_provider(
    *, credential: Mapping[str, Any] | None, transport: httpx.BaseTransport | None = None
) -> BitbucketProvider:
    if credential is None:
        raise ContractError("the Bitbucket adapter needs its registry entry")
    email, workspace = credential.get("probe_email"), credential.get("probe_workspace")
    if not isinstance(email, str) or not EMAIL.fullmatch(email):
        raise ContractError("the registry entry names no probe_email")
    if not isinstance(workspace, str) or not WORKSPACE.fullmatch(workspace):
        raise ContractError("the registry entry names no probe_workspace")
    path = f"/2.0/repositories/{workspace}"
    client = ConfinedClient(
        base_url=API,
        user_agent=USER_AGENT,
        headers={},
        timeout=TIMEOUT_SECONDS,
        permits=lambda method, asked: (method, asked) == ("GET", path),
        refuse=lambda method, asked: ContractError(f"the probe may not {method} {asked}"),
        transport=transport,
    )
    return BitbucketProvider(client, path, email)


class BitbucketProvider:
    name = NAME
    probe_target = "Bitbucket's workspace repository listing, with the token by Basic auth"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = False

    def __init__(self, client: ConfinedClient, path: str, email: str) -> None:
        self._client = client
        self._path = path
        self._email = email

    def mint(self) -> Value:
        raise ContractError("a person mints an Atlassian API token, in the console")

    def known_bad(self) -> Value:
        return Value(secrets.token_urlsafe(48))

    def probe(self, value: Value) -> int | None:
        pair = base64.b64encode(f"{self._email}:{value.reveal()}".encode()).decode()
        try:
            response = self._client.request(
                "GET", self._path, params={"pagelen": 1}, headers={"Authorization": f"Basic {pair}"}
            )
        except TransportFailure:
            return None
        return response.status_code

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def same_identity(self, new: Value, old: Value) -> bool:
        """Whether both values are the same account's, with the same workspace in reach.

        The probe signs in with the registry's `probe_email`, so a token another Atlassian
        account issued is refused there: both answering is the account check. `GET /2.0/user`
        would name the account, but these tokens lack its scope (403, measured 2026-10-28).
        """
        return self.probe(new) == 200 and self.probe(old) == 200

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("an Atlassian token has no hosted service to host it")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("a Bitbucket token has no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person revokes an Atlassian API token, in the console")
