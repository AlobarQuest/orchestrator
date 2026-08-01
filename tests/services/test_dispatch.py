import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import orchestrator.services.dispatch as dispatch_module
from orchestrator.factory_policy import load_factory_policy
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Approval, DispatchRecord, Event, WorkUnit
from orchestrator.services.dispatch import (
    DispatchCommand,
    DispatchSettings,
    GitHubActionsDispatcher,
    GitHubDispatchError,
    circuit_open,
    dispatch_work_unit,
    failure_signature,
    signature_failure_count,
)
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)
from tests.services.test_authority_known_good import uv_bump

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
        capabilities={"repo.edit": "allowed"},
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
        "allowed_change_classes": frozenset({"repo.edit"}),
        "enabled_capabilities": frozenset({"repo.edit"}),
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
    enforcement_snapshot: dict[str, object] | None = None,
):
    # Revisions are append-only at the database level, so a test that needs a different
    # enforcement snapshot must register it, not mutate it afterwards.
    enforcement_snapshot = {} if enforcement_snapshot is None else enforcement_snapshot
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
        required_capability="repo.edit",
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


def test_the_off_switch_outranks_the_most_permissive_policy_expressible(
    migrated_session: Session,
) -> None:
    """WS-P2.18 R4, end to end: the artifact objects to nothing, and nothing is admitted anyway.

    Both halves matter. Without the first the claim is vacuous -- an artifact that refused
    everything would satisfy "nothing is admitted" while proving nothing about precedence. The
    shipped artifact IS the most permissive one expressible: there is no permission in its schema,
    so raising no objection for every reach is as far as it can go.
    """
    policy = load_factory_policy()
    for member in sorted(policy.rows):
        assert policy.refusals_for((member,)) == ()
    assert policy.refusals_for(tuple(sorted(policy.rows))) == ()

    unit = ready_unit(migrated_session, key="policy-cannot-outrank-the-switch")
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


def test_dispatch_skips_legacy_invalid_dependency_update_authority(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="legacy-invalid-authority")
    unit.authority = {
        "capabilities": {
            "repository_write": "allowed",
            "repo.edit": "allowed",
            "command.run": "allowed",
        },
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": "dependency-update",
        "constraints": {
            "target_repository": PILOT_REPOSITORY,
            "allowed_commands": ["uv sync --locked"],
        },
        "conformance": GREEN_CONFORMANCE,
    }
    migrated_session.flush()
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_change_classes=frozenset({"dependency-update"})),
        github,
    )

    assert record.status == "skipped"
    assert record.reason_code == "authority_mutation_commands_invalid"
    assert github.calls == []


def test_dispatch_uses_one_normalized_authority_snapshot(
    migrated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = ready_unit(migrated_session, key="one-authority-snapshot")
    github = FakeGitHubDispatcher([])
    original = dispatch_module.normalize_authority
    calls: list[Mapping[str, Any]] = []

    def track_normalization(value: Mapping[str, Any]) -> AuthorityEnvelope:
        calls.append(value)
        return original(value)

    monkeypatch.setattr(dispatch_module, "normalize_authority", track_normalization)

    record = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)

    assert record.status == "dispatched"
    assert len(calls) == 1


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


def test_circuit_open_is_a_pure_at_rest_predicate() -> None:
    assert circuit_open(2, 3) is False
    assert circuit_open(3, 3) is True
    assert circuit_open(4, 3) is True


def test_prospective_and_at_rest_predicates_differ_by_exactly_one_failure(
    migrated_session: Session,
) -> None:
    """Dispatch counts the failure it is ABOUT to write; a read-only view counts what is already
    on disk. Reusing dispatch's call site at rest would show a breaker open one failure early --
    which is why the `+ 1` lives at the dispatch call site and never inside `circuit_open`."""
    unit = ready_unit(migrated_session, key="offbyone")
    signature = failure_signature("workflow_dispatch", "workflow_not_found", "404")
    for attempt in (1, 2):
        migrated_session.add(
            DispatchRecord(
                work_unit_id=unit.id,
                work_package_revision_id=unit.work_package_revision_id,
                runner_attempt=attempt,
                status="failed",
                reason_code="workflow_not_found",
                idempotency_key=f"dispatch-offbyone-{attempt}",
                target_repository=PILOT_REPOSITORY,
                workflow_id="factory-runner-pilot.yml",
                workflow_ref="main",
                failure_signature=signature,
                payload={},
            )
        )
    migrated_session.commit()

    count = signature_failure_count(migrated_session, unit.id, signature)

    assert count == 2
    assert circuit_open(count + 1, 3) is True  # prospective: the next failure opens it
    assert circuit_open(count, 3) is False  # at rest: not yet open


def test_signature_failure_count_is_scoped_to_the_unit_and_signature(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="scoped")
    other = ready_unit(migrated_session, key="scoped-other")
    signature = failure_signature("workflow_dispatch", "workflow_not_found", "404")
    other_signature = failure_signature("workflow_dispatch", "forbidden", "403")
    rows = (
        (unit, 1, "failed", signature),
        (unit, 2, "blocked", signature),
        (unit, 3, "failed", other_signature),  # different signature
        (unit, 4, "dispatched", signature),  # not a failure status
        (other, 1, "failed", signature),  # different unit
    )
    for index, (target, attempt, status, sig) in enumerate(rows):
        migrated_session.add(
            DispatchRecord(
                work_unit_id=target.id,
                work_package_revision_id=target.work_package_revision_id,
                runner_attempt=attempt,
                status=status,
                idempotency_key=f"dispatch-scoped-{index}",
                target_repository=PILOT_REPOSITORY,
                workflow_id="factory-runner-pilot.yml",
                workflow_ref="main",
                failure_signature=sig,
                payload={},
            )
        )
    migrated_session.commit()

    assert signature_failure_count(migrated_session, unit.id, signature) == 2


# ---------------------------------------------------------------------------------------------
# WS-P2.18 Increment 3: the human-authority requirement is conditional on policy (ADR-0011)
# ---------------------------------------------------------------------------------------------


def recognised_unit(
    session: Session,
    *,
    key: str,
    reach: list[str] | None = None,
    **constraints: Any,
):
    """A READY unit that NOBODY approved, carrying the envelope the uv profile emits today.

    Deliberately skips `record_approval`: the whole question below is what happens to a unit with
    no human approval bound to it, and a helper that quietly recorded one would answer it wrongly
    in the direction that looks like success.
    """
    unit_id = uuid.uuid4()
    payload = uv_bump(unit_id, **constraints)
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
        enforcement_snapshot={} if reach is None else {"reach": reach},
        authority=AUTHORITY,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title="Bump a pin",
        outcome="Runner opens a PR",
        required_capability="repo.edit",
        authority=normalize_authority(payload),
        authority_payload=payload,
        unit_id=unit_id,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
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


def recognising_settings(**overrides: object) -> DispatchSettings:
    return settings(
        allowed_change_classes=frozenset({"dependency-update"}),
        allowed_target_repositories=frozenset({"AlobarQuest/change-manager"}),
        **overrides,
    )


def gate_events(session: Session, unit_id: uuid.UUID) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(
                Event.subject_id == unit_id,
                Event.action == "authority.human_gate_not_required",
            )
        )
    )


def test_a_unit_nobody_approved_is_admitted_when_policy_recognises_its_envelope(
    migrated_session: Session,
) -> None:
    unit = recognised_unit(migrated_session, key="recognised", reach=["source_repository"])
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), recognising_settings(), github
    )

    assert unit.authority_approval_id is None
    assert (record.status, record.reason_code) == ("dispatched", None)
    assert len(github.calls) == 1


def test_the_same_unit_is_refused_when_no_pattern_recognises_its_envelope(
    migrated_session: Session,
) -> None:
    """The control for the test above, differing in ONE field of the envelope.

    `uv sync --locked` is a command the shipped pattern does not declare -- and it is the command
    the one envelope this factory has actually dispatched carried, so this is the historical shape
    being flagged rather than an invented one.
    """
    unit = recognised_unit(
        migrated_session,
        key="novel",
        reach=["source_repository"],
        allowed_commands=["uv add --dev 'ruff>=0.15.21'", "uv sync --locked"],
        mutation_commands=["uv add --dev 'ruff>=0.15.21'"],
    )
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), recognising_settings(), github
    )

    assert (record.status, record.reason_code) == ("blocked", "authority_approval_missing")
    assert github.calls == []


def test_a_unit_whose_package_declared_no_reach_still_needs_a_human(
    migrated_session: Session,
) -> None:
    # The population as it stands: no authored package declares reach, so this is what every unit
    # gets today. The failure mode is benign -- it is exactly the behaviour that existed before.
    unit = recognised_unit(migrated_session, key="no-reach", reach=None)
    github = FakeGitHubDispatcher([])

    record = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), recognising_settings(), github
    )

    assert (record.status, record.reason_code) == ("blocked", "authority_approval_missing")
    assert gate_events(migrated_session, unit.id) == []


def test_a_lifted_gate_never_writes_an_approval_row(migrated_session: Session) -> None:
    """§3.1, and the one constraint here that cannot be walked back later.

    There is no standing human credential (ADR-0006) and the graduation ledger reasons over this
    evidence, so a machine-written "human" approval would corrupt the record this whole ladder is
    supposed to climb. Asserted directly on the table, not inferred from behaviour.
    """
    unit = recognised_unit(migrated_session, key="no-approval-row", reach=["source_repository"])

    dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        recognising_settings(),
        FakeGitHubDispatcher([]),
    )
    migrated_session.commit()

    with Session(migrated_session.get_bind()) as reader:
        approvals = list(reader.scalars(select(Approval).where(Approval.subject_id == unit.id)))
        assert approvals == []
        stored = reader.get(WorkUnit, unit.id)
        assert stored is not None
        assert stored.authority_approval_id is None


def test_a_lifted_gate_leaves_a_record_that_is_not_an_approval(
    migrated_session: Session,
) -> None:
    """§3.2. Increment 6 reads this, and it must never be confusable with a person's decision."""
    unit = recognised_unit(migrated_session, key="suppression-record", reach=["source_repository"])

    dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        recognising_settings(),
        FakeGitHubDispatcher([]),
    )
    migrated_session.commit()

    with Session(migrated_session.get_bind()) as reader:
        events = gate_events(reader, unit.id)
        assert len(events) == 1
        payload = events[0].payload
        assert payload["recognised_by"] == ["uv dependency pin bump into a named repository"]
        assert payload["policy_version"] == load_factory_policy().version
        assert payload["policy_source"] == "factory-policy.toml"
        stored = reader.get(WorkUnit, unit.id)
        assert stored is not None
        assert payload["authority_fingerprint"] == stored.authority_fingerprint
        # Attributed to the system actor that read the artifact, and saying the requirement did
        # not apply -- never that somebody agreed to anything.
        assert events[0].actor_id == SYSTEM.actor_id
        assert "approv" not in events[0].action


def test_a_unit_a_human_did_approve_records_no_suppression(migrated_session: Session) -> None:
    # The record cites the patterns where they were USED. A unit carrying an approval passed the
    # term on that approval, so citing policy for it would record a suppression that never
    # happened -- and Increment 6 would count it.
    unit = ready_unit(migrated_session, key="human-approved")

    dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), settings(), FakeGitHubDispatcher([])
    )

    assert unit.authority_approval_id is not None
    assert gate_events(migrated_session, unit.id) == []


def test_the_off_switch_outranks_a_recognising_pattern(migrated_session: Session) -> None:
    """R4, on the path Increment 3 opened. Both halves, so the claim is not vacuous.

    The first half is that policy DOES recognise this envelope -- proven by dispatching the same
    unit with the switch on. Without it, "nothing was admitted" would be satisfied by a unit no
    pattern recognised in the first place.
    """
    unit = recognised_unit(migrated_session, key="switch-outranks", reach=["source_repository"])
    github = FakeGitHubDispatcher([])

    blocked = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=1),
        recognising_settings(enabled=False),
        github,
    )
    admitted = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=2),
        recognising_settings(enabled=True),
        github,
    )

    assert (blocked.status, blocked.reason_code) == ("skipped", "dispatch_disabled")
    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert len(github.calls) == 1
    # The switch is the FIRST term, so the authority term was never reached and nothing claims it
    # was: one dispatch, one suppression record.
    assert len(gate_events(migrated_session, unit.id)) == 1
