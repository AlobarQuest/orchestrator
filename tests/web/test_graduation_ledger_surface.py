"""The graduation ledger's caller: the human gate itself (WS-P2.18 Inc 8, §3.2).

Increments 6 and 7 both stopped on *name the caller and when it runs*. This is it — the unit
page, rendered every time Devon opens a unit whose authority envelope he is being asked to clear.
The evidence arrives at the moment of the decision it informs, and it has a reader on day one.

Both directions are proven: the section renders where the authority-approval control does, and it
is absent where that control is, because a ledger on a settled unit is a verdict on finished work
rather than support for a decision. And the page must never read as a recommendation — it says in
words that nothing is suppressed by it.
"""

from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Approval, Event, WorkUnit
from tests.api.test_lifecycle_api import HUMAN
from tests.web.conftest import _review_unit

LEDGER_HEADING = "How comparable envelopes have gone"


def ledger_section(page: str) -> str:
    """Only the ledger's own markup, from its heading to the next one.

    A page-wide assertion would not discriminate: the unit page already prints capabilities, the
    change class and unit keys elsewhere, so "the repository name appears somewhere" passes with
    no ledger rendered at all.
    """
    match = re.search(rf"<h3>{LEDGER_HEADING}</h3>[\s\S]*?(?=<h3>)", page)
    assert match is not None, "no graduation ledger was rendered"
    return match.group(0)


def peer(migrated_engine: Engine, *, key: str, state: str, failed: bool) -> WorkUnit:
    """A comparable envelope somebody already cleared, parked in a terminal state.

    `_review_unit` records the authority approval and carries the shared test envelope, so a peer
    built from it is comparable to `review_unit` by construction.
    """
    unit = _review_unit(migrated_engine, unit_key=key)
    with Session(migrated_engine) as session:
        stored = session.get(WorkUnit, unit.id)
        assert stored is not None
        stored.state = state
        if failed:
            session.add(
                Event(
                    actor_id="orchestrator-system",
                    action="work_unit.transitioned",
                    subject_type="work_unit",
                    subject_id=stored.id,
                    from_state=WorkUnitState.EXECUTING,
                    to_state=WorkUnitState.FAILED,
                    payload={"reason": "obsolete allowed_commands"},
                    correlation_id=uuid.uuid4(),
                    idempotency_key=str(uuid.uuid4()),
                )
            )
        session.commit()
    return unit


def test_the_gate_shows_what_happened_the_last_times_this_shape_was_cleared(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    peer(migrated_engine, key="cleared-clean", state=WorkUnitState.COMPLETED, failed=False)
    peer(migrated_engine, key="cleared-badly", state=WorkUnitState.CANCELLED, failed=True)

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    section = ledger_section(page.text)
    assert "2 envelope(s) of this shape" in section
    assert "1 clean" in section
    assert "cleared-clean" in section
    assert "cleared-badly" in section
    # The reason is what makes a row informative, and it is reported unclassified.
    assert "obsolete allowed_commands" in section
    # It must not read as a recommendation.
    assert "This is a record, not a recommendation." in section
    assert "factory-policy.toml" in section


def test_a_shape_with_no_history_says_so_rather_than_rendering_an_empty_table(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    section = ledger_section(page.text)
    assert "No comparable envelope has ever cleared this gate" in section
    assert "<table>" not in section


def test_a_settled_unit_gets_no_ledger_because_there_is_no_decision_to_support(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    peer(migrated_engine, key="cleared-clean", state=WorkUnitState.COMPLETED, failed=False)
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.COMPLETED
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    assert LEDGER_HEADING not in page.text
    assert "Approve this authority envelope" not in page.text


def test_the_rendered_page_names_no_approver(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    """ADR-0014 at the surface: the contaminated column must not reach the page THROUGH THE LEDGER.

    Scoped to the ledger's own markup, and deliberately so. The unit page has always shown the
    approver of the subject unit's own approvals in its approvals section, and that is a different
    record answering a different question -- ADR-0014 governs what graduation evidence is drawn
    from, not what a unit's own audit trail displays.

    The rewrite is to a value that appears nowhere else, so a hit is unambiguous, and the
    page-wide assertion is the POSITIVE control: it proves the rewrite landed and that this page
    renders such a name readily, so the section's silence is the ledger's doing.
    """
    peer(migrated_engine, key="cleared-clean", state=WorkUnitState.COMPLETED, failed=False)
    with Session(migrated_engine) as session:
        for approval in session.query(Approval).all():
            approval.approved_by = "unmistakable-approver-name"
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    section = ledger_section(page.text)
    assert "cleared-clean" in section  # the ledger really did render
    assert "unmistakable-approver-name" in page.text  # control: the page does render this name
    assert "unmistakable-approver-name" not in section
