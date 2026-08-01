"""The two surfaces that tell a person the authority gate is waiting (WS-P2.18 Inc 3, ADR-0011).

Admission is where the requirement is enforced; these are where it is ASKED FOR. R2 is *"a
dependency bump becomes something he does not approve"*, and a queue that keeps listing work
policy already recognises would deliver none of that however correct admission became. So the
three consumers move together, and each is proven in both directions here.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.readiness import ReadinessStatus
from orchestrator.persistence.models import WorkPackageRevision
from orchestrator.persistence.repositories import PackageRepository
from orchestrator.services.authority_gate import POLICY_UNREADABLE, human_authority_gate
from orchestrator.services.packages import evaluate_readiness
from orchestrator.services.pending_decisions import pending_decisions
from tests.services.test_dispatch import recognised_unit

NOVEL_COMMANDS: dict[str, list[str]] = {
    # One field different from the recognised control, and a real difference: this is the command
    # list the one envelope this factory has dispatched actually carried.
    "allowed_commands": ["uv add --dev 'ruff>=0.15.21'", "uv sync --locked"],
    "mutation_commands": ["uv add --dev 'ruff>=0.15.21'"],
}


def authority_entries(session: Session, unit_id: object) -> list[dict[str, object]]:
    return [
        entry
        for entry in pending_decisions(session)
        if entry["kind"] == "authority_approval" and str(unit_id) in str(entry["href"])
    ]


def test_the_queue_stops_asking_for_work_policy_recognises(migrated_session: Session) -> None:
    recognised = recognised_unit(
        migrated_session, key="queue-recognised", reach=["source_repository"]
    )
    novel = recognised_unit(
        migrated_session, key="queue-novel", reach=["source_repository"], **NOVEL_COMMANDS
    )
    migrated_session.commit()

    assert authority_entries(migrated_session, recognised.id) == []
    assert len(authority_entries(migrated_session, novel.id)) == 1


def test_readiness_reports_ready_without_inventing_an_approval(migrated_session: Session) -> None:
    unit = recognised_unit(
        migrated_session, key="readiness-recognised", reach=["source_repository"]
    )
    migrated_session.commit()

    decision = evaluate_readiness(migrated_session, unit.id)

    assert decision.status is ReadinessStatus.READY
    # And the fact underneath it is unchanged: no approval exists, and none was written to make
    # this answer come out. Readiness reports conditions met, never that a person decided.
    assert PackageRepository(migrated_session).exact_authority_approval(unit) is None


def test_readiness_still_withholds_authorization_for_a_novel_envelope(
    migrated_session: Session,
) -> None:
    unit = recognised_unit(
        migrated_session, key="readiness-novel", reach=["source_repository"], **NOVEL_COMMANDS
    )
    migrated_session.commit()

    decision = evaluate_readiness(migrated_session, unit.id)

    assert decision.status is ReadinessStatus.NOT_AUTHORIZED
    assert [reason.code for reason in decision.reasons] == ["authority_not_approved"]


def test_an_unreadable_artifact_asks(migrated_session: Session, monkeypatch: pytest.MonkeyPatch):
    """§3.3's last ask-condition: policy that cannot be consulted has recognised nothing.

    Proven through the gate rather than the loader, because the loader RAISES by design and the
    question here is what the consumer does with that -- fail toward asking, or let a
    configuration fault of this process read as an absence of objection.
    """
    unit = recognised_unit(migrated_session, key="unreadable-artifact", reach=["source_repository"])
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    assert human_authority_gate(unit, revision).refusals == ()  # control

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", "correct it")

    monkeypatch.setattr("orchestrator.services.authority_gate.load_factory_policy", unreadable)
    gate = human_authority_gate(unit, revision)

    assert gate.refusals == (POLICY_UNREADABLE,)
    assert (gate.recognised_by, gate.policy_version) == ((), None)
    assert len(authority_entries(migrated_session, unit.id)) == 1
