"""The conformance claim must describe the repository, not the author's memory.

The admission gate trusts this claim, and it has no second line of defence behind it. Two
properties carry that trust: `status` is green only when the matrix has no violation anywhere,
and `accepted_standards` is the matrix's own `accepted-exception` verdict -- never a count of
exception entries in a manifest.

That distinction is the whole feature. An earlier revision of this module harvested
`entry["standard"]` from every exception in the manifest, ignoring the entry's fnmatch scope, its
expiry, and whether it matched anything at all. A mature repository carrying three narrow,
long-expired exceptions then reported `accepted_standards == standards_touched`, the gate's
subset branch admitted it unconditionally, and a repository with live BLOCK findings would have
dispatched. An adversarial review demonstrated it. The tests below exist to keep it dead.
"""

from pathlib import Path

import pytest

from orchestrator.conformance_claim import (
    ACCEPTED,
    GREEN,
    NOT_GREEN,
    ScannerUnavailableError,
    compute_conformance_claim,
    read_compliance_cells,
)

REPO = Path("/does/not/need/to/exist")


def claim(**cells: str):
    return compute_conformance_claim(REPO, read_cells=lambda _path: dict(cells))


def test_standards_touched_is_derived_from_the_matrix_not_declared_by_hand() -> None:
    result = claim(project="pass", code="pass", security="pass", infra="not-applicable")
    assert result.standards_touched == ("code", "project", "security")


def test_a_not_applicable_standard_is_not_touched() -> None:
    assert "infra" not in claim(project="pass", infra="not-applicable").standards_touched


def test_the_checks_column_is_not_a_standard() -> None:
    assert claim(project="pass", checks="pass").standards_touched == ("project",)


def test_status_is_green_only_when_no_cell_is_a_violation() -> None:
    assert claim(project="pass", code="pass").status == GREEN
    assert claim(project="pass", code="violation").status == NOT_GREEN


def test_a_checks_violation_is_not_green_even_though_checks_is_not_a_standard() -> None:
    """The named checks are not wired.

    `checks` never appears in `standards_touched`, so an earlier revision computed cleanliness
    over touched standards only and called this repository green -- while the scanner that judges
    it exits 1. In this factory that is the violation that matters most: a unit's acceptance
    evidence is carried by named checks on its pull-request head.
    """
    result = claim(project="pass", code="pass", checks="violation")

    assert "checks" not in result.standards_touched
    assert result.status == NOT_GREEN


def test_an_unknown_cell_is_not_green_because_nothing_verified_it() -> None:
    assert claim(project="pass", code="unknown").status == NOT_GREEN


def test_accepted_standards_is_the_matrix_verdict_not_a_count_of_exception_entries() -> None:
    """A standard is accepted only when the matrix resolved its findings as covered."""
    result = claim(project="pass", code=ACCEPTED)

    assert result.accepted_standards == ("code",)
    assert result.status == GREEN


def test_a_standard_with_a_live_violation_is_never_accepted() -> None:
    """AC-002. The named test, and the regression that killed the first implementation.

    An expired, narrow, or stale exception leaves its standard's cell at `violation` -- the matrix
    has already decided the exception does not cover it. Acceptance is read from that resolution
    and from nothing else, so no quantity of exception entries in a manifest can launder a live
    violation into `accepted_standards`.
    """
    result = claim(project="violation", code="violation", security="violation")

    assert result.standards_touched == ("code", "project", "security")
    assert result.accepted_standards == ()
    assert result.status == NOT_GREEN


def test_accepted_can_equal_touched_only_when_the_matrix_says_the_repository_is_clean() -> None:
    """The gate's subset branch (`touched <= accepted`) admits unconditionally.

    So `accepted == touched` must be reachable ONLY from a repository the scanner itself judges
    compliant. Here every standard resolved to `accepted-exception`, which is not a violation --
    the claim is green on its own, and the subset branch is redundant rather than a loophole.
    """
    result = claim(project=ACCEPTED, code=ACCEPTED, security=ACCEPTED)

    assert result.accepted_standards == result.standards_touched
    assert result.status == GREEN


def test_the_claim_renders_the_shape_the_authority_envelope_takes() -> None:
    result = claim(project="pass", code=ACCEPTED)
    assert result.as_authority_conformance() == {
        "standards_touched": ["code", "project"],
        "accepted_standards": ["code"],
        "status": GREEN,
    }


def test_the_real_reader_agrees_with_the_matrix_it_wraps() -> None:
    """The adapter, against a real repository, through the default reader.

    Every other test here injects the reader, so none of them touches the code that talks to
    project-standards -- and that is where the first implementation's defects all lived. This one
    drives the real thing and asserts it reports exactly what the matrix reports.
    """
    compliance = pytest.importorskip("portfolio.compliance")
    manifest = pytest.importorskip("portfolio.manifest")
    from datetime import date, datetime

    repo = Path(__file__).resolve().parents[2]
    found = manifest.read_manifest(repo)
    rows = compliance.build_rows(
        [(repo, found.frontmatter if found else None)], datetime.now(), date.today()
    )[0]
    expected = {standard: cell.status for standard, cell in rows[0].cells.items()}

    assert read_compliance_cells(repo) == expected
    assert compute_conformance_claim(repo).status in {GREEN, NOT_GREEN}


def test_a_missing_matrix_fails_closed_rather_than_guessing(monkeypatch) -> None:
    """No scanner, no claim. Never an empty claim that reads as green.

    The reader resolves project-standards through `importlib.import_module`, so that is what has
    to fail: patching `builtins.__import__` would be satisfied by `sys.modules` and prove nothing
    on a machine where the scanner happens to be installed.
    """
    import importlib

    def refuse(name: str, *args: object, **kwargs: object):
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", refuse)
    with pytest.raises(ScannerUnavailableError):
        read_compliance_cells(REPO)
