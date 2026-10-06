"""The unit page puts the deciding view before the auditing view (SDS 1.1, 4c-3)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN

AUDIT_OPEN = '<details class="audit">'
AUDIT_HEADINGS = (
    "<h2>Dependencies</h2>",
    "<h2>Claims and lease status</h2>",
    "<h2>Evidence</h2>",
    "<h2>Adjudications and waivers</h2>",
    "<h2>Approvals</h2>",
    "<h2>Event history</h2>",
)


def _offsets(page: str, needle: str) -> list[int]:
    offsets, start = [], page.find(needle)
    while start != -1:
        offsets.append(start)
        start = page.find(needle, start + 1)
    return offsets


def test_the_forms_follow_the_decision_facts_and_precede_the_collapsed_audit_record(
    db_client: TestClient, review_unit_with_judgment_ac: WorkUnit
) -> None:
    unit = review_unit_with_judgment_ac
    page = db_client.get(f"/review/units/{unit.id}", headers=HUMAN).text

    decision = page.index('<section class="decision"')
    actions = page.index("<h2>Human actions</h2>")
    assert page.count(AUDIT_OPEN) == 1
    audit = page.index(AUDIT_OPEN)
    audit_end = page.index("</details>", audit)
    assert decision < actions < audit

    # An awaiting-review unit with a judgment criterion offers several forms, the adjudication form
    # among them; every one sits between the actions heading and the audit record.
    forms = _offsets(page, '<form method="post"')
    assert f'action="/review/units/{unit.id}/adjudication"' in page
    assert len(forms) >= 3
    assert all(actions < form < audit for form in forms)

    # The authority envelope and its constraints stay beside the forms that attest them.
    assert decision < page.index("<h3>Authority envelope</h3>") < actions

    # Every long audit section is inside the collapsed element, and the element names the
    # Evidence Pack as the audit view.
    for heading in AUDIT_HEADINGS:
        assert audit < page.index(heading) < audit_end, heading
    audit_record = page[audit:audit_end]
    assert f'href="/review/units/{unit.id}/evidence-pack"' in audit_record
    assert "<summary>" in audit_record


@pytest.mark.parametrize("state", list(WorkUnitState))
@pytest.mark.parametrize("attempts_exhausted", [False, True])
def test_no_state_renders_a_form_below_the_audit_record(
    db_client: TestClient,
    migrated_engine: Engine,
    review_unit: WorkUnit,
    state: WorkUnitState,
    attempts_exhausted: bool,
) -> None:
    # Each form renders only in the states whose service would accept it, so the one-state test
    # above sees only some of them. This one checks, in each state, that whatever forms render sit
    # above the audit record, and that a state with none says so rather than rendering nothing.
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = state
        unit.attempt_count = unit.max_attempts if attempts_exhausted else 0
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN).text

    actions = page.index("<h2>Human actions</h2>")
    audit = page.index(AUDIT_OPEN)
    forms = _offsets(page, '<form method="post"')
    assert forms or "No action is available" in page
    assert all(actions < form < audit for form in forms)


def test_the_reconciliation_resolution_form_precedes_the_audit_record(
    db_client: TestClient, flagged_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{flagged_unit.id}", headers=HUMAN).text

    actions = page.index("<h2>Human actions</h2>")
    audit = page.index(AUDIT_OPEN)
    conditions = _offsets(page, 'action="/review/reconciliation/conditions/')
    assert len(conditions) == 1
    assert actions < conditions[0] < audit
