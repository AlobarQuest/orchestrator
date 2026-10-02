"""Shared setup for tests that drive a work unit through the public protocol.

A unit is born the way production births one: a package intake, a breakdown proposal, and a human
approval of that breakdown through `/review`. No test-only seeding route is involved, so whatever a
test then does to the unit, it does to a unit production could have made.

The intent-package approval is checked client-side, by the sibling `intent_packages` tool, before
an intake payload is built (`package_sources.load_package_intake_payload`). The `approved_package`
fixture stands in for that one external check; everything the server does is real.
"""

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import orchestrator.package_sources as package_sources
from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.identity.auth import M2MCredential
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.main import create_app
from orchestrator.package_sources import VerifiedApproval, load_package_intake_payload
from orchestrator.persistence.models import Claim, WorkUnit
from tests._support.review_forms import decide_decomposition

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}
VERIFIER = {"Authorization": "Bearer verifier-token", "X-Credential-Key-Id": "verifier-key"}
PACKAGE_FIXTURE = Path("tests/fixtures/intent-packages/ws32-approved-software")
AUTHORITY = {
    "capabilities": {"repo.edit": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}


def protocol_auth_config() -> AuthConfig:
    """One human, one worker, one system and one verifier actor, as production configures them."""
    registry = RegistryAdapter(
        {
            "schema": "orchestrator-actor-bundle/v1",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "actors": [
                {
                    "agent_id": "worker",
                    "version": 3,
                    "status": "active",
                    "runtime": "runner",
                    "authority_profile": "agent-queue-v1",
                },
                {
                    "agent_id": "devon",
                    "version": 1,
                    "status": "active",
                    "runtime": "human",
                    "authority_profile": "human-operator-v1",
                },
                {
                    "agent_id": "system",
                    "version": 1,
                    "status": "active",
                    "runtime": "orchestrator",
                    "authority_profile": "system-v1",
                },
                {
                    "agent_id": "verifier",
                    "version": 1,
                    "status": "active",
                    "runtime": "verifier",
                    "authority_profile": "verifier-v1",
                },
            ],
        }
    )
    return AuthConfig(
        registry=registry,
        m2m_credentials={
            "worker-key": M2MCredential(
                agent_id="worker",
                token_hash=hashlib.sha256(b"fixture-token").hexdigest(),
            ),
            "system-key": M2MCredential(
                agent_id="system",
                token_hash=hashlib.sha256(b"system-token").hexdigest(),
            ),
            "verifier-key": M2MCredential(
                agent_id="verifier",
                token_hash=hashlib.sha256(b"verifier-token").hexdigest(),
            ),
        },
        trusted_proxy_ips=frozenset({"testclient"}),
        proxy_marker_header="X-Alobar-Proxy",
        proxy_marker="fixture-marker",
        email_header="X-Alobar-Email",
        email_to_actor={"devon@example.invalid": "devon"},
        m2m_roles={
            "system-key": ActorRole.SYSTEM,
            "verifier-key": ActorRole.VERIFIER,
        },
        csrf_secret=b"test-only-csrf-secret-with-32-bytes",
    )


@contextmanager
def protocol_client(auth: AuthConfig, engine: Engine) -> Iterator[TestClient]:
    """A fresh application process over the given database. Leaving the block discards it."""
    app_instance = create_app(auth)

    def database_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app_instance.dependency_overrides[get_session] = database_session
    with TestClient(
        app_instance, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client


def stand_in_approved_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the client-side approval check the intent-packages tool performs."""
    monkeypatch.setattr(package_sources, "_verify_current_approval", lambda *_: verified())
    monkeypatch.setattr(package_sources, "_git_head", lambda _path: "deadbeef")


def verified() -> VerifiedApproval:
    return VerifiedApproval(
        approved_by="devon",
        approved_at="2026-07-05T00:02:00Z",
        approval_event_id="22222222-2222-2222-2222-222222222222",
        approval_ledger_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def standing_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "code_standards_version": "1.0",
        "security_standards_version": "1.0",
        "project_standards_version": "1.0",
        "agent_id": "worker",
        "authority_profile": "agent-queue-v1",
        "runtime_name": "codex",
        "runtime_version": "1.0",
        "skill_bundle_id": "ws-3.3-protocol-smoke",
        "skill_bundle_version": "1",
        "capabilities": ["repository_write"],
    }
    context.update(overrides)
    return context


def decomposition_payload(acceptance_criteria: dict[str, str]) -> dict[str, object]:
    ac_ids = list(acceptance_criteria.values())
    return {
        "idempotency_key": "ws33-smoke-proposal",
        "expected_version": 0,
        "rationale": "Smoke suite covers one executable public protocol path.",
        "proposed_units": [
            {
                "unit_key": "smoke-unit",
                "title": "Exercise WS-3.3 smoke protocol",
                "outcome": "The public lifecycle path is smoke tested.",
                "required_capability": "repo.edit",
                "authority": AUTHORITY,
                "max_attempts": 3,
            }
        ],
        "dependencies": [],
        "ac_mappings": [{"ac_id": ac_ids[0], "unit_key": "smoke-unit"}],
        "retained_acs": [],
    }


@dataclass(frozen=True)
class BornUnit:
    revision_id: str
    unit_id: str
    ac_id: str  # the human id evidence and adjudication take, e.g. "AC-001"


def birth_unit(db_client: TestClient, *, suffix: str, max_attempts: int = 3) -> BornUnit:
    """Intake, propose a breakdown, approve it in `/review`: the unit is born in DRAFT."""
    package_payload = load_package_intake_payload(
        PACKAGE_FIXTURE,
        source_repository="AlobarQuest/intent-packages",
    )
    enforcement_snapshot = package_payload["enforcement_snapshot"]
    assert isinstance(enforcement_snapshot, dict)
    intake_body = {
        **package_payload,
        "idempotency_key": f"ws33-smoke-{suffix}-intake",
        "expected_version": 0,
        "package_id": f"ws33-smoke-{suffix}",
        "enforcement_snapshot": {
            **enforcement_snapshot,
            "required_context": standing_context(),
        },
    }
    intake = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_body)
    assert intake.status_code == 201, intake.json()
    criterion = intake.json()["acceptance_criteria"][0]
    proposal = db_client.post(
        f"/api/v1/package-intakes/{intake.json()['id']}/decomposition-proposals",
        headers=WORKER,
        json={
            **decomposition_payload({"AC-001": criterion["id"]}),
            "idempotency_key": f"ws33-smoke-{suffix}-proposal",
            "proposed_units": [
                {
                    "unit_key": f"ws33-smoke-{suffix}-unit",
                    "title": f"WS-3.3 smoke {suffix}",
                    "outcome": "Auxiliary smoke path is exercised.",
                    "required_capability": "repo.edit",
                    "authority": AUTHORITY,
                    "max_attempts": max_attempts,
                }
            ],
            "ac_mappings": [{"ac_id": criterion["id"], "unit_key": f"ws33-smoke-{suffix}-unit"}],
        },
    )
    assert proposal.status_code == 201, proposal.json()
    approved = decide_decomposition(
        db_client,
        proposal.json()["id"],
        "approve",
        "Approved auxiliary smoke activation.",
        headers=HUMAN,
    )
    return BornUnit(
        revision_id=intake.json()["id"],
        unit_id=str(approved["created_work_unit_ids"][f"ws33-smoke-{suffix}-unit"]),
        ac_id=criterion["ac_id"],
    )


def make_ready(db_client: TestClient, engine: Engine, unit_id: str, *, key: str) -> None:
    """Production's next two steps: a human approves the authority envelope, then SYSTEM readies."""
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-authority",
            "expected_version": unit_row(engine, unit_id).version,
            "subject_type": "authority",
            "reason": "Authority envelope approved for the drill.",
        },
    )
    ok(response)
    ok(
        db_client.post(
            f"/api/v1/work-units/{unit_id}/commands/ready",
            headers=SYSTEM,
            json={
                "idempotency_key": f"{key}-ready",
                "expected_version": unit_row(engine, unit_id).version,
            },
        )
    )


def unit_row(engine: Engine, unit_id: str) -> WorkUnit:
    """Read the unit through a fresh session, so the answer is what was committed."""
    with Session(engine, expire_on_commit=False) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        session.expunge(unit)
        return unit


def claims_of(engine: Engine, unit_id: str) -> list[Claim]:
    with Session(engine, expire_on_commit=False) as session:
        rows = list(
            session.scalars(
                select(Claim)
                .where(Claim.work_unit_id == uuid.UUID(unit_id))
                .order_by(Claim.attempt)
            )
        )
        session.expunge_all()
        return rows


def expire_latest_claim(engine: Engine, unit_id: str) -> None:
    """The one sanctioned write: `DEFAULT_LEASE` is 15 real minutes and nothing may shorten it."""
    with Session(engine) as session:
        claim = session.scalar(
            select(Claim)
            .where(Claim.work_unit_id == uuid.UUID(unit_id))
            .order_by(Claim.attempt.desc())
            .limit(1)
        )
        assert claim is not None
        claim.lease_expires_at = claim.acquired_at
        session.commit()


def ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.json()
    body = response.json()
    assert isinstance(body, dict)
    return body


def ok_rows(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.json()
    rows = response.json()
    assert isinstance(rows, list)
    return rows


def refused(response: Any) -> str:
    """The error code of a refused request. A refusal must name itself."""
    assert response.status_code >= 400, response.json()
    return str(response.json()["error"]["code"])
