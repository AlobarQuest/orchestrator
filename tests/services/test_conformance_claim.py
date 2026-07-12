"""The conformance claim must describe the repository, not the author's memory.

The dispatch gate trusts this claim. Two properties carry that trust and are tested here as
properties, not examples: `status` is green only when nothing is unclean, and `accepted_standards`
comes only from a real waiver source. The second is the one an author could quietly break -- and
the moment `accepted` echoes `touched`, the gate's `touched <= accepted` branch admits everything.
"""

from pathlib import Path

import pytest

from orchestrator.conformance_claim import (
    GREEN,
    NOT_GREEN,
    ScannerUnavailableError,
    compute_conformance_claim,
)

REPO = Path("/does/not/need/to/exist")


def cells(**statuses: str):
    return lambda _path: dict(statuses)


def waivers(*standards: str):
    return lambda _path: frozenset(standards)


def claim(cell_map, waiver_set=waivers()):
    return compute_conformance_claim(REPO, read_cells=cell_map, read_waivers=waiver_set)


def test_standards_touched_is_derived_from_the_repository_not_declared_by_hand() -> None:
    result = claim(cells(project="pass", code="pass", security="pass", infra="not-applicable"))
    assert result.standards_touched == ("code", "project", "security")


def test_a_not_applicable_standard_is_not_touched() -> None:
    assert "infra" not in claim(cells(project="pass", infra="not-applicable")).standards_touched


def test_the_checks_column_is_not_a_standard() -> None:
    assert claim(cells(project="pass", checks="pass")).standards_touched == ("project",)


def test_status_is_green_only_when_every_touched_standard_passes() -> None:
    assert claim(cells(project="pass", code="pass")).status == GREEN
    assert claim(cells(project="pass", code="violation")).status == NOT_GREEN


def test_an_unknown_cell_is_not_green_because_nothing_verified_it() -> None:
    """No checker ran, or the manifest is missing. Absence of a finding is not a pass."""
    assert claim(cells(project="pass", code="unknown")).status == NOT_GREEN


def test_accepted_standards_never_echoes_standards_touched() -> None:
    """AC-002. The named test.

    This is the tautology the dispatch gate's subset branch dies of: if a producer ever sets
    `accepted := touched`, then `touched <= accepted` holds unconditionally and every unit is
    admitted, whatever its real state. A repository with violations and no waivers must come back
    with an EMPTY accepted list -- never a copy of what it touched.
    """
    result = claim(cells(project="violation", code="violation", security="violation"))

    assert result.standards_touched == ("code", "project", "security")
    assert result.accepted_standards == ()
    assert result.accepted_standards != result.standards_touched
    assert result.status == NOT_GREEN


def test_a_standard_is_accepted_only_when_a_real_waiver_source_declares_it() -> None:
    result = claim(cells(project="violation", code="violation"), waivers("code"))
    assert result.accepted_standards == ("code",)


def test_a_waiver_for_an_untouched_standard_is_not_accepted() -> None:
    """A waiver says nothing about a standard this repository does not touch."""
    result = claim(cells(project="pass"), waivers("infra", "security"))
    assert result.accepted_standards == ()


def test_the_claim_renders_the_shape_the_authority_envelope_takes() -> None:
    result = claim(cells(project="pass", code="violation"), waivers("code"))
    assert result.as_authority_conformance() == {
        "standards_touched": ["code", "project"],
        "accepted_standards": ["code"],
        "status": NOT_GREEN,
    }


def test_a_missing_scanner_fails_closed_rather_than_guessing() -> None:
    with pytest.raises(ScannerUnavailableError):
        compute_conformance_claim(
            REPO,
            read_cells=lambda _p: (_ for _ in ()).throw(
                ScannerUnavailableError("portfolio.compliance.build_rows is not importable")
            ),
        )
