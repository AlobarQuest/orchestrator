"""`WorkUnit.context_enrichment` is write-once (WS-P2.12).

What a worker was told is a record of what the unit executed under. The document
is resolved at authoring time and lands inside the decomposition proposal a human
approves, so a path that rewrote it afterwards would mean the stored document is
no longer the one anyone approved -- and nothing downstream would notice, because
the approval is recorded against the authority fingerprint, which the document
deliberately does not enter.

That is the same structural argument `test_authority_write_once.py` makes for the
envelope, and it has the same weakness: an AST scan for `unit.context_enrichment
= ...` is trivially green today and blind to the forms a rewriter would actually
use. So this guard REUSES that module's scanner rather than growing a second copy
of six AST forms. A second copy would drift, and the copy would be the blind one.
"""

from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from orchestrator.persistence.models import WorkUnit
from tests.architecture.test_authority_write_once import _mutations

SRC = Path("src")
ENRICHMENT = "context_enrichment"


def test_the_stored_enrichment_document_is_never_mutated() -> None:
    offenders = _mutations(SRC, ENRICHMENT)

    assert not offenders, (
        "the stored governed-knowledge document is written outside construction:\n  "
        + "\n  ".join(offenders)
        + "\n\nIt is write-once. It records what a worker was told, and a human approved "
        "the decomposition proposal it arrived on. Rewriting it here means the stored "
        "document is no longer the approved one, and nothing downstream can tell -- the "
        "document deliberately does not enter the authority fingerprint. If a unit genuinely "
        "needs different material, that is a new proposal, not an edit."
    )


def test_the_enrichment_column_is_not_mutation_tracked() -> None:
    """The AST guard is only load-bearing while the column is a PLAIN ``JSONB``.

    Wrap it in ``MutableDict.as_mutable(JSONB)`` and an in-place edit through an
    alias persists without naming the attribute at all, defeating every AST form.
    Checked on a transient instance: ``as_mutable`` coerces at attribute-set time,
    so no database or fixture can weaken the assertion.
    """
    unit = WorkUnit(context_enrichment={"schema_version": 1, "rules": []})

    assert type(unit.context_enrichment) is dict, (
        f"WorkUnit.{ENRICHMENT} now coerces to {type(unit.context_enrichment).__name__}, so the "
        "column is mutation-tracked and the AST guard above has gone quietly blind. Either "
        "revert it to plain JSONB, or replace the write-once guarantee with a fail-closed check."
    )


def test_the_pin_would_catch_a_mutation_tracked_column() -> None:
    """The control: without it, the assertion above passes for any plain-dict model."""

    class Base(DeclarativeBase):
        pass

    class TrackedUnit(Base):
        __tablename__ = "planted_tracked_enrichment_unit"

        id: Mapped[int] = mapped_column(primary_key=True)
        context_enrichment: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))

    planted = TrackedUnit(id=1, context_enrichment={"rules": []})

    assert isinstance(planted.context_enrichment, MutableDict)


# -------------------------------------------------------------------------------------------
# The guard must be shown to fail -- on the form a rewriter would most naturally write.
# -------------------------------------------------------------------------------------------


@pytest.fixture
def fake_src(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "orchestrator").mkdir(parents=True)
    return root


def _plant(root: Path, body: str) -> list[str]:
    (root / "orchestrator" / "planted.py").write_text(body, encoding="utf-8")
    return _mutations(root, ENRICHMENT)


def test_the_guard_flags_attribute_assignment(fake_src: Path) -> None:
    offenders = _plant(
        fake_src, "def refresh(unit, document):\n    unit.context_enrichment = document\n"
    )

    assert any("attribute assignment" in offender for offender in offenders), offenders


def test_the_guard_flags_in_place_mutation_of_a_record_list(fake_src: Path) -> None:
    """THE control: re-resolving in place, which a "keep it fresh" change would write."""
    offenders = _plant(
        fake_src, 'def refresh(unit, rules):\n    unit.context_enrichment["rules"] = rules\n'
    )

    assert any("in-place mutation" in offender for offender in offenders), offenders


def test_the_guard_flags_setattr(fake_src: Path) -> None:
    offenders = _plant(
        fake_src,
        'def refresh(unit, document):\n    setattr(unit, "context_enrichment", document)\n',
    )

    assert any("setattr" in offender for offender in offenders), offenders


def test_the_guard_flags_a_bulk_update(fake_src: Path) -> None:
    offenders = _plant(
        fake_src,
        "def refresh(session, document):\n"
        "    session.execute(update(WorkUnit).values(context_enrichment=document))\n",
    )

    assert any("bulk update" in offender for offender in offenders), offenders


def test_the_guard_is_quiet_on_the_construction_site(fake_src: Path) -> None:
    """Assigning it as a kwarg while BUILDING a unit is the one legitimate write."""
    offenders = _plant(
        fake_src,
        "def build(document):\n    return WorkUnit(context_enrichment=document)\n",
    )

    assert not offenders, offenders
