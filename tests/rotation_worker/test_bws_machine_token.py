"""The BWS machine-token adapter (ADR-0055 amendment 12): its probe, its waits, and a rotation
through the contract on a Keychain-shaped store."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from rotation_worker.adapters import ADAPTERS
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.contract import ContractError, Rotation, StepKind, UnitRun
from rotation_worker.destinations import render
from rotation_worker.evidence import StepEvidence
from rotation_worker.secret_store import StoredSecret, StoreError
from rotation_worker.values import Value

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "rotation_evidence_schema.json").read_text()
)
TOKEN = "0.11111111-2222-4333-8444-555555555555.Abc123:ZGVmZ2hp=="
WORK = "rotation-executor"
KEEPER = 'keychain service="Claude" account="BWS_ACCESS_TOKEN_BRAIN_OPS"'
COPY = 'keychain service="Claude" account="BWS_ACCESS_TOKEN_BRAIN_OPS_COPY"'
NY = bws.QUIET_ZONE


# ---- the probe ------------------------------------------------------------------------------


def _child(monkeypatch: pytest.MonkeyPatch, stdout: str | None) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"argv": argv, **kwargs})
        if stdout is None:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(bws.subprocess, "run", run)
    return seen


@pytest.mark.parametrize(
    ("stdout", "status"),
    [("live\n", 200), ("refused\n", 400), ("other\n", None), ("", None), (None, None)],
)
def test_the_probe_answers_live_dead_or_indeterminate(
    monkeypatch: pytest.MonkeyPatch, stdout: str | None, status: int | None
) -> None:
    _child(monkeypatch, stdout)
    assert bws.BwsMachineTokenProvider("/py").probe(Value(TOKEN)) == status


def test_the_probe_is_a_fresh_login_with_the_token_only_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _child(monkeypatch, "live\n")
    bws.BwsMachineTokenProvider("/py").probe(Value(TOKEN))
    [call] = seen
    assert call["argv"][:3] == ["/py", "-I", "-c"]
    assert all(TOKEN not in argument for argument in call["argv"])
    assert call["input"] == TOKEN
    assert set(call["env"]) == {"PATH", "HOME"}
    # A throwaway HOME, gone once the probe answers: no cached session to reuse (amendment 1).
    assert "rotation-login-" in call["env"]["HOME"] and not Path(call["env"]["HOME"]).exists()


def test_the_control_is_a_well_formed_token_that_differs_each_time() -> None:
    from rotation_worker.cli import BWS_TOKEN

    provider = bws.BwsMachineTokenProvider()
    first, second = provider.known_bad(), provider.known_bad()
    assert BWS_TOKEN.fullmatch(first.reveal()) and not first.same_as(second)


def test_a_person_creates_and_revokes_the_token() -> None:
    provider = bws.BwsMachineTokenProvider()
    with pytest.raises(ContractError, match="a person creates"):
        provider.mint()
    with pytest.raises(ContractError, match="a person revokes"):
        provider.revoke(Value(TOKEN))


def test_the_adapter_runs_on_the_keychain_instance() -> None:
    adapter = ADAPTERS[bws.NAME]
    assert adapter.store_kind == "keychain"
    assert adapter.provider_name == "bitwarden-sm"
    assert {segment.name for segment in adapter.plan.segments} == {
        "mint",
        "store",
        "verify",
        "revoke",
        "confirm",
    }
    assert all(
        other.store_kind == "bws-secret" for name, other in ADAPTERS.items() if name != bws.NAME
    )


# ---- the waits ------------------------------------------------------------------------------


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 22, hour, minute, tzinfo=NY).astimezone(UTC)


@pytest.mark.parametrize(
    ("local", "waits"),
    [((1, 59), False), ((2, 0), True), ((7, 34), True), ((7, 35), False), ((14, 0), False)],
)
def test_the_switch_waits_out_the_scheduled_lanes(local: tuple[int, int], waits: bool) -> None:
    reason = bws.wait_reason(bws.PLAN.unit("store"), [], _at(*local))
    assert (reason is not None) is waits
    # Only the switch waits for the hour.
    assert bws.wait_reason(bws.PLAN.unit("verify"), [], _at(*local)) is None


# ---- a rotation through the contract --------------------------------------------------------


class KeychainShaped:
    """The Keychain store's shape in memory: ids are destination strings, items update in place."""

    read_back_kind = "keychain"

    def __init__(self) -> None:
        self.items: dict[str, Value] = {}
        self.revisions: dict[str, datetime] = {}
        self.deleted: list[str] = []

    def locate(self, consumer: Mapping[str, Any]) -> str | None:
        return render(consumer) if consumer.get("kind") == "keychain" else None

    def read(self, secret_id: str) -> Value:
        if secret_id not in self.items:
            raise StoreError("no such item")
        return self.items[secret_id]

    def read_key(self, secret_id: str) -> Value:
        return self.read(secret_id)

    def revised(self, secret_id: str) -> datetime:
        return self.revisions[secret_id]

    def find(self, project_id: str, key: str) -> StoredSecret | None:
        secret_id = render({"kind": "keychain", "service": project_id, "account": key})
        return StoredSecret(secret_id, key, project_id) if secret_id in self.items else None

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret:
        secret_id = render({"kind": "keychain", "service": project_id, "account": key})
        assert secret_id not in self.items
        self.put(secret_id, value)
        return StoredSecret(secret_id, key, project_id)

    def write(self, secret_id: str, value: Value) -> None:
        assert secret_id in self.items, "an update in place, never a create"
        self.put(secret_id, value)

    def put(self, secret_id: str, value: Value) -> None:
        self.items[secret_id] = value
        self.revisions[secret_id] = datetime.now(UTC)

    def delete(self, secret_id: str) -> None:
        del self.items[secret_id]
        self.deleted.append(secret_id)


@dataclass
class Bitwarden:
    """The provider: a set of live tokens, and a revoke only the test (the person) performs."""

    live: set[str] = field(default_factory=set)
    name: str = bws.PROVIDER
    probe_target: str = "a fresh login"
    revoke_probe_target: str = "a fresh login"
    dead_statuses: frozenset[int] = frozenset({400})
    hosts_destinations: bool = False

    def probe(self, value: Value) -> int | None:
        return 200 if value.reveal() in self.live else 400

    probe_before_revoke = probe

    def known_bad(self) -> Value:
        return bws.BwsMachineTokenProvider().known_bad()

    def mint(self) -> Value:
        raise ContractError("a person mints")

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("no host")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person revokes")


class Rotating:
    def __init__(self) -> None:
        self.store = KeychainShaped()
        self.old = Value(TOKEN)
        self.new = Value("0.66666666-7777-4888-8999-000000000000.Def456:cXJzdHV2==")
        self.bitwarden = Bitwarden({self.old.reveal()})
        self.store.put(KEEPER, self.old)
        self.store.put(COPY, self.old)
        self.rotation = Rotation(
            "bws-tok-brain-ops",
            "2026-10-22-requested",
            KEEPER,
            WORK,
            (COPY, KEEPER, 'provider name="bitwarden-sm"'),
        )
        self.filed: list[StepEvidence] = []

    def stage(self) -> None:
        # The person's mint act: the token exists in Bitwarden and is staged by prompt.
        self.bitwarden.live.add(self.new.reveal())
        self.store.create(WORK, self.rotation.staging_key, self.new)

    def run(self, unit: str, **changes: Any) -> None:
        run = UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.bitwarden,
            attestations=(),
            record=lambda _step, evidence: self.filed.append(evidence),
            before_step=lambda: None,
            **changes,
        )
        run.run(bws.PLAN.unit(unit))


def test_a_token_rotates_through_every_keychain_item_and_is_proved_dead() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    assert r.store.read(KEEPER).same_as(r.new) and r.store.read(COPY).same_as(r.new)
    r.run("verify", person_revokes_next=True)
    [verify] = [e for e in r.filed if e.step == StepKind.VERIFY_CONSUMERS]
    assert [(p.subject, p.outcome) for p in verify.probes] == [
        ("known_bad", "dead"),
        ("old", "live"),
    ]
    r.bitwarden.live.discard(r.old.reveal())
    r.run("confirm")
    assert [e.step for e in r.filed] == [
        "verify_new",
        "store",
        "distribute",
        "verify_consumers",
        "confirm_dead",
        "confirm_dead",
    ]
    assert set(r.store.items) == {KEEPER, COPY}
    assert len(r.store.deleted) == 2
    for evidence in r.filed:
        jsonschema.validate(evidence.as_payload(), SCHEMA)
        assert TOKEN not in json.dumps(evidence.as_payload())


def test_an_item_the_switch_missed_stops_the_rotation_before_the_revoke() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    r.store.put(COPY, r.old)
    with pytest.raises(ContractError, match="stored consumer doesn't read back"):
        r.run("verify", person_revokes_next=True)


def test_an_old_token_already_dead_before_the_revoke_stops_the_rotation() -> None:
    # Confirm-dead's "dead" would then prove nothing about the person's revoke.
    r = Rotating()
    r.stage()
    r.run("store")
    r.bitwarden.live.discard(r.old.reveal())
    with pytest.raises(ContractError, match="old value probed dead"):
        r.run("verify", person_revokes_next=True)


def test_without_a_person_revoking_next_verify_probes_nothing() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    r.bitwarden.live.discard(r.old.reveal())
    r.run("verify")
    assert r.filed[-1].probes == ()


def test_confirm_refuses_an_old_token_still_live() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    r.run("verify", person_revokes_next=True)
    with pytest.raises(ContractError, match="old value probed live"):
        r.run("confirm")
    assert set(r.store.items) > {KEEPER, COPY}


def test_the_keychain_adapters_are_the_ones_the_orchestrator_confines() -> None:
    # Two lists, one in each program: if they drift, the units of an adapter one side calls
    # Keychain and the other doesn't are never claimed, and nothing reports it.
    from orchestrator.services.lifecycle.rotation_claims import KEYCHAIN_ADAPTERS

    keychain = {name for name, adapter in ADAPTERS.items() if adapter.store_kind == "keychain"}
    assert keychain == KEYCHAIN_ADAPTERS
