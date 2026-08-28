import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Event,
    EventPublication,
    Evidence,
)
from orchestrator.services.budget import BREACH_ACTION
from orchestrator.services.evidence_pack import (
    evidence_pack_projection,
    evidence_pack_response,
    render_evidence_pack_markdown,
)
from tests.services.test_slo_report import _add_event, _build_unit


def test_projection_assembles_core_facts(migrated_session: Session) -> None:
    _revision, unit = _build_unit(migrated_session, "evidence-pack-projection")
    evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://result",
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-projection-evidence",
    )
    migrated_session.add(evidence)
    migrated_session.commit()

    pack = evidence_pack_projection(migrated_session, unit.id)

    assert set(pack) == {
        "unit",
        "authority",
        "authority_violation",
        "revision",
        "dependencies",
        "claims",
        "evidence",
        "current_evidence_ids",
        "adjudications",
        "current_adjudication_ids",
        "verifier_decided_completion",
        "approvals",
        "events",
        "event_publications",
    }
    assert pack["unit"].id == unit.id
    assert [row.id for row in pack["evidence"]] == [evidence.id]
    assert pack["current_evidence_ids"] == {evidence.id}


def test_render_markdown_includes_every_section_and_key_facts(migrated_session: Session) -> None:
    _revision, unit = _build_unit(migrated_session, "evidence-pack-markdown")
    evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://result",
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-markdown-evidence",
    )
    migrated_session.add(evidence)
    migrated_session.flush()
    adjudication = Adjudication(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="passed",
        decided_by="human",
        rationale="looks correct",
        event_id=uuid.uuid4(),
    )
    migrated_session.add(adjudication)
    migrated_session.commit()

    pack = evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    markdown = render_evidence_pack_markdown(pack)

    assert isinstance(markdown, str)
    assert unit.authority_fingerprint in markdown
    assert "ac-1" in markdown
    assert "passed" in markdown
    # The adjudication's rationale is redacted from the markdown (the approver identity here,
    # "human", is not distinctive enough to assert against -- it collides with the unrelated,
    # intentionally-kept provenance `registered_by` value; see
    # test_render_markdown_redacts_approver_identity_and_rationale for the full identity split).
    assert "looks correct" not in markdown
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
        assert header in markdown


def test_render_markdown_redacts_approver_identity_and_rationale(
    migrated_session: Session,
) -> None:
    """The markdown is relayed as a PR comment on the (possibly public) target repo -- approver
    identities and free-text reasoning must never appear in it. The JSON pack is auth-gated and
    internal, and must keep full fidelity: this is the split the fix exists to prove."""
    revision, unit = _build_unit(migrated_session, "evidence-pack-redaction")
    evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://result",
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-redaction-evidence",
    )
    migrated_session.add(evidence)
    migrated_session.flush()
    migrated_session.add(
        Adjudication(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            outcome="waived",
            decided_by="devon",
            rationale="secret reasoning xyz",
            failed_evidence_id=evidence.id,
            risk="medium",
            follow_up="monitor in prod",
            scope="ac-1",
            event_id=uuid.uuid4(),
        )
    )
    migrated_session.add(
        Approval(
            subject_type="authority",
            subject_id=unit.id,
            subject_revision_or_fingerprint=unit.authority_fingerprint,
            decision="approved",
            approved_by="alice-approver",
            reason="confidential business justification",
            event_id=uuid.uuid4(),
            idempotency_key="evidence-pack-redaction-approval",
        )
    )
    migrated_session.commit()

    pack = evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    markdown = render_evidence_pack_markdown(pack)

    # Redacted in markdown -- the surface that may be posted to a public repo.
    for leaked in ("devon", "secret reasoning xyz", "alice-approver", "confidential business"):
        assert leaked not in markdown
    # Kept in markdown -- these are not identity/rationale.
    assert "waived" in markdown
    assert "medium" in markdown
    assert "authority" in markdown
    assert "approved" in markdown

    # Full fidelity retained on the JSON path.
    waiver = next(a for a in pack.adjudications if a.outcome == "waived")
    assert waiver.decided_by == "devon"
    assert waiver.rationale == "secret reasoning xyz"
    approval = next(a for a in pack.approvals if a.subject_type == "authority")
    assert approval.approved_by == "alice-approver"
    assert approval.reason == "confidential business justification"


def test_render_markdown_carries_the_deciding_role_but_not_the_evidence_row_id(
    migrated_session: Session,
) -> None:
    """WS-P3.7's two markdown decisions, made rather than inherited from the redaction rule above.

    A ROLE is a kind, not an identity -- "human" names no person -- and it is the fact a reader of
    a possibly-public pull request most needs, so it is rendered. An `evidence_id` is an internal
    row identifier that resolves to nothing outside the orchestrator, so it stays on the JSON path.
    """
    _revision, unit = _build_unit(migrated_session, "evidence-pack-role-markdown")
    evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://result",
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-role-markdown-evidence",
    )
    migrated_session.add(evidence)
    migrated_session.flush()
    migrated_session.add(
        Adjudication(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            outcome="passed",
            decided_by="devon",
            decided_by_role="human",
            evidence_id=evidence.id,
            rationale="reviewed",
            event_id=uuid.uuid4(),
        )
    )
    migrated_session.commit()

    pack = evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    markdown = render_evidence_pack_markdown(pack)

    assert "decided by the human role" in markdown
    assert str(evidence.id) not in markdown
    assert (
        "Every required criterion decided by the verifier from its own evaluation: no" in markdown
    )
    assert "ac-1: decider_was_not_the_verifier" in markdown

    decision = next(row for row in pack.adjudications if row.ac_id == "ac-1")
    assert decision.evidence_id == evidence.id


def test_render_markdown_reports_an_unrecorded_decider_as_unrecorded(
    migrated_session: Session,
) -> None:
    """A historical row carries NULL, and the comment must say so rather than leave a blank that
    reads as machine-decided."""
    _revision, unit = _build_unit(migrated_session, "evidence-pack-role-null")
    migrated_session.add(
        Adjudication(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            outcome="passed",
            decided_by="whoever",
            rationale="recorded before the column existed",
            event_id=uuid.uuid4(),
        )
    )
    migrated_session.commit()

    markdown = render_evidence_pack_markdown(
        evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    )

    assert "decided by the unrecorded role" in markdown
    assert "ac-1: decider_kind_unrecorded" in markdown


def test_render_markdown_keeps_payload_out_of_a_row_that_has_a_reference(
    migrated_session: Session,
) -> None:
    """WS-P2.17 Increment 4 made the `/review` HTML render `stable_ref` AND payload, because that
    page is human-only behind forward-auth. This markdown is posted as a comment on the target
    repository, which may be PUBLIC -- the same edit here would widen what leaves the system. The
    two renderers have different rules on purpose; this pins the markdown side."""
    _revision, unit = _build_unit(migrated_session, "evidence-pack-markdown-payload")
    migrated_session.add(
        Evidence(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://public-ref",
            payload={"detail": "internal-only payload zzz"},
            source_revision="abc123",
            recorded_by="worker",
            event_id=uuid.uuid4(),
            idempotency_key="evidence-pack-markdown-payload-evidence",
        )
    )
    migrated_session.commit()

    pack = evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    markdown = render_evidence_pack_markdown(pack)

    assert "artifact://public-ref" in markdown
    assert "internal-only payload zzz" not in markdown


def _table_data_rows(markdown: str, section_header: str, row_marker: str) -> list[str]:
    """Pull the data rows (excluding the header/separator rows) of the table under
    `section_header` whose text contains `row_marker`, without assuming where the table ends."""
    start = markdown.index(section_header)
    section = markdown[start:]
    return [
        line
        for line in section.splitlines()
        if line.startswith("|") and row_marker in line and "---" not in line
    ]


def test_render_markdown_escapes_pipe_and_newline_in_table_cells(
    migrated_session: Session,
) -> None:
    """A literal `|` or newline in evidence/last-error free text must not shift table columns
    or break out of the row -- this is the WS-P2.5 review defect: this markdown is posted
    verbatim as a PR comment, and unescaped values corrupt the rendered table."""
    _revision, unit = _build_unit(migrated_session, "evidence-pack-escaping")
    # stable_ref is interpolated directly -- a raw newline here would truly break the row.
    ref_evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://a|b\nc",
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-escaping-ref",
    )
    # payload is only rendered when stable_ref is None -- exercise that branch too.
    payload_evidence = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-2",
        attempt=1,
        evidence_type="test",
        stable_ref=None,
        payload={"detail": "line1|line2"},
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-pack-escaping-payload",
    )
    migrated_session.add_all([ref_evidence, payload_evidence])
    migrated_session.flush()
    migrated_session.add(
        EventPublication(
            source_kind="evidence",
            source_id=ref_evidence.id,
            source_action="evidence.recorded",
            event_id="evt-" + "b" * 64,
            mapping_version="ws34.v1",
            status="failed",
            last_error="boom: pipe|delimited\nmulti-line traceback",
        )
    )
    migrated_session.commit()

    pack = evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    markdown = render_evidence_pack_markdown(pack)

    # Escaped values render verbatim -- `\|` in place of the literal pipe, a space in place
    # of the newline -- and the un-escaped originals never reappear.
    assert "artifact://a\\|b c" in markdown
    assert "artifact://a|b\nc" not in markdown
    assert "line1\\|line2" in markdown
    assert "boom: pipe\\|delimited multi-line traceback" in markdown
    assert "boom: pipe|delimited\nmulti-line traceback" not in markdown

    # Both tables' data rows keep exactly 5 columns (6 unescaped pipes) -- proving the
    # offending cell's `|`/newline did not shift or split the row.
    for section_header, row_marker in (
        ("## AC-keyed evidence, including supersession", "artifact://a"),
        ("## AC-keyed evidence, including supersession", "line1"),
        ("## Event publications", "boom: pipe"),
    ):
        data_rows = _table_data_rows(markdown, section_header, row_marker)
        assert data_rows, f"expected a data row containing {row_marker!r}"
        for row in data_rows:
            assert "\n" not in row
            unescaped_pipe_count = len(re.findall(r"(?<!\\)\|", row))
            assert unescaped_pipe_count == 6, row


def test_render_markdown_surfaces_a_recorded_budget_breach(migrated_session: Session) -> None:
    """The pack is where a human and a possibly-public PR comment learn a unit overran.

    The breach reaches the pack through the event-history section's `reason`, which is why the
    emitter writes `reason: budget_exceeded` rather than only the two counts.
    """
    _revision, unit = _build_unit(migrated_session, "evidence-pack-budget-breach")
    _add_event(
        migrated_session,
        unit.id,
        action=BREACH_ACTION,
        to_state=None,
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
        reason="budget_exceeded",
    )
    migrated_session.commit()

    markdown = render_evidence_pack_markdown(
        evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    )

    assert BREACH_ACTION in markdown
    assert "Reason: budget_exceeded" in markdown


def test_render_markdown_omits_a_breach_when_none_was_recorded(migrated_session: Session) -> None:
    """Control for the test above: the section renders for every unit, so a substring assertion
    that never fails would look identical."""
    _revision, unit = _build_unit(migrated_session, "evidence-pack-no-breach")
    migrated_session.commit()

    markdown = render_evidence_pack_markdown(
        evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    )

    assert BREACH_ACTION not in markdown


def test_a_change_window_override_reaches_the_json_and_is_not_quoted_in_the_markdown(
    migrated_session: Session,
) -> None:
    """ADR-0032, and the asymmetry is deliberate rather than an omission.

    The JSON is authenticated and full-fidelity. The markdown is relayed onto a pull request
    comment that may be public, so it reports that an override happened and never the operator's
    own words -- the same hand-redaction every other free-text field in that renderer gets.
    """
    _revision, unit = _build_unit(migrated_session, "evidence-pack-override")
    words = "supervised build session, watched throughout"
    # Written at INSERT: `events` is append-only at the database level, so a payload set
    # afterwards is refused by the trigger rather than stored.
    _override_event(
        migrated_session,
        unit.id,
        {
            "reason": words,
            "applied": True,
            "authority_approval_id": str(uuid.uuid4()),
            "authority_fingerprint": unit.authority_fingerprint,
        },
    )

    pack = evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    markdown = render_evidence_pack_markdown(pack)

    carried = [
        row.change_window_override for row in pack.events if row.change_window_override is not None
    ]
    assert [override["reason"] for override in carried] == [words]
    assert "Change window overridden" in markdown
    assert words not in markdown


def test_an_override_the_window_never_needed_is_reported_as_carried(
    migrated_session: Session,
) -> None:
    """The control for the line above: the two states are distinguishable in the markdown, so a
    reader cannot infer a suppression from an override having been offered."""
    _revision, unit = _build_unit(migrated_session, "evidence-pack-override-carried")
    _override_event(migrated_session, unit.id, {"reason": "watched", "applied": False})

    markdown = render_evidence_pack_markdown(
        evidence_pack_response(evidence_pack_projection(migrated_session, unit.id))
    )

    assert "Change window override carried, not applied" in markdown


def _override_event(session: Session, unit_id: uuid.UUID, override: dict[str, object]) -> Event:
    event = Event(
        occurred_at=datetime(2026, 8, 26, 13, 50, tzinfo=UTC),
        actor_id="orchestrator-system",
        action="dispatch.dispatched",
        subject_type="work_unit",
        subject_id=unit_id,
        from_state="ready",
        to_state="ready",
        payload={"change_window_override": override},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"override-evt-{uuid.uuid4()}",
    )
    session.add(event)
    session.commit()
    return event
