import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import orchestrator.services.dispatch as dispatch_module
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event
from orchestrator.services.dispatch import (
    DispatchCommand,
    DispatchSettings,
    GitHubActionsDispatcher,
    GitHubDispatchError,
    age_out_human_gates,
    dispatch_work_unit,
)
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)

PILOT_REPOSITORY = "AlobarQuest/orchestrator"
GREEN_CONFORMANCE: dict[str, object] = {
    "status": "green",
    "accepted_standards": [],
    "standards_touched": ["project-standards"],
}
MISSING = object()


def authority(
    target_repository: str | None = PILOT_REPOSITORY,
    conformance: dict[str, object] | object = MISSING,
) -> AuthorityEnvelope:
    """Conformance is attested per unit, against the unit's own target repository."""
    constraints: dict[str, object] = {}
    if target_repository is not None:
        constraints["target_repository"] = target_repository
    if conformance is MISSING:
        conformance = GREEN_CONFORMANCE
    return AuthorityEnvelope(
        capabilities={"repository_write": "allowed"},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
        constraints=constraints,
        conformance=conformance if isinstance(conformance, dict) else None,
    )


AUTHORITY = authority()
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
        "allowed_target_repositories": frozenset({PILOT_REPOSITORY}),
        "workflow_id": "factory-runner-pilot.yml",
        "workflow_ref": "main",
        "github_app_configured": True,
        "failure_signature_threshold": 3,
    }
    values.update(overrides)
    return DispatchSettings(**values)


def ready_unit(
    session: Session,
    *,
    key: str = "dispatch-unit",
    conformance: dict[str, object] | object = MISSING,
    target_repository: str | None = PILOT_REPOSITORY,
):
    enforcement_snapshot: dict[str, object] = {}
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
        authority=authority(target_repository, conformance),
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
    assert github.calls[0]["workflow_id"] == "factory-runner-pilot.yml"
    assert github.calls[0]["ref"] == "main"
    assert github.calls[0]["inputs"] == {"work_unit_id": str(unit.id)}


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


def test_dispatch_routes_to_the_units_own_target_repository(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="fanout-brain", target_repository="AlobarQuest/brain")
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=frozenset({"AlobarQuest/brain"})),
        github,
    )

    assert record.status == "dispatched"
    assert record.target_repository == "AlobarQuest/brain"
    assert github.calls[0]["repository"] == "AlobarQuest/brain"


def test_fanout_units_route_to_their_own_repositories_in_one_process(
    migrated_session: Session,
) -> None:
    """Routing must be per-unit, never process-global.

    A process-global target would silently send every unit of a fan-out to whichever
    repository was configured at startup — a runner opening a dependency PR against the
    wrong repo, which fails open rather than closed.
    """
    allowed = frozenset({"AlobarQuest/brain", "AlobarQuest/security-standards"})
    brain = ready_unit(migrated_session, key="fanout-a", target_repository="AlobarQuest/brain")
    standards = ready_unit(
        migrated_session, key="fanout-b", target_repository="AlobarQuest/security-standards"
    )
    github = FakeGitHubDispatcher([])

    first = dispatch_work_unit(
        migrated_session,
        dispatch_command(brain.id),
        settings(allowed_target_repositories=allowed),
        github,
    )
    second = dispatch_work_unit(
        migrated_session,
        dispatch_command(standards.id),
        settings(allowed_target_repositories=allowed),
        github,
    )

    assert first.target_repository == "AlobarQuest/brain"
    assert second.target_repository == "AlobarQuest/security-standards"
    assert [call["repository"] for call in github.calls] == [
        "AlobarQuest/brain",
        "AlobarQuest/security-standards",
    ]


def test_dispatch_blocks_when_unit_declares_no_target_repository(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="no-target", target_repository=None)
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)

    assert record.status == "blocked"
    assert record.reason_code == "target_repository_missing"
    assert github.calls == []


def test_dispatch_blocks_when_target_repository_is_not_allowlisted(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="off-list", target_repository="AlobarQuest/private")
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)

    assert record.status == "blocked"
    assert record.reason_code == "target_repository_not_allowed"
    assert github.calls == []


def test_dispatch_allowlist_is_empty_by_default(migrated_session: Session) -> None:
    """Fail closed: an unconfigured allowlist dispatches nowhere."""
    unit = ready_unit(migrated_session, key="empty-allowlist")
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=frozenset()),
        github,
    )

    assert record.status == "blocked"
    assert record.reason_code == "target_repository_not_allowed"
    assert github.calls == []


def test_dispatch_replay_is_idempotent_against_the_per_unit_repository(
    migrated_session: Session,
) -> None:
    """Idempotent replay must compare the resolved per-unit repo, not a global setting."""
    unit = ready_unit(migrated_session, key="replay-repo", target_repository="AlobarQuest/brain")
    allowed = frozenset({"AlobarQuest/brain"})
    github = FakeGitHubDispatcher([])

    first = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=allowed),
        github,
    )
    replay = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=allowed),
        github,
    )

    assert replay.id == first.id
    assert len(github.calls) == 1


# --- WS-6.4.0c: the dispatch credential is a minted GitHub App installation token ---


def test_dispatch_fails_closed_when_github_app_credentials_are_missing(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session)
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(github_app_configured=False),
        github,
    )

    assert record.status == "blocked"
    assert record.reason_code == "github_app_credentials_missing"
    assert github.calls == []


def test_dispatch_disabled_short_circuits_before_the_credentials_check(
    migrated_session: Session,
) -> None:
    """The kill-switch proof runs on production before any App credential exists."""
    unit = ready_unit(migrated_session)
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(enabled=False, github_app_configured=False),
        github,
    )

    assert record.status == "skipped"
    assert record.reason_code == "dispatch_disabled"
    assert github.calls == []


def test_dispatcher_sends_the_minted_installation_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(dispatch_module.httpx, "post", fake_post)
    dispatcher = GitHubActionsDispatcher(lambda: "ghs_minted")

    dispatcher.dispatch_workflow(
        repository=PILOT_REPOSITORY,
        workflow_id="factory-runner-pilot.yml",
        ref="main",
        inputs={"work_unit_id": "u1"},
    )

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer ghs_minted"


def test_a_mint_failure_is_recorded_as_a_dispatch_failure_and_never_calls_github(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = ready_unit(migrated_session)

    def unreachable(url: str, **kwargs: object) -> object:
        raise AssertionError("GitHub must not be called when the token cannot be minted")

    def explode() -> str:
        raise GitHubAppTokenError("private_key_invalid")

    monkeypatch.setattr(dispatch_module.httpx, "post", unreachable)

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(),
        GitHubActionsDispatcher(explode),
    )

    assert record.status == "failed"
    assert record.reason_code == "app_token_mint"
    assert record.failure_signature is not None
    assert record.failure_signature.startswith("workflow_dispatch:app_token_mint:")
