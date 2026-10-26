"""The change-manager adapter: the executor rotates one of change-manager's four machine bearers.

ADR-0055 amendment 9, "change-manager's overlap, built and verified" and "The change-manager
adapter". change-manager holds each bearer as a sha256 in its own Coolify variable, one JSON
entry keyed by the bearer's scope, with an optional `previous` hash accepted until its `until`
(`tests/fixtures/m2m_bearer_setting.json`, pinned with change-manager). So, as with the
orchestrator's bearers, the executor mints the value itself and change-manager is the hosted
service that must accept it.

* **Mint** a random value.
* **Host** writes the scope's variable as the new hash with the old one as `previous`, `until`
  seven days out, and restarts change-manager. It builds the value from the old and new values
  alone: change-manager has no route that reports what it loaded, and the executor's Coolify token
  can't read a variable. Each variable is one entry, so nothing else in it can be lost.
* **Probe** asks `GET /api/landing-policy`, a read every scope may make: live is exactly 200, and
  a random bearer, the known-bad control, answers 401. It shows that some scope accepts the value,
  not which; a freshly minted value is in one variable only. A restart swaps containers behind
  Cloudflare, so any other answer is asked again until `SWAP_WAIT` passes, and a value this
  provider just hosted or retired is asked again until it answers as it now must.
* **Revoke** writes the variable as the new hash alone, read from the keeper, which store has
  written, and restarts. change-manager can't say which hash is a scope's `previous`, so the
  contract's before-revoke probe, which proves the old value live, is the only word on it.

**TWO RULES THAT TAKE CHANGE-MANAGER DOWN, REFUSED BEFORE A BYTE IS WRITTEN.** change-manager
stops booting when any scope's `previous` equals any scope's current hash, or an `until` is more
than eight days ahead, and Coolify doesn't keep the old container when the new one can't start.
The executor can't read the other scopes' variables, so it reads their keepers instead (the
registry's other bearers on the same application) and refuses a host or retire whose old or new
value is one of them. A hand edit in Coolify that no keeper holds is invisible to this check.

**THE RESTART WAITS FOR CHANGE-MANAGER'S OWN CLIENTS.** Its clients are the scheduled readers of
its four bearers, several of which run inside the `live_estate` window. Before writing and again
before each restart, the provider asks Healthchecks whether any reader the registry names for any
change-manager bearer is mid-run, and waits (`GATE_WAIT`) until none is. The worker asks the same
before claiming a unit that writes, so a busy reader usually costs a pass, not an attempt. A reader
the registry names with no check can't be asked.

Two plans share one provider. `change-manager` holds machine units only, so every scheduled reader
needs a read in the registry. `change-manager-attested` adds a human act before retire, for a
bearer one of whose readers has no read that shows it worked (the `full` bearer's `drift-audit.sh`,
whose change-manager calls only log a warning when they fail).
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.contract import (
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
from rotation_worker.coolify import CoolifyClient
from rotation_worker.destinations import parse
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.m2m_setting import OVERLAP, bearer_hash
from rotation_worker.orchestrator_client import TIMEOUT_SECONDS, USER_AGENT
from rotation_worker.readers import (
    CheckRead,
    Healthchecks,
    ReadError,
    ReadUnavailable,
    scheduled_reads,
)
from rotation_worker.registry import RegistryEntry, provider_entries
from rotation_worker.secret_store import SecretStore
from rotation_worker.values import Value

NAME: Final = "change-manager"
ATTESTED_NAME: Final = "change-manager-attested"
DISTRIBUTED_NAME: Final = "change-manager-distributed"
BASE_URL: Final = "https://change-mgr.alobar.net"
# Every scope may read the landing policy (change-manager's `app/scopes.py`), and it writes nothing.
PROBE_PATH: Final = "/api/landing-policy"
HOSTED_KIND: Final = "coolify-env-hash"
INSTANCE: Final = "prod"
# Each variable and the scope its one entry is keyed by. The pinned fixture's `variables` holds
# change-manager's word for it, and a test holds this to that.
VARIABLES: Final[Mapping[str, str]] = {
    "M2M_TOKEN": "full",
    "M2M_TOKEN_READ": "read",
    "M2M_TOKEN_PROPOSE": "propose",
    "M2M_TOKEN_OBSERVE": "observe",
}
BEARER_BYTES: Final = 32
DETERMINATE: Final = frozenset({200, 401})
SWAP_WAIT: Final = timedelta(minutes=5)
# How long a step waits for change-manager's readers to finish before it refuses the restart. The
# worker waits unclaimed first, so this covers only a reader that started since that check.
GATE_WAIT: Final = timedelta(minutes=5)
RECHECK_SECONDS: Final = 10.0
# Answers in a row a value just hosted or retired must give before the swap counts as finished.
# Coolify kept both containers serving for about 18 seconds in the orchestrator's measured swap.
SETTLED_RUNS: Final = 3

M = Actor.MACHINE
_MINT: Final = UnitPlan(
    "mint",
    (
        Step(StepKind.MINT, M, "AC-001"),
        Step(StepKind.STAGE, M, "AC-002"),
        Step(StepKind.HOST, M, "AC-003"),
        Step(StepKind.VERIFY_NEW, M, "AC-004"),
        Step(StepKind.STORE, M, "AC-005"),
        Step(StepKind.VERIFY_CONSUMERS, M, "AC-006"),
    ),
)
_RETIRE: Final = UnitPlan(
    "retire",
    (
        Step(StepKind.VERIFY_CONSUMERS, M, "AC-007"),
        Step(StepKind.RETIRE_OLD, M, "AC-008"),
        Step(StepKind.CONFIRM_DEAD, M, "AC-009"),
    ),
)
# Two units, each claimed inside the live_estate window because each restarts change-manager,
# with the verifier's completion of the first between them (ADR-0055 amendment 14). Every reader
# is verified by its registry read.
PLAN: Final = Plan((_MINT, _RETIRE))
# The same units with a person attesting, at /review with the new value's fingerprint, the
# readers no read can verify.
ATTESTED_PLAN: Final = Plan(
    (_MINT, HumanAct("attest", (Step(StepKind.VERIFY_CONSUMERS, Actor.HUMAN),)), _RETIRE)
)
# The plan for a bearer another application also holds (ADR-0055 amendment 13): distribute writes
# that copy after store, and the last verify-consumers asks the holding application what it loaded.
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
validate_plan(PLAN)
validate_plan(ATTESTED_PLAN)
validate_plan(DISTRIBUTED_PLAN)


def setting(scope: str, new_hash: str, previous: tuple[str, datetime] | None = None) -> str:
    """The value of one bearer variable: the scope's entry, in the pinned fixture's form."""
    entry: dict[str, Any] = {"token_hash": new_hash}
    if previous is not None:
        old_hash, until = previous
        entry["previous"] = {"token_hash": old_hash, "until": until.isoformat()}
    return json.dumps({scope: entry}, separators=(",", ":"))


def hosted_destination(rotation: Rotation) -> Mapping[str, Any]:
    """The rotation's one production variable, and the scope its entry must be keyed by."""
    hosted = [parse(d) for d in rotation.destinations if parse(d)["kind"] == HOSTED_KIND]
    if len(hosted) != 1 or hosted[0]["instance"] != INSTANCE:
        raise ContractError("a change-manager bearer has exactly one production coolify-env-hash")
    destination = hosted[0]
    if destination["resource_type"] != "application":
        raise ContractError("change-manager's bearer settings are application variables")
    if VARIABLES.get(destination["key"]) != destination["entry"]:
        raise ContractError("the destination isn't a change-manager bearer variable and its scope")
    return destination


def registry_reads(
    rotation: Rotation | None,
    store: SecretStore,
    mirror: RepoMirror | None,
    healthchecks: Healthchecks | None,
) -> tuple[Callable[[], list[Value]], Callable[[], list[str]]]:
    """The other bearers' keepers, and the readers mid-run, as the registry names them at `main`."""
    if rotation is None or mirror is None:
        raise ContractError("the change-manager adapter needs its rotation and the registry")
    registry = _Registry(hosted_destination(rotation), store, mirror, healthchecks)
    return registry.siblings, registry.readers_mid_run


class _Registry:
    """The registry is read once per provider, when a step first asks: a provider lives for one
    unit in one pass, and the reader wait polls Healthchecks, not git, every few seconds."""

    def __init__(
        self,
        destination: Mapping[str, Any],
        store: SecretStore,
        mirror: RepoMirror,
        healthchecks: Healthchecks | None,
    ) -> None:
        self._application = destination["uuid"]
        self._others = {key for key, scope in VARIABLES.items() if scope != destination["entry"]}
        self._store = store
        self._mirror = mirror
        self._healthchecks = healthchecks
        self._entries: list[RegistryEntry] | None = None

    def entries(self) -> list[RegistryEntry]:
        if self._entries is None:
            self._entries = provider_entries(self._mirror, NAME)
        return self._entries

    def siblings(self) -> list[Value]:
        # One keeper for each other variable, or a value no keeper is checked against slips by.
        keepers: dict[str, list[str]] = {key: [] for key in self._others}
        for entry in self.entries():
            for consumer in map(parse, entry.destinations):
                if self._is_sibling_variable(consumer):
                    keepers[consumer["key"]].append(str(entry.credential.get("bws_uuid")))
        unregistered = sorted(key for key, found in keepers.items() if len(found) != 1)
        if unregistered:
            raise ContractError(
                "the registry doesn't name exactly one keeper for " + ", ".join(unregistered)
            )
        return [self._store.read(found[0]) for found in keepers.values()]

    def _is_sibling_variable(self, consumer: Mapping[str, Any]) -> bool:
        return (
            consumer["kind"] == HOSTED_KIND
            and consumer["uuid"] == self._application
            and consumer["key"] in self._others
        )

    def readers_mid_run(self) -> list[str]:
        names = {
            read.check
            for entry in self.entries()
            for read in scheduled_reads(entry.credential).values()
            if isinstance(read, CheckRead)
        }
        if not names:
            return []
        if self._healthchecks is None:
            raise ReadError("change-manager's readers name checks and no Healthchecks key is set")
        return self._healthchecks.running(names)


def build_provider(
    *,
    rotation: Rotation | None,
    store: SecretStore,
    coolify_token_id: str | None,
    coolify_url: str | None,
    change_manager_application: str | None,
    siblings: Callable[[], Sequence[Value]],
    readers_mid_run: Callable[[], list[str]],
    transport: httpx.BaseTransport | None = None,
    coolify_transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> ChangeManagerProvider:
    if rotation is None:
        raise ContractError("the change-manager adapter needs its rotation")
    if coolify_token_id is None or not coolify_url:
        raise ContractError("the change-manager adapter needs coolify_url and its token's id")
    destination = hosted_destination(rotation)
    # Defence beside Devon's approval of the destination: the one application this adapter may
    # write is the executor's own configuration, not the package's word alone.
    if not change_manager_application or destination["uuid"] != change_manager_application:
        raise ContractError("the destination isn't the configured change-manager application")

    def coolify() -> CoolifyClient:
        token = store.read(coolify_token_id)
        return CoolifyClient(coolify_url, token, destination["uuid"], coolify_transport)

    probe_client = ConfinedClient(
        base_url=BASE_URL,
        user_agent=USER_AGENT,
        headers={},
        timeout=TIMEOUT_SECONDS,
        permits=lambda method, path: (method, path) == ("GET", PROBE_PATH),
        refuse=lambda method, path: ContractError(f"the probe may not {method} {path}"),
        transport=transport,
    )
    return ChangeManagerProvider(
        probe_client,
        coolify,
        destination["key"],
        destination["entry"],
        lambda: store.read(rotation.keeper_id),
        siblings,
        readers_mid_run,
        clock,
        sleep,
    )


class ChangeManagerProvider:
    name = NAME
    probe_target = f"change-manager's {PROBE_PATH}"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = True

    def __init__(
        self,
        probe_client: ConfinedClient,
        coolify: Callable[[], CoolifyClient],
        variable: str,
        scope: str,
        keeper: Callable[[], Value],
        siblings: Callable[[], Sequence[Value]],
        readers_mid_run: Callable[[], list[str]],
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._probe_client = probe_client
        self._coolify = coolify
        self._variable = variable
        self._scope = scope
        self._keeper = keeper
        self._siblings = siblings
        self._readers_mid_run = readers_mid_run
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
        # During a restart both containers can answer, so a value just hosted or retired counts
        # as settled only once it has answered as it now must several times running.
        needed = SETTLED_RUNS if expected is not None else 1
        deadline = self._clock() + SWAP_WAIT
        status = self._ask(value)
        runs = 1 if _settled(status, expected) else 0
        while runs < needed and self._clock() < deadline:
            self._sleep(RECHECK_SECONDS)
            status = self._ask(value)
            runs = runs + 1 if _settled(status, expected) else 0
        if runs < needed and _settled(status, expected):
            # It answered as it must, but not long enough to rule out the old container.
            return None
        return status

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def readers_mid_run(self) -> list[str]:
        """change-manager's readers running now. Raises `ReadUnavailable` if it can't be asked."""
        return self._readers_mid_run()

    def host(self, new: Value, old: Value) -> str:
        new_hash = bearer_hash(new)
        if self._accepts(new):
            # An earlier attempt wrote and restarted: a value minted moments ago is in no other
            # variable, so change-manager accepting it means this scope does.
            return "change-manager already accepts the new value"
        if not self._accepts(old):
            raise ContractError("change-manager doesn't accept the keeper's old value: never guess")
        self._refuse_a_shared_value(new, old)
        until = self._clock().replace(microsecond=0) + OVERLAP
        text = setting(self._scope, new_hash, (bearer_hash(old), until))
        self._expected[new_hash] = 200
        self._write_and_restart(text)
        if self.probe(new) != 200:
            raise ContractError("the restarted change-manager didn't accept the new value in time")
        return (
            f"wrote {self._variable} with the new hash and the old one as previous, and "
            "restarted; change-manager accepts the new value"
        )

    def revoke(self, value: Value) -> str:
        new = self._keeper()
        if new.same_as(value):
            raise ContractError("the keeper still holds the old value")
        if not self._accepts(new):
            raise ContractError("change-manager doesn't accept the keeper's new value")
        self._refuse_a_shared_value(new, value)
        self._expected[bearer_hash(value)] = 401
        self._write_and_restart(setting(self._scope, bearer_hash(new)))
        if self.probe(value) != 401:
            raise ContractError("the restarted change-manager still accepts the old value")
        return f"wrote {self._variable} with the new hash alone and restarted"

    def _refuse_a_shared_value(self, new: Value, old: Value) -> None:
        # change-manager won't boot with a previous equal to any scope's current hash, and a
        # current hash two scopes share authenticates as the narrower one.
        for sibling in self._siblings():
            if sibling.same_as(old) or sibling.same_as(new):
                raise ContractError("another change-manager bearer's keeper holds this value")

    def _accepts(self, value: Value) -> bool:
        """Whether change-manager accepts `value` now, asked beside a known-bad control."""
        control = self.probe(self.known_bad())
        if control != 401:
            raise ContractError(f"the probe didn't refuse a known-bad value ({control})")
        answer = self.probe(value)
        if answer not in DETERMINATE:
            raise ContractError(f"change-manager gave no answer to tell live from dead ({answer})")
        return answer == 200

    def _ask(self, value: Value) -> int | None:
        headers = {"Authorization": f"Bearer {value.reveal()}"}
        try:
            response = self._probe_client.request("GET", PROBE_PATH, headers=headers)
        except TransportFailure:
            return None
        return response.status_code

    def _write_and_restart(self, text: str) -> None:
        # Asked before writing and again straight before the restart: a reader that starts in
        # between is caught by the second.
        self._await_readers()
        coolify = self._coolify()
        coolify.set_variable(self._variable, text)
        self._await_readers()
        coolify.restart()

    def _await_readers(self) -> None:
        deadline = self._clock() + GATE_WAIT
        running = self._running()
        while running and self._clock() < deadline:
            self._sleep(RECHECK_SECONDS)
            running = self._running()
        if running:
            raise ContractError(
                "a restart now would cut short change-manager's readers: " + ", ".join(running)
            )

    def _running(self) -> list[str]:
        try:
            return self._readers_mid_run()
        except ReadUnavailable as error:
            return [f"unreadable: {error}"]


def _settled(status: int | None, expected: int | None) -> bool:
    """An answer that isn't a swap's noise, and, for a value just hosted or retired, the new one."""
    return status in DETERMINATE and expected in (None, status)
