"""The GitHub PAT adapter: a person mints a fine-grained PAT; the executor does the rest.

ADR-0055 increment 7. GitHub has no API that mints a fine-grained PAT, so mint and stage are a
human act: Devon creates the token in the console, with the repository list and permissions the
credential's registry entry states, and pastes it into the rotation's staging secret in
`Rotation / Work`. From there the executor works it like any other value.

* **Probe** is `GET /user` with the value as a bearer: live is exactly 200, and a malformed
  `github_pat_` value, the known-bad control, answers 401.
* **Revoke** is `POST /credentials/revoke`, which takes the value itself, so it can't revoke the
  wrong key (T8). GitHub refuses it with any credential, so its client sends none. It answers 202
  and says nothing more; measured on 2026-10-19, a revoked throwaway answered 401 within six
  seconds. So a value this provider revoked that still probes live is probed again every
  `RECHECK_SECONDS` until it is refused or `REVOKE_LAG` has passed, as amendment 8 does for
  OpenRouter.
* **A revoke fails a run that holds the value.** A credential with Actions-secret copies is the
  factory's PAT, so the retire unit waits unclaimed while a dispatched run is live
  (`readers_mid_run`, which the worker asks before claiming a unit that retires), and the revoke
  asks once more and refuses if one started since.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import (
    ACTIONS_KIND,
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Rotation,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.destinations import parse
from rotation_worker.github_actions import API, HEADERS, TIMEOUT_SECONDS, USER_AGENT
from rotation_worker.live_runs import stranded_by_a_restart
from rotation_worker.m2m_setting import bearer_hash
from rotation_worker.orchestrator_client import OrchestratorClient, OrchestratorError
from rotation_worker.readers import ReadUnavailable
from rotation_worker.values import Value

NAME: Final = "github-pat"
PROVIDER: Final = "github"
PROBE_PATH: Final = "/user"
REVOKE_PATH: Final = "/credentials/revoke"
REVOKE_ACCEPTED: Final = 202
REVOKE_LAG: Final = timedelta(minutes=2)
RECHECK_SECONDS: Final = 10.0
# A fine-grained PAT is `github_pat_` and 82 more characters.
KNOWN_BAD_HEX: Final = 41

M, H = Actor.MACHINE, Actor.HUMAN
# A person mints and stages; `store` writes the keeper and every Actions secret; `retire` waits,
# unclaimed, until every copy's report shows the new value, then revokes the old one.
PLAN: Final = Plan(
    (
        HumanAct("mint", (Step(StepKind.MINT, H), Step(StepKind.STAGE, H))),
        UnitPlan(
            "store",
            (
                Step(StepKind.VERIFY_NEW, M, "AC-001"),
                Step(StepKind.STORE, M, "AC-002"),
                Step(StepKind.DISTRIBUTE, M, "AC-003"),
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-004"),
            ),
        ),
        UnitPlan(
            "retire",
            (
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-005"),
                Step(StepKind.RETIRE_OLD, M, "AC-006"),
                Step(StepKind.CONFIRM_DEAD, M, "AC-007"),
            ),
        ),
    )
)
validate_plan(PLAN)


def build_provider(
    *,
    rotation: Rotation | None,
    orchestrator: OrchestratorClient | None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> GitHubPatProvider:
    if rotation is None or orchestrator is None:
        raise ContractError("the GitHub PAT adapter needs its rotation and the orchestrator")
    guards_runs = any(parse(d)["kind"] == ACTIONS_KIND for d in rotation.destinations)
    probe = ConfinedClient(
        base_url=API,
        user_agent=USER_AGENT,
        headers=HEADERS,
        timeout=TIMEOUT_SECONDS,
        permits=lambda method, path: (method, path) == ("GET", PROBE_PATH),
        refuse=lambda method, path: ContractError(f"the probe may not {method} {path}"),
        transport=transport,
    )
    # No Authorization header, ever: GitHub answers an authenticated revoke 403.
    revoke = ConfinedClient(
        base_url=API,
        user_agent=USER_AGENT,
        headers=HEADERS,
        timeout=TIMEOUT_SECONDS,
        permits=lambda method, path: (method, path) == ("POST", REVOKE_PATH),
        refuse=lambda method, path: ContractError(f"the revoke may not {method} {path}"),
        transport=transport,
    )
    return GitHubPatProvider(probe, revoke, orchestrator if guards_runs else None, clock, sleep)


class GitHubPatProvider:
    name = NAME
    probe_target = "GitHub's /user, with the value as a bearer"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = False

    def __init__(
        self,
        probe: ConfinedClient,
        revoke: ConfinedClient,
        runs: OrchestratorClient | None,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._probe = probe
        self._revoke = revoke
        self._runs = runs
        self._clock = clock
        self._sleep = sleep
        self._revoked: set[str] = set()

    def mint(self) -> Value:
        raise ContractError("a person mints a GitHub PAT, in the console")

    def known_bad(self) -> Value:
        return Value("github_pat_" + secrets.token_hex(KNOWN_BAD_HEX))

    def probe(self, value: Value) -> int | None:
        status = self._ask(value)
        if bearer_hash(value) in self._revoked:
            deadline = self._clock() + REVOKE_LAG
            while status == 200 and self._clock() < deadline:
                self._sleep(RECHECK_SECONDS)
                status = self._ask(value)
        return status

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a GitHub PAT has no hosted service to host it")

    def readers_mid_run(self) -> list[str]:
        """The dispatched runs a revoke now would fail: the worker waits, unclaimed, for none.

        An orchestrator that doesn't answer is a wait too (`ReadUnavailable`), as it is for
        change-manager's readers, never a verdict.
        """
        if self._runs is None:
            return []
        try:
            return stranded_by_a_restart(self._runs)
        except OrchestratorError as error:
            raise ReadUnavailable(f"the orchestrator's live runs can't be read: {error}") from None

    def revoke(self, value: Value) -> str:
        live = self.readers_mid_run()
        if live:
            raise ContractError("a revoke now would fail a live run: " + ", ".join(live))
        try:
            response = self._revoke.request(
                "POST", REVOKE_PATH, json={"credentials": [value.reveal()]}
            )
        except TransportFailure as failure:
            raise ContractError(f"GitHub's revoke got no answer: {failure.error_type}") from None
        # The request held the value, so only the status is kept.
        if response.status_code != REVOKE_ACCEPTED:
            raise ContractError(f"GitHub's revoke answered HTTP {response.status_code}")
        self._revoked.add(bearer_hash(value))
        return "GitHub accepted the revoke of the quarantined value itself (202)"

    def _ask(self, value: Value) -> int | None:
        headers = {"Authorization": f"Bearer {value.reveal()}"}
        try:
            return self._probe.request("GET", PROBE_PATH, headers=headers).status_code
        except TransportFailure:
            return None
