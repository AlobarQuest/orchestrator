import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event
from orchestrator.services.dispatch import (
    DispatchCommand,
    DispatchSettings,
    GitHubDispatchError,
    age_out_human_gates,
    dispatch_work_unit,
)
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)

AUTHORITY = AuthorityEnvelope(
    capabilities={"repository_write": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 8, tzinfo=UTC)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HUMAN = ActorContext("devon", ActorRole.HUMAN)


@dataclass
class FakeGitHubDispatcher:
    calls: list[dict[str, object]]
    failure: GitHubDispatchError | None = None

    def dispatch_workflow(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return {"workflow_run_id": "12345", "workflow_run_url": "https://github.invalid/run/12345"}


def settings(**overrides: object) -> DispatchSettings:
    values = {
        "enabled": True,
        "allowed_change_classes": frozenset({"repository_write"}),
        "enabled_capabilities": frozenset({"repository_write"}),
        "target_repository": "AlobarQuest/orchestrator",
        "workflow_id": ".github/workflows/factory-runner-pilot.yml",
        "workflow_ref": "main",
        "github_token": "test-token",
        "failure_signature_threshold": 3,
    }
    values.update(overrides)
    return DispatchSettings(**values)


MISSING = object()


def ready_unit(
    session: Session,
    *,
    key: str = "dispatch-unit",
    conformance: dict[str, object] | object = MISSING,
):
    if conformance is MISSING:
        enforcement_snapshot = {
            "conformance": {
                "status": "green",
                "accepted_standards": [],
                "standards_touched": ["project-standards"],
            }
        }
    elif conformance is None:
        enforcement_snapshot = {}
    else:
        enforcement_snapshot = {"conformance": conformance}
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="AlobarQuest/orchestrator",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot=enforcement_snapshot,
        authority=AUTHORITY,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title="Dispatch work",
        outcome="Runner opens a PR",
        required_capability="repository_write",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    record_approval(
        session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
        reason="approved",
        idempotency_key=f"{key}-authority",
        expected_version=1,
    )
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.READY,
            actor=SYSTEM,
            expected_version=1,
            idempotency_key=f"{key}-ready",
        ),
    )
    return unit


def dispatch_command(unit_id: uuid.UUID, *, attempt: int = 1) -> DispatchCommand:
    return DispatchCommand(
        unit_id=unit_id,
        runner_attempt=attempt,
        actor=SYSTEM,
        idempotency_key=f"dispatch:{unit_id}:{attempt}",
    )


def test_dispatch_fails_closed_when_global_switch_disabled(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session)
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(enabled=False),
        github,
    )

    assert record.status == "skipped"
    assert record.reason_code == "dispatch_disabled"
    assert github.calls == []


def test_dispatch_sends_ws41_workflow_dispatch_once(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session)
    github = FakeGitHubDispatcher([])
    command = dispatch_command(unit.id)

    first = dispatch_work_unit(migrated_session, command, settings(), github)
    replay = dispatch_work_unit(migrated_session, command, settings(), github)

    assert replay.id == first.id
    assert first.status == "dispatched"
    assert first.github_run_id == "12345"
    assert len(github.calls) == 1
    assert github.calls[0]["repository"] == "AlobarQuest/orchestrator"
    assert github.calls[0]["workflow_id"] == ".github/workflows/factory-runner-pilot.yml"
    assert github.calls[0]["ref"] == "main"
    assert github.calls[0]["inputs"] == {
        "work_unit_id": str(unit.id),
        "orchestrator_url": "https://sds.alobar.net",
    }


def test_dispatch_blocks_unknown_conformance_without_calling_github(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="missing-conformance", conformance=None)
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(),
        github,
    )

    assert record.status == "blocked"
    assert record.reason_code == "conformance_missing"
    assert github.calls == []


def test_dispatch_requires_green_or_accepted_conformance(migrated_session: Session) -> None:
    unit = ready_unit(
        migrated_session,
        key="red-conformance",
        conformance={
            "status": "red",
            "accepted_standards": [],
            "standards_touched": ["project-standards"],
        },
    )
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)

    assert record.status == "blocked"
    assert record.reason_code == "conformance_not_green"
    assert github.calls == []


def test_dispatch_allows_explicitly_accepted_touched_standards(
    migrated_session: Session,
) -> None:
    unit = ready_unit(
        migrated_session,
        key="accepted-conformance",
        conformance={
            "status": "red",
            "accepted_standards": ["project-standards"],
            "standards_touched": ["project-standards"],
        },
    )
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)

    assert record.status == "dispatched"
    assert len(github.calls) == 1


def test_dispatch_circuit_breaker_blocks_repeated_failure_signature(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="circuit")
    failure = GitHubDispatchError("github_api", "same failure")
    github = FakeGitHubDispatcher([], failure=failure)

    first = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id, attempt=1), settings(), github
    )
    second = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id, attempt=2), settings(), github
    )
    third = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id, attempt=3), settings(), github
    )

    assert first.status == "failed"
    assert second.status == "failed"
    assert third.status == "blocked"
    assert third.reason_code == "failure_signature_circuit_open"
    assert first.failure_signature == second.failure_signature == third.failure_signature


def test_dispatch_records_canonical_event(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="event")

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(),
        FakeGitHubDispatcher([]),
    )

    event = migrated_session.scalar(select(Event).where(Event.id == record.event_id))
    assert event is not None
    assert event.action == "dispatch.dispatched"
    assert event.subject_id == unit.id
    assert event.payload["dispatch_record_id"] == str(record.id)


def test_human_gate_age_out_records_blocked_evidence_without_auto_proceeding(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="human-gate")
    persisted = migrated_session.get(type(unit), unit.id)
    assert persisted is not None
    persisted.state = "awaiting_approval"
    persisted.version = 3
    migrated_session.commit()
    evaluation_time = datetime.now(UTC) + timedelta(days=2)

    records = age_out_human_gates(
        migrated_session,
        settings(human_gate_age_out_seconds=3600),
        SYSTEM,
        now=evaluation_time,
    )
    replay = age_out_human_gates(
        migrated_session,
        settings(human_gate_age_out_seconds=3600),
        SYSTEM,
        now=evaluation_time,
    )

    refreshed = migrated_session.get(type(unit), unit.id)
    assert refreshed is not None
    assert refreshed.state == "awaiting_approval"
    assert len(records) == len(replay) == 1
    assert replay[0].id == records[0].id
    assert records[0].status == "blocked"
    assert records[0].reason_code == "human_gate_aged_out"
