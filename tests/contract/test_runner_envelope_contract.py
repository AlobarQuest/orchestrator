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
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES
from orchestrator.kernel.authority import normalize_authority
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

TARGET_REPOSITORY = "AlobarQuest/change-manager"
CHANGE_CLASS = "dependency-update"
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


def _authored_envelope() -> dict[str, Any]:
    """The golden envelope minus the server-owned stamp, i.e. what a human authors."""
    envelope = golden_envelope()
    constraints = dict(envelope["constraints"])
    del constraints["work_unit_id"]
    return {**envelope, "constraints": constraints}


def _dispatch_settings() -> DispatchSettings:
    return DispatchSettings(
        enabled=True,
        allowed_change_classes=frozenset({CHANGE_CLASS}),
        enabled_capabilities=frozenset({CAPABILITY}),
        allowed_target_repositories=frozenset({TARGET_REPOSITORY}),
        workflow_id="factory-runner-pilot.yml",
        workflow_ref="main",
        github_app_configured=True,
    )


def _fanout_unit() -> ProposedUnit:
    payload = _authored_envelope()
    return ProposedUnit(
        unit_key="bump-dependency",
        title="Bump the pinned dependency",
        outcome="Dependency updated and checks green.",
        required_capability=CAPABILITY,
        authority=normalize_authority(payload),
        authority_payload=payload,
    )


def _approved_ready_unit(session: Session):
    revision = register_package_intake(
        session,
        intake_command(
            package_id="pkg-fanout",
            idempotency_key="intake-fanout",
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
            # No conformance here: it is attested per unit against that unit's own target
            # repository, not once per package revision at intake.
            enforcement_snapshot={
                "title": "Fan out a dependency update",
                "reach": ["source_repository"],
                "outcome": "Every target repo gets a PR",
                "scope": {"in": ["dependency-update"]},
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
            proposed_units=(_fanout_unit(),),
            dependencies=(),
            ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="bump-dependency"),),
            retained_acs=(
                RetainedAc(ac_id=str(ac_ids["AC-002"]), rationale="Package-level gate."),
            ),
            idempotency_key="proposal-fanout",
        ),
        human_actor(),
    )
    approve_decomposition_proposal(
        session,
        proposal.id,
        actor=human_actor(),
        reason="Approve the fan-out.",
        idempotency_key="proposal-fanout-approve",
    )
    unit_id = uuid.UUID(str(uuid.uuid5(proposal.id, "bump-dependency")))
    record_approval(
        session,
        unit_id=unit_id,
        subject_type="authority",
        actor_id=human_actor().actor_id,
        actor_role=ActorRole.HUMAN,
        reason="Authority approved for this repository.",
        idempotency_key="fanout-authority",
        expected_version=1,
    )
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit_id,
            target=WorkUnitState.READY,
            actor=SYSTEM,
            expected_version=1,
            idempotency_key="fanout-ready",
        ),
    )
    return unit_id


def test_golden_envelope_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_envelope(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256


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
