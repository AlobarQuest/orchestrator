"""The WS-4.1 <-> WS-4.2 seam contract.

WS-4.2 (this repo's dispatch adapter) and WS-4.1 (`AlobarQuest/factory-runner`) were each
built and unit-tested against their *own* fixtures, and those fixtures disagreed: the
orchestrator's admission gate wanted `required_capability="repository_write"` in the
envelope, while the runner's `validate_authority` hard-rejects any capability outside its
six-term vocabulary. Nothing ever validated one envelope against both ends, so the seam
had never executed.

This module pins the shared envelope. `tests/fixtures/runner_authority_envelope.json` is
the single source of truth for its shape, and `factory-runner` keeps a byte-identical copy
under the same name, asserted there against `validate_authority`. The two copies must be
changed together; `CONTRACT_SHA256` makes an accidental one-sided edit loud.

WS-P2.33 added a second pinned fixture, `runner_authority_envelope_edit.json`: the
edit-shaped envelope, where the coding agent produces the diff and no command mutates a
tracked file, so `mutation_commands` is honestly absent. The byte pin alone cannot catch
the two sides disagreeing about a RULE (both fixtures stayed green while production
disagreed), so both repos also assert the rule against the fixtures: this envelope is
admitted, and the dependency-update envelope with `mutation_commands` stripped is refused.
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.runner_authority import runner_command_authority_violation
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.services.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedUnit,
    RetainedAc,
    approve_decomposition_proposal,
    submit_decomposition_proposal,
)
from orchestrator.services.dispatch import (
    DispatchCommand,
    DispatchSettings,
    dispatch_work_unit,
)
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.package_intake import register_package_intake
from orchestrator.services.packages import record_approval
from orchestrator.services.runner_brief import runner_brief
from tests.services.estate_doubles import inert_source
from tests.services.test_decomposition import package_ac_ids
from tests.services.test_package_intake import acceptance_criterion, human_actor, intake_command

# The orchestrator's shipped capability vocabulary is the single orchestrator-side source of
# truth for the runner six. It must be DERIVED from this byte-pinned envelope, not a second
# hand-maintained copy -- `test_capability_vocabulary_is_derived_from_the_golden_envelope`
# asserts exactly that, so a divergence (here or in the module) is loud. factory-runner mirrors
# the same six in its own `capability_vocabulary` and raises AuthorityError on anything outside
# them, so an orchestrator envelope that strays can never be executed.
RUNNER_SUPPORTED_CAPABILITIES = RUNNER_CAPABILITIES
RUNNER_SUPPORTED_LEVELS = frozenset({"allowed", "prohibited"})

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runner_authority_envelope.json"
CONTRACT_SHA256 = "049ab53e2b257fa3d7eb24748a4278ffc7e0e91f8174b05220eefd7d526e5a56"

FIXTURE_EDIT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "runner_authority_envelope_edit.json"
)
CONTRACT_SHA256_EDIT = "90b73de69bdd9d5ee88be38b0a0ac2eeff1e4bb467ec72062cd1b70f49888f6e"

TARGET_REPOSITORY = "AlobarQuest/change-manager"
CHANGE_CLASS = "dependency-update"
EDIT_TARGET_REPOSITORY = "AlobarQuest/intent-packages"
EDIT_CHANGE_CLASS = "maintenance-remediation"
CAPABILITY = "repo.edit"
SYSTEM = ActorContext("system", ActorRole.SYSTEM)


class FakeGitHubDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def dispatch_workflow(self, **kwargs: object) -> dict[str, str | None]:
        self.calls.append(kwargs)
        return {"workflow_run_id": None, "workflow_run_url": None}


def golden_envelope() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def golden_edit_envelope() -> dict[str, Any]:
    return json.loads(FIXTURE_EDIT.read_text())


def _authored(envelope: dict[str, Any]) -> dict[str, Any]:
    """A golden envelope minus the server-owned stamp, i.e. what a human authors."""
    constraints = dict(envelope["constraints"])
    del constraints["work_unit_id"]
    return {**envelope, "constraints": constraints}


def _dispatch_settings(
    change_class: str = CHANGE_CLASS, target_repository: str = TARGET_REPOSITORY
) -> DispatchSettings:
    return DispatchSettings(
        enabled=True,
        allowed_change_classes=frozenset({change_class}),
        enabled_capabilities=frozenset({CAPABILITY}),
        allowed_target_repositories=frozenset({target_repository}),
        workflow_id="factory-runner-pilot.yml",
        workflow_ref="main",
        github_app_configured=True,
    )


def _proposed_unit(payload: dict[str, Any], unit_key: str) -> ProposedUnit:
    return ProposedUnit(
        unit_key=unit_key,
        title="Apply the authorized change",
        outcome="Change applied and checks green.",
        required_capability=CAPABILITY,
        authority=normalize_authority(payload),
        authority_payload=payload,
    )


def _approved_ready_unit(
    session: Session,
    *,
    envelope: dict[str, Any] | None = None,
    change_class: str = CHANGE_CLASS,
    prefix: str = "fanout",
    unit_key: str = "bump-dependency",
):
    payload = _authored(envelope if envelope is not None else golden_envelope())
    revision = register_package_intake(
        session,
        intake_command(
            package_id=f"pkg-{prefix}",
            idempotency_key=f"intake-{prefix}",
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
            # No conformance here: it is attested per unit against that unit's own target
            # repository, not once per package revision at intake.
            enforcement_snapshot={
                "title": "Fan out an authorized change",
                "reach": ["source_repository"],
                "outcome": "Every target repo gets a PR",
                "scope": {"in": [change_class]},
                "dependencies": [],
                "applicable_standards": {"project": "1.0"},
            },
        ),
        human_actor(),
    )
    ac_ids = package_ac_ids(session, revision.id)
    proposal = submit_decomposition_proposal(
        session,
        DecompositionProposalCommand(
            work_package_revision_id=revision.id,
            rationale="One unit per target repository.",
            proposed_units=(_proposed_unit(payload, unit_key),),
            dependencies=(),
            ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key=unit_key),),
            retained_acs=(
                RetainedAc(ac_id=str(ac_ids["AC-002"]), rationale="Package-level gate."),
            ),
            idempotency_key=f"proposal-{prefix}",
        ),
        human_actor(),
    )
    approve_decomposition_proposal(
        session,
        proposal.id,
        actor=human_actor(),
        reason="Approve the fan-out.",
        idempotency_key=f"proposal-{prefix}-approve",
    )
    unit_id = uuid.UUID(str(uuid.uuid5(proposal.id, unit_key)))
    record_approval(
        session,
        unit_id=unit_id,
        subject_type="authority",
        actor_id=human_actor().actor_id,
        actor_role=ActorRole.HUMAN,
        reason="Authority approved for this repository.",
        idempotency_key=f"{prefix}-authority",
        expected_version=1,
    )
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit_id,
            target=WorkUnitState.READY,
            actor=SYSTEM,
            expected_version=1,
            idempotency_key=f"{prefix}-ready",
        ),
    )
    return unit_id


def test_golden_envelope_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_envelope(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256


def test_golden_edit_envelope_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_edit_envelope(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256_EDIT


def test_capability_vocabulary_is_derived_from_the_golden_envelope() -> None:
    """The shipped runner vocabulary IS the envelope's capability set -- not a second copy.

    A hash pin proves the fixture file is unchanged; it says nothing about whether production
    code consumes it. This asserts the derivation instead: the module orchestrator ingress reads
    equals the capability keys of the byte-pinned cross-repo envelope. Hardcoding the module's
    runner set and adding a term reds this (the WS-P2.16 negative control), where flipping a
    fixture byte would only red the hash test -- which proves nothing about use.
    """
    assert RUNNER_CAPABILITIES == frozenset(golden_envelope()["capabilities"])


def test_golden_envelope_satisfies_the_runner_vocabulary() -> None:
    envelope = golden_envelope()

    assert set(envelope["capabilities"]) <= RUNNER_SUPPORTED_CAPABILITIES
    assert set(envelope["capabilities"].values()) <= RUNNER_SUPPORTED_LEVELS
    # validate_authority reads exactly these four constraint keys.
    assert set(envelope["constraints"]) == {
        "work_unit_id",
        "target_repository",
        "allowed_commands",
        "mutation_commands",
    }
    # command.run is allowed, so both lists must be non-empty lists of strings.
    assert envelope["constraints"]["allowed_commands"]
    assert all(isinstance(item, str) for item in envelope["constraints"]["allowed_commands"])
    assert envelope["constraints"]["mutation_commands"]
    assert all(isinstance(item, str) for item in envelope["constraints"]["mutation_commands"])


def test_edit_envelope_satisfies_the_runner_vocabulary() -> None:
    """The edit shape carries THREE constraint keys: no command mutates, so
    `mutation_commands` is honestly absent rather than present-and-empty."""
    envelope = golden_edit_envelope()

    assert frozenset(envelope["capabilities"]) == RUNNER_CAPABILITIES
    assert set(envelope["capabilities"].values()) <= RUNNER_SUPPORTED_LEVELS
    assert envelope["change_class"] == EDIT_CHANGE_CLASS
    assert set(envelope["constraints"]) == {
        "work_unit_id",
        "target_repository",
        "allowed_commands",
    }
    assert envelope["constraints"]["allowed_commands"]
    assert all(isinstance(item, str) for item in envelope["constraints"]["allowed_commands"])


def test_edit_envelope_is_admitted() -> None:
    """The rule the fixtures pin, positive direction: edit-shaped work is expressible."""
    assert runner_command_authority_violation(normalize_authority(golden_edit_envelope())) is None


def test_mutation_commands_guard_fires_on_dependency_update_without_the_field() -> None:
    """The rule the fixtures pin, negative direction: the conditional requirement
    did not delete the dependency-update guard."""
    payload = golden_envelope()
    del payload["constraints"]["mutation_commands"]

    violation = runner_command_authority_violation(normalize_authority(payload))

    assert violation is not None
    assert violation.code == "authority_mutation_commands_invalid"


def test_allowed_commands_is_required_for_every_change_class() -> None:
    """command.run authority without a command allowlist dies at the runner —
    the same defect shape one field over, refused here instead."""
    payload = golden_edit_envelope()
    del payload["constraints"]["allowed_commands"]

    violation = runner_command_authority_violation(normalize_authority(payload))

    assert violation is not None
    assert violation.code == "authority_allowed_commands_invalid"


def test_present_mutation_commands_is_validated_for_every_change_class() -> None:
    """A present key is never ignored: subset-of-allowed_commands holds whatever
    the change class, so the runner can never refuse what admission ignored."""
    payload = golden_edit_envelope()
    payload["constraints"]["mutation_commands"] = ["a command nobody allowed"]

    violation = runner_command_authority_violation(normalize_authority(payload))

    assert violation is not None
    assert violation.code == "authority_mutation_command_not_allowed"


def test_orchestrator_serves_the_golden_envelope_and_admits_it(migrated_session: Session) -> None:
    """The envelope the runner receives is the one the orchestrator admits.

    This is the assertion that never existed: one envelope, both ends.
    """
    unit_id = _approved_ready_unit(migrated_session)

    brief = runner_brief(migrated_session, unit_id)
    served = cast(dict[str, Any], cast(dict[str, Any], brief["authority"])["envelope"])

    expected = golden_envelope()
    expected["constraints"] = {**expected["constraints"], "work_unit_id": str(unit_id)}
    assert served == expected
    assert cast(dict[str, Any], brief["target"])["repository"] == TARGET_REPOSITORY

    # The runner asserts constraints.work_unit_id == the id it was dispatched with,
    # and constraints.target_repository == the repo the workflow runs in.
    assert served["constraints"]["work_unit_id"] == str(unit_id)

    github = FakeGitHubDispatcher()
    record = dispatch_work_unit(
        migrated_session,
        DispatchCommand(
            unit_id=unit_id,
            runner_attempt=1,
            actor=SYSTEM,
            idempotency_key="fanout-dispatch",
        ),
        _dispatch_settings(),
        github,
        inert_source(),
    )

    assert record.status == "dispatched"
    assert record.target_repository == TARGET_REPOSITORY
    assert github.calls[0]["repository"] == TARGET_REPOSITORY
    assert cast(dict[str, str], github.calls[0]["inputs"])["work_unit_id"] == str(unit_id)


def test_orchestrator_serves_the_edit_envelope_and_admits_it(migrated_session: Session) -> None:
    """The edit-shaped envelope traverses the same full path: intake, breakdown,
    approval, admission. This is the half of the WS-P2.33 pin the byte hash cannot
    carry — the orchestrator ADMITS the shape factory-runner asserts it accepts."""
    unit_id = _approved_ready_unit(
        migrated_session,
        envelope=golden_edit_envelope(),
        change_class=EDIT_CHANGE_CLASS,
        prefix="editshape",
        unit_key="pin-the-caller",
    )

    brief = runner_brief(migrated_session, unit_id)
    served = cast(dict[str, Any], cast(dict[str, Any], brief["authority"])["envelope"])

    expected = golden_edit_envelope()
    expected["constraints"] = {**expected["constraints"], "work_unit_id": str(unit_id)}
    assert served == expected
    assert cast(dict[str, Any], brief["target"])["repository"] == EDIT_TARGET_REPOSITORY

    github = FakeGitHubDispatcher()
    record = dispatch_work_unit(
        migrated_session,
        DispatchCommand(
            unit_id=unit_id,
            runner_attempt=1,
            actor=SYSTEM,
            idempotency_key="editshape-dispatch",
        ),
        _dispatch_settings(
            change_class=EDIT_CHANGE_CLASS, target_repository=EDIT_TARGET_REPOSITORY
        ),
        github,
        inert_source(),
    )

    assert record.status == "dispatched"
    assert record.target_repository == EDIT_TARGET_REPOSITORY
    assert github.calls[0]["repository"] == EDIT_TARGET_REPOSITORY
