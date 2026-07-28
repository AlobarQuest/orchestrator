import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

import orchestrator.package_sources as package_sources
import orchestrator.services.package_intake as package_intake
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.package_sources import (
    PackageSourceError,
    VerifiedApproval,
    load_package_intake_payload,
    load_protocol_fixture_intake_payload,
)
from orchestrator.persistence.models import Event
from orchestrator.services.decomposition import (
    DecompositionProposalCommand,
    ProposedUnit,
    submit_decomposition_proposal,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import PackageIntakeCommand, register_package_intake
from orchestrator.services.packages import register_approved_unit, register_revision
from tests.services.test_package_intake import AUTHORITY, acceptance_criterion, human_actor


def _verified_approval() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def closed_fixture(tmp_path: Path) -> Path:
    package_dir = tmp_path / "ws32-closed-software"
    shutil.copytree("tests/fixtures/intent-packages/ws32-approved-software", package_dir)
    package_path = package_dir / "package.yaml"
    lineage_path = package_dir / "lineage.yaml"
    package_path.write_text(
        package_path.read_text(encoding="utf-8").replace("status: approved", "status: closed"),
        encoding="utf-8",
    )
    lineage_path.write_text(
        lineage_path.read_text(encoding="utf-8").replace(
            "current_state: approved",
            "current_state: closed",
        ),
        encoding="utf-8",
    )
    return package_dir


def command_from_payload(payload: dict[str, object]) -> PackageIntakeCommand:
    approved_at = payload["approved_at"]
    assert isinstance(approved_at, str)
    approval_event_id = payload["approval_event_id"]
    assert isinstance(approval_event_id, str)
    revision = payload["revision"]
    assert isinstance(revision, int)
    registry_version = payload["registry_version"]
    assert isinstance(registry_version, int)
    verification_limitations = payload["verification_limitations"]
    assert isinstance(verification_limitations, dict)
    enforcement_snapshot = payload["enforcement_snapshot"]
    assert isinstance(enforcement_snapshot, dict)
    return PackageIntakeCommand(
        package_id=str(payload["package_id"]),
        source_repository=str(payload["source_repository"]),
        revision=revision,
        content_hash=str(payload["content_hash"]),
        source_path=str(payload["source_path"]),
        source_commit=str(payload["source_commit"]),
        approved_by=str(payload["approved_by"]),
        approved_at=datetime.fromisoformat(approved_at.replace("Z", "+00:00")),
        approval_event_id=approval_event_id,
        approval_ledger_commit=str(payload["approval_ledger_commit"]),
        profile=payload["profile"] if isinstance(payload["profile"], str) else None,
        status_at_intake=str(payload["status_at_intake"]),
        verification_mode=str(payload["verification_mode"]),
        verification_limitations=verification_limitations,
        enforcement_snapshot=enforcement_snapshot,
        authority=AUTHORITY,
        registry_version=registry_version,
        acceptance_criteria=(acceptance_criterion(),),
        idempotency_key="protocol-fixture-intake",
        expected_version=0,
        intake_purpose=str(payload["intake_purpose"]),
    )


def test_executable_loader_rejects_closed_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )

    with pytest.raises(PackageSourceError, match="not approved for intake"):
        load_package_intake_payload(
            closed_fixture(tmp_path),
            source_repository="AlobarQuest/intent-packages",
        )


def test_protocol_fixture_loader_accepts_chain_verified_closed_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")

    payload = load_protocol_fixture_intake_payload(
        closed_fixture(tmp_path),
        source_repository="AlobarQuest/intent-packages",
    )

    assert payload["status_at_intake"] == "closed"
    assert payload["intake_purpose"] == "protocol_fixture"
    limitations = payload["verification_limitations"]
    assert isinstance(limitations, dict)
    assert limitations["protocol_fixture_only"] is True


def test_protocol_fixture_registration_stores_fixture_source(
    migrated_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    payload = load_protocol_fixture_intake_payload(
        closed_fixture(tmp_path),
        source_repository="AlobarQuest/intent-packages",
    )

    revision = register_package_intake(
        migrated_session,
        command_from_payload(payload),
        human_actor(),
    )

    assert revision.intake_source == "protocol_fixture"
    assert revision.status_at_intake == "closed"


def test_a_protocol_fixture_pre_wsp28_intake_event_still_replays(
    migrated_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The follow_up exemption is not executable-lane-only (Important 1, task-2 review): a
    protocol_fixture intake event written before follow_up existed must also replay, not raise
    idempotency_conflict. `_legacy_identity_matches` popped `intake_purpose` unconditionally
    inside its (now-removed) executable-only guard, which meant the follow_up pop -- gated only
    by `command.follow_up is None` -- never even ran for a protocol_fixture command.
    """
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    payload = load_protocol_fixture_intake_payload(
        closed_fixture(tmp_path),
        source_repository="AlobarQuest/intent-packages",
    )
    command = command_from_payload(payload)
    actor = human_actor()
    revision = register_revision(
        migrated_session,
        package_id=command.package_id,
        source_repository=command.source_repository,
        revision=command.revision,
        content_hash=command.content_hash,
        source_path=command.source_path,
        source_commit=command.source_commit,
        approved_by=command.approved_by,
        approved_at=command.approved_at,
        approval_event_id=command.approval_event_id,
        enforcement_snapshot={
            **command.enforcement_snapshot,
            "acceptance_criteria": ["AC-001"],
        },
        authority=command.authority,
        registry_version=command.registry_version,
        profile=command.profile,
        status_at_intake=command.status_at_intake,
        intake_source="protocol_fixture",
        approval_ledger_commit=command.approval_ledger_commit,
        verification_mode=command.verification_mode,
        verification_limitations=command.verification_limitations,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        expected_version=command.expected_version,
    )
    legacy_identity = package_intake._command_identity(command, actor)
    legacy_identity.pop("follow_up")
    migrated_session.add(
        Event(
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            payload={"command": legacy_identity},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.flush()

    replayed = register_package_intake(migrated_session, command, actor)

    assert replayed.id == revision.id


def test_protocol_fixture_revision_cannot_submit_decomposition(
    migrated_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    revision = register_package_intake(
        migrated_session,
        command_from_payload(
            load_protocol_fixture_intake_payload(
                closed_fixture(tmp_path),
                source_repository="AlobarQuest/intent-packages",
            )
        ),
        human_actor(),
    )

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            DecompositionProposalCommand(
                work_package_revision_id=revision.id,
                rationale="Fixture is not executable.",
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Fixture unit",
                        outcome="Fixture only",
                        required_capability="repo.edit",
                        authority=AUTHORITY,
                    ),
                ),
                dependencies=(),
                ac_mappings=(),
                retained_acs=(),
                idempotency_key="fixture-proposal",
            ),
            ActorContext("worker-1", ActorRole.WORKER),
        )

    assert error.value.code == "revision_not_intaken"


def test_protocol_fixture_revision_cannot_create_executable_unit(
    migrated_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_sources,
        "_verify_current_approval",
        lambda *args: _verified_approval(),
    )
    monkeypatch.setattr(package_sources, "_git_head", lambda path: "deadbeef")
    revision = register_package_intake(
        migrated_session,
        command_from_payload(
            load_protocol_fixture_intake_payload(
                closed_fixture(tmp_path),
                source_repository="AlobarQuest/intent-packages",
            )
        ),
        human_actor(),
    )

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-1",
            title="Fixture unit",
            outcome="Fixture only",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=datetime.fromisoformat("2026-07-05T00:02:00+00:00"),
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "protocol_fixture_not_executable"


def test_executable_intake_status_allowlist_remains_approved_only() -> None:
    assert package_intake._VALID_STATUSES == frozenset({"approved"})
