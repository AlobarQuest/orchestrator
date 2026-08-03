"""WS-P2.16 U1 -- capability-vocabulary enforcement at orchestrator unit ingress.

A work unit's ``required_capability`` and every key in its ``authority.capabilities`` must name a
capability the orchestrator recognises (the runner six plus ``post_deploy_verification``). The
registry vocabulary (``repository_write``) is a package-level vocabulary; a UNIT carrying it is
test-only drift and must be rejected with a NAMED error at the gate, where a human can fix it --
not left to fail closed and silently as ``capability_not_authorized`` at dispatch.

Both ingress paths are covered: ``register_approved_unit`` (unit construction, reached from the
raw ``/work-units`` route and from decomposition approval) and ``_validate_unit_constraints``
(the decomposition PROPOSAL gate, earlier still).
"""

import pytest
from sqlalchemy.orm import Session

from orchestrator.capability_vocabulary import ORCHESTRATOR_CAPABILITIES, RUNNER_CAPABILITIES
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope, normalize_authority
from orchestrator.kernel.states import ActorRole
from orchestrator.services.decomposition import ProposedUnit, _validate_unit_constraints
from tests.services.test_package_registration import NOW, register_test_revision

_BUDGETS = AuthorityBudgets(max_attempts=3, max_llm_calls=4)


def _register_unit(session: Session, *, required_capability: str, capabilities: dict[str, str]):
    from orchestrator.services.packages import register_approved_unit

    revision = register_test_revision(session)
    return register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key="unit-ingress",
        title="Ingress unit",
        outcome="Works",
        required_capability=required_capability,
        authority=AuthorityEnvelope(capabilities=capabilities, budgets=_BUDGETS),
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def _proposed_unit(*, required_capability: str, capabilities: dict[str, str]) -> ProposedUnit:
    payload: dict[str, object] = {
        "capabilities": capabilities,
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
    }
    if capabilities.get("command.run") == "allowed":
        # command.run authority requires a declared command allowlist for every
        # change class (WS-P2.33); these cases are about the capability VOCABULARY.
        payload["constraints"] = {"allowed_commands": ["make check"]}
    return ProposedUnit(
        unit_key="proposed-ingress",
        title="Proposed ingress unit",
        outcome="Works",
        required_capability=required_capability,
        authority=normalize_authority(payload),
        authority_payload=payload,
    )


# --- register_approved_unit --------------------------------------------------------------------


def test_register_rejects_registry_vocabulary_required_capability(migrated_session: Session):
    with pytest.raises(DomainError) as error:
        _register_unit(
            migrated_session,
            required_capability="repository_write",
            capabilities={"repo.edit": "allowed"},
        )
    assert error.value.code == "unknown_capability"
    assert "repository_write" in error.value.message


def test_register_rejects_registry_vocabulary_envelope_capability(migrated_session: Session):
    with pytest.raises(DomainError) as error:
        _register_unit(
            migrated_session,
            required_capability="repo.edit",
            capabilities={"repository_write": "allowed"},
        )
    assert error.value.code == "unknown_capability"
    assert "repository_write" in error.value.message


def test_register_accepts_runner_vocabulary(migrated_session: Session):
    unit = _register_unit(
        migrated_session,
        required_capability="repo.edit",
        capabilities={"repo.edit": "allowed", "command.run": "allowed"},
    )
    assert unit.state == "draft"


def test_register_accepts_orchestrator_only_post_deploy_capability(migrated_session: Session):
    # The orchestrator vocabulary is a strict superset of the runner's: ingress must admit the
    # capability the orchestrator mints for its own generated units, or it would reject them.
    unit = _register_unit(
        migrated_session,
        required_capability="post_deploy_verification",
        capabilities={"post_deploy_verification": "allowed"},
    )
    assert unit.state == "draft"
    assert "post_deploy_verification" in ORCHESTRATOR_CAPABILITIES
    assert "post_deploy_verification" not in RUNNER_CAPABILITIES


def test_register_accepts_operational_action_capability(migrated_session: Session):
    # WS-P2.13: a non-software unit -- a credential rotation -- has no repository, opens no pull
    # request and runs no command. Before this term it could only pass ingress by declaring a
    # runner capability it never used.
    unit = _register_unit(
        migrated_session,
        required_capability="operational_action",
        capabilities={"operational_action": "allowed"},
    )
    assert unit.state == "draft"


def test_operational_action_is_orchestrator_only_and_grants_no_runner_capability():
    """The term must be accepted at ingress AND unusable by a runner.

    Accepting it is half the guarantee; the half that matters is that an envelope declaring only
    `operational_action` authorises nothing a runner could act on. `level_for` returns
    "prohibited" for every runner capability, so an operational unit that somehow reached the
    dispatch path would fail closed there rather than executing with borrowed authority.
    """
    assert "operational_action" in ORCHESTRATOR_CAPABILITIES
    assert "operational_action" not in RUNNER_CAPABILITIES
    envelope = AuthorityEnvelope(capabilities={"operational_action": "allowed"}, budgets=_BUDGETS)
    assert envelope.level_for("operational_action") == "allowed"
    assert {envelope.level_for(name) for name in RUNNER_CAPABILITIES} == {"prohibited"}


# --- decomposition proposal gate (_validate_unit_constraints) -----------------------------------


def test_proposal_rejects_registry_vocabulary_required_capability():
    with pytest.raises(DomainError) as error:
        _validate_unit_constraints(
            _proposed_unit(
                required_capability="repository_write",
                capabilities={"repo.edit": "allowed"},
            )
        )
    assert error.value.code == "unknown_capability"


def test_proposal_rejects_registry_vocabulary_envelope_capability():
    with pytest.raises(DomainError) as error:
        _validate_unit_constraints(
            _proposed_unit(
                required_capability="repo.edit",
                capabilities={"repository_write": "allowed"},
            )
        )
    assert error.value.code == "unknown_capability"


def test_proposal_accepts_operational_action():
    # The proposal gate is the EARLIER of the two ingress paths and the one a hand-authored
    # non-software decomposition actually meets first; admitting the term at unit construction
    # while rejecting it here would block the profile before a human ever saw the proposal.
    payload = _validate_unit_constraints(
        _proposed_unit(
            required_capability="operational_action",
            capabilities={"operational_action": "allowed"},
        )
    )
    assert payload["capabilities"] == {"operational_action": "allowed"}


def test_proposal_accepts_runner_vocabulary():
    payload = _validate_unit_constraints(
        _proposed_unit(
            required_capability="repo.edit",
            capabilities={"repo.edit": "allowed", "command.run": "allowed"},
        )
    )
    assert payload["capabilities"] == {"repo.edit": "allowed", "command.run": "allowed"}
