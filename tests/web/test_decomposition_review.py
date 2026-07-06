import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import DecompositionProposal, WorkPackageRevision
from orchestrator.services.decomposition import submit_decomposition_proposal
from orchestrator.services.package_intake import register_package_intake
from tests.api.test_lifecycle_api import HUMAN
from tests.services.test_decomposition import package_ac_ids, proposal_command, worker_actor
from tests.services.test_package_intake import acceptance_criterion, human_actor, intake_command


def _seed_intake_and_proposal(
    migrated_engine: Engine,
) -> tuple[WorkPackageRevision, DecompositionProposal]:
    suffix = uuid.uuid4().hex
    with Session(migrated_engine) as session:
        revision = register_package_intake(
            session,
            intake_command(
                package_id=f"pkg-web-{suffix}",
                content_hash=f"sha256:{suffix}",
                idempotency_key=f"package-intake-web-{suffix}",
                acceptance_criteria=(
                    acceptance_criterion("AC-001"),
                    acceptance_criterion("AC-002"),
                ),
            ),
            human_actor(),
        )
        proposal = submit_decomposition_proposal(
            session,
            proposal_command(
                revision.id,
                package_ac_ids(session, revision.id),
                idempotency_key=f"proposal-web-{suffix}",
            ),
            worker_actor(),
        )
        session.commit()
        session.refresh(revision)
        session.refresh(proposal)
        session.expunge(revision)
        session.expunge(proposal)
        return revision, proposal


def _form(page: str, proposal_id: object, action: str) -> tuple[str, str]:
    form = re.search(
        rf'action="/review/decomposition-proposals/{proposal_id}/{action}"[\s\S]*?</form>',
        page,
    )
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(0))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(0))
    assert token is not None and key is not None
    return token.group(1), key.group(1)


def test_proposal_page_shows_ac_mapping_and_decision_controls(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    _, proposal = _seed_intake_and_proposal(migrated_engine)

    page = db_client.get(f"/review/decomposition-proposals/{proposal.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "AC-001" in page.text
    assert "retained_package_level" in page.text
    assert "Approve" in page.text
    assert "Require revision" in page.text
    assert "Reject" in page.text
    assert "dispatch" not in page.text.lower()
    assert "merge" not in page.text.lower()


def test_approve_form_requires_human_confirmation_and_records_decision(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    _, proposal = _seed_intake_and_proposal(migrated_engine)
    page = db_client.get(f"/review/decomposition-proposals/{proposal.id}", headers=HUMAN)
    token, key = _form(page.text, proposal.id, "approve")

    denied = db_client.post(
        f"/review/decomposition-proposals/{proposal.id}/approve",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "reason": "Approved for draft activation.",
        },
        follow_redirects=False,
    )

    assert denied.status_code == 403

    approved = db_client.post(
        f"/review/decomposition-proposals/{proposal.id}/approve",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "reason": "Approved for draft activation.",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert approved.status_code == 303
    assert approved.headers["location"] == f"/review/decomposition-proposals/{proposal.id}"
    with Session(migrated_engine) as session:
        current = session.get(DecompositionProposal, proposal.id)
        assert current is not None
        assert current.state == "approved"
        assert current.decision_reason == "Approved for draft activation."

    detail = db_client.get(f"/review/decomposition-proposals/{proposal.id}", headers=HUMAN)
    assert "Approved for draft activation." in detail.text
    assert "State: approved" in detail.text
    assert "No further review action is available" in detail.text
    assert f'action="/review/decomposition-proposals/{proposal.id}/approve"' not in detail.text


def test_intake_page_is_read_only_and_shows_source_facts(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    revision, _ = _seed_intake_and_proposal(migrated_engine)

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "Source commit" in page.text
    assert "Authority fingerprint" in page.text
    assert revision.content_hash in page.text
    assert revision.source_path in page.text
    assert "AC-001" in page.text
    assert "The change is tested." in page.text
    assert "<form" not in page.text


def test_reject_and_require_revision_forms_record_decisions(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    _, reject_proposal = _seed_intake_and_proposal(migrated_engine)
    reject_page = db_client.get(
        f"/review/decomposition-proposals/{reject_proposal.id}",
        headers=HUMAN,
    )
    reject_token, reject_key = _form(reject_page.text, reject_proposal.id, "reject")

    rejected = db_client.post(
        f"/review/decomposition-proposals/{reject_proposal.id}/reject",
        headers=HUMAN,
        data={
            "csrf_token": reject_token,
            "idempotency_key": reject_key,
            "reason": "The unit split is too broad.",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert rejected.status_code == 303
    with Session(migrated_engine) as session:
        current = session.get(DecompositionProposal, reject_proposal.id)
        assert current is not None
        assert current.state == "rejected"
        assert current.decision_reason == "The unit split is too broad."

    _, revision_proposal = _seed_intake_and_proposal(migrated_engine)
    revision_page = db_client.get(
        f"/review/decomposition-proposals/{revision_proposal.id}",
        headers=HUMAN,
    )
    revision_token, revision_key = _form(
        revision_page.text,
        revision_proposal.id,
        "require-revision",
    )

    required = db_client.post(
        f"/review/decomposition-proposals/{revision_proposal.id}/require-revision",
        headers=HUMAN,
        data={
            "csrf_token": revision_token,
            "idempotency_key": revision_key,
            "reason": "Map the retained criterion explicitly.",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert required.status_code == 303
    with Session(migrated_engine) as session:
        current = session.get(DecompositionProposal, revision_proposal.id)
        assert current is not None
        assert current.state == "revision_required"
        assert current.decision_reason == "Map the retained criterion explicitly."
