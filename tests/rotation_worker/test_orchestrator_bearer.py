"""The orchestrator-bearer adapter, against a simulated orchestrator and Coolify.

The simulation holds what each side holds in production: Coolify stores the variable's two rows,
and the orchestrator serves the identities it loaded at its last restart and authenticates a bearer
against them. Nothing reaches a live endpoint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from rotation_worker.adapters import ADAPTERS, AdapterContext, orchestrator_bearer
from rotation_worker.adapters.orchestrator_bearer import (
    PLAN,
    SWAP_WAIT,
    OrchestratorBearerProvider,
    build_provider,
)
from rotation_worker.contract import ContractError, Rotation, Step, UnitRun
from rotation_worker.coolify import CoolifyClient, url_problem
from rotation_worker.evidence import StepEvidence
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

ORCHESTRATOR = "https://sds.test"
COOLIFY = "http://100.112.188.96:8000"
APP = "eqj5l7k705fhi12x9i74fqf0"
VARIABLE = "ORCHESTRATOR_M2M_CREDENTIALS"
ENTRY = "orchestrator-operator"
WORK = "work-project"
HOSTED = (
    f'coolify-env-hash instance="prod" resource_type="application" uuid="{APP}" '
    f'key="{VARIABLE}" entry="{ENTRY}"'
)
START = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Clock:
    def __init__(self) -> None:
        self.now = START
        self.slept = 0

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += 1
        self.now += timedelta(seconds=seconds)


class Estate:
    """Coolify's stored rows and the orchestrator's loaded identities, behind two transports."""

    def __init__(self, old: str) -> None:
        self.loaded: dict[str, Any] = {
            "orchestrator-system": {"agent_id": "sds-system", "token_hash": _hash("system")},
            ENTRY: {"agent_id": "claude-code-interactive", "token_hash": _hash(old)},
            "rotation-executor-1": {"agent_id": "rotation-executor", "token_hash": _hash("x")},
        }
        self.rows: dict[bool, str] = {}
        self.sent: list[tuple[str, str]] = []
        self.ledger: list[dict[str, Any]] = []
        self.views: dict[str, dict[str, Any]] = {}
        self.histories: dict[str, list[dict[str, Any]]] = {}
        # Statuses the probe answers before it consults the identities, one per probe request.
        self.scripted: list[int | None] = []
        self.coolify_status = 201
        self.restart_applies = True
        # A previous the restarted orchestrator loads beside what was written, for every entry.
        self.stray_previous: dict[str, Any] | None = None
        # A ledger to serve once the variable has been written, before the restart.
        self.ledger_after_write: list[dict[str, Any]] | None = None
        # Requests the old container answers after a restart, with what it loaded before.
        self.lag = 0
        self._before: dict[str, Any] | None = None

    def orchestrator(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/status-ledger" and "work_unit_id" in request.url.params:
            return self._probe(request)
        if path == "/api/v1/m2m-identities":
            return httpx.Response(200, json=self.loaded)
        if path == "/api/v1/status-ledger":
            return httpx.Response(200, json=self.ledger)
        unit_id = path.split("/")[4]
        if path.endswith("/history"):
            return httpx.Response(200, json=self.histories.get(unit_id, []))
        return httpx.Response(200, json=self.views[unit_id])

    def _probe(self, request: httpx.Request) -> httpx.Response:
        if self.scripted:
            status = self.scripted.pop(0)
            if status is None:
                raise httpx.ConnectError("mid-swap")
            return httpx.Response(status)
        identities = self.loaded
        if self.lag and self._before is not None:
            self.lag -= 1
            identities = self._before
        entry = identities.get(request.headers["X-Credential-Key-Id"], {})
        presented = _hash(request.headers["Authorization"].removeprefix("Bearer "))
        previous = entry.get("previous") or {}
        accepted = presented in (entry.get("token_hash"), previous.get("token_hash"))
        return httpx.Response(200 if accepted else 401, json=[])

    def coolify(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((request.method, request.url.path))
        if request.method == "PATCH":
            if self.coolify_status >= 300:
                return httpx.Response(self.coolify_status)
            body = json.loads(request.content)
            assert body["key"] == VARIABLE
            self.rows[body["is_preview"]] = body["value"]
            if self.ledger_after_write is not None and False in self.rows:
                self.ledger = self.ledger_after_write
            return httpx.Response(201)
        if self.restart_applies:
            self._before = self.loaded
            self.loaded = json.loads(self.rows[False])
            if self.stray_previous is not None:
                for entry in self.loaded.values():
                    entry["previous"] = self.stray_previous
        return httpx.Response(200)


class Harness:
    def __init__(self) -> None:
        self.store = MemoryStore()
        self.original = Value("the-original-operator-bearer")
        self.keeper_id = self.store.put(WORK, "orchestrator-operator-bearer", self.original)
        self.token_id = self.store.put(
            WORK, "rotation-coolify-token", Value("throwaway-coolify-token-for-tests")
        )
        self.estate = Estate(self.original.reveal())
        self.clock = Clock()
        self.rotation = Rotation(
            credential_id="orchestrator-operator-bearer",
            occurrence="2026-10-15-requested",
            keeper_id=self.keeper_id,
            work_project_id=WORK,
            destinations=(
                f'bws-secret uuid="{self.keeper_id}"',
                HOSTED,
                'provider name="orchestrator"',
            ),
        )
        self.provider = self.build()
        self.filed: list[tuple[Step, StepEvidence]] = []

    def build(self, **changes: Any) -> OrchestratorBearerProvider:
        transport = httpx.MockTransport(self.estate.orchestrator)
        arguments: dict[str, Any] = {
            "rotation": self.rotation,
            "store": self.store,
            "coolify_token_id": self.token_id,
            "orchestrator": OrchestratorClient(
                ORCHESTRATOR,
                "rotation-executor-1",
                Value("throwaway-executor-bearer-for-tests"),
                transport,
            ),
            "orchestrator_url": ORCHESTRATOR,
            "coolify_url": COOLIFY,
            "orchestrator_application": APP,
            "executor_key_id": "rotation-executor-1",
            "transport": transport,
            "coolify_transport": httpx.MockTransport(self.estate.coolify),
            "clock": self.clock,
            "sleep": self.clock.sleep,
            **changes,
        }
        return build_provider(**arguments)

    def run(self, unit: str, before_step: Callable[[], None] = lambda: None) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=before_step,
            filed=frozenset(step.kind for step, _ in self.filed),
        ).run(PLAN.unit(unit))

    def keeper(self) -> Value:
        return self.store.read(self.keeper_id)

    def entry(self) -> dict[str, Any]:
        return self.estate.loaded[ENTRY]


@pytest.fixture
def h() -> Harness:
    return Harness()


def test_a_rotation_hosts_stores_and_retires_through_the_contract(h: Harness) -> None:
    h.run("mint")
    new = h.keeper()
    assert not new.same_as(h.original)
    assert h.entry()["token_hash"] == _hash(new.reveal())
    assert h.entry()["previous"]["token_hash"] == _hash(h.original.reveal())
    h.run("retire")
    assert h.entry() == {"agent_id": "claude-code-interactive", "token_hash": _hash(new.reveal())}
    assert (
        h.estate.sent
        == [
            ("PATCH", f"/api/v1/applications/{APP}/envs"),
            ("PATCH", f"/api/v1/applications/{APP}/envs"),
            ("POST", f"/api/v1/applications/{APP}/restart"),
        ]
        * 2
    )
    # Both rows hold what was written, and every other entry survived both rewrites.
    assert h.estate.rows[False] == h.estate.rows[True]
    assert h.estate.loaded["rotation-executor-1"]["token_hash"] == _hash("x")
    assert [evidence.step for _, evidence in h.filed] == [
        "mint",
        "stage",
        "host",
        "verify_new",
        "store",
        "verify_consumers",
        "verify_consumers",
        "retire_old",
        "confirm_dead",
        "confirm_dead",
    ]
    for _, evidence in h.filed:
        assert new.reveal() not in json.dumps(evidence.as_payload())


def test_host_writes_nothing_when_the_service_already_holds_both(h: Harness) -> None:
    new = Value("already-hosted")
    h.estate.loaded[ENTRY] = {
        "agent_id": "claude-code-interactive",
        "token_hash": _hash(new.reveal()),
        "previous": {"token_hash": _hash(h.original.reveal()), "until": START.isoformat()},
    }
    assert "already" in h.provider.host(new, h.original)
    assert h.estate.sent == []


def test_host_refuses_to_restart_under_a_live_run(h: Harness) -> None:
    h.estate.ledger = [
        {
            "unit_id": "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11",
            "unit_key": "bump-x",
            "unit_state": "executing",
        }
    ]
    h.estate.views["0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"] = {"authority": {"constraints": {}}}
    with pytest.raises(ContractError, match="strand a run: bump-x"):
        h.provider.host(Value("new-one"), h.original)
    assert h.estate.sent == []


def test_host_refuses_to_restart_under_a_dispatched_unclaimed_run(h: Harness) -> None:
    h.estate.ledger = [
        {
            "unit_id": "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11",
            "unit_key": "bump-x",
            "unit_state": "ready",
        }
    ]
    h.estate.histories["0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"] = [{"action": "dispatch.dispatched"}]
    with pytest.raises(ContractError, match="bump-x"):
        h.provider.host(Value("new-one"), h.original)
    assert h.estate.sent == []


def test_a_live_rotation_unit_doesn_t_block_the_restart(h: Harness) -> None:
    h.estate.ledger = [
        {
            "unit_id": "2d7dfa03-8f2e-4a0c-9c2f-3b2d6f0b8c33",
            "unit_key": "rotate",
            "unit_state": "executing",
        },
        {
            "unit_id": "1c6ce9f2-7e1d-4f9b-8b1e-2a1c5e9a7b22",
            "unit_key": "claimed-then-ready",
            "unit_state": "ready",
        },
    ]
    h.estate.views["2d7dfa03-8f2e-4a0c-9c2f-3b2d6f0b8c33"] = {
        "authority": {"constraints": {"rotation_adapter": "orchestrator"}}
    }
    h.estate.histories["1c6ce9f2-7e1d-4f9b-8b1e-2a1c5e9a7b22"] = [
        {"action": "dispatch.dispatched"},
        {"to_state": "claimed"},
    ]
    h.provider.host(Value("new-one"), h.original)
    assert h.entry()["token_hash"] == _hash("new-one")


def test_a_refused_write_stops_before_the_restart(h: Harness) -> None:
    h.estate.coolify_status = 500
    with pytest.raises(ContractError, match="HTTP 500"):
        h.provider.host(Value("new-one"), h.original)
    assert h.estate.sent == [("PATCH", f"/api/v1/applications/{APP}/envs")]


def test_a_restart_that_never_loads_the_write_is_refused(h: Harness) -> None:
    h.estate.restart_applies = False
    with pytest.raises(ContractError, match="didn't load"):
        h.provider.host(Value("new-one"), h.original)


def test_the_coolify_client_reaches_only_the_approved_application() -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"reached the transport: {request.url}")

    client = CoolifyClient(
        COOLIFY, Value("throwaway-coolify-token-for-tests"), APP, httpx.MockTransport(never)
    )
    assert client.permits("PATCH", f"/api/v1/applications/{APP}/envs")
    assert client.permits("POST", f"/api/v1/applications/{APP}/restart")
    for method, path in [
        ("PATCH", "/api/v1/applications/another0000000000000000/envs"),
        ("DELETE", f"/api/v1/applications/{APP}/envs"),
        ("GET", f"/api/v1/applications/{APP}/envs"),
        ("POST", f"/api/v1/applications/{APP}/stop"),
        ("POST", "/api/v1/applications/another0000000000000000/restart"),
    ]:
        assert not client.permits(method, path)
    with pytest.raises(ContractError, match="may not"):
        client._send("DELETE", f"/api/v1/applications/{APP}/envs")


def test_a_probe_waits_out_a_swap(h: Harness) -> None:
    h.estate.scripted = [502, None, 503]
    assert h.provider.probe(h.original) == 200
    assert h.clock.slept == 3


def test_a_probe_that_never_settles_answers_what_it_last_got(h: Harness) -> None:
    h.estate.scripted = [502] * 1000
    assert h.provider.probe(h.original) == 502
    assert h.clock.now - START >= SWAP_WAIT


def test_a_hosted_value_the_old_container_still_refuses_is_asked_again(h: Harness) -> None:
    new = Value("the-new-operator-bearer")
    h.estate.lag = 3
    h.provider.host(new, h.original)
    assert h.provider.probe(new) == 200
    assert h.clock.slept > 0


def test_a_retired_value_the_old_container_still_accepts_is_asked_again(h: Harness) -> None:
    h.run("mint")
    # Retire runs in a later process: nothing from mint's provider carries over.
    h.provider = h.build()
    h.estate.lag = 3
    h.provider.revoke(h.original)
    assert h.provider.probe(h.original) == 401


def test_a_retire_the_restart_never_loads_is_refused(h: Harness) -> None:
    h.run("mint")
    h.estate.restart_applies = False
    with pytest.raises(ContractError, match="didn't load"):
        h.provider.revoke(h.original)


def test_a_value_nobody_hosted_or_retired_is_asked_once(h: Harness) -> None:
    assert h.provider.probe(Value("stranger")) == 401
    assert h.clock.slept == 0


def test_revoke_refuses_unless_the_old_value_is_the_entry_s_previous(h: Harness) -> None:
    with pytest.raises(ContractError, match="as the entry's previous"):
        h.provider.revoke(h.original)
    h.estate.loaded[ENTRY] = {
        "agent_id": "claude-code-interactive",
        "token_hash": _hash("new"),
        "previous": {"token_hash": _hash("someone else"), "until": START.isoformat()},
    }
    with pytest.raises(ContractError, match="as the entry's previous"):
        h.provider.revoke(h.original)
    assert h.estate.sent == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"coolify_url": None}, "coolify_url"),
        ({"coolify_token_id": None}, "coolify_url"),
        ({"orchestrator": None}, "the orchestrator"),
    ],
)
def test_the_provider_needs_its_hosted_context(
    h: Harness, changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ContractError, match=message):
        h.build(**changes)


@pytest.mark.parametrize(
    "hosted",
    [
        (HOSTED, HOSTED.replace(ENTRY, "orchestrator-observer")),
        (HOSTED.replace('instance="prod"', 'instance="dev"'),),
    ],
)
def test_the_provider_needs_exactly_one_production_setting(
    h: Harness, hosted: tuple[str, ...]
) -> None:
    keeper = f'bws-secret uuid="{h.keeper_id}"'
    h.rotation = Rotation(
        h.rotation.credential_id,
        h.rotation.occurrence,
        h.keeper_id,
        WORK,
        (keeper, 'provider name="orchestrator"', *hosted),
    )
    with pytest.raises(ContractError, match="exactly one production"):
        h.build()


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://100.112.188.96:8000", True),
        ("https://coolify.example", True),
        ("http://178.156.247.239:8000", False),
        ("http://coolify-1.devonwatkins.com", False),
        ("http://10.0.0.5:8000", False),
        ("ftp://100.112.188.96", False),
    ],
)
def test_the_token_goes_only_over_tls_or_the_tailnet(url: str, allowed: bool) -> None:
    assert (url_problem(url) is None) is allowed


def test_the_preview_row_is_written_before_the_production_row(h: Harness) -> None:
    h.provider.host(Value("the-new-operator-bearer"), h.original)
    assert list(h.estate.rows) == [True, False]


def test_a_run_dispatched_during_the_write_stops_the_restart(h: Harness) -> None:
    unit = "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"
    h.estate.ledger_after_write = [{"unit_id": unit, "unit_key": "bump-y", "unit_state": "ready"}]
    h.estate.histories[unit] = [{"action": "dispatch.dispatched"}]
    with pytest.raises(ContractError, match="bump-y"):
        h.provider.host(Value("the-new-operator-bearer"), h.original)
    assert ("POST", f"/api/v1/applications/{APP}/restart") not in h.estate.sent


def test_a_retire_retry_after_the_swap_finished_is_already_done(h: Harness) -> None:
    h.run("mint")
    h.provider = h.build()
    h.provider.revoke(h.original)
    sent = len(h.estate.sent)
    retry = h.build()
    assert "already loaded" in retry.revoke(h.original)
    assert len(h.estate.sent) == sent
    assert retry.probe(h.original) == 401


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"orchestrator_application": "another0000000000000000"}, "configured orchestrator"),
        ({"orchestrator_application": None}, "configured orchestrator"),
        ({"executor_key_id": ENTRY}, "its own bearer"),
    ],
)
def test_the_configuration_bounds_what_a_package_may_name(
    h: Harness, changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ContractError, match=message):
        h.build(**changes)


def test_a_destination_naming_another_variable_is_refused(h: Harness) -> None:
    roles = HOSTED.replace(VARIABLE, "ORCHESTRATOR_M2M_ROLES")
    keeper = f'bws-secret uuid="{h.keeper_id}"'
    h.rotation = Rotation(
        h.rotation.credential_id,
        h.rotation.occurrence,
        h.keeper_id,
        WORK,
        (keeper, 'provider name="orchestrator"', roles),
    )
    with pytest.raises(ContractError, match="must be ORCHESTRATOR_M2M_CREDENTIALS"):
        h.build()


DRIFT = "orchestrator-drift-reporter"


def _share_the_old_value(h: Harness) -> None:
    """The observer and drift-reporter case: another entry holds the rotated entry's value."""
    h.estate.loaded[DRIFT] = {
        "agent_id": "drift-reconciler",
        "token_hash": _hash(h.original.reveal()),
    }


def _accepts(h: Harness, key_id: str, value: Value) -> bool:
    request = httpx.Request(
        "GET",
        f"{ORCHESTRATOR}/api/v1/status-ledger",
        params={"work_unit_id": "x"},
        headers={"Authorization": f"Bearer {value.reveal()}", "X-Credential-Key-Id": key_id},
    )
    return h.estate._probe(request).status_code == 200


def test_a_rotation_without_an_overlap_runs_the_whole_plan(h: Harness) -> None:
    _share_the_old_value(h)
    h.provider = h.build(without_overlap=True)
    h.run("mint")
    new = h.keeper()
    assert h.entry() == {"agent_id": "claude-code-interactive", "token_hash": _hash(new.reveal())}
    assert not _accepts(h, ENTRY, h.original)
    h.provider = h.build(without_overlap=False)
    h.run("retire")
    # Retire wrote nothing: the host already stopped the old value working for this key id.
    assert h.estate.sent == [
        ("PATCH", f"/api/v1/applications/{APP}/envs"),
        ("PATCH", f"/api/v1/applications/{APP}/envs"),
        ("POST", f"/api/v1/applications/{APP}/restart"),
    ]
    notes = {evidence.step: evidence.note for _, evidence in h.filed}
    assert "no previous" in str(notes["host"])
    assert notes["retire_old"] == "the old value was already refused; nothing to revoke"
    # Confirm-dead proved the old value dead for this key id while the entry sharing it still
    # accepts it, and cleaned up.
    assert [e.step for _, e in h.filed][-2:] == ["confirm_dead", "confirm_dead"]
    assert _accepts(h, DRIFT, h.original) and not _accepts(h, ENTRY, h.original)
    assert _accepts(h, ENTRY, new)
    assert len(h.store.deleted) == 2


def test_a_rotation_without_an_overlap_resumes_after_a_stop_after_host(h: Harness) -> None:
    _share_the_old_value(h)
    h.provider = h.build(without_overlap=True)
    steps = 0

    def stop_after_host() -> None:
        nonlocal steps
        steps += 1
        if steps == 4:
            raise ContractError("stopped after host")

    with pytest.raises(ContractError, match="stopped after host"):
        h.run("mint", stop_after_host)
    # Between host and store the old value works for no key id but the one sharing it, and the
    # keeper still holds it; the undo's hash is that sharing entry's current one.
    assert h.keeper().same_as(h.original)
    assert not _accepts(h, ENTRY, h.original)
    assert h.estate.loaded[DRIFT]["token_hash"] == _hash(h.keeper().reveal())
    written = len(h.estate.sent)
    # The retry re-derives from BWS and the identities route: host writes nothing again.
    h.provider = h.build(without_overlap=True)
    h.run("mint")
    assert len(h.estate.sent) == written
    new = h.keeper()
    assert not new.same_as(h.original)
    assert _accepts(h, ENTRY, new)
    h.provider = h.build()
    h.run("retire")
    assert len(h.estate.sent) == written
    assert _accepts(h, DRIFT, h.original) and not _accepts(h, ENTRY, h.original)


def test_an_undeclared_host_is_refused_where_another_entry_shares_the_old_value(
    h: Harness,
) -> None:
    _share_the_old_value(h)
    with pytest.raises(ContractError, match="still current"):
        h.provider.host(Value("the-new-observer-bearer"), h.original)
    assert h.estate.sent == []


def test_a_declared_host_without_an_overlap_is_refused_where_one_is_possible(
    h: Harness,
) -> None:
    provider = h.build(without_overlap=True)
    with pytest.raises(ContractError, match="must overlap"):
        provider.host(Value("the-new-operator-bearer"), h.original)
    assert h.estate.sent == []


def test_a_host_without_an_overlap_waits_until_no_previous_is_loaded(h: Harness) -> None:
    """A restart that loads the new hash beside a previous isn't the setting written."""
    _share_the_old_value(h)
    h.estate.stray_previous = {"token_hash": _hash("stray"), "until": START.isoformat()}
    provider = h.build(without_overlap=True)
    with pytest.raises(ContractError, match="didn't load"):
        provider.host(Value("the-new-observer-bearer"), h.original)


def test_an_overlapping_host_says_it_kept_the_old_value_as_previous(h: Harness) -> None:
    note = h.provider.host(Value("the-new-operator-bearer"), h.original)
    assert "with the old one as previous" in note


@pytest.mark.parametrize("declared", [True, False])
def test_the_adapter_registry_passes_the_declaration_to_the_provider(
    h: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: bool
) -> None:
    # Captured at the builder: a provider built here has no simulated transport to talk to.
    passed: list[bool] = []
    monkeypatch.setattr(
        orchestrator_bearer,
        "build_provider",
        lambda **arguments: passed.append(arguments["without_overlap"]),
    )
    context = AdapterContext(
        tmp_path, h.store, h.token_id, rotation=h.rotation, host_without_overlap=declared
    )
    ADAPTERS["orchestrator"].build(context)
    assert passed == [declared]
