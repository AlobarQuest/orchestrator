"""The Postgres role type: a host with no overlap, which undoes itself rather than go down.

The service here is a small model of the real one: Coolify's variable, the role's password, and
what the consumers loaded at their last restart. The Coolify client is the real one over a mock
transport, so its permits and request shapes are exercised, and the helper is the model's.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from rotation_worker.adapters import ADAPTERS, AdapterContext
from rotation_worker.adapters import postgres_role as pg
from rotation_worker.adapters.change_manager import ChangeManagerProvider
from rotation_worker.adapters.orchestrator_bearer import OrchestratorBearerProvider
from rotation_worker.contract import (
    ContractError,
    Rotation,
    Step,
    StepKind,
    UnitRun,
)
from rotation_worker.destinations import credential_destinations
from rotation_worker.evidence import StepEvidence
from rotation_worker.helper_client import HelperClient
from rotation_worker.plans import Capabilities, derive
from rotation_worker.secret_store import StoreError
from rotation_worker.types import CLASS_TYPES, TYPES
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

OLD = Value("a0" * 32)
NEW = Value("b1" * 32)
KEEPER = "11111111-2222-4333-8444-555555555555"
TOKEN_ID = "22222222-3333-4444-8555-666666666666"
SERVICE = "svc123"
HEALTH_URL = "https://id.alobar.net/-/health/ready/"
COOLIFY_URL = "http://100.92.138.19:8000"
CONSUMERS = ("server-svc123/AUTHENTIK_POSTGRESQL__PASSWORD", "worker-svc123/PG_PASS")
# Every 5 seconds through the 180-second wait, the first at once.
SET_ATTEMPTS = 37


def _print(value: str) -> str:
    fingerprint = Value(value).fingerprint
    return f"{fingerprint.sha256_prefix}:{fingerprint.length}"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Authentik:
    """The Coolify service and its database, and everything done to them, in order."""

    def __init__(self) -> None:
        self.variable, self.password = OLD.reveal(), OLD.reveal()
        self.running = {name: OLD.reveal() for name in CONSUMERS}
        self.log: list[str] = []
        # The exception a stage raises, and on how many calls of it; `None` means every call.
        self.fail: dict[str, tuple[Exception, int | None]] = {}
        self.health = httpx.Response(200)
        self.health_paths: list[str] = []
        self.health_agents: list[str] = []
        self.loaded_answer: dict[str, str] | None = None

    def _failing(self, stage: str) -> None:
        if stage in self.fail:
            error, left = self.fail[stage]
            if left is None or left > 0:
                if left is not None:
                    self.fail[stage] = (error, left - 1)
                raise error

    def coolify(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            self._failing("set_variable")
            self.variable = json.loads(request.content)["value"]
            self.log.append(f"set_variable:{self.variable[:2]}")
        else:
            self._failing("restart")
            self.log.append("restart")
            if "ignore_restart" not in self.fail:
                self.running = dict.fromkeys(CONSUMERS, self.variable)
        return httpx.Response(200, json={})

    def set(self, target: str, value: Value) -> bool:
        assert target == "alobar-id"
        self._failing("set")
        self.log.append(f"set:{value.reveal()[:2]}")
        changed = self.password != value.reveal()
        self.password = value.reveal()
        return changed

    def probe(self, target: str, value: Value) -> int | None:
        assert target == "alobar-id"
        self._failing("probe")
        self.log.append(f"probe:{value.reveal()[:2]}")
        return 200 if value.reveal() == self.password else 401

    def loaded(self, target: str) -> dict[str, str]:
        assert target == "alobar-id"
        self._failing("loaded")
        self.log.append("loaded")
        if self.loaded_answer is not None:
            return self.loaded_answer
        return {name: _print(value) for name, value in self.running.items()}

    def health_check(self, request: httpx.Request) -> httpx.Response:
        self.health_paths.append(request.url.path)
        self.health_agents.append(request.headers["user-agent"])
        return self.health

    def writes(self) -> list[str]:
        return [entry for entry in self.log if not entry.startswith(("probe", "loaded"))]


def _credential(**changes: Any) -> dict[str, Any]:
    credential: dict[str, Any] = {
        "id": "alobar-id-postgres",
        "provider": "postgres-role",
        "bws_uuid": KEEPER,
        "helper_target": "alobar-id",
        "health_url": HEALTH_URL,
        "consumer": [
            {"kind": "bws-secret", "uuid": KEEPER},
            {
                "kind": "coolify-env",
                "instance": "prod",
                "resource_type": "service",
                "uuid": SERVICE,
                "key": "PG_PASS",
                "redeploy": True,
            },
        ],
    }
    return {**credential, **changes}


def _with_consumer(**changes: Any) -> dict[str, Any]:
    credential = _credential()
    hosted = {**credential["consumer"][1], **changes}
    return {**credential, "consumer": [credential["consumer"][0], hosted]}


def _rotation(credential: dict[str, Any] | None = None) -> Rotation:
    destinations = credential_destinations(credential or _credential())
    return Rotation("alobar-id-postgres", "occ", KEEPER, "work", tuple(destinations))


class Built:
    def __init__(self, **changes: Any) -> None:
        self.authentik = Authentik()
        self.clock = Clock()
        self.store = MemoryStore()
        self.store.secrets[TOKEN_ID] = ("admin", "token", Value("coolify-token"))
        arguments: dict[str, Any] = {
            "rotation": _rotation(),
            "credential": _credential(),
            "store": self.store,
            "coolify_token_id": TOKEN_ID,
            "coolify_url": COOLIFY_URL,
            "pg_helper": lambda: cast(HelperClient, self.authentik),
            "transport": httpx.MockTransport(self.authentik.health_check),
            "coolify_transport": httpx.MockTransport(self.authentik.coolify),
            "clock": self.clock,
            "sleep": self.clock.sleep,
            **changes,
        }
        self.provider = pg.build_provider(**arguments)


# ---- the type and its plan -----------------------------------------------------------------


def test_derived_plan_is_the_expected_nine_steps() -> None:
    capabilities = TYPES[pg.NAME].capabilities
    assert capabilities == Capabilities(
        machine_mint=True, machine_revoke=True, hosted=True, overlap=False
    )
    keeper = f'bws-secret uuid="{KEEPER}"'
    plan = derive(capabilities, credential_destinations(_credential()), keeper, "bws-secret")
    assert plan == pg.PLAN
    assert [(unit.name, [step.kind.value for step in unit.steps]) for unit in plan.segments] == [
        ("mint", ["mint", "stage", "host", "verify_new", "store", "verify_consumers"]),
        ("retire", ["verify_consumers", "retire_old", "confirm_dead"]),
    ]


@pytest.mark.parametrize("name", [name for name in TYPES if name != pg.NAME])
def test_existing_types_derive_unchanged(name: str) -> None:
    """`overlap` defaults true for every existing type, and no value of it moves a step."""
    capabilities = TYPES[name].capabilities
    assert capabilities.overlap is True
    keeper = f'bws-secret uuid="{KEEPER}"'
    for hosted in (True, False):
        shape = replace(capabilities, hosted=hosted)
        assert derive(shape, [keeper], keeper, "bws-secret") == derive(
            replace(shape, overlap=False), [keeper], keeper, "bws-secret"
        )


def test_the_type_is_registered_under_its_class_and_adapter() -> None:
    assert CLASS_TYPES["postgres-role-password"] == pg.NAME
    assert ADAPTERS[pg.NAME].provider_name == pg.NAME
    assert ADAPTERS[pg.NAME].capabilities == TYPES[pg.NAME].capabilities
    assert ADAPTERS[pg.NAME].plan == pg.PLAN


def test_minted_and_control_values_are_what_the_helper_accepts() -> None:
    provider = Built().provider
    for value in (provider.mint(), provider.known_bad()):
        assert len(value.reveal()) == 64
        assert set(value.reveal()) <= set("0123456789abcdef")
    assert not provider.mint().same_as(provider.mint())


# ---- host ----------------------------------------------------------------------------------


def test_host_order_is_coolify_then_set_then_restart_then_loaded() -> None:
    built = Built()
    note = built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]
    log = built.authentik.log
    assert log.index("restart") < len(log) - 1 and log[-1] == "loaded"
    assert "b1" not in note and NEW.reveal() not in note
    assert (built.authentik.variable, built.authentik.password) == (NEW.reveal(), NEW.reveal())
    assert set(built.authentik.running.values()) == {NEW.reveal()}


def test_host_writes_nothing_when_a_value_is_already_fully_in_place() -> None:
    """A later step's failure re-runs host, and a second restart would be a second outage."""
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.log.clear()
    note = built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []
    assert "already" in note


def test_host_finishes_when_the_database_took_the_value_and_nothing_restarted() -> None:
    built = Built()
    built.authentik.variable, built.authentik.password = NEW.reveal(), NEW.reveal()
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_host_refuses_before_any_write_when_neither_value_is_accepted() -> None:
    built = Built()
    built.authentik.password = "c2" * 32
    with pytest.raises(ContractError, match="never guess"):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []


def test_host_refuses_before_any_write_when_the_control_is_not_refused() -> None:
    """A probe that accepts everything would make "already in place" read true."""
    built = Built()
    built.authentik.probe = lambda _target, _value: 200  # type: ignore[method-assign]
    with pytest.raises(ContractError):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []


def test_a_coolify_token_that_cannot_be_read_writes_and_undoes_nothing() -> None:
    built = Built(coolify_token_id="33333333-4444-4555-8666-777777777777")
    with pytest.raises(StoreError):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []


def test_host_waits_through_a_restart_for_every_consumer() -> None:
    """Containers come back one at a time, and one mid-restart answers an empty fingerprint."""
    built = Built()
    answers = iter(
        [
            {CONSUMERS[0]: "", CONSUMERS[1]: ""},
            {CONSUMERS[0]: _print(NEW.reveal()), CONSUMERS[1]: _print(OLD.reveal())},
            {CONSUMERS[0]: _print(NEW.reveal()), CONSUMERS[1]: _print(NEW.reveal())},
        ]
    )
    built.authentik.loaded = lambda _target: next(answers)  # type: ignore[method-assign]
    built.provider.host(NEW, OLD)
    assert next(answers, None) is None
    assert built.clock.now - datetime(2026, 10, 30, tzinfo=UTC) == timedelta(seconds=10)


def test_a_helper_refusal_while_waiting_is_asked_again() -> None:
    built = Built()
    built.authentik.fail["loaded"] = (ContractError("the helper refused loaded: x"), 2)
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_an_empty_loaded_answer_is_not_every_consumer_matching() -> None:
    built = Built()
    built.authentik.loaded_answer = {}
    with pytest.raises(ContractError):
        built.provider.host(NEW, OLD)
    assert "restart" in built.authentik.writes()


def test_the_database_write_is_asked_again() -> None:
    """A restart takes the database down too, so an undo right after one can find it unreachable."""
    built = Built()
    built.authentik.fail["set"] = (ContractError("the helper refused set: probe_unavailable"), 2)
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]
    assert built.clock.now - datetime(2026, 10, 30, tzinfo=UTC) == timedelta(seconds=10)


def _assert_restored(built: Built) -> None:
    assert (built.authentik.variable, built.authentik.password) == (OLD.reveal(), OLD.reveal())
    assert set(built.authentik.running.values()) == {OLD.reveal()}
    assert built.authentik.writes()[-3:] == ["set_variable:a0", "set:a0", "restart"]
    assert built.authentik.log[-1] == "loaded"


def test_a_failure_at_set_restores_the_old_value_and_raises() -> None:
    built = Built()
    built.authentik.fail["set"] = (
        ContractError("the helper refused set: alter_failed"),
        SET_ATTEMPTS,
    )
    with pytest.raises(ContractError, match="alter_failed.*restored"):
        built.provider.host(NEW, OLD)
    _assert_restored(built)


def test_a_failure_at_restart_restores_the_old_value_and_raises() -> None:
    built = Built()
    built.authentik.fail["restart"] = (ContractError("Coolify POST answered HTTP 500"), 1)
    with pytest.raises(ContractError, match="HTTP 500.*restored"):
        built.provider.host(NEW, OLD)
    _assert_restored(built)


def test_a_failure_at_loaded_restores_the_old_value_and_raises() -> None:
    built = Built()
    built.authentik.fail["ignore_restart"] = (ContractError("never raised"), 0)
    with pytest.raises(ContractError, match="in time.*restored"):
        built.provider.host(NEW, OLD)
    assert built.clock.now - datetime(2026, 10, 30, tzinfo=UTC) >= pg.LOADED_WAIT
    assert built.authentik.writes()[-3:] == ["set_variable:a0", "set:a0", "restart"]
    assert (built.authentik.variable, built.authentik.password) == (OLD.reveal(), OLD.reveal())


def test_a_failure_writing_the_variable_restores_the_old_value() -> None:
    """A write that errored may still have landed."""
    built = Built()
    built.authentik.fail["set_variable"] = (ContractError("Coolify PATCH answered HTTP 500"), 1)
    with pytest.raises(ContractError, match="HTTP 500.*restored"):
        built.provider.host(NEW, OLD)
    _assert_restored(built)


@pytest.mark.parametrize("stage", ["set", "restart", "loaded"])
def test_an_exception_that_is_no_refusal_restores_too(stage: str) -> None:
    """The value inside the exception must not survive into the unit's failure reason."""
    built = Built()
    original = built.authentik.loaded
    if stage == "loaded":
        calls: list[int] = []

        def loaded(target: str) -> dict[str, str]:
            calls.append(1)
            if len(calls) == 1:
                raise KeyError(NEW.reveal())
            return original(target)

        built.authentik.loaded = loaded  # type: ignore[method-assign]
    else:
        built.authentik.fail[stage] = (KeyError(NEW.reveal()), 1)
    with pytest.raises(ContractError, match="KeyError.*restored") as caught:
        built.provider.host(NEW, OLD)
    assert NEW.reveal() not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    _assert_restored(built)


def test_a_failed_undo_names_both_failures_with_no_value() -> None:
    built = Built()
    built.authentik.fail["set"] = (ContractError(f"first {NEW.reveal()}"), SET_ATTEMPTS)
    built.authentik.fail["restart"] = (KeyError(OLD.reveal()), None)
    with pytest.raises(ContractError) as caught:
        built.provider.host(NEW, OLD)
    message = str(caught.value)
    assert "first" in message and "KeyError" in message and "pg-restore" in message
    assert "restored" not in message.replace("restoring", "")
    assert NEW.reveal() not in message and OLD.reveal() not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_a_failed_undo_at_its_own_refusal_names_that_refusal() -> None:
    built = Built()
    built.authentik.fail["restart"] = (ContractError("Coolify POST answered HTTP 503"), None)
    with pytest.raises(ContractError, match="HTTP 503.*HTTP 503"):
        built.provider.host(NEW, OLD)
    assert built.authentik.password == OLD.reveal()


def test_restore_runs_the_same_writes_for_the_value_given() -> None:
    """The CLI's recovery: the keeper's value, written the same way, with no undo of its own."""
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.log.clear()
    note = built.provider.restore(OLD)
    assert built.authentik.writes() == ["set_variable:a0", "set:a0", "restart"]
    assert built.authentik.log[-1] == "loaded"
    assert OLD.reveal() not in note


def test_restore_refuses_when_the_value_never_loads_and_writes_no_other_value() -> None:
    built = Built()
    built.authentik.fail["ignore_restart"] = (ContractError("never raised"), 0)
    with pytest.raises(ContractError, match="in time"):
        built.provider.restore(NEW)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_a_refusal_quoting_a_value_is_scrubbed_from_the_message() -> None:
    built = Built()
    built.authentik.fail["set"] = (ContractError(f"echo {NEW.reveal()}"), SET_ATTEMPTS)
    with pytest.raises(ContractError, match="echo .*restored") as caught:
        built.provider.host(NEW, OLD)
    assert NEW.reveal() not in str(caught.value)


# ---- probe, revoke, consumers ---------------------------------------------------------------


def test_probe_is_live_at_200_dead_at_401() -> None:
    built = Built()
    assert built.provider.probe(OLD) == 200
    assert built.provider.probe(NEW) == 401
    assert built.provider.probe_before_revoke(OLD) == 200
    assert built.provider.dead_statuses == frozenset({401})
    assert built.provider.hosts_destinations is True


def test_revoke_raises_because_the_old_value_should_already_be_refused() -> None:
    built = Built()
    with pytest.raises(ContractError, match="reset behind"):
        built.provider.revoke(OLD)
    assert built.authentik.writes() == []


def test_consumer_loaded_needs_fingerprints_and_health_200() -> None:
    built = Built()
    consumer = {"kind": "coolify-env"}
    built.provider.host(NEW, OLD)
    assert built.provider.consumer_loaded(consumer, NEW) is True
    assert built.authentik.health_paths == ["/-/health/ready/"]
    assert "rotation-worker" in built.authentik.health_agents[0]
    # A value the consumers didn't load.
    built.authentik.running[CONSUMERS[1]] = OLD.reveal()
    assert built.provider.consumer_loaded(consumer, NEW) is False
    built.authentik.running[CONSUMERS[1]] = NEW.reveal()
    # A health answer that isn't exactly 200.
    for status in (204, 302, 503):
        built.authentik.health = httpx.Response(status)
        assert built.provider.consumer_loaded(consumer, NEW) is False
    built.authentik.health = httpx.Response(200)
    assert built.provider.consumer_loaded(consumer, NEW) is True
    # The database refusing the value.
    built.authentik.password = OLD.reveal()
    assert built.provider.consumer_loaded(consumer, NEW) is False


def test_consumer_loaded_is_false_for_no_fingerprints_and_for_a_blank_one() -> None:
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.loaded_answer = {}
    assert built.provider.consumer_loaded({}, NEW) is False
    built.authentik.loaded_answer = {CONSUMERS[0]: _print(NEW.reveal()), CONSUMERS[1]: ""}
    assert built.provider.consumer_loaded({}, NEW) is False


def test_consumer_loaded_is_false_when_it_cannot_ask() -> None:
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.fail["loaded"] = (ContractError("the helper refused loaded: x"), None)
    assert built.provider.consumer_loaded({}, NEW) is False
    del built.authentik.fail["loaded"]

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    unreachable = Built(transport=httpx.MockTransport(down))
    unreachable.provider.host(NEW, OLD)
    assert unreachable.provider.consumer_loaded({}, NEW) is False


def test_the_health_probe_may_reach_only_its_one_path() -> None:
    built = Built()
    client = built.provider._health
    with pytest.raises(ContractError):
        client.request("GET", "/api/v3/core/users/")
    with pytest.raises(ContractError):
        client.request("POST", "/-/health/ready/")


@pytest.mark.parametrize("provider", [OrchestratorBearerProvider, ChangeManagerProvider])
def test_hosted_providers_consumer_loaded_matches_accepts(provider: type) -> None:
    class Answering(provider):
        def __init__(self) -> None:
            self.live = NEW.reveal()

        def probe(self, value: Value) -> int:
            return 200 if value.reveal() == self.live else 401

        def known_bad(self) -> Value:
            return Value("c3" * 32)

    answering = Answering()
    assert answering.consumer_loaded({"kind": "coolify-env-hash"}, NEW) is True
    assert answering.consumer_loaded({"kind": "coolify-env-hash"}, OLD) is False


def test_the_contract_asks_the_provider_for_a_hosted_consumer() -> None:
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.health = httpx.Response(503)
    store = MemoryStore()
    store.secrets[KEEPER] = ("keepers", "keeper", NEW)
    run = _run(built, store)
    assert [d for d in run.unverified(NEW) if d.startswith("coolify-env")]
    built.authentik.health = httpx.Response(200)
    assert run.unverified(NEW) == []


def _run(built: Built, store: MemoryStore) -> UnitRun:
    def record(_step: Step, _evidence: StepEvidence) -> None:
        raise AssertionError("nothing files here")

    return UnitRun(_rotation(), store, built.provider, (), record, lambda: None)


def test_the_retire_unit_finds_the_old_value_already_refused() -> None:
    """Retire never reaches `revoke`: confirm_dead follows and the plan finishes."""
    built = Built()
    built.provider.host(NEW, OLD)
    store = MemoryStore()
    store.secrets[KEEPER] = ("keepers", "keeper", NEW)
    store.secrets["q"] = ("work", _rotation().quarantine_key, OLD)
    store.secrets["s"] = ("work", _rotation().staging_key, NEW)
    filed: list[StepKind] = []
    notes: dict[StepKind, str | None] = {}

    def record(step: Step, evidence: StepEvidence) -> None:
        filed.append(step.kind)
        notes[step.kind] = evidence.note

    run = UnitRun(_rotation(), store, built.provider, (), record, lambda: None)
    run.run(pg.PLAN.unit("retire"))
    assert filed[:3] == [
        StepKind.VERIFY_CONSUMERS,
        StepKind.RETIRE_OLD,
        StepKind.CONFIRM_DEAD,
    ]
    assert "already refused" in str(notes[StepKind.RETIRE_OLD])
    assert store.deleted == ["s", "q"]


# ---- what the row and the destinations must say ---------------------------------------------


@pytest.mark.parametrize(
    "credential",
    [
        {k: v for k, v in _credential().items() if k != "helper_target"},
        {k: v for k, v in _credential().items() if k != "health_url"},
        _credential(helper_target=""),
        _credential(helper_target=7),
        _credential(health_url="http://id.alobar.net/-/health/ready/"),
        _credential(health_url="https://"),
        _credential(health_url=9),
        _credential(health_url="https://id.alobar.net/-/health/ready/?x=1"),
        _credential(health_url="https://user@id.alobar.net/-/health/ready/"),
    ],
)
def test_a_row_that_doesnt_say_where_to_ask_is_refused(credential: dict[str, Any]) -> None:
    with pytest.raises(ContractError, match="helper_target|health_url"):
        pg.check_row(credential)
    with pytest.raises(ContractError, match="helper_target|health_url"):
        Built(credential=credential)


@pytest.mark.parametrize(
    "credential",
    [
        _with_consumer(resource_type="application"),
        _with_consumer(redeploy=False),
        _with_consumer(instance="dev"),
        _with_consumer(kind="coolify-env-hash", entry="x"),
        {
            **_credential(),
            "consumer": [*_credential()["consumer"], {**_credential()["consumer"][1], "key": "B"}],
        },
        {**_credential(), "consumer": [_credential()["consumer"][0]]},
    ],
)
def test_the_one_hosted_destination_is_a_restarting_service_variable(
    credential: dict[str, Any],
) -> None:
    with pytest.raises(ContractError, match="coolify-env"):
        pg.check_row(credential)


def test_a_good_row_passes() -> None:
    pg.check_row(_credential())


def _context(**changes: Any) -> AdapterContext:
    authentik = Authentik()
    arguments: dict[str, Any] = {
        "state_dir": Path("/nonexistent"),
        "store": MemoryStore(),
        "admin_secret_id": TOKEN_ID,
        "rotation": _rotation(),
        "coolify_url": COOLIFY_URL,
        "credential": _credential(),
        "pg_helper": lambda: cast(HelperClient, authentik),
        **changes,
    }
    return AdapterContext(**arguments)


def test_the_adapter_builds_with_its_context() -> None:
    assert isinstance(ADAPTERS[pg.NAME].build(_context()), pg.PostgresRoleProvider)


def test_host_without_overlap_on_a_postgres_unit_is_refused() -> None:
    """The type declares no overlap itself; the envelope's flag means something else."""
    with pytest.raises(ContractError, match="host_without_overlap|declares"):
        ADAPTERS[pg.NAME].build(_context(host_without_overlap=True))


@pytest.mark.parametrize(
    "changes",
    [
        {"pg_helper": None},
        {"admin_secret_id": None},
        {"coolify_url": None},
        {"rotation": None},
        {"credential": None},
    ],
)
def test_an_adapter_missing_what_it_needs_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ContractError):
        ADAPTERS[pg.NAME].build(_context(**changes))
