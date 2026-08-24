"""Which units a machine-local working copy could bind a release artifact for (ADR-0030).

THE SUBJECT IS THE TWO-PARTY CONFIRMATION, and every test here is about a way it could go wrong.
A candidate exists only when GitHub -- via the landing ledger's independent observation -- says a
pull request landed at a head, and the orchestrator's own worker-written PR binding says that head
is this unit's. Neither half alone is admitted: a landing that chose its own subject would be the
runner attesting to its own compliance, and a unit with no observed landing has no commit at all.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.machine_activation import machine_activation_candidates
from orchestrator.services.observations import ObservationCommand, record_observation
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.release_artifacts import (
    ReleaseArtifactCommand,
    record_release_artifact,
)

NOW = datetime(2026, 8, 19, 21, 34, 18, tzinfo=UTC)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
OBSERVER = ActorContext("drift-reconciler", ActorRole.OBSERVER)

REPOSITORY = "AlobarQuest/infraops-mcp-server"
HEAD_SHA = "fcc4f8811b51ea74293b79e16ddabc4250d00b41"
MERGE_COMMIT = "ac01f838fdc96e2ce3916f5a2601d3e9c232c064"
PR_NUMBER = 81
PACKAGE_HASH = "sha256:machine-activation-package"
DIGEST = "sha256:" + "7" * 64


def _authority(repository: str) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        capabilities={"repo.edit": "allowed"},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=120),
        constraints={"target_repository": repository},
    )


def landed_unit(
    session: Session,
    *,
    key: str = "infraops-ac-001",
    repository: str = REPOSITORY,
    head_sha: str = HEAD_SHA,
    pr_number: int = PR_NUMBER,
    state: WorkUnitState = WorkUnitState.COMPLETED,
) -> WorkUnit:
    """A completed unit with the orchestrator's own half of the answer: a PR binding."""
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository=repository,
        revision=1,
        content_hash=f"{PACKAGE_HASH}-{key}",
        source_path="intent.md",
        source_commit=head_sha,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["AC-001"]},
        authority=_authority(repository),
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title="Update eslint",
        outcome="The bump lands",
        required_capability="repo.edit",
        authority=_authority(repository),
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit.state = state
    session.add(UnitPrBinding(work_unit_id=unit.id, pr_number=pr_number, head_sha=head_sha))
    session.commit()
    return unit


def record_landing(
    session: Session,
    *,
    repository: str = REPOSITORY,
    subject_reference: str | None = None,
    pull_request: Any = PR_NUMBER,
    head_commit: Any = HEAD_SHA,
    commit: str = MERGE_COMMIT,
) -> None:
    """One landing, in the shape `landing_ledger/record.py::what_changed` actually writes."""
    reference = f"landing:{repository}@{commit}"
    result = record_observation(
        session,
        ObservationCommand(
            actor=OBSERVER,
            source_system="github",
            source_reference=reference,
            source_url=f"https://github.com/{repository}/commit/{commit}",
            trust_classification="delivery_system",
            subject_type="repo",
            subject_reference=subject_reference if subject_reference is not None else repository,
            environment=None,
            observation_type="landing",
            status="observed",
            severity="info",
            observed_at=NOW,
            summary=f"{commit[:12]} landed on main of {repository}",
            facts={
                "what_changed": {
                    "repository": repository,
                    "base_ref": "main",
                    "commit": commit,
                    "head_commit": head_commit,
                    "pull_request": pull_request,
                    "title": "feat: implement SDS unit",
                    "files_changed": 2,
                    "files": ["package.json", "package-lock.json"],
                },
                "permitted_by": {"basis": "human", "landed_by": "AlobarQuest"},
            },
            payload_digest=None,
            idempotency_key=f"{reference}:{uuid.uuid4()}",
        ),
    )
    assert not isinstance(result, Exception), result


def test_a_confirmed_landing_makes_a_completed_unit_a_candidate(
    migrated_session: Session,
) -> None:
    unit = landed_unit(migrated_session)
    record_landing(migrated_session)

    candidates = machine_activation_candidates(migrated_session, REPOSITORY)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.work_unit_id == unit.id
    assert candidate.source_repository == REPOSITORY
    assert candidate.pr_number == PR_NUMBER
    assert candidate.source_commit == HEAD_SHA
    assert candidate.merge_commit == MERGE_COMMIT
    assert candidate.package_revision_hash == f"{PACKAGE_HASH}-infraops-ac-001"
    assert candidate.binding_id is None


def test_a_unit_whose_landing_nobody_observed_is_not_a_candidate(
    migrated_session: Session,
) -> None:
    """The ordinary state between a merge and the ledger's next pass. Fail closed, not a finding."""
    landed_unit(migrated_session)

    assert machine_activation_candidates(migrated_session, REPOSITORY) == ()


def test_a_landing_at_a_head_this_unit_does_not_name_is_not_a_candidate(
    migrated_session: Session,
) -> None:
    """THE CONFIRMATION, and the reason both parties are required.

    A pull request number alone would bind any unit that happened to share it. The head is what
    makes the landing THIS unit's, and it is the value the orchestrator's own worker-written
    binding holds rather than anything the landing asserted about itself.
    """
    landed_unit(migrated_session)
    record_landing(migrated_session, head_commit="0" * 40)

    assert machine_activation_candidates(migrated_session, REPOSITORY) == ()


def test_a_landing_of_a_different_pull_request_is_not_a_candidate(
    migrated_session: Session,
) -> None:
    landed_unit(migrated_session)
    record_landing(migrated_session, pull_request=82)

    assert machine_activation_candidates(migrated_session, REPOSITORY) == ()


def test_a_unit_that_has_not_completed_is_not_a_candidate(migrated_session: Session) -> None:
    landed_unit(migrated_session, state=WorkUnitState.SUBMITTED)
    record_landing(migrated_session)

    assert machine_activation_candidates(migrated_session, REPOSITORY) == ()


def test_a_unit_targeting_another_repository_is_not_a_candidate(
    migrated_session: Session,
) -> None:
    landed_unit(migrated_session, key="other-repo-unit", repository="AlobarQuest/brain")
    record_landing(migrated_session, repository="AlobarQuest/brain")

    assert machine_activation_candidates(migrated_session, REPOSITORY) == ()
    assert len(machine_activation_candidates(migrated_session, "AlobarQuest/brain")) == 1


def test_a_units_target_repository_is_checked_even_when_a_landing_is_present(
    migrated_session: Session,
) -> None:
    """The unit filter, isolated from the observation filter.

    The test above passes with the unit's target repository ignored entirely, because no landing
    exists for the queried repository and the pass returns before the unit loop is reached -- the
    guard measured by accident rather than on purpose. Here the landing IS present and the two
    units differ only in the repository their authority names, so the target check is the sole
    thing that can exclude one of them. Found by mutation: `if target != wanted: continue`
    survived removal until this existed.
    """
    landed_unit(migrated_session, key="right-repo-unit")
    landed_unit(migrated_session, key="wrong-repo-unit", repository="AlobarQuest/brain")
    record_landing(migrated_session)

    candidates = machine_activation_candidates(migrated_session, REPOSITORY)

    assert [candidate.unit_key for candidate in candidates] == ["right-repo-unit"]


def test_a_landing_row_naming_another_repository_in_its_facts_is_skipped(
    migrated_session: Session,
) -> None:
    """The row's SUBJECT and the row's FACTS are two separate writes and can disagree.

    The query selects on `subject_reference`; the facts carry `repository` as well, and reading
    the commit out of a row whose facts describe a different repository would attribute a landing
    that never happened here. Found by mutation.
    """
    landed_unit(migrated_session)
    record_landing(migrated_session, repository="AlobarQuest/brain", subject_reference=REPOSITORY)

    assert machine_activation_candidates(migrated_session, REPOSITORY) == ()


def test_the_repository_match_is_case_folded_on_both_sides(migrated_session: Session) -> None:
    """Production holds both spellings, so an exact comparison would refuse a real candidate.

    The three sides that must agree are spelled differently on purpose here: the authority
    envelope's casing, the ledger observation's casing, and the caller's.
    """
    landed_unit(migrated_session, repository=REPOSITORY)
    record_landing(migrated_session, subject_reference=REPOSITORY.lower())

    assert len(machine_activation_candidates(migrated_session, REPOSITORY.upper())) == 1


def test_an_existing_machine_local_binding_is_reported_rather_than_hidden(
    migrated_session: Session,
) -> None:
    """Reported so the producer can say it skipped a unit, and skip rather than rewrite it."""
    unit = landed_unit(migrated_session)
    record_landing(migrated_session)
    binding = record_release_artifact(migrated_session, _machine_local_command(unit))
    assert isinstance(binding, ReleaseArtifactBinding)

    candidates = machine_activation_candidates(migrated_session, REPOSITORY)

    assert len(candidates) == 1
    assert candidates[0].binding_id == binding.id


def test_a_container_image_binding_does_not_stand_in_for_a_machine_local_one(
    migrated_session: Session,
) -> None:
    """KIND-SCOPED, and this is what the scoping is for.

    A unit can legitimately reach both a registry image and a working copy. Keying the skip on
    "has any binding" would let a container image suppress the machine-local row -- the two models
    collapsing into each other through a predicate rather than through a column.
    """
    unit = landed_unit(migrated_session)
    record_landing(migrated_session)
    record_release_artifact(
        migrated_session,
        replace(
            _machine_local_command(unit),
            kind="container_image",
            artifact_registry="ghcr.io",
            artifact_repository="alobarquest/infraops-mcp-server",
            artifact_name="infraops-mcp-server",
            idempotency_key="container-binding-1",
        ),
    )

    candidates = machine_activation_candidates(migrated_session, REPOSITORY)

    assert len(candidates) == 1
    assert candidates[0].binding_id is None


def test_a_push_with_no_pull_request_is_skipped_rather_than_raised_on(
    migrated_session: Session,
) -> None:
    """The majority of the ledger's rows. An incomplete row is ordinary, not a fault."""
    landed_unit(migrated_session)
    record_landing(migrated_session, pull_request=None, head_commit=None, commit="b" * 40)
    record_landing(migrated_session)

    assert len(machine_activation_candidates(migrated_session, REPOSITORY)) == 1


def test_an_empty_repository_answers_with_no_candidates(migrated_session: Session) -> None:
    assert machine_activation_candidates(migrated_session, "   ") == ()
    assert machine_activation_candidates(migrated_session, "AlobarQuest/nothing") == ()


def _machine_local_command(unit: WorkUnit) -> ReleaseArtifactCommand:
    return ReleaseArtifactCommand(
        work_unit_id=unit.id,
        actor=SYSTEM,
        package_revision_id=unit.work_package_revision_id,
        package_revision_hash=f"{PACKAGE_HASH}-{unit.unit_key}",
        source_repository=REPOSITORY,
        implementation_pr_number=PR_NUMBER,
        source_commit=HEAD_SHA,
        merge_commit=MERGE_COMMIT,
        artifact_registry=None,
        artifact_repository=None,
        artifact_name=None,
        artifact_digest=DIGEST,
        artifact_tag=None,
        workflow_run_id=None,
        workflow_run_attempt=None,
        workflow_path=None,
        workflow_ref=None,
        workflow_run_url=None,
        builder_id=None,
        builder_class=None,
        provenance_ref=None,
        provenance_digest=None,
        sbom_ref=None,
        sbom_digest=None,
        summary={"activation": {"path": "/Users/x/Projects/infraops-mcp-server"}},
        idempotency_key=f"machine-activation:{unit.id}",
        kind="machine_local",
    )
