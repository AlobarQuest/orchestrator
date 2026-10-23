"""The pass around the contract: claim, start, evidence, submit, and every way it stops."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

import rotation_worker.worker as worker
from rotation_worker.adapters import ADAPTERS, AdapterContext
from rotation_worker.checks import Approved
from rotation_worker.contract import Actor, ContractError, Plan, Rotation, Step, StepKind
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.github_actions import ActionsSecrets, FingerprintReports, RotationApp
from rotation_worker.held import HeldSettings
from rotation_worker.orchestrator_client import Lease, OrchestratorClient, OrchestratorError
from rotation_worker.readers import ObservationRead, Read, ReadError, ReadUnavailable
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
        self.observed_at: list[datetime] = []
        self.loaded: dict[str, dict[str, Any]] = {}
        self.active: list[dict[str, Any]] = []
        # Units another lane runs: readable, but never this executor's to work.
        self.others: dict[str, dict[str, Any]] = {}

    def observations(
        self, source_system: str, subject_reference: str, observed_from: datetime
    ) -> list[dict[str, Any]]:
        return [{"observed_at": at.isoformat()} for at in self.observed_at if at >= observed_from]

    def identities(self) -> dict[str, dict[str, Any]]:
        return self.loaded

    def active_units(self) -> list[dict[str, Any]]:
        return self.active

    def held(self) -> dict[str, dict[str, Any]]:
        if "held" in self.unreachable:
            raise OrchestratorError("no answer")
        return {"ORCHESTRATOR_CHANGE_RECORD_TOKEN": {"sha256_prefix": "0" * 16, "length": 43}}

    def ready_units(self) -> list[str]:
        return list(self.units)

    def unit(self, unit_id: str) -> dict[str, Any]:
        if unit_id in self.unreachable:
            raise OrchestratorError("no answer")
        if unit_id in self.others:
            return self.others[unit_id]
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
        self.provider = adapter.build(AdapterContext(tmp_path, self.store, admin_secret_id=None))
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
        self.reads: dict[str, Read] = {}

    def add(self, unit_id: str, name: str) -> None:
        self.client.units[unit_id] = {
            "id": unit_id,
            "version": 4,
            "work_package_revision_id": "revision",
            "authority": {"constraints": {"rotation_adapter": "fake", "rotation_unit": name}},
        }

    def _check(
        self, unit: dict[str, Any], intake: Any, mirror: Any, work: str, store: Any
    ) -> Approved:
        if self.refusal is not None:
            raise self.refusal
        plan_unit = self.adapter.plan.unit(unit["authority"]["constraints"]["rotation_unit"])
        entry = RegistryEntry("AlobarQuest/orchestrator", "c" * 40, {"rotated_by_sds": True})
        return Approved(self.adapter, plan_unit, self.rotation, entry, self.reads)

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


def test_an_adapter_missing_its_admin_secret_spends_no_attempt(h: Harness) -> None:
    h.adapter = ADAPTERS["openrouter"]
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []


def test_earlier_evidence_is_tracked_per_criterion_not_per_kind(h: Harness) -> None:
    approved = h._check(_unit("retire"), None, None, "", h.store)
    rows = [{"ac_id": "AC-006"}, {"ac_id": "AC-007"}, {"ac_id": "AC-099"}]
    kinds, criteria = worker._filed(rows, approved)
    assert criteria == {"AC-006", "AC-007"}
    assert {kind.value for kind in kinds} == {"verify_consumers", "retire_old"}


def _unit(name: str) -> dict[str, Any]:
    return {"authority": {"constraints": {"rotation_unit": name}}}


def _scheduled(h: Harness) -> None:
    """The canary's one reader is a scheduled one, read through its observations."""
    h.reads = {RUNTIME: ObservationRead("recovery_floor", "backup:canary")}
    h.client.observed_at = [datetime.now(UTC) - timedelta(days=1)]


def _stored(h: Harness) -> None:
    for unit_id, name in (("u1", "mint"), ("u2", "store")):
        h.add(unit_id, name)
        assert run_pass(h.setup()) == 1
        del h.client.units[unit_id]
    h.client.calls.clear()


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_retire_waits_unclaimed_until_every_scheduled_reader_has_run(h: Harness) -> None:
    _scheduled(h)
    _stored(h)
    h.add("u3", "retire")
    # Attached to the executor's logger itself and re-enabled: a test that runs alembic's
    # `fileConfig` disables every logger that already exists.
    records = _Records()
    logger = logging.getLogger("rotation_worker")
    logger.addHandler(records)
    level, disabled = logger.level, logger.disabled
    logger.setLevel(logging.INFO)
    logger.disabled = False
    try:
        assert run_pass(h.setup()) == 0
    finally:
        logger.removeHandler(records)
        logger.setLevel(level)
        logger.disabled = disabled
    assert h.client.calls == []
    waiting = [r for r in records.records if r.getMessage() == "unit_waiting"]
    assert [getattr(r, "readers", None) for r in waiting] == [[RUNTIME]]
    assert h.provider.probe(h.original) == 200


def test_retire_goes_ahead_once_the_reader_has_run_since_the_store(h: Harness) -> None:
    _scheduled(h)
    _stored(h)
    h.client.observed_at.append(h.store.revisions[h.keeper] + timedelta(hours=1))
    h.add("u3", "retire")
    assert run_pass(h.setup()) == 1
    assert ("submit", "u3") in h.client.calls
    assert h.provider.probe(h.original) == 401


def test_a_reader_the_registry_names_wrongly_is_refused_before_any_step(h: Harness) -> None:
    _scheduled(h)
    h.client.observed_at = []
    h.add("u2", "store")
    assert run_pass(h.setup()) == 0
    assert "posted nothing" in h.client.failures["u2"]
    assert ("start", "u2") not in h.client.calls


def test_a_unit_that_verifies_but_doesn_t_retire_never_waits(h: Harness) -> None:
    _scheduled(h)
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1
    h.add("u2", "store")
    del h.client.units["u1"]
    assert run_pass(h.setup()) == 1
    assert ("submit", "u2") in h.client.calls


def test_the_wait_names_when_the_overlap_s_backstop_ends(h: Harness) -> None:
    hosted = (
        'coolify-env-hash instance="prod" resource_type="application" uuid="a" '
        'key="ORCHESTRATOR_M2M_CREDENTIALS" entry="orchestrator-observer"'
    )
    h.rotation = Rotation("canary", "o", h.keeper, WORK, (*h.rotation.destinations, hosted))
    until = "2026-10-23T06:00:00+00:00"
    h.client.loaded = {"orchestrator-observer": {"previous": {"until": until}}}
    approved = h._check(_unit_named("retire"), None, None, WORK, h.store)
    assert worker._overlap_until(h.setup(), approved) == until
    h.client.loaded = {"orchestrator-observer": {}}
    assert worker._overlap_until(h.setup(), approved) is None


def _unit_named(name: str) -> dict[str, Any]:
    return {"authority": {"constraints": {"rotation_unit": name}}}


@pytest.mark.parametrize("declared", [True, False])
def test_the_approved_host_without_overlap_reaches_the_provider(
    h: Harness, monkeypatch: pytest.MonkeyPatch, declared: bool
) -> None:
    built: list[AdapterContext] = []

    def build(context: AdapterContext) -> Any:
        built.append(context)
        return h.provider

    adapter = replace(h.adapter, build=build)
    checked = h._check

    def check(*arguments: Any) -> Approved:
        return replace(checked(*arguments), adapter=adapter, host_without_overlap=declared)

    monkeypatch.setattr(worker, "check_unit", check)
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1
    assert [context.host_without_overlap for context in built] == [declared]


class _Gated:
    """The fake provider, with a service whose own clients a restart would cut short."""

    def __init__(self, inner: Any, answers: list[Any]) -> None:
        self._inner = inner
        self._answers = answers

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def readers_mid_run(self) -> list[str]:
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _gate(h: Harness, monkeypatch: pytest.MonkeyPatch, answers: list[Any]) -> None:
    adapter = replace(h.adapter, build=lambda _context: _Gated(h.provider, answers))
    checked = h._check
    monkeypatch.setattr(
        worker, "check_unit", lambda *arguments: replace(checked(*arguments), adapter=adapter)
    )


def test_a_writing_unit_waits_unclaimed_while_a_reader_is_mid_run(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate(h, monkeypatch, [["sds-estate-landing"]])
    h.add("u3", "retire")
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []


def test_a_listing_that_can_t_be_read_also_waits(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate(h, monkeypatch, [ReadUnavailable("Healthchecks answered 503")])
    h.add("u3", "retire")
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []


def test_a_reader_the_registry_names_wrongly_refuses_the_writing_unit(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate(h, monkeypatch, [ReadError("0 Healthchecks checks are named 'x'")])
    h.add("u3", "retire")
    assert run_pass(h.setup()) == 0
    assert "named 'x'" in h.client.failures["u3"]
    assert ("start", "u3") not in h.client.calls


def test_a_writing_unit_goes_ahead_once_no_reader_is_mid_run(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate(h, monkeypatch, [[]])
    h.add("u1", "mint")
    h.add("u3", "retire")
    run_pass(h.setup())
    assert ("claim", "u3") in h.client.calls


def test_a_unit_that_writes_nothing_never_asks(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _gate(h, monkeypatch, [])
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1


def test_a_configured_rotation_app_reaches_the_unit_and_the_reads(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: dict[str, Any] = {}

    class Run:
        def __init__(self, **arguments: Any) -> None:
            built["run"] = arguments

        def run(self, unit: Any) -> None:
            pass

    class Reads:
        def __init__(self, *arguments: Any, **keywords: Any) -> None:
            built["reads"] = keywords

        def pending(self, stored_at: Any) -> list[str]:
            return []

    monkeypatch.setattr(worker, "UnitRun", Run)
    monkeypatch.setattr(worker, "ConsumerReads", Reads)
    _scheduled(h)
    h.add("u1", "retire")
    app = RotationApp("5383095", lambda: Value("unused"))
    assert run_pass(replace(h.setup(), github_app=app)) == 1
    assert isinstance(built["run"]["actions_secrets"], ActionsSecrets)
    assert isinstance(built["reads"]["reports"], FingerprintReports)
    assert built["reads"]["keeper"]() == h.store.read(h.keeper).fingerprint


def test_without_a_rotation_app_nothing_can_write_or_read_an_actions_secret(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: dict[str, Any] = {}

    class Run:
        def __init__(self, **arguments: Any) -> None:
            built.update(arguments)

        def run(self, unit: Any) -> None:
            pass

    monkeypatch.setattr(worker, "UnitRun", Run)
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1
    assert built["actions_secrets"] is None


def _adapted(h: Harness, monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    adapter = replace(h.adapter, **changes)
    checked = h._check
    monkeypatch.setattr(
        worker, "check_unit", lambda *arguments: replace(checked(*arguments), adapter=adapter)
    )


def test_a_unit_its_adapter_says_must_wait_is_left_unclaimed(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def wait(unit: Any, history: Any, now: datetime) -> str | None:
        asked.append(unit.name)
        return "not yet" if len(asked) == 1 else None

    _adapted(h, monkeypatch, wait=wait)
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []
    assert run_pass(h.setup()) == 1
    assert asked == ["mint", "mint"]


def test_the_unit_before_a_person_s_revoke_proves_the_old_value_live(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Amendment 1's first half, which no machine retire step files when a person revokes.
    from rotation_worker.adapters import bws_machine_token

    runs: list[bool] = []

    class Recorded:
        def __init__(self, **fields: Any) -> None:
            runs.append(fields["person_revokes_next"])

        def run(self, unit: Any) -> None:
            pass

    monkeypatch.setattr(worker, "UnitRun", Recorded)
    h.adapter = replace(h.adapter, plan=bws_machine_token.PLAN)
    for unit_id, name in (("u1", "store"), ("u2", "verify"), ("u3", "confirm")):
        h.add(unit_id, name)
    assert run_pass(h.setup()) == 3
    assert runs == [False, True, False]


# ---- held copies (ADR-0055 amendment 13) -------------------------------------------------------

HELD_APP = "eqj5l7k705fhi12x9i74fqf0"


def _held_destination(**changes: str) -> str:
    fields = {
        "instance": "prod",
        "resource_type": "application",
        "uuid": HELD_APP,
        "key": "ORCHESTRATOR_CHANGE_RECORD_TOKEN",
        **changes,
    }
    return "coolify-env-held " + " ".join(f'{k}="{v}"' for k, v in fields.items())


def _holding(h: Harness, monkeypatch: pytest.MonkeyPatch, **changes: str) -> dict[str, Any]:
    """A rotation that also writes the orchestrator's held copy; returns what `UnitRun` got."""
    h.rotation = replace(
        h.rotation, destinations=(*h.rotation.destinations, _held_destination(**changes))
    )
    built: dict[str, Any] = {}

    class Run:
        def __init__(self, **arguments: Any) -> None:
            built.update(arguments)

        def run(self, unit: Any) -> None:
            pass

    monkeypatch.setattr(worker, "UnitRun", Run)
    h.add("u1", "mint")
    return built


def _held_setup(h: Harness, **changes: Any) -> Setup:
    configured: dict[str, Any] = {
        "admin_secrets": {"orchestrator": h.store.put(WORK, "coolify", Value("coolify-token"))},
        "coolify_url": "http://100.112.188.96:8000",
        "orchestrator_application": HELD_APP,
    }
    return replace(h.setup(), **{**configured, **changes})


def test_a_unit_with_no_held_copy_gets_no_writer(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: dict[str, Any] = {}

    class Run:
        def __init__(self, **arguments: Any) -> None:
            built.update(arguments)

        def run(self, unit: Any) -> None:
            pass

    monkeypatch.setattr(worker, "UnitRun", Run)
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1
    assert built["held"] is None


def test_a_unit_with_a_held_copy_gets_the_writer(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _holding(h, monkeypatch)
    assert run_pass(_held_setup(h)) == 1
    assert isinstance(built["held"], HeldSettings)


@pytest.mark.parametrize(
    "missing",
    [{"admin_secrets": {}}, {"coolify_url": None}, {"orchestrator_application": None}],
)
def test_an_executor_that_can_t_write_a_held_copy_leaves_the_unit_unclaimed(
    h: Harness, monkeypatch: pytest.MonkeyPatch, missing: dict[str, Any]
) -> None:
    _holding(h, monkeypatch)
    assert run_pass(_held_setup(h, **missing)) == 0
    # The executor's configuration is at fault, not the unit: no claim, so no attempt spent.
    assert h.client.calls == []


@pytest.mark.parametrize(
    "change",
    [{"uuid": "re45tafypao3nly3qa9a79dp"}, {"key": "ORCHESTRATOR_NOT_REPORTED"}],
)
def test_a_held_copy_the_executor_couldn_t_confirm_is_refused_before_any_step(
    h: Harness, monkeypatch: pytest.MonkeyPatch, change: dict[str, str]
) -> None:
    built = _holding(h, monkeypatch, **change)
    assert run_pass(_held_setup(h)) == 0
    assert [call for call, _ in h.client.calls] == ["claim", "fail"]
    assert built == {}


def test_an_orchestrator_that_can_t_say_what_it_holds_is_a_wait(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _holding(h, monkeypatch)
    h.client.unreachable.add("held")
    assert run_pass(_held_setup(h)) == 0
    assert h.client.calls == []


def _distributing(h: Harness) -> None:
    """The fake adapter with a distribute step closing its mint unit."""
    mint = h.adapter.plan.unit("mint")
    step = Step(StepKind.DISTRIBUTE, Actor.MACHINE, "AC-099")
    segments = tuple(
        replace(s, steps=(*s.steps, step)) if s is mint else s for s in h.adapter.plan.segments
    )
    h.adapter = replace(h.adapter, plan=Plan(segments))


def _live_run(h: Harness) -> None:
    h.client.others["u9"] = {"id": "u9", "authority": {"constraints": {}}}
    h.client.active = [{"unit_id": "u9", "unit_key": "deps-bump", "unit_state": "executing"}]


def test_a_unit_that_distributes_a_held_copy_waits_unclaimed_while_a_run_is_live(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _distributing(h)
    built = _holding(h, monkeypatch)
    _live_run(h)
    assert run_pass(_held_setup(h)) == 0
    assert h.client.calls == []
    h.client.active = []
    assert run_pass(_held_setup(h)) == 1
    assert isinstance(built["held"], HeldSettings)


def test_a_unit_that_doesn_t_distribute_isn_t_held_back_by_a_live_run(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _holding(h, monkeypatch)
    _live_run(h)
    assert run_pass(_held_setup(h)) == 1


def _mid_overlap(h: Harness) -> None:
    h.client.others["u9"] = {
        "id": "u9",
        "work_package_revision_id": "another-revision",
        "attempt_count": 1,
        "authority": {
            "constraints": {"rotation_adapter": "orchestrator", "rotation_unit": "retire"}
        },
    }
    h.client.active = [{"unit_id": "u9", "unit_key": "drift-retire", "unit_state": "ready"}]


def test_a_unit_that_waits_for_overlaps_is_left_unclaimed_while_another_rotation_is_mid_overlap(
    h: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _adapted(h, monkeypatch, overlap_waits=frozenset({"mint"}))
    _mid_overlap(h)
    h.add("u1", "mint")
    with caplog.at_level(logging.INFO):
        assert run_pass(h.setup()) == 0
    assert h.client.calls == []
    assert [vars(r)["mid_overlap"] for r in caplog.records if "mid_overlap" in vars(r)] == [
        ["drift-retire (ready)"]
    ]
    h.client.active = []
    assert run_pass(h.setup()) == 1


def test_a_unit_that_doesn_t_wait_for_overlaps_is_claimed_mid_overlap(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adapted(h, monkeypatch, overlap_waits=frozenset({"store"}))
    _mid_overlap(h)
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1


def test_only_the_bws_token_switch_waits_for_overlaps() -> None:
    waiting = {name: a.overlap_waits for name, a in ADAPTERS.items() if a.overlap_waits}
    assert waiting == {"bws-machine-token": frozenset({"store"})}
