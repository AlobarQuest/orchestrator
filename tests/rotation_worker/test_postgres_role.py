"""The Postgres role type: a host with no overlap, which undoes itself rather than go down.

The service here is a small model of the real one: Coolify's variable, the role's password, and
what the consumers loaded at their last restart. The Coolify client is the real one over a mock
transport, so its permits and request shapes are exercised, and the helper is the model's.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from orchestrator.kernel.leases import DEFAULT_LEASE
from rotation_worker import values
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
from rotation_worker.coolify import CoolifyRefused
from rotation_worker.destinations import credential_destinations
from rotation_worker.evidence import StepEvidence
from rotation_worker.helper_client import HelperClient, HelperRefused
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
CONSUMERS: tuple[str, str] = (
    "server-svc123/AUTHENTIK_POSTGRESQL__PASSWORD",
    "worker-svc123/PG_PASS",
)
START = datetime(2026, 10, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _only_these_values_are_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`scrub` remembers every value in the process, and another test's `Value("v")` would redact
    every "v" in the messages asserted here."""
    monkeypatch.setattr(values, "_TOUCHED", {OLD.reveal(), NEW.reveal()})


def _print(value: str) -> str:
    fingerprint = Value(value).fingerprint
    return f"{fingerprint.sha256_prefix}:{fingerprint.length}"


class Clock:
    def __init__(self) -> None:
        self.now = START

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
        # Answers `loaded` gives first, in order; an exception is raised.
        self.loaded_script: list[dict[str, str] | Exception] = []
        # The compose orders nothing, so a restart takes the database down past the consumers.
        self.down_after_restart = 2
        self.down = 0
        # How long a POST takes the database down whatever Coolify answers: it may run regardless.
        self.down_at_post = 0
        self.ignores_restart = False
        # How many more requests Coolify answers; `None` means it stays up.
        self.requests_left: int | None = None
        # Whether a restart arriving as Coolify dies happens, with its answer lost.
        self.restart_lands = False
        # Whether a refused variable write landed anyway.
        self.patch_lands = False
        # Which POST, counting from 1, Coolify refuses with a 409.
        self.refused_post: int | None = None
        self.posts = 0
        # An undo's POST, by number: the status it answers (0 for no answer), and whether the
        # restart ran anyway, bringing the consumers up after the next `loaded` read.
        self.post_answers: dict[int, tuple[int, bool]] = {}
        self.landing = False
        self.patched_at: list[datetime] = []
        self.now: Callable[[], datetime] = lambda: START
        # Each call taking just under its limit, on the test's clock.
        self.tick: Callable[[float], None] = lambda _seconds: None

    def _failing(self, stage: str) -> None:
        if stage in self.fail:
            error, left = self.fail[stage]
            if left is None or left > 0:
                if left is not None:
                    self.fail[stage] = (error, left - 1)
                raise error

    def _restarted(self) -> None:
        if not self.ignores_restart:
            self.running = dict.fromkeys(CONSUMERS, self.variable)
            self.down = self.down_after_restart

    def _coolify_answers(self, request: httpx.Request) -> None:
        self.tick(59)
        if self.requests_left is None:
            return
        if self.requests_left == 0:
            if request.method == "POST" and self.restart_lands:
                self._restarted()
            raise httpx.ConnectTimeout("no answer", request=request)
        self.requests_left -= 1

    def coolify(self, request: httpx.Request) -> httpx.Response:
        self._coolify_answers(request)
        if request.method == "PATCH":
            self.patched_at.append(self.now())
            if self.patch_lands:
                self.variable = json.loads(request.content)["value"]
            self._failing("set_variable")
            self.variable = json.loads(request.content)["value"]
            self.log.append(f"set_variable:{self.variable[:2]}")
        else:
            self.posts += 1
            self.down += self.down_at_post
            if self.posts == self.refused_post:
                return httpx.Response(409, json={})
            if self.posts in self.post_answers:
                status, self.landing = self.post_answers[self.posts]
                if status == 0:
                    raise httpx.ConnectTimeout("no answer", request=request)
                return httpx.Response(status, json={})
            self._failing("restart")
            self.log.append("restart")
            self._restarted()
        return httpx.Response(200, json={})

    def set(self, target: str, value: Value) -> bool:
        assert target == "alobar-id"
        self.tick(89)
        self._failing("set")
        if self.down:
            self.down -= 1
            raise HelperRefused("set", "probe_unavailable")
        self.log.append(f"set:{value.reveal()[:2]}")
        changed = self.password != value.reveal()
        self.password = value.reveal()
        return changed

    def probe(self, target: str, value: Value) -> int | None:
        assert target == "alobar-id"
        self.tick(29)
        self._failing("probe")
        self.log.append(f"probe:{value.reveal()[:2]}")
        if self.down:
            self.down -= 1
            return None
        return 200 if value.reveal() == self.password else 401

    def loaded(self, target: str) -> dict[str, str]:
        assert target == "alobar-id"
        self.tick(29)
        self._failing("loaded")
        self.log.append("loaded")
        if self.loaded_script:
            answer = self.loaded_script.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if self.loaded_answer is not None:
            return self.loaded_answer
        answer = {name: _print(value) for name, value in self.running.items()}
        if self.landing:
            self.landing = False
            self._restarted()
        return answer

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


KEEPER_DESTINATION = f'bws-secret uuid="{KEEPER}"'
EVERY_DESTINATION = [
    KEEPER_DESTINATION,
    'coolify-env-hash instance="prod" resource_type="application" uuid="u" key="K" entry="E"',
    'coolify-env-held instance="prod" resource_type="application" uuid="u" key="H"',
    'gh-actions-secret repo="AlobarQuest/x" name="S"',
]
SWITCH = ["verify_new", "store", "distribute", "verify_consumers"]
RETIRE = ("retire", ["verify_consumers", "retire_old", "confirm_dead"])
BY_PERSON = [
    ("mint", ["mint", "stage"]),
    ("store", ["verify_new", "store", "distribute"]),
    ("verify", ["verify_consumers"]),
    ("revoke", ["retire_old"]),
    ("confirm", ["confirm_dead"]),
]
# Written out, not derived, so a change to `derive` shows here.
EXISTING_PLANS: dict[str, list[tuple[str, list[str]]]] = {
    "openrouter": [("mint", ["mint", "stage", *SWITCH]), RETIRE],
    "orchestrator": [("mint", ["mint", "stage", "host", *SWITCH]), RETIRE],
    "change-manager": [("mint", ["mint", "stage", "host", *SWITCH]), RETIRE],
    "github-pat": [("mint", ["mint", "stage"]), ("store", SWITCH), RETIRE],
    "bitbucket": BY_PERSON,
    "bws-machine-token": BY_PERSON,
}


@pytest.mark.parametrize("name", EXISTING_PLANS)
def test_existing_types_derive_unchanged(name: str) -> None:
    """Against a hosted, a held and an Actions destination, so every branch of `derive` runs."""
    capabilities = TYPES[name].capabilities
    assert capabilities.overlap is True
    for shape in (capabilities, replace(capabilities, overlap=False)):
        plan = derive(shape, EVERY_DESTINATION, KEEPER_DESTINATION, "bws-secret")
        rendered = [
            (segment.name, [step.kind.value for step in segment.steps]) for segment in plan.segments
        ]
        assert rendered == EXISTING_PLANS[name]


def test_every_existing_type_has_a_pinned_plan() -> None:
    assert set(EXISTING_PLANS) == set(TYPES) - {pg.NAME}


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


def _elapsed(built: Built) -> timedelta:
    return built.clock.now - START


def _assert_restored(built: Built) -> None:
    authentik = built.authentik
    assert (authentik.variable, authentik.password) == (OLD.reveal(), OLD.reveal())
    assert set(authentik.running.values()) == {OLD.reveal()}
    assert authentik.writes()[-3:] == ["set_variable:a0", "set:a0", "restart"]
    # The last thing done was to prove the database back, after the restart.
    assert authentik.log[-1] == f"probe:{OLD.reveal()[:2]}"


def test_host_order_is_coolify_then_set_then_restart_then_loaded() -> None:
    built = Built()
    note = built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]
    after_restart = built.authentik.log[built.authentik.log.index("restart") + 1 :]
    assert after_restart[0] == "loaded"
    assert NEW.reveal() not in note
    assert (built.authentik.variable, built.authentik.password) == (NEW.reveal(), NEW.reveal())
    assert set(built.authentik.running.values()) == {NEW.reveal()}


def test_host_waits_for_the_database_after_the_consumers_are_back() -> None:
    """The compose orders nothing, so the consumers can be up with the database still down."""
    built = Built()
    built.authentik.down_after_restart = 3
    built.provider.host(NEW, OLD)
    assert built.authentik.down == 0
    assert built.authentik.log[-1] == f"probe:{NEW.reveal()[:2]}"
    assert built.provider.probe(NEW) == 200


def test_a_database_that_never_comes_back_is_not_a_finished_host() -> None:
    built = Built()
    built.authentik.down_after_restart = 10_000
    with pytest.raises(ContractError, match="failed too"):
        built.provider.host(NEW, OLD)


def test_the_undo_reports_restored_only_with_the_database_back() -> None:
    built = Built()
    built.authentik.fail["restart"] = (ContractError("Coolify POST answered HTTP 500"), 1)
    built.authentik.down_after_restart = 3
    with pytest.raises(ContractError, match="restored"):
        built.provider.host(NEW, OLD)
    assert built.authentik.down == 0
    assert built.provider.probe(OLD) == 200


def test_host_with_everything_in_place_writes_only_the_variable() -> None:
    """A later step's failure re-runs host, and a second restart would be a second outage."""
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.log.clear()
    note = built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1"]
    assert "already" in note


def test_host_puts_a_variable_a_crash_left_on_old_back_on_new() -> None:
    """V=old, R=new, C=new: without this the next restart loads the old value and sign-in stops."""
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.variable = OLD.reveal()
    built.authentik.log.clear()
    built.provider.host(NEW, OLD)
    assert built.authentik.variable == NEW.reveal()
    assert "restart" not in built.authentik.writes()


def test_a_failed_blind_write_fails_host_and_changes_nothing_else() -> None:
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.log.clear()
    built.authentik.fail["set_variable"] = (ContractError("Coolify PATCH answered HTTP 500"), None)
    with pytest.raises(ContractError, match="HTTP 500.*next restart may.*pg-restore"):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []
    assert built.authentik.password == NEW.reveal()


def test_a_failed_blind_write_carries_no_value_from_any_exception() -> None:
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.fail["set_variable"] = (KeyError(NEW.reveal()), 1)
    with pytest.raises(ContractError, match="KeyError") as caught:
        built.provider.host(NEW, OLD)
    assert NEW.reveal() not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_host_finishes_when_the_database_took_the_value_and_nothing_restarted() -> None:
    built = Built()
    built.authentik.variable, built.authentik.password = NEW.reveal(), NEW.reveal()
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_a_refused_early_read_with_the_new_value_accepted_writes_nothing() -> None:
    """A healthy service isn't restarted because one read failed."""
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.log.clear()
    built.authentik.fail["loaded"] = (HelperRefused("loaded", "docker_unavailable"), 1)
    with pytest.raises(ContractError, match="couldn't be read: nothing was written"):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []
    assert built.authentik.password == NEW.reveal()


def test_a_crash_after_set_finishes_on_the_next_attempt() -> None:
    """V=new, R=new, C=old: a crash between set and restart."""
    built = Built()
    built.authentik.variable, built.authentik.password = NEW.reveal(), NEW.reveal()
    built.provider.host(NEW, OLD)
    assert set(built.authentik.running.values()) == {NEW.reveal()}
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_a_failed_first_write_with_the_role_on_new_is_not_called_unchanged() -> None:
    """The consumers still show old, but the role doesn't, so "restored" needs the real writes."""
    built = Built()
    built.authentik.variable, built.authentik.password = NEW.reveal(), NEW.reveal()
    built.authentik.fail["set_variable"] = (ContractError("Coolify PATCH answered HTTP 500"), 1)
    with pytest.raises(ContractError, match="HTTP 500.*restored"):
        built.provider.host(NEW, OLD)
    _assert_restored(built)
    assert built.provider.probe(OLD) == 200


def test_host_refuses_before_any_write_when_neither_value_is_accepted() -> None:
    built = Built()
    built.authentik.password = "c2" * 32
    with pytest.raises(ContractError, match="may be down: never guess"):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == []


def test_host_refuses_before_any_write_when_the_control_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that accepts everything would make "already in place" read true."""
    built = Built()
    monkeypatch.setattr(built.authentik, "probe", lambda _target, _value: 200)
    with pytest.raises(ContractError, match="never guess"):
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
    built.authentik.down_after_restart = 0
    built.authentik.loaded_script = [
        {CONSUMERS[0]: "", CONSUMERS[1]: ""},
        {CONSUMERS[0]: _print(NEW.reveal()), CONSUMERS[1]: _print(OLD.reveal())},
    ]
    built.provider.host(NEW, OLD)
    assert built.authentik.loaded_script == []
    assert _elapsed(built) == timedelta(seconds=10)


def test_a_helper_refusal_while_waiting_is_asked_again() -> None:
    built = Built()
    built.authentik.loaded_script = [HelperRefused("loaded", "docker_unavailable")] * 2
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_an_empty_loaded_answer_is_not_every_consumer_matching() -> None:
    built = Built()
    built.authentik.loaded_answer = {}
    with pytest.raises(ContractError):
        built.provider.host(NEW, OLD)
    assert "restart" in built.authentik.writes()


def test_the_database_write_is_asked_again_while_the_database_is_down() -> None:
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", "probe_unavailable"), 2)
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


def test_no_answer_from_the_helper_is_asked_again() -> None:
    built = Built()
    built.authentik.fail["set"] = (ContractError("the helper didn't answer in time"), 2)
    built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


@pytest.mark.parametrize(
    "reason", ["alter_failed", "alter_unproven", "unknown_target", "bad_target"]
)
def test_a_permanent_refusal_of_set_is_not_asked_again(reason: str) -> None:
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", reason), 1)
    with pytest.raises(ContractError, match=f"{reason}.*restored"):
        built.provider.host(NEW, OLD)
    assert _elapsed(built) < timedelta(seconds=30)
    _assert_restored(built)


@pytest.mark.parametrize(
    "error",
    [HelperRefused("set", "probe_unavailable"), ContractError("the helper didn't answer in time")],
)
def test_the_retry_of_set_ends(error: Exception) -> None:
    built = Built()
    built.authentik.fail["set"] = (error, None)
    with pytest.raises(ContractError, match="failed too"):
        built.provider.host(NEW, OLD)
    assert _elapsed(built) <= 2 * (pg.SET_RETRY + timedelta(seconds=pg.POLL_SECONDS))


def test_a_failure_at_set_restores_the_old_value_and_raises() -> None:
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", "alter_failed"), 1)
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
    built.authentik.ignores_restart = True
    with pytest.raises(ContractError, match="in time.*restored"):
        built.provider.host(NEW, OLD)
    # The host's whole wait, then the undo's, which finds the old value at once.
    assert pg.LOADED_WAIT <= _elapsed(built) <= pg.LOADED_WAIT + timedelta(seconds=pg.POLL_SECONDS)
    assert built.authentik.writes()[-3:] == ["set_variable:a0", "set:a0", "restart"]
    assert (built.authentik.variable, built.authentik.password) == (OLD.reveal(), OLD.reveal())


def test_a_first_write_that_failed_with_nothing_changed_restarts_nothing() -> None:
    built = Built()
    built.authentik.fail["set_variable"] = (ContractError("Coolify PATCH answered HTTP 500"), 1)
    with pytest.raises(ContractError, match="HTTP 500.*restored"):
        built.provider.host(NEW, OLD)
    assert built.authentik.writes() == ["set_variable:a0"]


def test_a_first_write_failing_with_no_refusal_restores_too() -> None:
    built = Built()
    built.authentik.fail["set_variable"] = (KeyError(NEW.reveal()), 1)
    with pytest.raises(ContractError, match="KeyError.*restored") as caught:
        built.provider.host(NEW, OLD)
    assert NEW.reveal() not in str(caught.value)


def test_a_first_write_that_landed_anyway_is_written_back() -> None:
    built = Built()
    built.authentik.patch_lands = True
    built.authentik.fail["set_variable"] = (ContractError("Coolify PATCH answered HTTP 500"), 1)
    with pytest.raises(ContractError, match="restored"):
        built.provider.host(NEW, OLD)
    assert built.authentik.variable == OLD.reveal()
    assert "restart" not in built.authentik.writes()


def test_a_first_write_that_failed_and_cant_be_read_restarts_to_be_sure() -> None:
    built = Built()
    built.authentik.fail["set_variable"] = (ContractError("Coolify PATCH answered HTTP 500"), 1)
    built.authentik.fail["loaded"] = (HelperRefused("loaded", "docker_unavailable"), 1)
    with pytest.raises(ContractError, match="restored"):
        built.provider.host(NEW, OLD)
    _assert_restored(built)


@pytest.mark.parametrize("stage", ["set", "restart", "loaded"])
def test_an_exception_that_is_no_refusal_restores_too(stage: str) -> None:
    """The value inside the exception must not survive into the unit's failure reason."""
    built = Built()
    if stage == "loaded":
        built.authentik.loaded_script = [KeyError(NEW.reveal())]
    else:
        built.authentik.fail[stage] = (KeyError(NEW.reveal()), 1)
    with pytest.raises(ContractError, match="KeyError.*restored") as caught:
        built.provider.host(NEW, OLD)
    assert NEW.reveal() not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    _assert_restored(built)


def test_a_failed_undo_names_both_failures_with_no_value() -> None:
    built = Built()
    built.authentik.fail["set"] = (ContractError(f"first {NEW.reveal()}"), 1)
    # Past host's retry window, so the first failure stands.
    built.authentik.tick = lambda seconds: built.clock.sleep(seconds + 100)
    built.authentik.fail["restart"] = (KeyError(OLD.reveal()), None)
    with pytest.raises(ContractError) as caught:
        built.provider.host(NEW, OLD)
    message = str(caught.value)
    assert "first" in message and "KeyError" in message and "pg-restore" in message
    assert "is restored" not in message
    assert NEW.reveal() not in message and OLD.reveal() not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_restored_is_never_reported_on_containers_a_restart_didnt_reach() -> None:
    """The consumers still show the old value, but Coolify refused the undo's restart."""
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", "alter_failed"), 1)
    built.authentik.fail["restart"] = (ContractError("Coolify POST answered HTTP 503"), None)
    with pytest.raises(ContractError, match="HTTP 503") as caught:
        built.provider.host(NEW, OLD)
    assert set(built.authentik.running.values()) == {OLD.reveal()}
    assert "is restored" not in str(caught.value)


def test_a_refusal_quoting_a_value_is_scrubbed_from_the_message() -> None:
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", NEW.reveal()), 1)
    with pytest.raises(ContractError, match=".*restored") as caught:
        built.provider.host(NEW, OLD)
    assert NEW.reveal() not in str(caught.value)


def test_restore_runs_the_same_writes_for_the_value_given() -> None:
    """The CLI's recovery: the keeper's value, written the same way, with no undo of its own."""
    built = Built()
    built.provider.host(NEW, OLD)
    built.authentik.log.clear()
    note = built.provider.restore(OLD)
    _assert_restored(built)
    assert OLD.reveal() not in note


def test_restore_refuses_when_the_value_never_loads_and_writes_no_other_value() -> None:
    built = Built()
    built.authentik.ignores_restart = True
    with pytest.raises(ContractError, match="in time"):
        built.provider.restore(NEW)
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "restart"]


# ---- the undo without Coolify ----------------------------------------------------------------


def _coolify_dies_at_the_restart(built: Built, *, lands: bool) -> None:
    built.authentik.requests_left = 1
    built.authentik.restart_lands = lands


def _says_pg_restore_is_due(error: ContractError) -> None:
    assert "may still hold the replaced value" in str(error)
    assert "pg-restore once Coolify returns" in str(error)
    assert "is restored" not in str(error)


def test_patch_and_restart_both_failing_moves_the_role_back_to_the_consumers() -> None:
    built = Built()
    _coolify_dies_at_the_restart(built, lands=False)
    with pytest.raises(ContractError) as caught:
        built.provider.host(NEW, OLD)
    _says_pg_restore_is_due(caught.value)
    authentik = built.authentik
    assert authentik.password == OLD.reveal()
    assert set(authentik.running.values()) == {OLD.reveal()}
    # The variable is host's, and nothing could write it back.
    assert authentik.variable == NEW.reveal()
    assert "every consumer is on the value" in str(caught.value)


def test_a_restart_that_landed_with_every_consumer_on_new_is_left_alone() -> None:
    """That is host's finished state, and setting the role back would take them all down."""
    built = Built()
    _coolify_dies_at_the_restart(built, lands=True)
    with pytest.raises(ContractError) as caught:
        built.provider.host(NEW, OLD)
    _says_pg_restore_is_due(caught.value)
    assert built.authentik.password == NEW.reveal()
    assert built.authentik.writes() == ["set_variable:b1", "set:b1"]
    assert "role was left alone" in str(caught.value)


@pytest.mark.parametrize("blank_reads", [2, 14])
def test_a_restart_that_landed_late_has_the_role_follow_the_consumers_onto_new(
    blank_reads: int,
) -> None:
    """The database is still coming up. 14 reads is past `SET_RETRY` from the undo's start."""
    built = Built()
    _coolify_dies_at_the_restart(built, lands=True)
    built.authentik.loaded_script = [dict.fromkeys(CONSUMERS, "")] * blank_reads
    with pytest.raises(ContractError, match="the role followed") as caught:
        built.provider.host(NEW, OLD)
    _says_pg_restore_is_due(caught.value)
    assert built.authentik.password == NEW.reveal()
    assert built.authentik.writes() == ["set_variable:b1", "set:b1", "set:b1"]


def _calls_but_not_sets_take_their_limit(built: Built) -> None:
    """Past `SET_RETRY` from the start of the writes, with each `set` quick enough to retry."""
    built.authentik.tick = lambda seconds: built.clock.sleep(0 if seconds == 89 else seconds)


def _coolify_gone_with_the_database_down(built: Built) -> None:
    built.authentik.requests_left = 0
    built.authentik.down = 1


def test_a_follow_at_the_first_read_has_its_own_retry_onto_the_restored_value() -> None:
    built = Built()
    _calls_but_not_sets_take_their_limit(built)
    _coolify_gone_with_the_database_down(built)
    built.authentik.password = NEW.reveal()
    with pytest.raises(ContractError, match="every consumer is on the value"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == OLD.reveal()


def test_a_follow_at_the_first_read_has_its_own_retry_onto_the_replaced_value() -> None:
    built = Built()
    _calls_but_not_sets_take_their_limit(built)
    built.authentik.refused_post = 1
    built.authentik.down_at_post = 1
    built.authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    with pytest.raises(ContractError, match="every consumer is on the replaced value"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == NEW.reveal()


def test_consumers_coming_up_late_have_the_role_retry_onto_the_restored_value() -> None:
    built = Built()
    _calls_but_not_sets_take_their_limit(built)
    _coolify_gone_with_the_database_down(built)
    built.authentik.password = NEW.reveal()
    built.authentik.loaded_script = [dict.fromkeys(CONSUMERS, "")]
    with pytest.raises(ContractError, match="came up on the value"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == OLD.reveal()


def test_consumers_coming_up_late_have_the_role_retry_onto_the_replaced_value() -> None:
    built = Built()
    _calls_but_not_sets_take_their_limit(built)
    _coolify_gone_with_the_database_down(built)
    built.authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    built.authentik.loaded_script = [dict.fromkeys(CONSUMERS, "")]
    with pytest.raises(ContractError, match="came up on the replaced value"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == NEW.reveal()


def test_a_watched_restart_has_the_role_retry_onto_the_value_they_stayed_on() -> None:
    built = Built()
    _calls_but_not_sets_take_their_limit(built)
    built.authentik.post_answers[1] = (502, False)
    built.authentik.down_at_post = 1
    built.authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    with pytest.raises(ContractError, match="stayed on the replaced value"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == NEW.reveal()


def test_a_variable_write_failing_with_no_refusal_takes_the_same_branch() -> None:
    built = Built()
    built.authentik.fail["set_variable"] = (KeyError(OLD.reveal()), None)
    with pytest.raises(ContractError, match="KeyError") as caught:
        built.provider.restore(OLD)
    _says_pg_restore_is_due(caught.value)
    assert OLD.reveal() not in str(caught.value)


def test_the_role_actually_moves_to_the_replaced_value_when_it_follows() -> None:
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.password = OLD.reveal()
    built.authentik.loaded_script = [
        dict.fromkeys(CONSUMERS, ""),
        dict.fromkeys(CONSUMERS, _print(NEW.reveal())),
    ]
    with pytest.raises(ContractError, match="the role followed"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == NEW.reveal()


def test_consumers_coming_up_on_the_restored_value_take_the_role_there() -> None:
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.password = NEW.reveal()
    built.authentik.loaded_script = [
        dict.fromkeys(CONSUMERS, ""),
        dict.fromkeys(CONSUMERS, _print(OLD.reveal())),
    ]
    with pytest.raises(ContractError, match="came up on the value"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.password == OLD.reveal()


def test_consumers_that_never_agree_leave_the_role_alone() -> None:
    built = Built()
    _coolify_dies_at_the_restart(built, lands=False)
    built.authentik.loaded_answer = {
        CONSUMERS[0]: _print(NEW.reveal()),
        CONSUMERS[1]: _print(OLD.reveal()),
    }
    with pytest.raises(ContractError, match="never agreed") as caught:
        built.provider.host(NEW, OLD)
    _says_pg_restore_is_due(caught.value)
    assert built.authentik.password == NEW.reveal()
    assert _elapsed(built) >= pg.LOADED_WAIT


def test_a_blank_answer_from_every_consumer_is_not_agreement() -> None:
    built = Built()
    _coolify_dies_at_the_restart(built, lands=False)
    built.authentik.loaded_answer = dict.fromkeys(CONSUMERS, "")
    with pytest.raises(ContractError, match="never agreed"):
        built.provider.host(NEW, OLD)
    assert built.authentik.password == NEW.reveal()


def test_a_failed_read_of_loaded_leaves_everything_alone_and_says_so() -> None:
    built = Built()
    _coolify_dies_at_the_restart(built, lands=False)
    built.authentik.fail["loaded"] = (HelperRefused("loaded", "docker_unavailable"), None)
    with pytest.raises(ContractError, match="couldn't be read") as caught:
        built.provider.host(NEW, OLD)
    _says_pg_restore_is_due(caught.value)
    assert built.authentik.password == NEW.reveal()
    assert built.authentik.writes() == ["set_variable:b1", "set:b1"]


def test_one_failed_read_is_enough_to_leave_everything_alone() -> None:
    """A later read could tell, but the rule is that a failed read touches nothing."""
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.password = NEW.reveal()
    built.authentik.loaded_script = [HelperRefused("loaded", "docker_unavailable")]
    with pytest.raises(ContractError, match="couldn't be read"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.writes() == []


def test_a_failed_read_while_polling_leaves_everything_alone_too() -> None:
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.password = NEW.reveal()
    built.authentik.loaded_script = [
        dict.fromkeys(CONSUMERS, ""),
        HelperRefused("loaded", "docker_unavailable"),
    ]
    with pytest.raises(ContractError, match="couldn't be read"):
        built.provider.restore(OLD, NEW)
    assert built.authentik.writes() == []


def test_a_role_that_wont_move_is_named_with_pg_restore_still_due() -> None:
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.fail["set"] = (HelperRefused("set", "alter_failed"), None)
    with pytest.raises(ContractError, match="setting the role failed too.*alter_failed") as caught:
        built.provider.restore(OLD)
    _says_pg_restore_is_due(caught.value)


def test_restore_without_coolify_needs_no_replaced_value() -> None:
    """Task 5's CLI has only the keeper's value: on old it sets it, on another it stops."""
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.password = NEW.reveal()
    with pytest.raises(ContractError, match="every consumer is on the value"):
        built.provider.restore(OLD)
    assert built.authentik.password == OLD.reveal()

    built = Built()
    built.authentik.requests_left = 0
    built.authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    with pytest.raises(ContractError, match="role was left alone"):
        built.provider.restore(OLD)
    assert built.authentik.writes() == []


def test_restore_with_no_replaced_value_never_guesses_one() -> None:
    built = Built()
    built.authentik.requests_left = 0
    built.authentik.loaded_script = [dict.fromkeys(CONSUMERS, "")]
    built.authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    with pytest.raises(ContractError, match="never agreed"):
        built.provider.restore(OLD)
    assert built.authentik.writes() == []


def _restart_refused_after_the_wait(built: Built) -> None:
    """Host's restart landed, its wait saw nothing, and Coolify refuses the undo's restart."""
    built.authentik.down_after_restart = 0
    built.authentik.refused_post = 2
    polls = int(pg.LOADED_WAIT.total_seconds() / pg.POLL_SECONDS)
    built.authentik.loaded_script = [dict.fromkeys(CONSUMERS, "")] * polls


def _says_the_restart_is_owed(error: ContractError) -> None:
    assert "HTTP 409" in str(error)
    assert "may not have loaded the value" in str(error)
    assert "pg-restore once Coolify returns" in str(error)
    assert "is restored" not in str(error)


def test_a_refused_undo_restart_has_the_role_follow_the_consumers_at_once() -> None:
    """The undo set the role to old with the containers on new, and they can't reload."""
    built = Built()
    _restart_refused_after_the_wait(built)
    with pytest.raises(ContractError, match="the role followed") as caught:
        built.provider.host(NEW, OLD)
    _says_the_restart_is_owed(caught.value)
    authentik = built.authentik
    assert authentik.writes()[-2:] == ["set:a0", "set:b1"]
    assert authentik.password == NEW.reveal()
    assert set(authentik.running.values()) == {NEW.reveal()}
    assert authentik.variable == OLD.reveal()


def test_a_refused_undo_restart_follows_consumers_still_coming_up() -> None:
    built = Built()
    _restart_refused_after_the_wait(built)
    built.authentik.loaded_script += [dict.fromkeys(CONSUMERS, "")] * 3
    with pytest.raises(ContractError, match="the role followed") as caught:
        built.provider.host(NEW, OLD)
    _says_the_restart_is_owed(caught.value)
    assert built.authentik.password == NEW.reveal()


def test_a_refused_undo_restart_with_consumers_on_old_stays_on_old() -> None:
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", "alter_failed"), 1)
    built.authentik.refused_post = 1
    with pytest.raises(ContractError, match="every consumer is on the value") as caught:
        built.provider.host(NEW, OLD)
    _says_the_restart_is_owed(caught.value)
    assert built.authentik.password == OLD.reveal()


def test_a_restart_failing_with_no_refusal_is_unconfirmed_too() -> None:
    built = Built()
    built.authentik.fail["restart"] = (KeyError(OLD.reveal()), None)
    with pytest.raises(ContractError, match="unconfirmed.*KeyError") as caught:
        built.provider.restore(OLD)
    assert "pg-restore once Coolify returns" in str(caught.value)
    assert OLD.reveal() not in str(caught.value)


def _undo_restart_answers(built: Built, status: int, *, landed: bool) -> None:
    """Host's restart landed and its wait saw nothing; the undo's POST answers `status`."""
    _restart_refused_after_the_wait(built)
    built.authentik.refused_post = None
    built.authentik.post_answers[2] = (status, landed)


@pytest.mark.parametrize("status", [0, 502])
def test_a_restart_that_landed_unconfirmed_leaves_the_role_with_what_comes_up(status: int) -> None:
    """The consumers show new at the first read, and following them there would strand them."""
    built = Built()
    _undo_restart_answers(built, status, landed=True)
    with pytest.raises(ContractError, match="unconfirmed.*the role is on the old value") as caught:
        built.provider.host(NEW, OLD)
    authentik = built.authentik
    assert (authentik.password, set(authentik.running.values())) == (OLD.reveal(), {OLD.reveal()})
    assert authentik.writes()[-2:] == ["set_variable:a0", "set:a0"]
    assert "pg-restore once Coolify returns" in str(caught.value)
    # Consumers on the role's value end the watch: sign-in is back, and nothing is left to wait for.
    assert _undo_elapsed(built) < timedelta(seconds=pg.POLL_SECONDS * 2)


@pytest.mark.parametrize("status", [0, 502])
def test_a_restart_that_never_landed_is_followed_only_at_the_end_of_the_window(
    status: int,
) -> None:
    built = Built()
    _undo_restart_answers(built, status, landed=False)
    with pytest.raises(ContractError, match="stayed on the replaced value.*the role followed"):
        built.provider.host(NEW, OLD)
    authentik = built.authentik
    assert (authentik.password, set(authentik.running.values())) == (NEW.reveal(), {NEW.reveal()})
    assert _undo_elapsed(built) >= pg.LOADED_WAIT


def _undo_elapsed(built: Built) -> timedelta:
    """How long the undo ran: everything after host's own wait."""
    return _elapsed(built) - pg.LOADED_WAIT


def test_a_409_proves_no_restart_ran_so_the_role_follows_at_once() -> None:
    built = Built()
    _restart_refused_after_the_wait(built)
    with pytest.raises(ContractError, match="HTTP 409.*onto the new value"):
        built.provider.host(NEW, OLD)
    assert built.authentik.password == NEW.reveal()
    assert _undo_elapsed(built) < timedelta(seconds=pg.POLL_SECONDS)


def test_only_a_4xx_is_coolify_refusing() -> None:
    built = Built()
    built.authentik.refused_post = 1
    with pytest.raises(CoolifyRefused) as caught:
        built.provider._service().restart()
    assert caught.value.status == 409

    built = Built()
    built.authentik.post_answers[1] = (502, False)
    with pytest.raises(ContractError) as answered:
        built.provider._service().restart()
    assert not isinstance(answered.value, CoolifyRefused)


def test_a_refused_restart_with_consumers_split_reports_where_the_role_went() -> None:
    built = Built()
    authentik = built.authentik
    authentik.variable, authentik.password = NEW.reveal(), NEW.reveal()
    authentik.running = {CONSUMERS[0]: NEW.reveal(), CONSUMERS[1]: OLD.reveal()}
    authentik.refused_post = 1
    with pytest.raises(ContractError, match="never agreed, and the role is on the old value"):
        built.provider.restore(OLD, NEW)
    assert authentik.password == OLD.reveal()


def test_an_unconfirmed_restart_with_consumers_split_reports_where_the_role_went() -> None:
    built = Built()
    authentik = built.authentik
    authentik.running = {CONSUMERS[0]: NEW.reveal(), CONSUMERS[1]: OLD.reveal()}
    authentik.post_answers[1] = (502, False)
    with pytest.raises(ContractError, match="never agreed, and the role is on the old value"):
        built.provider.restore(OLD, NEW)
    assert authentik.password == OLD.reveal()


def test_an_unconfirmed_restart_with_no_replaced_value_never_guesses_one() -> None:
    built = Built()
    authentik = built.authentik
    authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    authentik.post_answers[1] = (502, False)
    with pytest.raises(ContractError, match="never agreed, and the role is on the value being"):
        built.provider.restore(OLD)
    assert authentik.password == OLD.reveal()


def test_a_failed_read_after_an_unconfirmed_restart_says_where_the_role_is() -> None:
    built = Built()
    authentik = built.authentik
    authentik.post_answers[1] = (502, False)
    authentik.fail["loaded"] = (HelperRefused("loaded", "docker_unavailable"), None)
    with pytest.raises(ContractError, match="couldn't be read.*the role is on the old value"):
        built.provider.restore(OLD, NEW)
    assert authentik.writes() == ["set_variable:a0", "set:a0"]


def test_a_failed_read_after_a_refused_restart_says_where_the_role_is() -> None:
    built = Built()
    built.authentik.refused_post = 1
    built.authentik.fail["loaded"] = (HelperRefused("loaded", "docker_unavailable"), None)
    with pytest.raises(ContractError, match="couldn't be read.*the role is on the old value"):
        built.provider.restore(OLD, NEW)


def test_restore_with_a_refused_restart_and_no_replaced_value_says_the_role_moved() -> None:
    """`set` ran before the restart was refused, so "left alone" would be false."""
    built = Built()
    authentik = built.authentik
    authentik.variable, authentik.password = NEW.reveal(), NEW.reveal()
    authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    authentik.refused_post = 1
    with pytest.raises(ContractError) as caught:
        built.provider.restore(OLD)
    _says_the_restart_is_owed(caught.value)
    assert "the role is on the value being restored" in str(caught.value)
    assert "left alone" not in str(caught.value)
    assert authentik.password == OLD.reveal()
    assert authentik.writes() == ["set_variable:a0", "set:a0"]


def test_a_role_left_on_old_under_consumers_on_new_isnt_claimed_to_match() -> None:
    """With Coolify gone that branch never reads the role, so the words can't say where it is."""
    built = Built()
    authentik = built.authentik
    authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    authentik.requests_left = 1
    authentik.fail["set"] = (HelperRefused("set", "alter_failed"), 1)
    with pytest.raises(ContractError, match="another value, so the role was left alone") as caught:
        built.provider.host(NEW, OLD)
    _says_pg_restore_is_due(caught.value)
    assert authentik.password == OLD.reveal()


def test_an_accepted_restart_that_hasnt_happened_yet_is_reported_restored() -> None:
    """Coolify queues a restart, so its acceptance proves nothing has come back yet. The values
    are right, and what the queued restart then does is for the live run to measure."""
    built = Built()
    built.authentik.fail["set"] = (HelperRefused("set", "alter_failed"), 1)
    built.authentik.ignores_restart = True
    with pytest.raises(ContractError, match="restored"):
        built.provider.host(NEW, OLD)
    assert (built.authentik.variable, built.authentik.password) == (OLD.reveal(), OLD.reveal())
    assert set(built.authentik.running.values()) == {OLD.reveal()}
    assert built.authentik.writes()[-1] == "restart"


class Cycling:
    """A helper on a clock: the database is down for a time after a restart, not for some calls."""

    def __init__(self, authentik: Authentik, clock: Clock) -> None:
        self.authentik, self.clock = authentik, clock
        self.down_until: datetime | None = None

    def _up(self) -> bool:
        return self.down_until is None or self.clock.now >= self.down_until

    def set(self, _target: str, value: Value) -> bool:
        self.clock.sleep(2)
        if not self._up():
            raise HelperRefused("set", "probe_unavailable")
        self.authentik.password = value.reveal()
        return True

    def probe(self, _target: str, value: Value) -> int | None:
        self.clock.sleep(1)
        if not self._up():
            return None
        return 200 if value.reveal() == self.authentik.password else 401

    def loaded(self, _target: str) -> dict[str, str]:
        self.clock.sleep(1)
        running = self.authentik.running.items()
        return {name: _print(value) if self._up() else "" for name, value in running}


def _cycle_of(seconds: int) -> Built:
    """Host's restart answers 502, runs anyway, and takes the database down for `seconds`."""
    clock = Clock()
    authentik = Authentik()
    helper = Cycling(authentik, clock)
    posts: list[int] = []

    def coolify(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            authentik.variable = json.loads(request.content)["value"]
            return httpx.Response(200, json={})
        posts.append(1)
        authentik.running = dict.fromkeys(CONSUMERS, authentik.variable)
        helper.down_until = clock.now + timedelta(seconds=seconds)
        return httpx.Response(502 if len(posts) == 1 else 200, json={})

    built = Built(
        pg_helper=lambda: cast(HelperClient, helper),
        coolify_transport=httpx.MockTransport(coolify),
        clock=clock,
        sleep=clock.sleep,
    )
    built.authentik = authentik
    return built


def test_a_cycle_inside_set_retry_is_restored() -> None:
    """`SET_RETRY`'s window is measured from when the undo starts."""
    built = _cycle_of(30)
    with pytest.raises(ContractError, match="the old value is restored"):
        built.provider.host(NEW, OLD)
    assert (built.authentik.password, built.authentik.variable) == (OLD.reveal(), OLD.reveal())


def test_a_cycle_longer_than_set_retry_fails_the_undo_and_names_pg_restore() -> None:
    built = _cycle_of(90)
    with pytest.raises(ContractError, match="probe_unavailable.*pg-restore") as caught:
        built.provider.host(NEW, OLD)
    assert "is restored" not in str(caught.value)
    assert built.authentik.password == NEW.reveal()


def test_a_queued_restart_finishing_after_restored_strands_the_consumers() -> None:
    """Open for the live run: if Coolify renders the variable when it accepts a restart, a restart
    queued by host and finishing after the undo brings the consumers up on new under a role on
    old. Pinned so the measurement has a named state to compare with."""
    built = Built()
    authentik = built.authentik
    authentik.ignores_restart = True
    with pytest.raises(ContractError, match="restored"):
        built.provider.host(NEW, OLD)
    authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    assert built.provider.probe(NEW) == 401


# ---- the worst case --------------------------------------------------------------------------


def test_the_worst_case_is_within_the_lease_with_margin() -> None:
    assert pg.BEFORE_WRITES == 4 * pg.PROBE_TIMEOUT_SECONDS
    assert pg.LAST_POLL == pg.LOADED_TIMEOUT_SECONDS + 2 * pg.PROBE_TIMEOUT_SECONDS
    assert pg.WRITES_BY_CALLS == 215 < pg.HOST_WRITES == 260
    assert pg.SET_AT_ITS_LIMIT == 155
    assert pg.UNDO_WITHOUT_COOLIFY == 360
    assert pg.UNDO_RESTART_REFUSED == 400
    assert pg.WORST_CASE == timedelta(seconds=120 + 260 + 400)
    assert pg.WORST_CASE < pg.LEASE_WITH_MARGIN < DEFAULT_LEASE


class SlowestHelper:
    """Each call just under its limit, a database that takes the value and never proves it, and
    two `set`s refused just inside `SET_RETRY`: the undo's first, and the follow's first."""

    def __init__(self, authentik: Authentik, clock: Clock) -> None:
        self.authentik, self.clock = authentik, clock
        self.sets = 0

    def set(self, _target: str, value: Value) -> bool:
        self.sets += 1
        if self.sets == 2:
            raise HelperRefused("set", "probe_unavailable")
        if self.sets == 4:
            self.clock.sleep(59.9)
            raise HelperRefused("set", "probe_unavailable")
        self.clock.sleep(89.9)
        self.authentik.password = value.reveal()
        return True

    def probe(self, _target: str, value: Value) -> int | None:
        self.clock.sleep(29.9)
        if value.reveal() == NEW.reveal() == self.authentik.password:
            return None
        return 200 if value.reveal() == self.authentik.password else 401

    def loaded(self, _target: str) -> dict[str, str]:
        self.clock.sleep(29.9)
        return dict.fromkeys(CONSUMERS, _print(NEW.reveal()))


def test_the_longest_path_there_is_comes_within_five_seconds_of_the_figure() -> None:
    """Host's restart is quick enough to enter the wait, whose one poll then runs to its limit;
    the undo's set retries, its restart is refused, and the follow's set retries too. Close to
    the figure, so it can fail."""
    clock = Clock()
    authentik = Authentik()
    helper = SlowestHelper(authentik, clock)
    posts: list[int] = []

    def coolify(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            clock.sleep(59.9)
            authentik.variable = json.loads(request.content)["value"]
            return httpx.Response(200, json={})
        posts.append(1)
        clock.sleep(19.9 if len(posts) == 1 else 59.9)
        return httpx.Response(200 if len(posts) == 1 else 409, json={})

    built = Built(
        pg_helper=lambda: cast(HelperClient, helper),
        coolify_transport=httpx.MockTransport(coolify),
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(ContractError, match="the role followed"):
        built.provider.host(NEW, OLD)
    assert pg.WORST_CASE - timedelta(seconds=5) <= clock.now - START <= pg.WORST_CASE


def _at_every_limit(built: Built) -> Authentik:
    built.authentik.tick = built.clock.sleep
    built.authentik.ignores_restart = True
    built.authentik.loaded_answer = dict.fromkeys(CONSUMERS, "")
    return built.authentik


@pytest.mark.parametrize("coolify_dies", [False, True])
def test_every_call_at_its_limit_stays_inside_the_worst_case(coolify_dies: bool) -> None:
    built = Built()
    authentik = _at_every_limit(built)
    if coolify_dies:
        # Host's variable and restart are answered; the undo's variable isn't.
        authentik.requests_left = 2
    with pytest.raises(ContractError):
        built.provider.host(NEW, OLD)
    assert _elapsed(built) <= pg.WORST_CASE


def test_host_s_wait_is_measured_from_the_start_of_its_writes() -> None:
    """The undo's first write shows when host gave up, whatever its calls cost it."""
    built = Built()
    authentik = _at_every_limit(built)
    authentik.now = built.clock
    with pytest.raises(ContractError):
        built.provider.host(NEW, OLD)
    host_writes = authentik.patched_at[1] - authentik.patched_at[0]
    assert host_writes <= timedelta(seconds=pg.HOST_WRITES)


def test_the_undo_s_wait_is_measured_from_the_start_of_its_writes() -> None:
    built = Built()
    _at_every_limit(built)
    with pytest.raises(ContractError):
        built.provider.restore(OLD, NEW)
    assert _elapsed(built) <= timedelta(seconds=pg.HOST_WRITES)


def test_following_the_consumers_is_measured_from_the_start_of_the_writes() -> None:
    built = Built()
    authentik = _at_every_limit(built)
    authentik.requests_left = 0
    with pytest.raises(ContractError, match="never agreed"):
        built.provider.restore(OLD, NEW)
    # The last read starts one poll past the window, and nothing follows it.
    assert _elapsed(built) <= pg.LOADED_WAIT + timedelta(
        seconds=pg.POLL_SECONDS + pg.LOADED_TIMEOUT_SECONDS
    )


def test_watching_an_unconfirmed_restart_is_measured_from_the_start_of_the_writes() -> None:
    built = Built()
    authentik = _at_every_limit(built)
    authentik.post_answers[1] = (502, False)
    with pytest.raises(ContractError, match="never agreed"):
        built.provider.restore(OLD, NEW)
    # The calls used up the window, so one read is all that's left.
    assert _elapsed(built) <= timedelta(seconds=pg.WRITES_BY_CALLS + pg.LOADED_TIMEOUT_SECONDS)


def test_the_undo_without_coolify_stays_inside_its_figure() -> None:
    """Its last read starts one poll past the window, so the figure has to allow for that."""
    built = Built()
    authentik = _at_every_limit(built)
    authentik.requests_left = 0
    # Reads end at 89, 123 and 157; the fourth starts past the window's last check, and follows.
    authentik.loaded_script = [dict.fromkeys(CONSUMERS, "")] * 3
    authentik.loaded_answer = None
    authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    with pytest.raises(ContractError):
        built.provider.restore(OLD, NEW)
    assert _elapsed(built) <= timedelta(seconds=pg.UNDO_WITHOUT_COOLIFY)
    assert authentik.password == NEW.reveal()


def test_a_refused_undo_restart_stays_inside_its_figure() -> None:
    built = Built()
    authentik = _at_every_limit(built)
    authentik.down_after_restart = 0
    authentik.refused_post = 1
    authentik.loaded_answer = None
    authentik.running = dict.fromkeys(CONSUMERS, NEW.reveal())
    with pytest.raises(ContractError, match="the role followed"):
        built.provider.restore(OLD, NEW)
    assert _elapsed(built) <= timedelta(seconds=pg.UNDO_RESTART_REFUSED)


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
