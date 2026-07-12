"""Compute a work unit's `authority.conformance` claim from real repository state.

The conformance admission gate (`_conformance_blocked_reason`) trusts the claim it is given: it
admits a unit whose claim says `status: green`, or whose `standards_touched` is a subset of its
`accepted_standards`. Today a decomposition author types that claim by hand, from memory, about
a repository the gate never looks at. This module derives it from the same scanners that would
actually judge the repository, so the claim and the judgement cannot drift apart.

`accepted_standards` is derived ONLY from real waiver sources -- the `exceptions` entries in a
repository's project-standards manifest, and the security-standards allowlist. It is never
echoed from `standards_touched`. That is not a stylistic preference: the gate's subset branch
(`touched <= accepted`) becomes a tautology the moment a producer echoes one into the other,
and every unit is then admitted regardless of its real state.

Failure is closed. A standard whose compliance cell is unknown -- because no checker ran, or a
manifest is missing or unreadable -- is not green.

The two scanners live in sibling repositories and are not dependencies of this package, so they
are read through injectable callables whose defaults import lazily. That keeps the module
importable, and testable, wherever the orchestrator runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Cell statuses produced by project-standards' compliance matrix.
PASS = "pass"
NOT_APPLICABLE = "not-applicable"

# `checks` is a column of the compliance matrix, not a standard.
NOT_A_STANDARD = frozenset({"checks"})

GREEN = "green"
NOT_GREEN = "violations"

CellReader = Callable[[Path], Mapping[str, str]]
WaiverReader = Callable[[Path], frozenset[str]]


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
    read_waivers: WaiverReader | None = None,
) -> ConformanceClaim:
    """Derive the conformance claim for `repo_path` from its real state.

    `read_cells` returns each standard's compliance status; `read_waivers` returns only those
    standards a real waiver source has accepted. They are separate readers on purpose: nothing
    here can turn a touched standard into an accepted one.
    """
    path = Path(repo_path)
    cells = (read_cells or _read_compliance_cells)(path)
    waived = (read_waivers or _read_waived_standards)(path)

    touched = tuple(
        sorted(
            standard
            for standard, status in cells.items()
            if standard not in NOT_A_STANDARD and status != NOT_APPLICABLE
        )
    )
    # Only a standard that is actually in play can be waived; a waiver for a standard the
    # repository does not touch says nothing about this unit.
    accepted = tuple(sorted(standard for standard in touched if standard in waived))
    clean = all(cells[standard] == PASS for standard in touched)
    return ConformanceClaim(touched, accepted, GREEN if clean else NOT_GREEN)


def _scanner(module: str, attribute: str) -> Any:
    """Resolve a scanner entry point at call time.

    project-standards and security-standards are sibling repositories, not dependencies of this
    package: they are local-only tools a decomposition author already has, and the orchestrator
    must not grow a runtime dependency on either. Importing them dynamically says exactly that,
    and keeps this module importable -- and its tests runnable -- where they are absent.
    """
    from importlib import import_module

    try:
        return getattr(import_module(module), attribute)
    except (ImportError, AttributeError) as error:
        raise ScannerUnavailableError(
            f"{module}.{attribute} is not importable; the conformance claim is derived from the "
            "project-standards and security-standards scanners, which must be on the path"
        ) from error


class ScannerUnavailableError(RuntimeError):
    """A scanner this claim is derived from could not be imported."""


def _read_compliance_cells(repo_path: Path) -> Mapping[str, str]:
    """Every standard's compliance status, from project-standards' own matrix."""
    from datetime import date, datetime

    build_rows = _scanner("portfolio.compliance", "build_rows")
    read_manifest = _scanner("portfolio.manifest", "read_manifest")

    manifest = read_manifest(repo_path)
    frontmatter = manifest.frontmatter if manifest else None
    rows = build_rows([(repo_path, frontmatter)], datetime.now(), date.today())[0]
    return {standard: cell.status for standard, cell in rows[0].cells.items()}


def _read_waived_standards(repo_path: Path) -> frozenset[str]:
    """Standards a real waiver source has accepted -- and nothing else.

    Two sources, both of which a human had to write deliberately: an `exceptions` entry in the
    repository's project-standards manifest, and a finding the security-standards allowlist
    suppresses. Neither can be produced by echoing `standards_touched`.
    """
    parse_contract = _scanner("portfolio.compliance", "parse_contract")
    read_manifest = _scanner("portfolio.manifest", "read_manifest")
    scan = _scanner("security_scan.cli", "scan")

    waived: set[str] = set()

    manifest = read_manifest(repo_path)
    if manifest and manifest.frontmatter:
        contract = parse_contract(manifest.frontmatter)
        waived.update(
            entry["standard"]
            for entry in contract.exceptions
            if isinstance(entry, dict) and entry.get("standard")
        )

    if scan(repo_path)[0].get("allowlisted"):
        waived.add("security")

    return frozenset(waived)


def render_claim(repo_path: Path | str) -> str:
    """The claim as the JSON a decomposition author pastes into a unit's envelope."""
    claim = compute_conformance_claim(repo_path)
    return json.dumps(claim.as_authority_conformance(), indent=2, sort_keys=True)
