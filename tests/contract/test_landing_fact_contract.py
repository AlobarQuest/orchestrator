"""The landing ledger's fact vocabulary, pinned to the orchestrator's transcription of it.

`services/machine_activation.py` reads a landing commit out of `facts.what_changed`, which is
written by `landing_ledger/record.py::what_changed`. The two live in DIFFERENT PROGRAMS on
purpose -- `src/orchestrator` imports nothing from `src/landing_ledger` and an architecture test
enforces that -- so nothing in either module can notice if one side renames a key.

THE FAILURE WOULD BE SILENT AND FLATTERING. A renamed key makes every candidate incomplete, which
the reader skips as an ordinary push-with-no-pull-request row; the candidate stream simply empties
and the producer reports a clean pass over nothing. Fail-closed, and invisible.

Tests are not confined by the isolation guards -- they may import both sides, which is exactly
what makes this the right place for the pin.
"""

from datetime import UTC, datetime

from landing_ledger.model import Landing
from landing_ledger.record import (
    OBSERVATION_TYPE,
    SOURCE_SYSTEM,
    SUBJECT_TYPE,
    what_changed,
)
from orchestrator.services import machine_activation

LANDING = Landing(
    repository="AlobarQuest/infraops-mcp-server",
    base_ref="main",
    commit="ac01f838fdc96e2ce3916f5a2601d3e9c232c064",
    landed_at=datetime(2026, 8, 19, 21, 34, 18, tzinfo=UTC),
    title="feat: implement SDS unit",
    files=("package.json", "package-lock.json"),
    files_changed=2,
    pull_request=81,
    head_commit="fcc4f8811b51ea74293b79e16ddabc4250d00b41",
    landed_by="AlobarQuest",
)


def test_the_orchestrator_reads_the_keys_the_ledger_writes() -> None:
    """The four values a candidate is composed from, asserted against the writer's own output."""
    written = what_changed(LANDING)

    assert machine_activation.LANDING_FACTS_KEY == "what_changed"
    for key in (
        machine_activation.LANDING_REPOSITORY,
        machine_activation.LANDING_PULL_REQUEST,
        machine_activation.LANDING_HEAD_COMMIT,
        machine_activation.LANDING_COMMIT,
    ):
        assert key in written, f"the landing ledger no longer writes {key}"


def test_the_orchestrator_selects_the_rows_the_ledger_files() -> None:
    """The three coordinates the query filters on, read from the ledger's own constants."""
    assert machine_activation.LANDING_SOURCE_SYSTEM == SOURCE_SYSTEM
    assert machine_activation.LANDING_SUBJECT_TYPE == SUBJECT_TYPE
    assert machine_activation.LANDING_OBSERVATION_TYPE == OBSERVATION_TYPE


def test_a_real_ledger_row_yields_the_landing_the_orchestrator_needs() -> None:
    """The pin above is about NAMES; this is about the row actually parsing.

    A key that survives a rename but changes TYPE -- a string pull-request number, say -- would
    pass every assertion above and be skipped as incomplete by the reader. So the writer's own
    output is put through the reader.
    """
    facts = {"what_changed": what_changed(LANDING)}

    parsed = machine_activation._landing_of(facts, LANDING.repository.lower())

    assert parsed is not None
    pull_request, landing = parsed
    assert pull_request == LANDING.pull_request
    assert landing.head_commit == LANDING.head_commit
    assert landing.commit == LANDING.commit
