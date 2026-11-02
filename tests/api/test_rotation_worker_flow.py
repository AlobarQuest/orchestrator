"""The executor end to end against a real orchestrator: the fake adapter's three units and its act.

Everything the production run does happens here through the orchestrator's own routes: the package
intake, a decomposition proposal with the hand-offs and the human act as dependencies, its approval
at `/review`, each unit's authority approval, the executor's claims and evidence, and the
attestation that releases the last unit. intent-packages and the four registries are local git
repositories the mirror fetches; BWS is an in-memory store; the provider is the real fake adapter.
Only the network and BWS itself are stand-ins.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.config import get_settings
from orchestrator.persistence.models import Dependency, Evidence, WorkUnit
from rotation_worker.adapters.fake import FakeProvider
from rotation_worker.canonical import package_hash
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.registry import PACKAGES_REPOSITORY, REGISTRY_PATH, REGISTRY_REPOSITORIES
from rotation_worker.values import Value
from rotation_worker.worker import Setup, run_pass
from tests._support.review_forms import decide_decomposition
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, VERIFIER, WORKER
from tests.api.test_observations_api import uptime_body
from tests.rotation_worker.conftest import Origins
from tests.rotation_worker.stores import MemoryStore
from tests.services.test_member_proposals import grant_services

NOW = datetime(2026, 10, 11, tzinfo=UTC)
WORK = "00000000-0000-4000-8000-00000000a0a0"
CREDENTIAL = "rotation-canary"
RUNTIME = 'runtime-fetch repo="orchestrator" ref="no program reads the canary; a person attests"'
PACKAGE_PATH = f"packages/rotation-{CREDENTIAL}/package.yaml"
UNITS = {"mint": ["AC-001", "AC-002", "AC-003"], "store": ["AC-004", "AC-005"]}
UNITS["retire"] = ["AC-006", "AC-007", "AC-008"]
# Each criterion names its step as every standing package does; the two verify_consumers steps
# also name their unit.
STEPS = {
    "AC-001": "mint",
    "AC-002": "stage",
    "AC-003": "verify_new",
    "AC-004": "store",
    "AC-005": "store unit's verify_consumers",
    "AC-006": "retire unit's verify_consumers",
    "AC-007": "retire_old",
    "AC-008": "confirm_dead",
}
CAPABILITIES = {"operational_action": "allowed"}
BUDGETS = {"max_attempts": 3, "max_llm_calls": None}


class _ThroughTestClient(httpx.BaseTransport):
    """Hands each request to the app's test client and returns its answer as an httpx response."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self._client.request(
            request.method,
            request.url.raw_path.decode(),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)


@pytest.fixture
def origins(tmp_path: Path) -> Origins:
    return Origins(tmp_path / "origins")


@pytest.fixture
def executor_is_worker(db_client: TestClient) -> Iterator[None]:
    app = cast(FastAPI, db_client.app)
    configured = get_settings().model_copy(update={"rotation_executor_agent_id": "worker"})
    app.dependency_overrides[get_settings] = lambda: configured
    yield
    app.dependency_overrides.pop(get_settings, None)


class Estate:
    """The canary's keeper, registry entry and approved package, decomposed into its units."""

    def __init__(self, tmp_path: Path, origins: Origins, engine: Engine, client: TestClient):
        self.engine = engine
        self.client = client
        self.store = MemoryStore()
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        self.provider = FakeProvider(self.state_dir / "fake.json")
        self.original = self.provider.mint()
        self.keeper_id = str(uuid.uuid4())
        self.store.secrets[self.keeper_id] = (WORK, "rotation-canary keeper", self.original)
        self.destinations = sorted(
            [f'bws-secret uuid="{self.keeper_id}"', 'provider name="fake"', RUNTIME]
        )
        self._registries(origins)
        # The executor acts only where the member record grants credential_rotation; the canary
        # lives in the orchestrator's registry.
        with Session(engine) as session:
            grant_services(session, "AlobarQuest/orchestrator", "credential_rotation")
            session.commit()
        self.package: dict[str, Any] = {
            "profile": "non-software-operational",
            "profile_fields": {
                "standing": True,
                "credential_id": CREDENTIAL,
                "occurrence": "2026-10-11-requested",
                "destinations": self.destinations,
            },
        }
        approval = {"revision": 1, "approved_hash": package_hash(self.package), "approver": "devon"}
        self.commit = origins.commit(
            PACKAGES_REPOSITORY,
            {
                PACKAGE_PATH: yaml.safe_dump(self.package),
                PACKAGE_PATH.replace("package.yaml", "lineage.yaml"): yaml.safe_dump(
                    {"approvals": [approval]}
                ),
            },
        )
        self.setup = Setup(
            client=OrchestratorClient(
                "https://testserver",
                "worker-key",
                Value("fixture-token"),
                transport=_ThroughTestClient(client),
            ),
            mirror=RepoMirror(tmp_path / "mirrors", origin=origins.url),
            store=self.store,
            work_project_id=WORK,
            state_dir=self.state_dir,
            source_revision="rotation-worker@0123456789ab",
        )

    def _registries(self, origins: Origins) -> None:
        canary = f"""
[[credential]]
id = "{CREDENTIAL}"
class = "rotation-canary"
provider = "fake"
bws_uuid = "{self.keeper_id}"
rotated_by_sds = true

  [[credential.consumer]]
  kind = "bws-secret"
  uuid = "{self.keeper_id}"

  [[credential.consumer]]
  kind = "runtime-fetch"
  repo = "orchestrator"
  ref = "no program reads the canary; a person attests"
"""
        for repository in REGISTRY_REPOSITORIES:
            text = "version = 1\n" + (canary if repository.endswith("/orchestrator") else "")
            origins.commit(repository, {REGISTRY_PATH: text})

    def decompose(self, **constraints: Any) -> dict[str, str]:
        """Register the intake, propose the three units, approve them, approve each authority."""
        revision_id = self._intake()
        criteria = self._criteria(revision_id)
        proposed = self.client.post(
            f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
            headers=WORKER,
            json={
                "idempotency_key": f"proposal-{revision_id}",
                "expected_version": 0,
                "rationale": "The fake adapter's fixed decomposition.",
                "proposed_units": [self._proposed(name, constraints) for name in UNITS],
                "dependencies": [
                    _after("store", "mint"),
                    _after("retire", "store"),
                    {
                        "source_unit_key": "canary-retire",
                        "kind": "external_system",
                        "required_state_or_condition": "attested",
                        "target_unit_key": None,
                        "external_ref": "a person attests the runtime-fetch consumer",
                    },
                ],
                "ac_mappings": [
                    {"ac_id": criteria[ac], "unit_key": f"canary-{name}"}
                    for name, acs in UNITS.items()
                    for ac in acs
                ],
                "retained_acs": [],
            },
        )
        assert proposed.status_code == 201, proposed.text
        decided = decide_decomposition(
            self.client, proposed.json()["id"], "approve", "approved", headers=HUMAN
        )
        units = cast(dict[str, str], decided["created_work_unit_ids"])
        for unit_id in units.values():
            approved = self.client.post(
                f"/api/v1/work-units/{unit_id}/approvals",
                headers=HUMAN,
                json={
                    "idempotency_key": f"authority-{unit_id}",
                    "expected_version": self.version(unit_id),
                    "subject_type": "authority",
                    "reason": "approved",
                },
            )
            assert approved.status_code == 200, approved.text
        return {key.removeprefix("canary-"): unit_id for key, unit_id in units.items()}

    def _intake(self) -> str:
        response = self.client.post(
            "/api/v1/package-intakes",
            headers=HUMAN,
            json={
                "idempotency_key": f"intake-{uuid.uuid4()}",
                "expected_version": 0,
                "package_id": f"rotation-{CREDENTIAL}",
                "source_repository": PACKAGES_REPOSITORY,
                "revision": 1,
                "content_hash": package_hash(self.package),
                # What the emitter records: the package directory on the machine that emitted it.
                "source_path": f"/Users/someone/intent-packages/packages/rotation-{CREDENTIAL}",
                "source_commit": self.commit,
                "approved_by": "devon",
                "approved_at": NOW.isoformat(),
                "approval_event_id": str(uuid.uuid4()),
                "approval_ledger_commit": "a" * 40,
                "profile": "non-software-operational",
                "status_at_intake": "approved",
                "verification_mode": "caller_attested_cli_verified",
                "verification_limitations": {
                    "api_recomputes_remote_git_object": False,
                    "cli_verified_local_package_hash": True,
                    "cli_verified_approval_lineage": True,
                },
                "enforcement_snapshot": {
                    "profile_fields": self.package["profile_fields"],
                    "reach": ["external_system"],
                },
                "authority": {"capabilities": CAPABILITIES, "budgets": BUDGETS},
                "registry_version": 1,
                "acceptance_criteria": [
                    {
                        "ac_id": ac,
                        "condition": f"{ac} holds",
                        "evidence_type": "external_attestation",
                        "evidence": (
                            f"external: the {STEPS[ac]} step's rotation-step/1 evidence, "
                            "filed by the rotation executor."
                        ),
                        "approver": "devon",
                    }
                    for acs in UNITS.values()
                    for ac in acs
                ],
            },
        )
        assert response.status_code == 201, response.text
        return cast(str, response.json()["id"])

    def _criteria(self, revision_id: str) -> dict[str, str]:
        response = self.client.get(f"/api/v1/package-intakes/{revision_id}", headers=HUMAN)
        assert response.status_code == 200, response.text
        return {c["ac_id"]: c["id"] for c in response.json()["acceptance_criteria"]}

    def _proposed(self, name: str, constraints: dict[str, Any]) -> dict[str, Any]:
        envelope_constraints: dict[str, Any] = {
            "rotation_adapter": "fake",
            "rotation_unit": name,
            "destinations": self.destinations,
            "touches_no_hosted_service": True,
            **constraints,
        }
        return {
            "unit_key": f"canary-{name}",
            "title": f"canary {name}",
            "outcome": f"the canary's {name} steps are done",
            "required_capability": "operational_action",
            "authority": {
                "capabilities": CAPABILITIES,
                "budgets": BUDGETS,
                "constraints": {k: v for k, v in envelope_constraints.items() if v is not None},
            },
            "max_attempts": 3,
        }

    def version(self, unit_id: str) -> int:
        with Session(self.engine) as session:
            unit = session.get(WorkUnit, uuid.UUID(unit_id))
            assert unit is not None
            return unit.version

    def state(self, unit_id: str) -> str:
        with Session(self.engine) as session:
            unit = session.get(WorkUnit, uuid.UUID(unit_id))
            assert unit is not None
            return unit.state

    def steps(self, unit_id: str) -> list[str]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(Evidence)
                .where(Evidence.work_unit_id == uuid.UUID(unit_id))
                .order_by(Evidence.recorded_at)
            )
            return [row.payload["step"] for row in rows if row.payload]

    def resolve(self, unit_id: str, kind: str, detail: dict[str, Any]) -> None:
        """Resolve the unit's pending dependency of `kind` as SYSTEM (a person, at `/review`)."""
        with Session(self.engine) as session:
            dependency = session.scalar(
                select(Dependency).where(
                    Dependency.work_unit_id == uuid.UUID(unit_id),
                    Dependency.kind == kind,
                    Dependency.status == "pending",
                )
            )
            assert dependency is not None
            dependency_id = dependency.id
        resolved = self.client.post(
            f"/api/v1/dependencies/{dependency_id}/resolve",
            headers=SYSTEM,
            json={
                "idempotency_key": f"resolve-{dependency_id}",
                "expected_version": self.version(unit_id),
                "status": "satisfied",
                "detail": detail,
            },
        )
        assert resolved.status_code == 200, resolved.text

    def verify(self, unit_id: str) -> str:
        """Run the verifier over a submitted unit, as the rotation-verifier lane does."""
        version = self.version(unit_id)
        verified = self.client.post(
            f"/api/v1/work-units/{unit_id}/verify",
            headers=VERIFIER,
            json={"expected_version": version, "idempotency_key": f"verify-{unit_id}-{version}"},
        )
        assert verified.status_code == 200, verified.text
        return cast(str, verified.json()["state"])

    def keeper(self) -> Value:
        return self.store.read(self.keeper_id)


def _after(later: str, earlier: str) -> dict[str, Any]:
    return {
        "source_unit_key": f"canary-{later}",
        "kind": "work_unit",
        "required_state_or_condition": "completed",
        "target_unit_key": f"canary-{earlier}",
        "external_ref": None,
    }


@pytest.fixture
def estate(
    tmp_path: Path,
    origins: Origins,
    migrated_engine: Engine,
    db_client: TestClient,
    executor_is_worker: None,
) -> Estate:
    return Estate(tmp_path, origins, migrated_engine, db_client)


def test_the_canary_rotates_through_three_units_and_one_human_act(estate: Estate) -> None:
    units = estate.decompose()
    assert [estate.state(units[name]) for name in UNITS] == ["ready", "draft", "draft"]

    assert run_pass(estate.setup) == 1
    assert estate.state(units["mint"]) == "submitted"
    assert estate.steps(units["mint"]) == ["mint", "stage", "verify_new"]

    # The verifier completes the unit from the executor's evidence, and the completion hands off
    # to the next unit in the same transaction (ADR-0055 amendment 14).
    assert estate.verify(units["mint"]) == "completed"
    assert estate.state(units["store"]) == "ready"
    assert run_pass(estate.setup) == 1
    assert estate.steps(units["store"]) == ["store", "verify_consumers"]
    new = estate.keeper()
    assert not new.same_as(estate.original)

    assert estate.verify(units["store"]) == "completed"
    assert estate.state(units["retire"]) == "draft"
    assert run_pass(estate.setup) == 0  # the human act still holds it
    print_ = new.fingerprint
    estate.resolve(
        units["retire"],
        "external_system",
        {
            "note": "the runtime-fetch consumer holds the new value",
            "fingerprint": {"sha256_prefix": print_.sha256_prefix[:8], "length": print_.length},
        },
    )
    assert estate.state(units["retire"]) == "ready"
    assert run_pass(estate.setup) == 1
    assert estate.steps(units["retire"]) == [
        "verify_consumers",
        "retire_old",
        "confirm_dead",
        "confirm_dead",
    ]
    assert estate.verify(units["retire"]) == "completed"
    assert estate.provider.probe(estate.original) == 401
    assert estate.provider.probe(new) == 200
    assert estate.store.secrets.keys() == {estate.keeper_id}


def test_evidence_another_identity_filed_leaves_the_unit_for_a_person(estate: Estate) -> None:
    units = estate.decompose()
    assert run_pass(estate.setup) == 1
    assert estate.state(units["mint"]) == "submitted"

    # The VM executor is now someone else, so the evidence `worker` filed is no executor's.
    app = cast(FastAPI, estate.client.app)
    moved = get_settings().model_copy(update={"rotation_executor_agent_id": "another-executor"})
    app.dependency_overrides[get_settings] = lambda: moved

    assert estate.verify(units["mint"]) == "awaiting_review"
    assert estate.state(units["store"]) == "draft"


def test_a_persons_decision_before_the_verifier_is_never_replaced(estate: Estate) -> None:
    units = estate.decompose()
    assert run_pass(estate.setup) == 1
    mint = units["mint"]
    with Session(estate.engine) as session:
        filed = session.scalars(
            select(Evidence).where(
                Evidence.work_unit_id == uuid.UUID(mint), Evidence.ac_id == "AC-003"
            )
        ).first()
        assert filed is not None
        revision_id, evidence_id = str(filed.work_package_revision_id), str(filed.id)
    rejected = estate.client.post(
        f"/api/v1/work-units/{mint}/adjudications",
        headers=HUMAN,
        json={
            "idempotency_key": "a-person-rejects-verify-new",
            "expected_version": estate.version(mint),
            "work_package_revision_id": revision_id,
            "ac_id": "AC-003",
            "outcome": "failed",
            "rationale": "the probe target is wrong",
            "failed_evidence_id": evidence_id,
        },
    )
    assert rejected.status_code == 200, rejected.text

    assert estate.verify(mint) != "completed"
    assert estate.state(units["store"]) == "draft"


def test_the_other_executors_evidence_leaves_the_unit_for_a_person(estate: Estate) -> None:
    units = estate.decompose()
    assert run_pass(estate.setup) == 1

    # `worker` is now the Keychain instance, which owns only bws-machine-token units, so the
    # evidence it filed on this fake-adapter unit is not its owner's.
    app = cast(FastAPI, estate.client.app)
    swapped = get_settings().model_copy(
        update={
            "rotation_executor_agent_id": "another-executor",
            "rotation_keychain_executor_agent_id": "worker",
        }
    )
    app.dependency_overrides[get_settings] = lambda: swapped

    assert estate.verify(units["mint"]) == "awaiting_review"


def test_a_unit_whose_envelope_lists_other_destinations_is_claimed_and_failed(
    estate: Estate,
) -> None:
    units = estate.decompose(destinations=['provider name="fake"'])
    assert run_pass(estate.setup) == 0
    assert estate.state(units["mint"]) == "failed"
    assert estate.steps(units["mint"]) == []
    assert estate.keeper().same_as(estate.original)


def test_a_unit_naming_no_rotation_adapter_is_left_alone(estate: Estate) -> None:
    units = estate.decompose(rotation_adapter=None)
    assert run_pass(estate.setup) == 0
    assert estate.state(units["mint"]) == "ready"


def test_the_unit_read_route_serves_what_the_executor_reads(estate: Estate) -> None:
    units = estate.decompose()
    response = estate.client.get(f"/api/v1/work-units/{units['mint']}", headers=WORKER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "id",
        "unit_key",
        "state",
        "version",
        "required_capability",
        "work_package_revision_id",
        "authority",
        "authority_fingerprint",
        "attempt_count",
        "max_attempts",
        "latest_dispatch_id",
        "pr_number",
        "armed_head_sha",
        "automated_check_ac_ids",
        "named_check_filed",
    }
    assert body["state"] == "ready"
    assert body["authority"]["constraints"]["rotation_unit"] == "mint"
    missing = estate.client.get(f"/api/v1/work-units/{uuid.uuid4()}", headers=WORKER)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "work_unit_not_found"
    assert estate.client.get(f"/api/v1/work-units/{units['mint']}").status_code == 401


def test_the_executor_s_worker_bearer_reads_one_subject_s_observations(
    db_client: TestClient,
) -> None:
    """The scheduled-reader read (ADR-0055 amendment 9, decision 5) through the real route.

    The observation route admits any authenticated role, so the WORKER bearer reads it; a later
    confinement that refused the executor here would stop every drift-reporter rotation.
    """
    posted = db_client.post("/api/v1/observations", headers=SYSTEM, json=uptime_body())
    assert posted.status_code == 201
    client = OrchestratorClient(
        "https://testserver",
        "worker-key",
        Value("fixture-token"),
        transport=_ThroughTestClient(db_client),
    )
    subject = "https://sds.alobar.net/health/live"
    since = datetime(2026, 7, 8, 22, 0, tzinfo=UTC)
    [row] = client.observations("uptime_monitor", subject, since)
    assert row["id"] == posted.json()["id"]
    assert datetime.fromisoformat(row["observed_at"]) == datetime(2026, 7, 8, 22, 30, tzinfo=UTC)
    later = datetime(2026, 7, 8, 23, 0, tzinfo=UTC)
    assert client.observations("uptime_monitor", subject, later) == []
    assert client.observations("github", subject, since) == []
