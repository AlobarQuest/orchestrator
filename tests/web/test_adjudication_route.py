import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Evidence, WorkUnit
from orchestrator.services.evidence import current_adjudication
from tests.api.test_lifecycle_api import HUMAN


def _record_readable_evidence(migrated_engine: Engine, unit: WorkUnit) -> None:
    """Evidence the verifier can read, so the criterion resolves deterministically."""
    with Session(migrated_engine) as session:
        session.add(
            Evidence(
                work_package_revision_id=unit.work_package_revision_id,
                work_unit_id=unit.id,
                ac_id="ac-1",
                attempt=1,
                evidence_type="pytest",
                payload={"status": "pass"},
                source_revision="abc1234",
                recorded_by="worker-1",
                event_id=uuid.uuid4(),
                idempotency_key=f"evidence-{unit.id}-ac-1",
            )
        )
        session.commit()


def _form(page: str, unit_id: object, action: str) -> tuple[str, str]:
    # DOTALL: unlike the single-line forms in test_human_actions.py, the per-AC adjudication
    # form spans multiple lines in the template.
    form = re.search(rf'action="/review/units/{unit_id}/{action}">(.*?)</form>', page, re.DOTALL)
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and key is not None
    return token.group(1), key.group(1)


def test_human_pass_via_review_route_persists(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_judgment_ac: WorkUnit
) -> None:
    unit = review_unit_with_judgment_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    assert "Adjudicate acceptance criteria" in page.text
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": "ac-1",
            "outcome": "passed",
            "rationale": "reviewed and met",
            # A real browser submits empty optional inputs as "" -- prove that normalizes to
            # None rather than tripping the unconditional risk-class check in record_adjudication.
            "risk": "",
            "follow_up": "",
            "failed_evidence_id": "",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/review/units/{unit.id}"

    with Session(migrated_engine) as verify:
        verify.expire_all()
        row = current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1")
        assert row is not None
        assert row.outcome == "passed"
        assert row.decided_by  # a human actor id

    detail = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    assert "ac-1: passed" in detail.text


def test_human_pass_of_a_resolved_deterministic_ac_is_rejected(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_test_ac: WorkUnit
) -> None:
    # Supersedes `test_human_pass_of_deterministic_ac_is_rejected`, whose intent survives but whose
    # SETUP no longer expressed it. It relied on the criterion's declared type alone, and the
    # fixture unit is in AWAITING_REVIEW with NO evidence -- which, under this increment's clause
    # (b), is precisely the case a human now MAY decide: the verifier ran, could not resolve, and
    # handed off, so refusing the human would leave the criterion decidable by no actor at all.
    # What must still be refused is a human pre-empting a verifier result that EXISTS, so this
    # writes readable evidence first. See the clause-(b) companion below.
    unit = review_unit_with_test_ac
    _record_readable_evidence(migrated_engine, unit)
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": "ac-1",
            "outcome": "passed",
            "rationale": "x",
            "confirm": "yes",
        },
    )

    assert response.status_code >= 400
    with Session(migrated_engine) as verify:
        verify.expire_all()
        assert current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1") is None


def test_human_pass_of_an_unresolved_deterministic_ac_is_accepted(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_test_ac: WorkUnit
) -> None:
    # Clause (b) at the route. The companion to the refusal above: same criterion type, same unit
    # state, no evidence. Without this pairing, the refusal test would pass just as well under a
    # rule that refuses ALL deterministic types -- which is the rule this increment replaces.
    unit = review_unit_with_test_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": "ac-1",
            "outcome": "passed",
            "rationale": "the suite never reported; verified by hand",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(migrated_engine) as verify:
        verify.expire_all()
        row = current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1")
        assert row is not None
        assert row.outcome == "passed"


def test_naive_expires_at_is_rejected_cleanly(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_judgment_ac: WorkUnit
) -> None:
    # Regression: a timezone-naive expires_at (what an HTML date/datetime-local input emits,
    # e.g. "2027-06-01") used to flow uncaught into the service layer, where a naive/aware
    # datetime comparison raises TypeError -- an unhandled 500. _parse_optional_datetime must
    # reject it as a clean DomainError (mapped to 409) before it ever reaches the service.
    unit = review_unit_with_judgment_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": "ac-1",
            "outcome": "passed",
            "rationale": "reviewed and met",
            "risk": "",
            "follow_up": "",
            "failed_evidence_id": "",
            "expires_at": "2027-06-01",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert 400 <= response.status_code < 500

    with Session(migrated_engine) as verify:
        verify.expire_all()
        assert current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1") is None


def test_aware_expires_at_is_accepted(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_judgment_ac: WorkUnit
) -> None:
    # Companion to the naive-rejection test above: the fix must reject only naive datetimes,
    # not expiries in general.
    unit = review_unit_with_judgment_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": "ac-1",
            "outcome": "passed",
            "rationale": "reviewed and met",
            "risk": "",
            "follow_up": "",
            "failed_evidence_id": "",
            "expires_at": "2027-06-01T00:00:00+00:00",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    with Session(migrated_engine) as verify:
        verify.expire_all()
        row = current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1")
        assert row is not None
        assert row.outcome == "passed"
