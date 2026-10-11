"""The pass around the contract: claim, start, evidence, submit, and every way it stops."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import rotation_worker.worker as worker
from rotation_worker.adapters import ADAPTERS
from rotation_worker.checks import Approved
from rotation_worker.contract import ContractError, Rotation
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.orchestrator_client import Lease, OrchestratorClient, OrchestratorError
from rotation_worker.registry import RegistryEntry
from rotation_worker.values import Fingerprint, Value
from rotation_worker.worker import Setup, run_pass
from tests.rotation_worker.stores import MemoryStore

WORK = "work"
RUNTIME = 'runtime-fetch repo="orchestrator" ref="a person attests"'


class FakeClient:
    """The orchestrator as the pass sees it, recording every write."""

    def __init__(self, units: dict[str, dict[str, Any]]) -> None:
        self.units = units
        self.calls: list[tuple[str, str]] = []
        self.evidence_bodies: list[dict[str, Any]] = []
        self.failures: dict[str, str] = {}
        self.existing_evidence: dict[str, list[dict[str, Any]]] = {}
        self.history_events: list[dict[str, Any]] = []
        self.unreachable: set[str] = set()

    def ready_units(self) -> list[str]:
        return list(self.units)

    def unit(self, unit_id: str) -> dict[str, Any]:
        if unit_id in self.unreachable:
            raise OrchestratorError("no answer")
        return self.units[unit_id]

    def package_intake(self, revision_id: str) -> dict[str, Any]:
        return {"id": revision_id}

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return self.history_events

    def evidence(self, unit_id: str) -> list[dict[str, Any]]:
        return self.existing_evidence.get(unit_id, [])

    def claim(self, unit_id: str, version: int, key: str) -> Lease:
        self.calls.append(("claim", unit_id))
        return Lease(1, "lease")

    def renew(self, unit_id: str, version: int, lease: Lease, key: str) -> None:
        self.calls.append(("renew", unit_id))

    def start(self, unit_id: str, version: int, lease: Lease, key: str) -> int:
        self.calls.append(("start", unit_id))
        return version + 1

    def file_evidence(self, unit_id: str, body: dict[str, Any]) -> None:
        self.calls.append(("evidence", unit_id))
        self.evidence_bodies.append(body)

    def submit(self, unit_id: str, version: int, lease: Lease, key: str) -> int:
        self.calls.append(("submit", unit_id))
        return version + 1

    def fail(self, unit_id: str, version: int, lease: Lease, key: str, reason: str) -> int:
        self.calls.append(("fail", unit_id))
        self.failures[unit_id] = reason
        return version + 1


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store = MemoryStore()
        self.state = tmp_path
        adapter = ADAPTERS["fake"]
        self.provider = adapter.build(tmp_path)
        self.original = self.provider.mint()
        self.keeper = self.store.put(WORK, "keeper", self.original)
        destinations = (f'bws-secret uuid="{self.keeper}"', 'provider name="fake"', RUNTIME)
        self.rotation = Rotation("canary", "2026-10-11-requested", self.keeper, WORK, destinations)
        self.handed_back = False
        self.refusal: Exception | None = None
        monkeypatch.setattr(worker, "check_unit", self._check)
        monkeypatch.setattr(worker, "find_credential", self._registry)
        self.client = FakeClient({})
        self.adapter = adapter

    def add(self, unit_id: str, name: str) -> None:
        self.client.units[unit_id] = {
            "id": unit_id,
            "version": 4,
            "work_package_revision_id": "revision",
            "authority": {"constraints": {"rotation_adapter": "fake", "rotation_unit": name}},
        }

    def _check(self, unit: dict[str, Any], intake: Any, mirror: Any, work: str) -> Approved:
        if self.refusal is not None:
            raise self.refusal
        plan_unit = self.adapter.plan.unit(unit["authority"]["constraints"]["rotation_unit"])
        entry = RegistryEntry("AlobarQuest/orchestrator", "c" * 40, {"rotated_by_sds": True})
        return Approved(self.adapter, plan_unit, self.rotation, entry)

    def _registry(self, mirror: Any, credential_id: str) -> RegistryEntry:
        rotated = not self.handed_back
        return RegistryEntry("AlobarQuest/orchestrator", "c" * 40, {"rotated_by_sds": rotated})

    def setup(self) -> Setup:
        return Setup(
            client=cast(OrchestratorClient, self.client),
            # Never fetched: the checks and the registry read are replaced above.
            mirror=RepoMirror(self.state / "mirrors"),
            store=self.store,
            work_project_id=WORK,
            state_dir=self.state,
            source_revision="rotation-worker@0123456789ab",
        )


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


def test_a_unit_is_claimed_started_renewed_per_step_evidenced_and_submitted(h: Harness) -> None:
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1
    assert [call for call, _ in h.client.calls] == [
        "claim",
        "start",
        "renew",
        "evidence",
        "renew",
        "evidence",
        "renew",
        "evidence",
        "submit",
    ]
    body = h.client.evidence_bodies[0]
    assert body["ac_id"] == "AC-001"
    assert body["work_package_revision_id"] == "revision"
    assert body["evidence_type"] == "rotation.step"
    assert body["source_revision"] == "rotation-worker@0123456789ab"
    assert body["expected_version"] == 6
    assert body["supersede"] is False
    assert body["payload"]["format"] == "rotation-step/1"


def test_a_later_attempt_supersedes_what_an_earlier_one_filed(h: Harness) -> None:
    h.add("u1", "mint")
    h.client.existing_evidence["u1"] = [{"ac_id": "AC-001"}, {"ac_id": "AC-099"}]
    run_pass(h.setup())
    assert [body["supersede"] for body in h.client.evidence_bodies] == [True, False, False]


def test_a_second_row_for_one_criterion_in_one_attempt_supersedes_the_first(h: Harness) -> None:
    h.add("u1", "mint")
    run_pass(h.setup())
    h.add("u2", "store")
    del h.client.units["u1"]
    h.client.evidence_bodies.clear()
    run_pass(h.setup())
    new = h.store.read(h.keeper)
    h.add("u3", "retire")
    del h.client.units["u2"]
    h.client.history_events = _attested(new.fingerprint)
    h.client.evidence_bodies.clear()
    assert run_pass(h.setup()) == 1
    confirm = [b for b in h.client.evidence_bodies if b["ac_id"] == "AC-008"]
    assert [b["supersede"] for b in confirm] == [False, True]


def test_a_credential_handed_back_mid_rotation_fails_the_unit(h: Harness) -> None:
    h.add("u1", "mint")
    h.handed_back = True
    assert run_pass(h.setup()) == 0
    assert "handed back" in h.client.failures["u1"]
    assert ("evidence", "u1") not in h.client.calls


def test_a_contract_refusal_fails_the_unit_with_a_reason_and_no_value(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")
    secret = h.original.reveal()

    def leaks(value: Value) -> int:
        raise ContractError(f"probe answered oddly for {secret}: " + "detail " * 100)

    monkeypatch.setattr(type(h.provider), "probe", lambda self, value: leaks(value))
    assert run_pass(h.setup()) == 0
    reason = h.client.failures["u1"]
    assert reason.startswith("ContractError:")
    assert secret not in reason and "[redacted]" in reason
    assert len(reason) <= worker.MAX_REASON


def test_a_unit_the_checks_refuse_is_claimed_and_failed_without_starting(h: Harness) -> None:
    h.add("u1", "mint")
    h.refusal = ContractError("the registry's destinations differ")
    assert run_pass(h.setup()) == 0
    assert [call for call, _ in h.client.calls] == ["claim", "fail"]
    assert h.client.failures["u1"] == "the registry's destinations differ"


@pytest.mark.parametrize("error", [MirrorError("git down"), OrchestratorError("refused")])
def test_a_transient_failure_skips_the_unit_and_the_pass_goes_on(
    h: Harness, error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")
    h.add("u2", "mint")
    h.refusal = None
    original = h._check

    def flaky(unit: dict[str, Any], *args: Any) -> Approved:
        if unit["id"] == "u1":
            raise error
        return original(unit, *args)

    monkeypatch.setattr(worker, "check_unit", flaky)
    assert run_pass(h.setup()) == 1
    assert ("claim", "u1") not in h.client.calls
    assert ("submit", "u2") in h.client.calls


def test_a_unit_without_an_adapter_is_not_touched(h: Harness) -> None:
    h.client.units["other"] = {"id": "other", "authority": {"constraints": {}}}
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []


def _attested(print_: Fingerprint, status: str = "satisfied") -> list[dict[str, Any]]:
    return [
        {
            "action": "dependency.resolved",
            "payload": {
                "command": {
                    "status": status,
                    "detail": {
                        "note": "attested",
                        "fingerprint": {
                            "sha256_prefix": print_.sha256_prefix[:8],
                            "length": print_.length,
                        },
                    },
                }
            },
        }
    ]


def test_only_satisfied_resolutions_with_a_fingerprint_are_attestations() -> None:
    print_ = Fingerprint("0123456789abcdef", 43)
    events = [
        *_attested(print_),
        *_attested(print_, status="failed"),
        {"action": "work_unit.transitioned", "payload": {"command": {"status": "satisfied"}}},
        {"action": "dependency.resolved", "payload": {"command": {"status": "satisfied"}}},
        {
            "action": "dependency.resolved",
            "payload": {"command": {"status": "satisfied", "detail": {"fingerprint": {}}}},
        },
        {"action": "dependency.resolved"},
    ]
    assert worker._attestations(events) == [Fingerprint("01234567", 43)]


def test_a_fingerprint_on_any_other_event_is_not_an_attestation() -> None:
    event = _attested(Fingerprint("0123456789abcdef", 43))[0]
    assert worker._attestations([{**event, "action": "work_unit.transitioned"}]) == []


def test_an_unforeseen_error_fails_the_unit_and_the_pass_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")
    h.add("u2", "mint")
    calls = {"n": 0}
    real = h.store.read

    def breaks_once(secret_id: str) -> Value:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("a stored value with surrounding whitespace")
        return real(secret_id)

    monkeypatch.setattr(h.store, "read", breaks_once)
    assert run_pass(h.setup()) == 1
    assert h.client.failures["u1"].startswith("ValueError:")
    assert ("submit", "u2") in h.client.calls


def test_an_error_before_the_claim_is_logged_and_the_pass_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")
    h.add("u2", "mint")
    h.client.units["u1"]["work_package_revision_id"] = "unreadable"
    real = h.client.package_intake

    def breaks(revision_id: str) -> dict[str, Any]:
        if revision_id == "unreadable":
            raise KeyError("an answer missing a field")
        return real(revision_id)

    monkeypatch.setattr(h.client, "package_intake", breaks)
    assert run_pass(h.setup()) == 1
    assert ("claim", "u1") not in h.client.calls


def test_earlier_evidence_is_tracked_per_criterion_not_per_kind(h: Harness) -> None:
    approved = h._check(_unit("retire"), None, None, "")
    rows = [{"ac_id": "AC-006"}, {"ac_id": "AC-007"}, {"ac_id": "AC-099"}]
    kinds, criteria = worker._filed(rows, approved)
    assert criteria == {"AC-006", "AC-007"}
    assert {kind.value for kind in kinds} == {"verify_consumers", "retire_old"}


def _unit(name: str) -> dict[str, Any]:
    return {"authority": {"constraints": {"rotation_unit": name}}}
