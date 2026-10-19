"""The factory-runner bearer's plan: the orchestrator adapter with its Actions copies distributed.

It reuses the orchestrator-bearer simulation (Coolify's rows and the orchestrator's loaded
identities) and adds the rotation App as a writer whose reports echo what it wrote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from rotation_worker.adapters import ADAPTERS
from rotation_worker.adapters.orchestrator_bearer import DISTRIBUTED_NAME, DISTRIBUTED_PLAN, PLAN
from rotation_worker.contract import ContractError, UnitRun
from rotation_worker.destinations import parse
from rotation_worker.github_actions import FingerprintReports
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.readers import ConsumerReads, scheduled_reads
from tests.rotation_worker.test_github_pat_and_bitbucket import Reports, Writer
from tests.rotation_worker.test_orchestrator_bearer import Harness, _hash

COPIES = (
    'gh-actions-secret repo="AlobarQuest/brain" name="FACTORY_RUNNER_TOKEN"',
    'gh-actions-secret repo="AlobarQuest/orchestrator" name="FACTORY_RUNNER_TOKEN"',
)


class Distributed(Harness):
    def __init__(self) -> None:
        super().__init__()
        self.rotation = replace(self.rotation, destinations=(*self.rotation.destinations, *COPIES))
        self.provider = self.build()
        self.writer = Writer()
        self.reports = Reports(self.writer)
        self.with_writer = True

    def run(self, unit: str, before_step: Callable[[], None] = lambda: None) -> None:
        reads = ConsumerReads(
            scheduled_reads({"consumer": [parse(d) for d in self.rotation.destinations]}),
            None,
            cast(OrchestratorClient, None),
            reports=cast(FingerprintReports, self.reports),
            keeper=lambda: self.store.read(self.keeper_id).fingerprint,
        )
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=before_step,
            filed=frozenset(step.kind for step, _ in self.filed),
            scheduled=reads,
            actions_secrets=self.writer if self.with_writer else None,
        ).run(DISTRIBUTED_PLAN.unit(unit))


@pytest.fixture
def d() -> Distributed:
    return Distributed()


def test_the_plan_is_registered_beside_the_orchestrator_s_unchanged_one() -> None:
    assert ADAPTERS[DISTRIBUTED_NAME].plan is DISTRIBUTED_PLAN
    assert ADAPTERS["orchestrator"].plan is PLAN
    assert ADAPTERS[DISTRIBUTED_NAME].provider_name == "orchestrator"
    assert [s.ac_id for s in PLAN.steps()] == [f"AC-00{n}" for n in range(1, 10)]


def test_mint_hosts_stores_and_writes_every_copy_then_retire_ends_the_overlap(
    d: Distributed,
) -> None:
    d.run("mint")
    new = d.keeper()
    assert d.entry()["previous"]["token_hash"] == _hash(d.original.reveal())
    assert set(d.writer.written) == {
        ("AlobarQuest/brain", "FACTORY_RUNNER_TOKEN"),
        (
            "AlobarQuest/orchestrator",
            "FACTORY_RUNNER_TOKEN",
        ),
    }
    assert all(value.same_as(new) for value in d.writer.written.values())
    d.run("retire")
    assert "previous" not in d.entry()
    assert [e.step for _, e in d.filed][4:6] == ["store", "distribute"]


def test_retire_refuses_while_a_copy_reports_the_old_value(d: Distributed) -> None:
    d.run("mint")
    del d.writer.written[("AlobarQuest/brain", "FACTORY_RUNNER_TOKEN")]
    with pytest.raises(ContractError, match="hasn't verified"):
        d.run("retire")
    assert d.entry()["previous"]["token_hash"] == _hash(d.original.reveal())


def test_a_stop_after_host_resumes_without_writing_coolify_again(d: Distributed) -> None:
    steps = 0

    def stop_after_host() -> None:
        nonlocal steps
        steps += 1
        if steps == 4:
            raise ContractError("stopped after host")

    with pytest.raises(ContractError, match="stopped after host"):
        d.run("mint", stop_after_host)
    # Host has run: the orchestrator accepts both, the keeper still holds the old value, and no
    # copy was written.
    assert d.keeper().same_as(d.original)
    assert d.writer.written == {}
    written = len(d.estate.sent)
    d.provider = d.build()
    d.run("mint")
    assert len(d.estate.sent) == written
    assert len(d.writer.written) == 2
    d.run("retire")
    assert "previous" not in d.entry()


def test_without_a_writer_the_copies_are_refused_after_store(d: Distributed) -> None:
    d.with_writer = False
    with pytest.raises(ContractError, match="no writer is configured"):
        d.run("mint")


def test_mint_s_last_verify_records_copies_whose_report_hasn_t_run(d: Distributed) -> None:
    d.reports = Reports(Writer())
    d.run("mint")
    note = d.filed[-1][1].note
    assert d.filed[-1][1].step == "verify_consumers"
    # The keeper and the hosted entry hold it; neither copy has reported yet.
    assert note == "2 of 4 consumers hold or attest the new value"
