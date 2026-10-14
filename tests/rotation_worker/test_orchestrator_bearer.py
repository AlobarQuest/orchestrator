"""The orchestrator-bearer adapter, against a simulated orchestrator and Coolify.

The simulation holds what each side holds in production: Coolify stores the variable's two rows,
and the orchestrator serves the identities it loaded at its last restart and authenticates a bearer
against them. Nothing reaches a live endpoint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

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
            return httpx.Response(201)
        if self.restart_applies:
            self._before = self.loaded
            self.loaded = json.loads(self.rows[False])
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
            "transport": transport,
            "coolify_transport": httpx.MockTransport(self.estate.coolify),
            "clock": self.clock,
            "sleep": self.clock.sleep,
            **changes,
        }
        return build_provider(**arguments)

    def run(self, unit: str) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=lambda: None,
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


def test_a_retired_value_still_answered_by_the_old_container_is_asked_again(h: Harness) -> None:
    h.run("mint")
    h.estate.lag = 3
    h.run("retire")
    assert h.provider.probe(h.original) == 401


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
