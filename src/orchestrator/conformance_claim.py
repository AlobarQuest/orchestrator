"""Compute a work unit's `authority.conformance` claim from real repository state.

The conformance admission gate (`_conformance_blocked_reason`) trusts the claim it is given: it
admits a unit whose claim says `status: green`, or whose `standards_touched` is a subset of its
`accepted_standards`. Today a decomposition author types that claim by hand, from memory, about
a repository the gate never looks at. This module derives it from the same scanner that would
actually judge the repository, so the claim and the judgement cannot drift apart.

Everything here is read from ONE source: the compliance matrix project-standards already
computes. That is deliberate, and it is the whole design.

`accepted_standards` is exactly the set of standards the matrix marks `accepted-exception` --
its own verdict that every violation in that standard is covered by an *active, matching*
exception. It is never derived by re-reading the manifest and harvesting exception entries: an
exception's `finding` field is an fnmatch glob, entries expire, and entries that match nothing
are stale. A reader that counts entries rather than their resolution treats a narrow, expired,
long-dead exception as a blanket waiver -- and once every touched standard is "waived" that way,
`accepted_standards` equals `standards_touched`, the gate's subset branch admits unconditionally,
and a repository with live BLOCK findings dispatches. That is the exact tautology this helper
exists to prevent, and only the matrix's own resolution avoids it.

`status` is green only when the matrix has no violation anywhere -- the same condition the
scanner exits non-zero on. That includes the `checks` cell: a repository whose named checks are
not wired is not conformant, and in this factory it is the case that matters most, because a
unit's acceptance evidence is carried by named checks on its pull-request head.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Cell statuses, as project-standards' compliance matrix reports them.
PASS = "pass"
ACCEPTED = "accepted-exception"
NOT_APPLICABLE = "not-applicable"
CLEAN = frozenset({PASS, ACCEPTED, NOT_APPLICABLE})

# `checks` is a column of the matrix -- required-check wiring -- not a standard. It cannot be
# touched or accepted, but a violation in it is still a violation.
NOT_A_STANDARD = frozenset({"checks"})

GREEN = "green"
NOT_GREEN = "violations"

CellReader = Callable[[Path], Mapping[str, str]]


class ScannerUnavailableError(RuntimeError):
    """The compliance matrix could not be imported, so no claim can be derived."""


@dataclass(frozen=True)
class ConformanceClaim:
    """What a unit's authority envelope asserts about its target repository."""

    standards_touched: tuple[str, ...]
    accepted_standards: tuple[str, ...]
    status: str

    def as_authority_conformance(self) -> dict[str, Any]:
        """The exact shape `authority.conformance` takes in a work-unit envelope."""
        return {
            "standards_touched": list(self.standards_touched),
            "accepted_standards": list(self.accepted_standards),
            "status": self.status,
        }


def compute_conformance_claim(
    repo_path: Path | str,
    *,
    read_cells: CellReader | None = None,
) -> ConformanceClaim:
    """Derive the conformance claim for `repo_path` from the compliance matrix."""
    cells = (read_cells or read_compliance_cells)(Path(repo_path))

    touched = tuple(
        sorted(
            standard
            for standard, status in cells.items()
            if standard not in NOT_A_STANDARD and status != NOT_APPLICABLE
        )
    )
    accepted = tuple(standard for standard in touched if cells[standard] == ACCEPTED)
    # Every cell, `checks` included: this mirrors the scanner's own exit code, which counts a
    # violation anywhere. A standard resolved to `accepted-exception` is not a violation -- the
    # matrix has already decided that its findings are covered.
    clean = all(status in CLEAN for status in cells.values())
    return ConformanceClaim(touched, accepted, GREEN if clean else NOT_GREEN)


def read_compliance_cells(repo_path: Path) -> Mapping[str, str]:
    """Every cell of the repository's compliance matrix, as project-standards resolves it.

    project-standards is a sibling repository, not a dependency of this package: it is a
    local-only tool a decomposition author already has, and the orchestrator must not grow a
    runtime dependency on it. Importing it dynamically says exactly that, and keeps this module
    importable -- and testable -- where it is absent.
    """
    from datetime import date, datetime
    from importlib import import_module

    try:
        compliance = import_module("portfolio.compliance")
        manifest = import_module("portfolio.manifest")
    except ImportError as error:
        raise ScannerUnavailableError(
            "portfolio.compliance is not importable; the conformance claim is derived from the "
            "project-standards compliance matrix, which must be on the path"
        ) from error

    found = manifest.read_manifest(repo_path)
    rows = compliance.build_rows(
        [(repo_path, found.frontmatter if found else None)], datetime.now(), date.today()
    )[0]
    return {standard: cell.status for standard, cell in rows[0].cells.items()}
