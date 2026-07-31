import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Evidence, PackageAcceptanceCriterion, WorkUnit
from orchestrator.services.evidence import (
    HUMAN_ADJUDICABLE_OUTCOMES,
    current_adjudication,
    current_evidence,
)
from orchestrator.services.verifier_evaluators import human_may_adjudicate
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


def _adjudication_form(page: str, unit_id: object) -> str:
    form = re.search(
        rf'action="/review/units/{unit_id}/adjudication">(.*?)</form>', page, re.DOTALL
    )
    assert form is not None
    return form.group(1)


def _fieldset_legends(page: str, unit_id: object) -> tuple[str, ...]:
    return tuple(re.findall(r"<legend>([^ <]+)", _adjudication_form(page, unit_id)))


def test_the_form_submits_every_open_criterion_at_once(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_two_judgment_acs: WorkUnit
) -> None:
    # AC-010 at the HTTP boundary. One form, one expected_version, one CSRF token, one confirm --
    # and both criteria recorded. Under the per-criterion forms this was two submissions, the
    # second carrying an expected_version fixed before the first one ran.
    unit = review_unit_with_two_judgment_acs
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    assert _fieldset_legends(page.text, unit.id) == ("ac-1", "ac-2")
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": ["ac-1", "ac-2"],
            "outcome": ["passed", "not_applicable"],
            "rationale": ["reviewed and met", "out of scope here"],
            "risk": ["", ""],
            "follow_up": ["", ""],
            "failed_evidence_id": ["", ""],
            "expires_at": ["", ""],
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(migrated_engine) as verify:
        verify.expire_all()
        first = current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1")
        second = current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-2")
        assert first is not None and first.outcome == "passed"
        assert second is not None and second.outcome == "not_applicable"


def test_a_blank_criterion_is_not_written(
    db_client: TestClient, migrated_engine: Engine, review_unit_with_two_judgment_acs: WorkUnit
) -> None:
    # AC-012. Blank is not an outcome. A reviewer who answers one of two criteria must not
    # silently record anything for the other -- which is what a select defaulting to `passed`
    # would do the moment the form submits every criterion together.
    unit = review_unit_with_two_judgment_acs
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    token, key = _form(page.text, unit.id, "adjudication")

    response = db_client.post(
        f"/review/units/{unit.id}/adjudication",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(unit.version),
            "ac_id": ["ac-1", "ac-2"],
            "outcome": ["passed", ""],
            "rationale": ["reviewed and met", ""],
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(migrated_engine) as verify:
        verify.expire_all()
        assert (
            current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1") is not None
        )
        assert current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-2") is None


def test_the_blank_outcome_is_the_default(
    db_client: TestClient, review_unit_with_two_judgment_acs: WorkUnit
) -> None:
    # The companion AC-012 needs: a form whose first option is an outcome records that outcome for
    # every criterion the reviewer never looked at. The no-decision option must be the selected one.
    unit = review_unit_with_two_judgment_acs
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)
    form = re.search(
        rf'action="/review/units/{unit.id}/adjudication">(.*?)</form>', page.text, re.DOTALL
    )
    assert form is not None

    for options in re.findall(r'name="outcome">(.*?)</select>', form.group(1), re.DOTALL):
        first_option = re.search(r"<option[^>]*>", options)
        assert first_option is not None
        assert 'value=""' in first_option.group(0)
        assert "selected" in first_option.group(0)


def test_generated_post_deploy_criteria_are_still_excluded_from_the_form(
    db_client: TestClient, review_unit_with_post_deploy_ac: WorkUnit
) -> None:
    # AC-013. The existing rule must survive the rewrite: generated post-deploy criteria are
    # verifier-owned and public adjudication must reject them. Collapsing N forms into one is
    # exactly the change that could quietly widen the set the form renders.
    unit = review_unit_with_post_deploy_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)

    assert _fieldset_legends(page.text, unit.id) == ("ac-1",)
    # Scoped to the form, not the page: the id legitimately appears elsewhere on a page that
    # renders the package's own metadata. What must not exist is a control that submits it.
    assert 'value="post-deploy-health"' not in _adjudication_form(page.text, unit.id)


def _offered_outcomes(page: str, unit_id: object) -> set[str]:
    form = re.search(
        rf'action="/review/units/{unit_id}/adjudication">(.*?)</form>', page, re.DOTALL
    )
    assert form is not None
    select = re.search(r'name="outcome">(.*?)</select>', form.group(1), re.DOTALL)
    assert select is not None
    options = set(re.findall(r'<option value="([^"]*)"', select.group(1)))
    # The empty option is the ABSENCE of a decision (AC-012), not an outcome, so it is excluded
    # from the offered set rather than compared against what the service accepts. Asserted here so
    # the exclusion cannot quietly become "the no-decision option was dropped".
    assert "" in options
    return options - {""}


def _service_would_accept(migrated_engine: Engine, unit: WorkUnit) -> set[str]:
    """The outcomes `_authorize_outcome` would admit for this criterion, from its own constants."""
    with Session(migrated_engine) as session:
        declared = session.scalar(
            select(PackageAcceptanceCriterion.evidence_type).where(
                PackageAcceptanceCriterion.work_package_revision_id == unit.work_package_revision_id
            )
        )
        evidence = current_evidence(session, unit.work_package_revision_id, unit.id, "ac-1")
        may_decide = human_may_adjudicate(declared, evidence, unit.state)
    # `waived` is authorized for any HUMAN on any criterion, independently of the predicate.
    return ({"waived"} | HUMAN_ADJUDICABLE_OUTCOMES) if may_decide else {"waived"}


@pytest.mark.parametrize("with_evidence", [False, True])
def test_the_form_offers_exactly_the_outcomes_the_service_will_accept(
    db_client: TestClient,
    migrated_engine: Engine,
    review_unit_with_test_ac: WorkUnit,
    with_evidence: bool,
) -> None:
    # THE DIVERGENCE GUARD. Increment 1 moved evaluation onto the floor and left authorization and
    # this form's flag on JUDGMENT_TYPES; that is how a human could be offered an outcome the
    # service would refuse -- or refused one it would accept. Both parameters matter: the same
    # criterion type is offered different outcomes depending only on whether the verifier could
    # resolve it, which a type-keyed flag cannot express.
    unit = review_unit_with_test_ac
    if with_evidence:
        _record_readable_evidence(migrated_engine, unit)

    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)

    assert _offered_outcomes(page.text, unit.id) == _service_would_accept(migrated_engine, unit)


def test_the_form_offers_failed_where_a_human_may_decide(
    db_client: TestClient, review_unit_with_judgment_ac: WorkUnit
) -> None:
    # Spec AC-006 at the surface. The set-equality pin above would stay green if BOTH sides
    # dropped `failed`, so name it directly.
    unit = review_unit_with_judgment_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN)

    assert "failed" in _offered_outcomes(page.text, unit.id)


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
