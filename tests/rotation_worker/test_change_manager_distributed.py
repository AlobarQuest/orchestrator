"""The read bearer's plan: change-manager's adapter with the orchestrator's held copy distributed.

It reuses the change-manager simulation and adds a held-copy writer whose reads echo what it wrote,
as the orchestrator's `held-credentials` route answers after a restart (ADR-0055 amendment 13).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import pytest

from rotation_worker.adapters import ADAPTERS
from rotation_worker.adapters.change_manager import DISTRIBUTED_NAME, DISTRIBUTED_PLAN, PLAN
from rotation_worker.contract import ContractError, Plan, UnitRun
from rotation_worker.values import Value
from tests.rotation_worker.test_change_manager import Harness, _hash

HELD = (
    'coolify-env-held instance="prod" resource_type="application" '
    'uuid="eqj5l7k705fhi12x9i74fqf0" key="ORCHESTRATOR_CHANGE_RECORD_TOKEN"'
)


class Copies:
    """The orchestrator's copy: what was written, and whether a restart loaded it."""

    def __init__(self) -> None:
        self.written: list[Value] = []
        self.loads = True

    def write(self, consumer: Mapping[str, Any], value: Value) -> None:
        assert consumer["key"] == "ORCHESTRATOR_CHANGE_RECORD_TOKEN"
        self.written.append(value)

    def holds(self, consumer: Mapping[str, Any], value: Value) -> bool:
        return self.loads and bool(self.written) and self.written[-1].same_as(value)


class Distributed(Harness):
    def __init__(self) -> None:
        super().__init__()
        self.rotation = replace(self.rotation, destinations=(*self.rotation.destinations, HELD))
        self.provider = self.build()
        self.copies: Copies | None = Copies()

    def run(
        self,
        unit: str,
        before_step: Callable[[], None] = lambda: None,
        plan: Plan = DISTRIBUTED_PLAN,
    ) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=before_step,
            filed=frozenset(step.kind for step, _ in self.filed),
            held=self.copies,
        ).run(plan.unit(unit))

    def steps(self) -> list[str]:
        return [evidence.step for _, evidence in self.filed]


@pytest.fixture
def d() -> Distributed:
    return Distributed()


def test_the_plan_is_registered_beside_change_manager_s_unchanged_one() -> None:
    assert ADAPTERS[DISTRIBUTED_NAME].plan is DISTRIBUTED_PLAN
    assert ADAPTERS["change-manager"].plan is PLAN
    assert ADAPTERS[DISTRIBUTED_NAME].provider_name == "change-manager"
    assert [s.ac_id for s in DISTRIBUTED_PLAN.steps()] == [f"AC-{n:03}" for n in range(1, 11)]


def test_mint_writes_the_held_copy_after_store_and_retire_ends_the_overlap(d: Distributed) -> None:
    assert d.copies is not None
    d.run("mint")
    new = d.keeper()
    assert [value.same_as(new) for value in d.copies.written] == [True]
    assert d.steps() == [
        "mint",
        "stage",
        "host",
        "verify_new",
        "store",
        "distribute",
        "verify_consumers",
    ]
    distribute = d.filed[5][1].note
    assert distribute is not None and distribute.endswith("and 1 held cop(ies)")
    assert d.filed[6][1].note == "3 of 3 consumers hold or attest the new value"
    d.run("retire")
    assert d.cm.entry() == {"token_hash": _hash(new.reveal())}
    for _, evidence in d.filed:
        assert new.reveal() not in json.dumps(evidence.as_payload())
        assert d.original.reveal() not in json.dumps(evidence.as_payload())


def test_without_a_writer_the_held_copy_is_refused_at_distribute(d: Distributed) -> None:
    d.copies = None
    with pytest.raises(ContractError, match="no writer is configured"):
        d.run("mint")
    assert d.steps()[-1] == "store"


def test_a_copy_the_orchestrator_didn_t_load_stops_mint_s_verify(d: Distributed) -> None:
    assert d.copies is not None
    d.copies.loads = False
    with pytest.raises(ContractError, match="a held copy isn't loaded with the new value"):
        d.run("mint")
    assert d.steps()[-1] == "distribute"


def test_retire_refuses_while_the_held_copy_isn_t_loaded(d: Distributed) -> None:
    assert d.copies is not None
    d.run("mint")
    d.copies.loads = False
    with pytest.raises(ContractError, match="a held copy isn't loaded with the new value"):
        d.run("retire")
    # The overlap stands: the orchestrator's old copy still works at change-manager.
    assert d.cm.entry()["previous"]["token_hash"] == _hash(d.original.reveal())


def test_a_stop_after_store_resumes_without_writing_change_manager_again(d: Distributed) -> None:
    assert d.copies is not None

    def stop_after_store() -> None:
        if d.steps()[-1:] == ["store"]:
            raise RuntimeError("the pass stopped")

    with pytest.raises(RuntimeError):
        d.run("mint", before_step=stop_after_store)
    assert d.copies.written == []
    writes = list(d.cm.sent)
    d.provider = d.build()
    d.run("mint")
    assert d.cm.sent == writes
    assert len(d.copies.written) == 1
    d.run("retire")
    assert d.cm.entry() == {"token_hash": _hash(d.keeper().reveal())}
