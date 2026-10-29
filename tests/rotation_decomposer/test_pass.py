"""One pass over fake services: which carried rotations get a proposal, and which never do."""

from __future__ import annotations

from typing import Any, cast

import pytest

from rotation_decomposer import cli
from rotation_decomposer.cli import FOUND, INCOMPLETE, exit_code, run_pass
from rotation_decomposer.client import CallError, Orchestrator
from rotation_worker.registry import RegistryEntry, RegistryError
from tests.rotation_decomposer.test_decompose import _entry, _intake

REVISION = "0c7a484c-1111-4111-8111-111111111111"
RECORD = {"id": 139, "package_id": "rotation-example", "package_revision": 2, "status": "approved"}


class FakeOrchestrator:
    def __init__(self) -> None:
        self.entry = _entry("openrouter-key")
        self.works: dict[int, Any] = {139: {"revision_ids": [REVISION], "units": []}}
        self.states: list[str] = []
        self.proposed: list[tuple[str, dict[str, Any]]] = []
        self.unreadable: set[int] = set()
        self.refuse_with: int | None = None
        # The registered revision number, by revision id, where it isn't the record's.
        self.revisions: dict[str, int] = {}

    def work(self, change_record_id: int) -> dict[str, Any]:
        if change_record_id in self.unreadable:
            raise CallError("the orchestrator refused GET work: 503")
        return self.works[change_record_id]

    def proposals(self, revision_id: str) -> list[dict[str, Any]]:
        return [{"state": state} for state in self.states]

    def intake(self, revision_id: str) -> dict[str, Any]:
        return {**_intake(self.entry), "revision": self.revisions.get(revision_id, 2)}

    def propose(self, revision_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.refuse_with is not None:
            raise CallError(f"the orchestrator refused POST: {self.refuse_with}", self.refuse_with)
        self.proposed.append((revision_id, body))
        return {"id": "p-1", "state": "proposed"}


@pytest.fixture
def orch() -> FakeOrchestrator:
    return FakeOrchestrator()


def _run(
    orch: FakeOrchestrator,
    records: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    registry: dict[str, RegistryEntry] | None = None,
) -> list[cli.Outcome]:
    rows = registry if registry is not None else {"example": orch.entry}
    return run_pass(records, cast(Orchestrator, orch), lambda: rows, dry_run=dry_run)


def test_a_carried_rotation_with_no_proposal_gets_one_as_a_replayable_proposal(orch) -> None:
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == ["proposed"]
    [(revision, body)] = orch.proposed
    assert revision == REVISION
    assert body["idempotency_key"] == f"rotation-decomposer:{REVISION}"
    assert body["expected_version"] == 0
    assert [u["unit_key"] for u in body["proposed_units"]] == ["example-mint", "example-retire"]
    assert exit_code(outcomes) == 0


def test_a_dry_run_proposes_nothing(orch) -> None:
    assert [o.result for o in _run(orch, [RECORD], dry_run=True)] == ["would-propose"]
    assert orch.proposed == []


@pytest.mark.parametrize(
    "record",
    [
        {**RECORD, "package_id": "example-app-npm-zod"},
        {**RECORD, "status": "proposed"},
        {**RECORD, "status": "resolved"},
    ],
)
def test_only_an_approved_rotation_record_is_considered(orch, record) -> None:
    assert _run(orch, [record]) == []
    assert orch.proposed == []


def test_a_record_whose_work_has_units_is_left_alone(orch) -> None:
    orch.works[139] = {
        "revision_ids": [REVISION],
        "units": [{"unit_id": "u", "revision_id": REVISION}],
    }
    assert _run(orch, [RECORD]) == []


def test_a_record_the_carrier_hasn_t_reached_waits_quietly(orch) -> None:
    orch.works[139] = {"revision_ids": [], "units": []}
    assert _run(orch, [RECORD]) == []


@pytest.mark.parametrize(
    ("states", "result", "code"),
    [
        (["proposed"], "awaiting-approval", 0),
        # Approval creates its units at once: approved with none means a person superseded it.
        (["approved"], "superseded", FOUND),
        # A person declined it: never proposed again by machine, and they hear of it.
        (["rejected"], "declined", FOUND),
        (["revision_required"], "declined", FOUND),
    ],
)
def test_a_proposal_already_there_is_never_proposed_over(orch, states, result, code) -> None:
    orch.states = states
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == [result]
    assert orch.proposed == []
    assert exit_code(outcomes) == code


def test_a_package_that_can_t_be_decomposed_is_a_finding_and_proposes_nothing(orch) -> None:
    orch.entry = _entry("bws-machine-token")
    registry = {"example": _entry("openrouter-key")}
    outcomes = _run(orch, [RECORD], registry=registry)
    assert [o.result for o in outcomes] == ["refused"]
    assert orch.proposed == []
    assert exit_code(outcomes) == FOUND


def test_a_credential_missing_from_the_registry_is_refused(orch) -> None:
    outcomes = _run(orch, [RECORD], registry={})
    assert [o.result for o in outcomes] == ["refused"]
    assert "no registry row" in outcomes[0].detail


def test_one_unreadable_record_is_incomplete_and_the_pass_goes_on(orch) -> None:
    orch.unreadable = {138}
    orch.works[138] = {}
    outcomes = _run(orch, [{**RECORD, "id": 138}, RECORD])
    assert [o.result for o in outcomes] == ["unread", "proposed"]
    assert exit_code(outcomes) == INCOMPLETE


def test_an_unreadable_registry_is_incomplete(orch) -> None:
    def unreadable() -> dict[str, RegistryEntry]:
        raise RegistryError("AlobarQuest/orchestrator: .cred-consumers.toml doesn't parse")

    outcomes = run_pass([RECORD], cast(Orchestrator, orch), unreadable)
    assert [o.result for o in outcomes] == ["unread"]
    assert exit_code(outcomes) == INCOMPLETE


def test_the_command_needs_both_credentials(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CHANGE_MANAGER_TOKEN", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "t")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "orchestrator-system")
    assert cli.main([]) == 1
    assert "CHANGE_MANAGER_TOKEN" in capsys.readouterr().err


def test_a_proposal_the_orchestrator_refuses_is_a_finding_not_an_unread_record(orch) -> None:
    orch.refuse_with = 422
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == ["refused"]
    assert exit_code(outcomes) == FOUND


def test_a_proposal_the_orchestrator_couldn_t_answer_is_unread(orch) -> None:
    orch.refuse_with = 503
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == ["unread"]
    assert exit_code(outcomes) == INCOMPLETE


def test_only_the_revision_the_record_names_is_proposed(orch) -> None:
    """Kills: proposing for a revision the package has since replaced, whose units the executor
    would refuse as not the package's latest."""
    older = "0c7a484c-2222-4222-8222-222222222222"
    orch.works[139] = {"revision_ids": [older, REVISION], "units": []}
    orch.revisions[older] = 1
    outcomes = _run(orch, [RECORD])
    assert [o.result for o in outcomes] == ["proposed"]
    assert [revision for revision, _ in orch.proposed] == [REVISION]


def test_a_started_revision_doesn_t_hide_another_without_units(orch) -> None:
    older = "0c7a484c-2222-4222-8222-222222222222"
    orch.works[139] = {
        "revision_ids": [older, REVISION],
        "units": [{"unit_id": "u", "revision_id": older}],
    }
    assert [o.result for o in _run(orch, [RECORD])] == ["proposed"]
    assert [revision for revision, _ in orch.proposed] == [REVISION]
