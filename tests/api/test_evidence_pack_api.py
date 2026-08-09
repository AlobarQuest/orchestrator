"""GET /work-units/{unit_id}/evidence-pack (WS-P2.5 Increment 1).

The structured JSON twin of the `/review` evidence-pack HTML page: wires
`evidence_pack_projection` (Task 1) through `evidence_pack_response` (Task 2) to a route that is
authentication-ONLY -- no role gate -- because the runner's WORKER credential must be able to read
its own unit's evidentiary record, not just SYSTEM/VERIFIER/HUMAN actors.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.schemas import (
    EvidencePackAdjudicationResponse,
    EvidencePackCriterionRefusalResponse,
    EvidencePackResponse,
    EvidencePackVerifierDecidedResponse,
)
from orchestrator.persistence.models import Adjudication, Approval, Evidence, WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit


def _unit_version(migrated_engine: Engine, unit_id: str) -> tuple[int, str]:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        return unit.version, str(unit.work_package_revision_id)


def _built_and_evidenced_unit(
    db_client: TestClient, migrated_engine: Engine, key: str
) -> tuple[str, dict[str, object]]:
    """A ready unit, claimed, with a current evidence record and a waived adjudication."""
    unit_id = _register_ready_unit(db_client, key)
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": f"{key}-claim", "expected_version": 2},
    )
    assert claim.status_code == 200
    lease = claim.json()

    version, revision_id = _unit_version(migrated_engine, unit_id)
    evidence = db_client.post(
        f"/api/v1/work-units/{unit_id}/evidence",
        headers=WORKER,
        json={
            "idempotency_key": f"{key}-evidence",
            "expected_version": version,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "attempt": lease["attempt"],
            "lease_token": lease["lease_token"],
            "evidence_type": "test",
            "stable_ref": "artifact://evidence-pack",
            "payload": {"exit_code": 1},
            "source_revision": "abc123",
        },
    )
    assert evidence.status_code == 200
    evidence_id = evidence.json()["id"]

    version, _ = _unit_version(migrated_engine, unit_id)
    expires_at = (datetime.now(UTC) + timedelta(days=365)).isoformat()
    adjudication = db_client.post(
        f"/api/v1/work-units/{unit_id}/adjudications",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-waiver",
            "expected_version": version,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "outcome": "waived",
            "rationale": "accepted for release",
            "failed_evidence_id": evidence_id,
            "risk": "medium",
            "follow_up": "monitor in prod",
            "scope": "ac-1",
            "expires_at": expires_at,
        },
    )
    assert adjudication.status_code == 200

    return unit_id, lease


def test_evidence_pack_route_returns_the_structured_pack(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, _lease = _built_and_evidenced_unit(db_client, migrated_engine, "evidence-pack-api")

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)

    assert response.status_code == 200
    body = response.json()

    assert body["work_unit"]["id"] == unit_id
    assert body["work_unit"]["authority_fingerprint"]
    assert body["provenance"]["content_hash"]
    assert body["provenance"]["revision"] == 1

    evidence_entry = next(row for row in body["evidence"] if row["ac_id"] == "ac-1")
    assert evidence_entry["current"] is True
    assert evidence_entry["evidence_type"] == "test"
    assert evidence_entry["stable_ref"] == "artifact://evidence-pack"
    assert evidence_entry["supersedes"] is None

    waiver = next(row for row in body["adjudications"] if row["outcome"] == "waived")
    assert waiver["current"] is True
    assert waiver["decided_by"] == "devon"
    assert waiver["risk"] == "medium"
    assert waiver["follow_up"] == "monitor in prod"
    assert waiver["scope"] == "ac-1"
    assert waiver["failed_evidence_id"] == evidence_entry["id"]
    assert waiver["expires_at"] is not None

    assert any(
        a["subject_type"] == "authority" and a["decision"] == "approved" for a in body["approvals"]
    )
    assert body["events"], "the lifecycle transitions must be visible in the pack"
    assert body["event_publications"] == []


def test_the_served_pack_carries_the_deciding_role_and_the_evidence_it_cites(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """WS-P3.7. Asserted on the SERVED BODY, never on the service dict: a `response_model` drops
    every key the model does not declare, silently and without an error, so "the service returns
    it" has never been evidence "the consumer receives it" (WS-P2.1, WS-P2.12)."""
    unit_id, _lease = _built_and_evidenced_unit(db_client, migrated_engine, "evidence-pack-role")

    body = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER).json()

    waiver = next(row for row in body["adjudications"] if row["outcome"] == "waived")
    assert waiver["decided_by_role"] == "human"
    # `failed_evidence_id` is the waiver field and answers a different question; `evidence_id` is
    # the evidence the decision was recorded against, and was projected nowhere until now.
    assert "evidence_id" in waiver


def test_the_served_pack_answers_whether_the_verifier_decided_every_criterion(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The condition ADR-0020 rests on, readable off-process with ordinary credentials -- no
    `/history` parsing and no opaque event payload. This unit's one criterion was WAIVED by a
    human, so the answer is no, for three separately-named reasons."""
    unit_id, _lease = _built_and_evidenced_unit(db_client, migrated_engine, "evidence-pack-vdc")

    body = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER).json()

    answer = body["verifier_decided_completion"]
    assert answer["satisfied"] is False
    assert {refusal["code"] for refusal in answer["refusals"]} == {
        # ADR-0020's first clause: a waived criterion rests on evidence that FAILED, which is
        # nothing the orchestrator observed and passed.
        "criterion_evidence_not_observed",
        "criterion_waived",
        "outcome_does_not_settle_criterion",
        "decider_was_not_the_verifier",
    }
    assert {refusal["ac_id"] for refusal in answer["refusals"]} == {"ac-1"}


def test_the_pack_models_declare_exactly_the_fields_the_route_serves() -> None:
    """A literal pin, not a derived one. `response_model` fails by SILENT OMISSION, so the field
    set has to be asserted somewhere that cannot shrink along with the code -- a set built from the
    model itself would agree with the model however wrong the model became."""
    assert set(EvidencePackResponse.model_fields) == {
        "work_unit",
        "provenance",
        "authority",
        "dependencies",
        "claims",
        "evidence",
        "adjudications",
        "verifier_decided_completion",
        "approvals",
        "event_publications",
        "events",
    }
    assert set(EvidencePackAdjudicationResponse.model_fields) == {
        "id",
        "ac_id",
        "outcome",
        "current",
        "decided_by",
        "decided_by_role",
        "evidence_id",
        "rationale",
        "risk",
        "follow_up",
        "scope",
        "expires_at",
        "failed_evidence_id",
    }
    assert set(EvidencePackVerifierDecidedResponse.model_fields) == {
        "satisfied",
        "decided_by_verifier",
        "evidence_observed",
        "refusals",
    }
    assert set(EvidencePackCriterionRefusalResponse.model_fields) == {"ac_id", "code"}


def test_evidence_pack_route_is_readable_by_the_worker_credential(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """Auth-only, no role gate: the constraint this whole task exists to enforce."""
    unit_id, _lease = _built_and_evidenced_unit(
        db_client, migrated_engine, "evidence-pack-api-worker"
    )

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)

    assert response.status_code == 200


def test_evidence_pack_route_unknown_unit_is_clean_4xx_not_500(db_client: TestClient) -> None:
    response = db_client.get(f"/api/v1/work-units/{uuid.uuid4()}/evidence-pack", headers=WORKER)

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "work_unit_not_found"


def test_evidence_pack_markdown_route_returns_rendered_markdown(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, _lease = _built_and_evidenced_unit(db_client, migrated_engine, "evidence-pack-md")

    json_response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)
    markdown_response = db_client.get(
        f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER
    )

    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")

    body = markdown_response.text
    json_body = json_response.json()
    assert json_body["work_unit"]["authority_fingerprint"] in body
    assert "ac-1" in body
    assert "waived" in body
    for header in (
        "## Canonical provenance",
        "## Authority",
        "## Dependencies and claims",
        "## AC-keyed evidence",
        "## Adjudications and waiver facts",
        "## Approvals",
        "## Event publications",
        "## Event history",
    ):
        assert header in body


def test_evidence_pack_json_and_markdown_derive_from_the_same_projection(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """Both routes must reflect the SAME underlying data -- one structured source, two views."""
    unit_id, _lease = _built_and_evidenced_unit(
        db_client, migrated_engine, "evidence-pack-md-parity"
    )

    json_response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)
    markdown_response = db_client.get(
        f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER
    )

    evidence_entry = next(row for row in json_response.json()["evidence"] if row["ac_id"] == "ac-1")
    assert evidence_entry["stable_ref"] in markdown_response.text


def test_evidence_pack_markdown_route_is_readable_by_the_worker_credential(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, _lease = _built_and_evidenced_unit(
        db_client, migrated_engine, "evidence-pack-md-worker"
    )

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER)

    assert response.status_code == 200


def test_evidence_pack_markdown_route_unknown_unit_is_clean_4xx_not_500(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        f"/api/v1/work-units/{uuid.uuid4()}/evidence-pack/markdown", headers=WORKER
    )

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "work_unit_not_found"


def test_evidence_pack_markdown_route_redacts_approver_identity_and_rationale(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """This markdown is relayed by the runner into a PR comment on the target repo, which may be
    public -- approver identity and free-text reasoning must never reach it. The JSON pack is
    auth-gated and internal (P2.6/audit) and must keep full fidelity: this proves the split."""
    unit_id = _register_ready_unit(db_client, "evidence-pack-redaction")

    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        evidence = Evidence(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://redaction",
            source_revision="abc123",
            recorded_by="worker",
            event_id=uuid.uuid4(),
            idempotency_key="evidence-pack-redaction-evidence",
        )
        session.add(evidence)
        session.flush()
        session.add(
            Adjudication(
                work_package_revision_id=unit.work_package_revision_id,
                work_unit_id=unit.id,
                ac_id="ac-1",
                outcome="waived",
                decided_by="alice-approver",
                rationale="secret reasoning xyz",
                failed_evidence_id=evidence.id,
                risk="medium",
                follow_up="monitor in prod",
                scope="ac-1",
                event_id=uuid.uuid4(),
            )
        )
        session.add(
            Approval(
                subject_type="action",
                subject_id=unit.id,
                subject_revision_or_fingerprint=unit.authority_fingerprint,
                decision="approved",
                approved_by="bob-approver",
                reason="confidential business justification",
                event_id=uuid.uuid4(),
                idempotency_key="evidence-pack-redaction-approval",
            )
        )
        session.commit()

    json_response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)
    markdown_response = db_client.get(
        f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER
    )
    assert json_response.status_code == 200
    assert markdown_response.status_code == 200

    json_body = json_response.json()
    waiver = next(a for a in json_body["adjudications"] if a["outcome"] == "waived")
    assert waiver["decided_by"] == "alice-approver"
    assert waiver["rationale"] == "secret reasoning xyz"
    approval = next(a for a in json_body["approvals"] if a["subject_type"] == "action")
    assert approval["approved_by"] == "bob-approver"
    assert approval["reason"] == "confidential business justification"

    markdown = markdown_response.text
    for leaked in (
        "alice-approver",
        "secret reasoning xyz",
        "bob-approver",
        "confidential business justification",
    ):
        assert leaked not in markdown
    # Outcome/risk are kept -- only identity and rationale are redacted.
    assert "waived" in markdown
    assert "medium" in markdown
    assert "action" in markdown
    assert "approved" in markdown
