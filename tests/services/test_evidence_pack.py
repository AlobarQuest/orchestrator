import re
import uuid

from sqlalchemy.orm import Session

from orchestrator.persistence.models import Adjudication, Approval, EventPublication, Evidence
from orchestrator.services.evidence_pack import (
    evidence_pack_projection,
    evidence_pack_response,
    render_evidence_pack_markdown,
)
from tests.services.test_slo_report import _build_unit


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
