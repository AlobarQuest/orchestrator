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
    payload = {
        "capabilities": capabilities,
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
    }
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


def test_proposal_accepts_runner_vocabulary():
    payload = _validate_unit_constraints(
        _proposed_unit(
            required_capability="repo.edit",
            capabilities={"repo.edit": "allowed", "command.run": "allowed"},
        )
    )
    assert payload["capabilities"] == {"repo.edit": "allowed", "command.run": "allowed"}
