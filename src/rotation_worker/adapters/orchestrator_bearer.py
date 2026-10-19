"""The orchestrator-bearer adapter: the executor rotates a machine bearer the orchestrator issues.

ADR-0055 increment 6 and amendment 9. The orchestrator stores each machine identity's bearer as a
sha256 in one Coolify variable, `ORCHESTRATOR_M2M_CREDENTIALS`, and accepts an entry's `previous`
hash until its `until` (decision 7). So the bearer has no issuer to ask: the executor mints the
value itself, and the orchestrator is the hosted service that must accept it.

* **Mint** a random value, as `rotation-worker mint-bearer` does.
* **Host** writes the setting with the approved entry's new hash and the old one as `previous`,
  restarts the orchestrator, and waits until the orchestrator's identities route shows the new hash
  loaded. The setting is rebuilt from that route (`m2m_setting.py`), never from a stored copy.
  A mint unit whose approved envelope sets `host_without_overlap: true` writes the new hash with
  no `previous` (`Change.REPLACE`), which `build` refuses unless another entry holds the old hash
  as current. The old value then stops authenticating as this key id at the restart, and
  retire-old finds it already refused.
* **Probe** authenticates to the status ledger as the approved key id: live is exactly 200, and a
  random bearer, the known-bad control, answers 401. A restart swaps containers behind the proxy,
  so an answer that is neither (a 502 mid-swap, no answer at all) is asked again until
  `SWAP_WAIT` passes. A value this provider just hosted or retired is asked again until it answers
  as it now must, for the same reason: the old container may still be answering.
* **Revoke** confirms the entry's `previous` hash is the quarantined value's (the provider's own
  word for which key it is, T8), then writes the setting without it and restarts.

Before each restart the executor confirms no dispatched run is live (`live_runs.py`). It writes only
the variable and application its approved destination names (`coolify.py`), with the Coolify token
from `admin_secrets`, read from BWS only when a step writes.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem
from rotation_worker.contract import (
    Actor,
    ContractError,
    Plan,
    Rotation,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.coolify import CoolifyClient
from rotation_worker.destinations import parse
from rotation_worker.live_runs import stranded_by_a_restart
from rotation_worker.m2m_setting import Change, bearer_hash, build
from rotation_worker.orchestrator_client import (
    LEDGER,
    TIMEOUT_SECONDS,
    USER_AGENT,
    OrchestratorClient,
    OrchestratorError,
)
from rotation_worker.secret_store import SecretStore
from rotation_worker.values import Value

NAME: Final = "orchestrator"
BEARER_BYTES: Final = 32
HOSTED_KIND: Final = "coolify-env-hash"
VARIABLE: Final = "ORCHESTRATOR_M2M_CREDENTIALS"
INSTANCE: Final = "prod"
# The status ledger filtered to a unit that can't exist: an authenticated caller gets 200 and an
# empty list, anyone else 401, and nothing is read.
PROBE_PARAMS: Final = {"work_unit_id": "00000000-0000-0000-0000-000000000000"}
DETERMINATE: Final = frozenset({200, 401})
SWAP_WAIT: Final = timedelta(minutes=5)
RESTART_WAIT: Final = timedelta(minutes=10)
RECHECK_SECONDS: Final = 10.0
# What a host leaves the entry accepting, for the step's note.
HOSTED_AS: Final = {
    Change.HOST: "the new hash with the old one as previous",
    Change.REPLACE: "the new hash with no previous, since another entry holds the old one",
}

M = Actor.MACHINE
# Two units, each claimed inside the live_estate window because each restarts the orchestrator,
# with Devon's completion click between them, so nothing is retired until he has seen the new
# value stored. The operator bearer's readers fetch the keeper when they run, so the keeper
# readback verifies them and there is no human act.
PLAN: Final = Plan(
    (
        UnitPlan(
            "mint",
            (
                Step(StepKind.MINT, M, "AC-001"),
                Step(StepKind.STAGE, M, "AC-002"),
                Step(StepKind.HOST, M, "AC-003"),
                Step(StepKind.VERIFY_NEW, M, "AC-004"),
                Step(StepKind.STORE, M, "AC-005"),
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-006"),
            ),
        ),
        UnitPlan(
            "retire",
            (
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-007"),
                Step(StepKind.RETIRE_OLD, M, "AC-008"),
                Step(StepKind.CONFIRM_DEAD, M, "AC-009"),
            ),
        ),
    )
)
validate_plan(PLAN)
# The factory-runner bearer's plan (increment 7): the same units with the Actions-secret copies
# written straight after store, while the mint unit holds the window. A separate name, because
# approved packages pin `PLAN`'s criteria and a rotation under it may be in flight.
DISTRIBUTED_NAME: Final = "orchestrator-distributed"
DISTRIBUTED_PLAN: Final = Plan(
    (
        UnitPlan(
            "mint",
            (
                Step(StepKind.MINT, M, "AC-001"),
                Step(StepKind.STAGE, M, "AC-002"),
                Step(StepKind.HOST, M, "AC-003"),
                Step(StepKind.VERIFY_NEW, M, "AC-004"),
                Step(StepKind.STORE, M, "AC-005"),
                Step(StepKind.DISTRIBUTE, M, "AC-006"),
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-007"),
            ),
        ),
        UnitPlan(
            "retire",
            (
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-008"),
                Step(StepKind.RETIRE_OLD, M, "AC-009"),
                Step(StepKind.CONFIRM_DEAD, M, "AC-010"),
            ),
        ),
    )
)
validate_plan(DISTRIBUTED_PLAN)


def _settled(status: int | None, expected: int | None) -> bool:
    """An answer that isn't a swap's noise, and, for a value just hosted or retired, the new one."""
    return status in DETERMINATE and expected in (None, status)


def hosted_destination(rotation: Rotation) -> Mapping[str, Any]:
    """The rotation's one hashed-setting destination on production: what the executor rewrites."""
    hosted = [parse(d) for d in rotation.destinations if parse(d)["kind"] == HOSTED_KIND]
    if len(hosted) != 1 or hosted[0]["instance"] != INSTANCE:
        raise ContractError("an orchestrator bearer has exactly one production coolify-env-hash")
    if hosted[0]["resource_type"] != "application":
        raise ContractError("the orchestrator's setting is an application variable")
    return hosted[0]


def build_provider(
    *,
    rotation: Rotation | None,
    store: SecretStore,
    coolify_token_id: str | None,
    orchestrator: OrchestratorClient | None,
    orchestrator_url: str | None,
    coolify_url: str | None,
    orchestrator_application: str | None,
    executor_key_id: str | None,
    without_overlap: bool = False,
    transport: httpx.BaseTransport | None = None,
    coolify_transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> OrchestratorBearerProvider:
    if rotation is None or orchestrator is None or not orchestrator_url:
        raise ContractError("the orchestrator adapter needs its rotation and the orchestrator")
    if coolify_token_id is None or not coolify_url:
        raise ContractError("the orchestrator adapter needs coolify_url and its token's id")
    destination = hosted_destination(rotation)
    # Defence beside Devon's approval of the destination: the one variable and application this
    # adapter may write are the executor's own configuration, not the package's word alone.
    if destination["key"] != VARIABLE:
        raise ContractError(f"an orchestrator bearer's destination must be {VARIABLE}")
    if not orchestrator_application or destination["uuid"] != orchestrator_application:
        raise ContractError("the destination isn't the configured orchestrator application")
    # The executor's own bearer is hand-rotated (decision 4): retiring it would lock the
    # executor out of the orchestrator it reports to.
    if destination["entry"] == executor_key_id:
        raise ContractError("the executor never rotates its own bearer")
    problem = base_url_problem(orchestrator_url)
    if problem is not None:
        raise ContractError(f"the orchestrator URL {problem}")

    def coolify() -> CoolifyClient:
        token = store.read(coolify_token_id)
        return CoolifyClient(coolify_url, token, destination["uuid"], coolify_transport)

    probe_client = ConfinedClient(
        base_url=orchestrator_url,
        user_agent=USER_AGENT,
        headers={},
        timeout=TIMEOUT_SECONDS,
        permits=lambda method, path: (method, path) == ("GET", LEDGER),
        refuse=lambda method, path: ContractError(f"the probe may not {method} {path}"),
        transport=transport,
    )
    return OrchestratorBearerProvider(
        probe_client,
        orchestrator,
        coolify,
        destination["key"],
        destination["entry"],
        Change.REPLACE if without_overlap else Change.HOST,
        clock,
        sleep,
    )


class OrchestratorBearerProvider:
    name = NAME
    probe_target = "the orchestrator's status ledger, as the approved key id"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = True

    def __init__(
        self,
        probe_client: ConfinedClient,
        orchestrator: OrchestratorClient,
        coolify: Callable[[], CoolifyClient],
        variable: str,
        entry: str,
        hosting: Change,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._probe_client = probe_client
        self._orchestrator = orchestrator
        self._coolify = coolify
        self._variable = variable
        self._entry = entry
        self._hosting = hosting
        self._clock = clock
        self._sleep = sleep
        # The status a hash must come to answer after this provider hosted or retired it.
        self._expected: dict[str, int] = {}

    def mint(self) -> Value:
        return Value(secrets.token_urlsafe(BEARER_BYTES))

    def known_bad(self) -> Value:
        return Value(secrets.token_urlsafe(BEARER_BYTES))

    def probe(self, value: Value) -> int | None:
        expected = self._expected.get(bearer_hash(value))
        deadline = self._clock() + SWAP_WAIT
        status = self._ask(value)
        while not _settled(status, expected) and self._clock() < deadline:
            self._sleep(RECHECK_SECONDS)
            status = self._ask(value)
        return status

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def host(self, new: Value, old: Value) -> str:
        new_hash = bearer_hash(new)
        rewrite = build(
            self._orchestrator.identities(),
            self._entry,
            self._hosting,
            new_hash,
            bearer_hash(old),
            self._clock(),
        )
        self._expected[new_hash] = 200
        hosted_as = HOSTED_AS[self._hosting]
        if rewrite.text is None:
            return f"the orchestrator already serves {hosted_as}"
        self._write_and_restart(rewrite.text)
        alone = self._hosting is Change.REPLACE
        self._await(
            lambda entry: (
                entry.get("token_hash") == new_hash and (not alone or "previous" not in entry)
            )
        )
        return (
            f"wrote setting {rewrite.fingerprint} and restarted; the orchestrator loaded "
            + hosted_as
        )

    def revoke(self, value: Value) -> str:
        old_hash = bearer_hash(value)
        served = self._orchestrator.identities()
        entry = served.get(self._entry) or {}
        current = entry.get("token_hash")
        previous = entry.get("previous") or {}
        if not previous and current not in (None, old_hash):
            # A retry whose probe reached the old container just before the swap finished: the
            # retire already loaded. Confirm-dead waits for the old value to be refused.
            self._expected[old_hash] = 401
            return "the orchestrator had already loaded the setting without the old hash"
        # Boot refuses a previous hash equal to a current one, so this also means old isn't current.
        if previous.get("token_hash") != old_hash:
            raise ContractError(
                "the orchestrator doesn't hold the old value as the entry's previous"
            )
        rewrite = build(served, self._entry, Change.RETIRE, str(current), old_hash, self._clock())
        self._expected[old_hash] = 401
        if rewrite.text is None:
            raise ContractError("the entry has no previous hash to retire")
        self._write_and_restart(rewrite.text)
        self._await(lambda entry: "previous" not in entry)
        return (
            f"confirmed the entry's previous hash is the old value's, wrote setting "
            f"{rewrite.fingerprint} without it and restarted"
        )

    def _ask(self, value: Value) -> int | None:
        headers = {"Authorization": f"Bearer {value.reveal()}", "X-Credential-Key-Id": self._entry}
        try:
            response = self._probe_client.request(
                "GET", LEDGER, params=PROBE_PARAMS, headers=headers
            )
        except TransportFailure:
            return None
        return response.status_code

    def _write_and_restart(self, text: str) -> None:
        # Asked before writing, and again straight before the restart, to narrow the window in
        # which a lane could dispatch a run between the check and the restart. It can't close it:
        # dispatch has no off switch the executor may use.
        self._refuse_a_stranding_restart()
        coolify = self._coolify()
        coolify.set_variable(self._variable, text)
        self._refuse_a_stranding_restart()
        coolify.restart()

    def _refuse_a_stranding_restart(self) -> None:
        stranded = stranded_by_a_restart(self._orchestrator)
        if stranded:
            raise ContractError("a restart now would strand a run: " + ", ".join(stranded))

    def _await(self, loaded: Callable[[Mapping[str, Any]], bool]) -> None:
        """Wait until the restarted orchestrator serves the entry as written."""
        deadline = self._clock() + RESTART_WAIT
        while self._clock() < deadline:
            self._sleep(RECHECK_SECONDS)
            try:
                entry = self._orchestrator.identities().get(self._entry)
            except OrchestratorError:
                # Mid-restart: nothing is answering yet.
                continue
            if isinstance(entry, Mapping) and loaded(entry):
                return
        raise ContractError("the restarted orchestrator didn't load the written setting in time")
