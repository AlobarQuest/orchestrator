"""The rotation contract, driven through the fake adapter: each invariant, and the path through.

Every invariant here has a test that fails when the check is removed (the mutation review in the
increment 4 report counts them).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from rotation_worker.adapters.fake import PLAN, FakeProvider
from rotation_worker.contract import (
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Rotation,
    Step,
    StepKind,
    UnitPlan,
    UnitRun,
    validate_plan,
)
from rotation_worker.destinations import DestinationError
from rotation_worker.evidence import StepEvidence
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Fingerprint, Value
from tests.rotation_worker.stores import MemoryStore

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "rotation_evidence_schema.json").read_text()
)
WORK = "work-project"
RUNTIME = 'runtime-fetch repo="orchestrator" ref="the canary has no reader; a person attests it"'
M, H = Actor.MACHINE, Actor.HUMAN


class Harness:
    """One credential mid-rotation: its keeper, its provider and the evidence filed so far."""

    def __init__(self, tmp_path: Path, destinations: tuple[str, ...] | None = None) -> None:
        self.store = MemoryStore()
        self.provider = FakeProvider(tmp_path / "provider.json")
        self.original = self.provider.mint()
        self.keeper_id = self.store.put(WORK, "rotation-canary keeper", self.original)
        self.rotation = Rotation(
            credential_id="rotation-canary",
            occurrence="2026-10-11-requested",
            keeper_id=self.keeper_id,
            work_project_id=WORK,
            destinations=destinations
            or (f'bws-secret uuid="{self.keeper_id}"', 'provider name="fake"', RUNTIME),
        )
        self.filed: list[tuple[Step, StepEvidence]] = []
        self.checks = 0

    def run(self, unit: str, attestations: tuple[Fingerprint, ...] = (), **changes: Any) -> None:
        run = UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=attestations,
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=self._check,
            filed=frozenset(step.kind for step, _ in self.filed),
        )
        for name, value in changes.items():
            setattr(run, name, value)
        run.run(PLAN.unit(unit))

    def _check(self) -> None:
        self.checks += 1

    def keeper(self) -> Value:
        return self.store.read(self.keeper_id)

    def kinds(self) -> list[str]:
        return [evidence.step for _, evidence in self.filed]


def _attest(value: Value) -> tuple[Fingerprint, ...]:
    return (value.fingerprint,)


def test_the_fake_plan_rotates_end_to_end(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    new = h.keeper()
    assert not new.same_as(h.original)
    h.run("retire", attestations=_attest(new))
    assert h.kinds() == [
        "mint",
        "stage",
        "verify_new",
        "store",
        "verify_consumers",
        "verify_consumers",
        "retire_old",
        "confirm_dead",
        "confirm_dead",
    ]
    assert h.provider.probe(new) == 200
    assert h.provider.probe(h.original) == 401
    assert h.store.secrets.keys() == {h.keeper_id}
    assert h.checks == 8
    assert h.filed[-1][1].note is not None and h.filed[-1][1].note.startswith("deleted staging")


def test_every_payload_conforms_to_the_orchestrator_s_schema(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    h.run("retire", attestations=_attest(h.keeper()))
    for _, evidence in h.filed:
        jsonschema.validate(evidence.as_payload(), SCHEMA)
        assert h.original.reveal() not in json.dumps(evidence.as_payload())


# ---- new is never old -----------------------------------------------------------------------


def test_a_mint_that_returns_the_old_value_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    monkeypatch.setattr(h.provider, "mint", lambda: h.original)
    with pytest.raises(ContractError, match="equals the old"):
        h.run("mint")
    assert h.kinds() == []


def _single(h: Harness, kind: StepKind, **changes: Any) -> UnitRun:
    run = UnitRun(
        rotation=h.rotation,
        store=h.store,
        provider=h.provider,
        attestations=changes.pop("attestations", ()),
        record=lambda step, evidence: h.filed.append((step, evidence)),
        before_step=lambda: None,
        **changes,
    )
    run.run(UnitPlan("one", (Step(kind, M, "AC-1"),)))
    return run


@pytest.mark.parametrize(
    "kind", [StepKind.MINT, StepKind.VERIFY_NEW, StepKind.DISTRIBUTE, StepKind.VERIFY_CONSUMERS]
)
def test_a_staged_value_equal_to_the_old_is_refused_after_mint(
    tmp_path: Path, kind: StepKind
) -> None:
    h = Harness(tmp_path)
    h.store.put(WORK, h.rotation.staging_key, h.original)
    with pytest.raises(ContractError, match="equals the old"):
        _single(h, kind)
    assert h.kinds() == []


# ---- the quarantine ---------------------------------------------------------------------------


def test_store_quarantines_the_old_value_before_the_keeper_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    writes: list[str] = []
    original_write = h.store.write

    def watched(secret_id: str, value: Value) -> None:
        quarantine = h.store.find(WORK, h.rotation.quarantine_key)
        assert quarantine is not None and h.store.read(quarantine.id).same_as(h.original)
        writes.append(secret_id)
        original_write(secret_id, value)

    monkeypatch.setattr(h.store, "write", watched)
    h.run("store")
    assert writes == [h.keeper_id]


def test_a_keeper_holding_the_new_value_with_no_quarantine_is_refused(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    staging = h.store.find(WORK, h.rotation.staging_key)
    assert staging is not None
    h.store.write(h.keeper_id, h.store.read(staging.id))
    with pytest.raises(ContractError, match="nothing is quarantined"):
        h.run("store")


def test_a_keeper_holding_a_third_value_is_refused(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    h.store.write(h.keeper_id, Value("a-value-nobody-staged"))
    with pytest.raises(ContractError, match="neither the old value nor the new"):
        h.run("store")


def test_store_run_again_re_proves_rather_than_writes(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    new = h.keeper()
    h.run("store")
    assert h.keeper().same_as(new)
    assert len([k for k in h.store.secrets if k != h.keeper_id]) == 2


def test_a_keeper_that_does_not_read_back_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    monkeypatch.setattr(h.store, "write", lambda secret_id, value: None)
    with pytest.raises(ContractError, match="the keeper doesn't read back"):
        _single(h, StepKind.STORE)


# ---- probes and their controls ----------------------------------------------------------------


def test_a_probe_that_accepts_the_known_bad_value_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    monkeypatch.setattr(h.provider, "probe", lambda value: 200)
    with pytest.raises(ContractError, match="didn't refuse a known-bad"):
        h.run("mint")


@pytest.mark.parametrize("status", [None, 403, 404, 429, 500, 201])
def test_any_answer_but_200_or_the_dead_status_stops_the_rotation(
    tmp_path: Path, status: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    monkeypatch.setattr(h.provider, "probe", lambda value: status if _is_staged(h, value) else 401)
    with pytest.raises(ContractError, match="indeterminate"):
        h.run("mint")


def _is_staged(h: Harness, value: Value) -> bool:
    staging = h.store.find(WORK, h.rotation.staging_key)
    return staging is not None and h.store.read(staging.id).same_as(value)


def test_a_staged_value_the_provider_refuses_is_not_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    monkeypatch.setattr(h.provider, "mint", lambda: Value("minted-but-never-live"))
    with pytest.raises(ContractError, match="probed dead"):
        h.run("mint")
    assert h.keeper().same_as(h.original)


# ---- retire only after every consumer verifies ------------------------------------------------


def test_retire_waits_for_the_human_attestation(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    with pytest.raises(ContractError, match="hasn't verified"):
        h.run("retire")
    assert h.provider.probe(h.original) == 200


def test_an_attestation_of_another_value_does_not_count(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    with pytest.raises(ContractError, match="hasn't verified"):
        h.run("retire", attestations=_attest(h.original))
    assert h.provider.probe(h.original) == 200


def test_one_attestation_covers_one_attested_consumer(tmp_path: Path) -> None:
    second = 'shell-export file="/home/x/.profile" var="Y"'
    h = Harness(tmp_path)
    h.rotation = replace(h.rotation, destinations=(*h.rotation.destinations, second))
    h.run("mint")
    h.run("store")
    with pytest.raises(ContractError, match="hasn't verified"):
        h.run("retire", attestations=_attest(h.keeper()))
    h.run("retire", attestations=_attest(h.keeper()) * 2)


def test_retire_re_reads_bws_consumers_rather_than_trusting_store(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    new = h.keeper()
    h.store.write(h.keeper_id, h.original)
    h.store.write(h.keeper_id, h.original)
    with pytest.raises(ContractError):
        h.run("retire", attestations=_attest(new))
    assert h.provider.probe(h.original) == 200


def test_a_second_bws_consumer_is_written_by_distribute_and_verified(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    copy = h.store.put("elsewhere", "copy", h.original)
    h.rotation = replace(
        h.rotation, destinations=(*h.rotation.destinations, f'bws-secret uuid="{copy}"')
    )
    h.run("mint")
    with pytest.raises(ContractError, match="stored consumer doesn't read back"):
        h.run("store")
    _single(h, StepKind.DISTRIBUTE)
    assert h.store.read(copy).same_as(h.keeper())
    h.run("store")


def test_a_hosted_consumer_is_refused(tmp_path: Path) -> None:
    hosted = (
        'coolify-env-hash instance="prod" resource_type="application" uuid="u" key="K" entry="E"'
    )
    h = Harness(tmp_path)
    h.rotation = replace(h.rotation, destinations=(*h.rotation.destinations, hosted))
    h.run("mint")
    with pytest.raises(ContractError, match="can't reach a coolify-env-hash consumer"):
        h.run("store")


def test_a_consumer_kind_with_no_verification_is_refused(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.rotation = replace(h.rotation, destinations=(*h.rotation.destinations, "mystery"))
    h.run("mint")
    with pytest.raises(DestinationError):
        h.run("store")


# ---- retire, confirm and clean up -------------------------------------------------------------


def test_retire_needs_the_quarantine(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    with pytest.raises(ContractError, match="no quarantined old value"):
        _single(h, StepKind.RETIRE_OLD)
    assert h.provider.probe(h.original) == 200


def test_retire_derives_the_revoke_from_the_quarantined_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    revoked: list[Value] = []
    real = h.provider.revoke

    def watched(value: Value) -> None:
        revoked.append(value)
        real(value)

    monkeypatch.setattr(h.provider, "revoke", watched)
    h.run("retire", attestations=_attest(h.keeper()))
    assert len(revoked) == 1 and revoked[0].same_as(h.original)


def test_an_old_value_still_live_after_retire_stops_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    monkeypatch.setattr(h.provider, "revoke", lambda value: None)
    with pytest.raises(ContractError, match="old value probed live"):
        h.run("retire", attestations=_attest(h.keeper()))
    assert h.store.find(WORK, h.rotation.quarantine_key) is not None


def test_confirm_resumes_after_staging_is_deleted(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    _single(h, StepKind.RETIRE_OLD, attestations=_attest(h.keeper()))
    staging = h.store.find(WORK, h.rotation.staging_key)
    assert staging is not None
    h.store.delete(staging.id)
    _single(h, StepKind.CONFIRM_DEAD)
    assert h.store.secrets.keys() == {h.keeper_id}
    assert h.filed[-1][1].note is not None and "quarantine" in h.filed[-1][1].note


def test_a_finished_unit_is_skipped_and_an_unfinished_one_is_not(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    new = h.keeper()
    h.run("retire", attestations=_attest(new))
    count = len(h.filed)
    h.run("retire", attestations=_attest(new))
    assert len(h.filed) == count
    (tmp_path / "fresh").mkdir()
    fresh = Harness(tmp_path / "fresh")
    fresh.store.put(WORK, fresh.rotation.staging_key, Value("staged-only"))
    with pytest.raises(ContractError, match="gone before confirm_dead"):
        UnitRun(
            rotation=fresh.rotation,
            store=fresh.store,
            provider=fresh.provider,
            attestations=(),
            record=lambda step, evidence: None,
            before_step=lambda: None,
        ).run(UnitPlan("c", (Step(StepKind.CONFIRM_DEAD, M, "AC-1"),)))


# ---- mint and stage ---------------------------------------------------------------------------


def test_mint_run_again_re_proves_the_staged_value(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    staged = h.store.find(WORK, h.rotation.staging_key)
    h.run("mint")
    assert h.store.find(WORK, h.rotation.staging_key) == staged
    assert h.filed[3][1].note == "already minted and staged by an earlier attempt"


def test_a_quarantine_with_nothing_staged_is_refused_at_mint(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.store.put(WORK, h.rotation.quarantine_key, h.original)
    with pytest.raises(ContractError, match="never guess"):
        h.run("mint")


def test_stage_with_nothing_minted_is_refused(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    with pytest.raises(ContractError, match="nothing minted"):
        UnitRun(
            rotation=h.rotation,
            store=h.store,
            provider=h.provider,
            attestations=(),
            record=lambda step, evidence: None,
            before_step=lambda: None,
        ).run(UnitPlan("s", (Step(StepKind.STAGE, M, "AC-1"),)))


def test_stage_refuses_a_different_value_already_staged(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.store.put(WORK, h.rotation.staging_key, Value("someone-else-staged-this"))
    run = UnitRun(
        rotation=h.rotation,
        store=h.store,
        provider=h.provider,
        attestations=(),
        record=lambda step, evidence: None,
        before_step=lambda: None,
    )
    run._minted = Value("this-attempt-minted-this")
    with pytest.raises(ContractError, match="different value is already staged"):
        run.run(UnitPlan("s", (Step(StepKind.STAGE, M, "AC-1"),)))


def test_the_registry_check_runs_before_every_step(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    def handed_back() -> None:
        raise ContractError("no longer rotated_by_sds")

    with pytest.raises(ContractError, match="rotated_by_sds"):
        h.run("mint", before_step=handed_back)
    assert h.kinds() == []


# ---- the plan -----------------------------------------------------------------------------------


def _plan(*segments: UnitPlan | HumanAct) -> Plan:
    return Plan(segments)


def _with(kinds: list[StepKind]) -> Plan:
    return _plan(UnitPlan("u", tuple(Step(k, M, f"AC-{i}") for i, k in enumerate(kinds))))


GOOD = [
    StepKind.MINT,
    StepKind.STAGE,
    StepKind.VERIFY_NEW,
    StepKind.STORE,
    StepKind.VERIFY_CONSUMERS,
    StepKind.RETIRE_OLD,
    StepKind.CONFIRM_DEAD,
]


def test_the_fake_plan_and_a_minimal_plan_are_valid() -> None:
    validate_plan(PLAN)
    validate_plan(_with(GOOD))


def _without(kind: StepKind) -> list[StepKind]:
    return [k for k in GOOD if k is not kind]


BAD_ORDERS: dict[str, list[StepKind]] = {
    "no mint": _without(StepKind.MINT),
    "no stage": _without(StepKind.STAGE),
    "no verify_new": _without(StepKind.VERIFY_NEW),
    "no store": _without(StepKind.STORE),
    "no verify_consumers": _without(StepKind.VERIFY_CONSUMERS),
    "no retire": _without(StepKind.RETIRE_OLD),
    "no confirm": _without(StepKind.CONFIRM_DEAD),
    "two mints": [StepKind.MINT, *GOOD],
    "stage apart from mint": [
        StepKind.MINT,
        StepKind.VERIFY_NEW,
        StepKind.STAGE,
        *GOOD[3:],
    ],
    "retire before verify": [*GOOD[:4], StepKind.RETIRE_OLD, StepKind.VERIFY_CONSUMERS, GOOD[6]],
    "a step after confirm": [*GOOD, StepKind.VERIFY_CONSUMERS],
    "a step between retire and confirm": [
        *GOOD[:6],
        StepKind.VERIFY_CONSUMERS,
        StepKind.CONFIRM_DEAD,
    ],
    "verify before store": [*GOOD[:3], StepKind.VERIFY_CONSUMERS, *GOOD[3:]],
    "distribute after the last verify": [*GOOD[:5], StepKind.DISTRIBUTE, *GOOD[5:]],
    "verify_new after retire": [
        StepKind.MINT,
        StepKind.STAGE,
        StepKind.STORE,
        StepKind.VERIFY_CONSUMERS,
        StepKind.RETIRE_OLD,
        StepKind.CONFIRM_DEAD,
        StepKind.VERIFY_NEW,
    ],
}


@pytest.mark.parametrize("kinds", BAD_ORDERS.values(), ids=BAD_ORDERS.keys())
def test_a_plan_that_could_break_an_invariant_is_refused(kinds: list[StepKind]) -> None:
    with pytest.raises(ContractError):
        validate_plan(_with(kinds))


def test_segments_hold_the_right_actors_and_unique_names() -> None:
    machine = UnitPlan("u", tuple(Step(k, M, f"AC-{i}") for i, k in enumerate(GOOD)))
    with pytest.raises(ContractError, match="unique"):
        validate_plan(_plan(machine, UnitPlan("u", (Step(StepKind.VERIFY_CONSUMERS, M, "x"),))))
    with pytest.raises(ContractError, match="machine steps"):
        validate_plan(_plan(UnitPlan("u", (Step(StepKind.MINT, H, "AC-1"),))))
    with pytest.raises(ContractError, match="machine steps"):
        validate_plan(_plan(UnitPlan("u", (Step(StepKind.MINT, M, None),))))
    with pytest.raises(ContractError, match="human steps"):
        validate_plan(_plan(HumanAct("h", (Step(StepKind.MINT, M),))))
    with pytest.raises(ContractError, match="human steps"):
        validate_plan(_plan(HumanAct("h", (Step(StepKind.MINT, H, "AC-1"),))))
    with pytest.raises(ContractError, match="at least one step"):
        validate_plan(_plan(UnitPlan("u", ())))
    shared = UnitPlan("u", tuple(Step(k, M, "AC-1") for k in GOOD))
    with pytest.raises(ContractError, match="own acceptance criterion"):
        validate_plan(_plan(shared))


def test_a_machine_mint_and_its_stage_share_a_unit() -> None:
    split = _plan(
        UnitPlan("a", (Step(StepKind.MINT, M, "AC-1"),)),
        UnitPlan("b", tuple(Step(k, M, f"AC-{i + 2}") for i, k in enumerate(GOOD[1:]))),
    )
    with pytest.raises(ContractError, match="shares mint's segment"):
        validate_plan(split)


def test_unit_lookup_refuses_an_unknown_or_human_name() -> None:
    with pytest.raises(ContractError, match="no unit named"):
        PLAN.unit("attest")


def test_store_and_confirm_refuse_a_quarantine_equal_to_the_new_value(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.store.put(WORK, h.rotation.staging_key, h.original)
    h.store.put(WORK, h.rotation.quarantine_key, h.original)
    for kind in (StepKind.STORE, StepKind.CONFIRM_DEAD):
        with pytest.raises(ContractError, match="equals the old"):
            _single(h, kind)
    assert h.kinds() == []


def test_filed_evidence_alone_never_finishes_a_unit(tmp_path: Path) -> None:
    """A claim that confirm_dead was filed skips nothing while a secret it would delete remains."""
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    h.run(
        "retire",
        attestations=_attest(h.keeper()),
        filed=frozenset({StepKind.CONFIRM_DEAD}),
    )
    assert h.store.secrets.keys() == {h.keeper_id}
    assert h.provider.probe(h.original) == 401


def test_every_consumer_kind_has_one_way_to_be_verified() -> None:
    # On the VM, every kind has exactly one way. On the Keychain instance (amendment 12) a
    # Keychain item is read back, which `unverified` asks before attestation, and a BWS secret
    # has none, so a unit naming one is refused there.
    from rotation_worker.contract import ACTIONS_KIND, ATTESTED_KINDS, HELD_KIND
    from rotation_worker.destinations import HOSTED_KINDS, IDENTITY_FIELDS
    from rotation_worker.secret_store import KeychainStore

    for store, unverifiable in ((MemoryStore(), set()), (KeychainStore(), {"bws-secret"})):
        read_back = {store.read_back_kind}
        attested = ATTESTED_KINDS - read_back
        classes = [{"provider"}, read_back, attested, HOSTED_KINDS, {ACTIONS_KIND}, {HELD_KIND}]
        for kind in IDENTITY_FIELDS:
            ways = sum(kind in group for group in classes)
            assert ways == (0 if kind in unverifiable else 1), (store.read_back_kind, kind)


def test_a_kind_without_a_way_to_verify_it_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rotation_worker.contract as contract

    monkeypatch.setattr(contract, "ATTESTED_KINDS", frozenset({"runtime-fetch"}))
    keychain = 'keychain service="s" account="a"'
    h = Harness(tmp_path)
    h.rotation = replace(h.rotation, destinations=(*h.rotation.destinations, keychain))
    h.run("mint")
    with pytest.raises(ContractError, match="no way to verify a keychain"):
        h.run("store")


@pytest.mark.parametrize(
    ("recorded", "matches"),
    [
        (Fingerprint("0123456789abcdef", 43), True),
        (Fingerprint("01234567", 43), True),
        (Fingerprint("0123456", 43), False),
        (Fingerprint("01234567", 42), False),
        (Fingerprint("0123456789abcdef0", 43), False),
        (Fingerprint("11234567", 43), False),
    ],
)
def test_a_recorded_fingerprint_names_a_value_by_prefix_and_length(
    recorded: Fingerprint, matches: bool
) -> None:
    assert Fingerprint("0123456789abcdef", 43).matches(recorded) is matches


def test_the_fake_provider_refuses_to_revoke_what_it_never_issued(tmp_path: Path) -> None:
    provider = FakeProvider(tmp_path / "provider.json")
    with pytest.raises(ContractError, match="no live value"):
        provider.revoke(Value("never-issued"))


def test_a_cleanup_interrupted_after_staging_finishes_by_proving_death_again(
    tmp_path: Path,
) -> None:
    """Staging deleted, quarantine kept: the retire unit's next attempt runs confirm_dead only."""
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    new = h.keeper()
    _single(h, StepKind.RETIRE_OLD, attestations=_attest(new))
    staging = h.store.find(WORK, h.rotation.staging_key)
    assert staging is not None
    h.store.delete(staging.id)
    h.filed.clear()
    h.run("retire", attestations=_attest(new))
    assert h.kinds() == ["confirm_dead", "confirm_dead"]
    assert h.store.secrets.keys() == {h.keeper_id}


def test_any_other_unit_refuses_when_staging_is_gone_and_the_quarantine_kept(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    staging = h.store.find(WORK, h.rotation.staging_key)
    assert staging is not None
    h.store.delete(staging.id)
    with pytest.raises(ContractError, match="never guess"):
        h.run("store")


def test_retire_probes_the_old_value_beside_a_control_and_files_both(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    _single(h, StepKind.RETIRE_OLD, attestations=_attest(h.keeper()))
    probes = h.filed[-1][1].probes
    assert [(p.subject, p.outcome) for p in probes] == [("known_bad", "dead"), ("old", "live")]


def test_mint_reads_the_old_value_before_minting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    minted: list[Value] = []
    monkeypatch.setattr(h.provider, "mint", lambda: minted.append(Value("never")) or minted[0])
    del h.store.secrets[h.keeper_id]
    with pytest.raises(StoreError):
        h.run("mint")
    assert minted == []


class TwoTargetProvider(FakeProvider):
    """A fake whose probe before a revoke is its own endpoint, as OpenRouter's key-info is."""

    revoke_probe_target = "fake-provider/key-info"

    def __init__(self, state_path: Path) -> None:
        super().__init__(state_path)
        self.asked_before_revoke: list[Value] = []

    def probe_before_revoke(self, value: Value) -> int | None:
        self.asked_before_revoke.append(value)
        return super().probe(value)


def test_retire_asks_the_probe_before_revoke_for_control_and_subject_alike(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.provider = TwoTargetProvider(tmp_path / "provider.json")
    h.original = h.provider.mint()
    h.store.write(h.keeper_id, h.original)
    h.run("mint")
    h.run("store")
    assert h.provider.asked_before_revoke == []
    h.run("retire", attestations=_attest(h.keeper()))
    retire, confirm = h.filed[-3][1], h.filed[-1][1]
    assert {p.target for p in retire.probes} == {"fake-provider/key-info"}
    assert len(h.provider.asked_before_revoke) == 2
    assert h.provider.asked_before_revoke[1].same_as(h.original)
    assert {p.target for p in confirm.probes} == {"fake-provider/key"}


def test_retire_refuses_when_the_probe_can_t_tell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    monkeypatch.setattr(h.provider, "probe", lambda value: 401 if _is_known_bad(h, value) else 503)
    revoked: list[Value] = []
    monkeypatch.setattr(h.provider, "revoke", lambda value: revoked.append(value))
    with pytest.raises(ContractError, match="indeterminate"):
        _single(h, StepKind.RETIRE_OLD, attestations=_attest(h.keeper()))
    assert revoked == []


def test_retire_does_not_skip_the_revoke_on_a_probe_that_refuses_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchecked 401 once read as 'already dead'; now a probe refusing all values still needs
    the control to pass, and a dead answer for the old value is filed as such."""
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    monkeypatch.setattr(h.provider, "probe", lambda value: 401)
    _single(h, StepKind.RETIRE_OLD, attestations=_attest(h.keeper()))
    note = h.filed[-1][1].note
    assert note is not None and "already refused" in note
    assert [p.outcome for p in h.filed[-1][1].probes] == ["dead", "dead"]


def test_the_deletion_row_carries_the_proof_of_death(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.run("mint")
    h.run("store")
    h.run("retire", attestations=_attest(h.keeper()))
    last = h.filed[-1][1]
    assert last.note is not None and last.note.startswith("deleted")
    assert [(p.subject, p.outcome) for p in last.probes] == [
        ("known_bad", "dead"),
        ("old", "dead"),
        ("known_bad", "dead"),
        ("new", "live"),
    ]


def _is_known_bad(h: Harness, value: Value) -> bool:
    known = {h.original.fingerprint, h.keeper().fingerprint}
    staging = h.store.find(WORK, h.rotation.staging_key)
    if staging is not None:
        known.add(h.store.read(staging.id).fingerprint)
    return value.fingerprint not in known


@pytest.mark.parametrize(
    "kinds",
    [
        [StepKind.MINT, StepKind.STAGE, StepKind.STORE, StepKind.VERIFY_NEW, *GOOD[4:]],
        [*GOOD[:3], StepKind.DISTRIBUTE, StepKind.STORE, *GOOD[4:]],
    ],
    ids=["store before verify_new", "distribute before store"],
)
def test_nothing_unproved_reaches_a_consumer(kinds: list[StepKind]) -> None:
    with pytest.raises(ContractError):
        validate_plan(_with(kinds))


def test_distribute_between_store_and_the_last_verify_is_valid() -> None:
    validate_plan(_with([*GOOD[:4], StepKind.DISTRIBUTE, *GOOD[4:]]))


# ---- a provider that is the hosted service (ADR-0055 amendment 9) ----------------------------

HOSTED = 'coolify-env-hash instance="prod" resource_type="application" uuid="u" key="K" entry="E"'
HOSTED_PLAN = Plan(
    (
        UnitPlan(
            "mint",
            (
                Step(StepKind.MINT, M, "AC-001"),
                Step(StepKind.STAGE, M, "AC-002"),
                Step(StepKind.HOST, M, "AC-003"),
                Step(StepKind.VERIFY_NEW, M, "AC-004"),
            ),
        ),
        UnitPlan(
            "store",
            (Step(StepKind.STORE, M, "AC-005"), Step(StepKind.VERIFY_CONSUMERS, M, "AC-006")),
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


class HostedFake(FakeProvider):
    """A self-issued bearer's service: a minted value works only once `host` gives it over."""

    hosts_destinations = True

    def __init__(self, state_path: Path) -> None:
        super().__init__(state_path)
        self.hosted: list[tuple[str, str]] = []
        self.accept_hosted = True

    def mint(self) -> Value:
        return Value(f"minted-{len(self._live())}-{id(self)}-{len(self.hosted)}")

    def host(self, new: Value, old: Value) -> str:
        self.hosted.append((new.fingerprint.sha256_prefix, old.fingerprint.sha256_prefix))
        if self.accept_hosted:
            self._save(self._live() | {hashlib.sha256(new.reveal().encode()).hexdigest()})
        return "the service accepts the new value beside the old"


def _hosted(tmp_path: Path) -> Harness:
    h = Harness(tmp_path)
    h.provider = HostedFake(tmp_path / "hosted.json")
    h.original = Value("the-original-bearer")
    h.provider._save(frozenset({hashlib.sha256(b"the-original-bearer").hexdigest()}))
    h.store.write(h.keeper_id, h.original)
    h.rotation = replace(
        h.rotation,
        destinations=(f'bws-secret uuid="{h.keeper_id}"', 'provider name="fake"', HOSTED),
    )
    return h


def _run_hosted(h: Harness, unit: str) -> None:
    run = UnitRun(
        rotation=h.rotation,
        store=h.store,
        provider=h.provider,
        attestations=(),
        record=lambda step, evidence: h.filed.append((step, evidence)),
        before_step=lambda: None,
        filed=frozenset(step.kind for step, _ in h.filed),
    )
    run.run(HOSTED_PLAN.unit(unit))


def test_the_hosted_plan_is_valid() -> None:
    validate_plan(HOSTED_PLAN)


def test_a_hosted_rotation_hosts_the_new_value_before_probing_it(tmp_path: Path) -> None:
    h = _hosted(tmp_path)
    _run_hosted(h, "mint")
    _run_hosted(h, "store")
    _run_hosted(h, "retire")
    assert h.kinds()[:4] == ["mint", "stage", "host", "verify_new"]
    assert isinstance(h.provider, HostedFake)
    assert len(h.provider.hosted) == 1
    assert h.provider.probe(h.keeper()) == 200
    assert h.provider.probe(h.original) == 401
    for _, evidence in h.filed:
        jsonschema.validate(evidence.as_payload(), SCHEMA)


def test_a_value_the_service_was_never_given_fails_verify_new(tmp_path: Path) -> None:
    h = _hosted(tmp_path)
    assert isinstance(h.provider, HostedFake)
    h.provider.accept_hosted = False
    with pytest.raises(ContractError, match="new value probed dead"):
        _run_hosted(h, "mint")
    assert h.kinds() == ["mint", "stage", "host"]


def test_a_hosted_consumer_the_service_refuses_keeps_the_old_value_live(tmp_path: Path) -> None:
    h = _hosted(tmp_path)
    _run_hosted(h, "mint")
    _run_hosted(h, "store")
    # The service stops accepting the new value before retire: the consumer is unverified.
    h.provider._save(frozenset({hashlib.sha256(b"the-original-bearer").hexdigest()}))
    with pytest.raises(ContractError, match="hasn't verified"):
        _run_hosted(h, "retire")
    assert h.provider.probe(h.original) == 200


def test_a_hosted_consumer_whose_control_is_accepted_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _hosted(tmp_path)
    _run_hosted(h, "mint")
    _run_hosted(h, "store")
    run = UnitRun(h.rotation, h.store, h.provider, (), lambda *_: None, lambda: None)
    assert run.unverified(h.keeper()) == []
    monkeypatch.setattr(h.provider, "known_bad", lambda: h.keeper())
    assert run.unverified(h.keeper()) == [HOSTED]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ((StepKind.MINT, StepKind.HOST, StepKind.STAGE, StepKind.VERIFY_NEW), "mint, then stage"),
        ((StepKind.MINT, StepKind.STAGE, StepKind.VERIFY_NEW, StepKind.HOST), "before verify_new"),
        (
            (StepKind.MINT, StepKind.STAGE, StepKind.HOST, StepKind.HOST, StepKind.VERIFY_NEW),
            "at most one host",
        ),
    ],
)
def test_a_host_step_out_of_place_is_refused(steps: tuple[StepKind, ...], message: str) -> None:
    first = UnitPlan("mint", tuple(Step(kind, M, f"AC-0{i}") for i, kind in enumerate(steps)))
    with pytest.raises(ContractError, match=message):
        validate_plan(Plan((first, *HOSTED_PLAN.segments[1:])))


class Readers:
    """Scheduled readers that have switched once the test says so."""

    def __init__(self, *covered: str) -> None:
        self.covered = set(covered)
        self.switched_now = False
        self.asked: list[tuple[str, datetime]] = []

    def covers(self, destination: str) -> bool:
        return destination in self.covered

    def switched(self, destination: str, stored_at: datetime) -> bool:
        self.asked.append((destination, stored_at))
        return self.switched_now


def test_a_scheduled_reader_is_verified_by_its_read_not_an_attestation(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    readers = Readers(RUNTIME)
    h.run("mint")
    h.run("store")
    with pytest.raises(ContractError, match="hasn't verified"):
        h.run("retire", attestations=_attest(h.keeper()), scheduled=readers)
    assert h.provider.probe(h.original) == 200
    readers.switched_now = True
    h.run("retire", scheduled=readers)
    assert h.provider.probe(h.original) == 401


def test_a_reader_is_asked_about_the_keeper_s_last_write(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    readers = Readers(RUNTIME)
    h.run("mint")
    h.run("store")
    readers.switched_now = True
    h.run("retire", scheduled=readers)
    assert {stored_at for _, stored_at in readers.asked} == {h.store.revisions[h.keeper_id]}


def test_a_reader_the_reads_don_t_cover_still_needs_an_attestation(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    readers = Readers('runtime-fetch repo="orchestrator" ref="another reader"')
    readers.switched_now = True
    h.run("mint")
    h.run("store")
    with pytest.raises(ContractError, match="hasn't verified"):
        h.run("retire", scheduled=readers)
