"""The destination comparison: the registry's list beside the package's, and their difference.

ADR-0055 amendment 2. The executor refuses a rotation unless the registry's destinations, the
package revision's and the unit envelope's are equal; finding that out at the first claim is too
late, because ADR-0052's supersede has closed by then. So the approval runbook runs the same
comparison before Devon approves a revision, through `rotation-worker compare-destinations`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Comparison:
    registry: tuple[str, ...]
    package: tuple[str, ...]

    @property
    def equal(self) -> bool:
        return self.registry == self.package

    @property
    def only_in_registry(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.registry) - set(self.package)))

    @property
    def only_in_package(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.package) - set(self.registry)))


def report(comparison: Comparison) -> list[str]:
    """One line per destination, marked by where it appears, then the verdict."""
    lines = []
    for destination in sorted(set(comparison.registry) | set(comparison.package)):
        in_registry = destination in comparison.registry
        in_package = destination in comparison.package
        marker = (
            "both    "
            if in_registry and in_package
            else ("registry" if in_registry else "package ")
        )
        lines.append(f"  {marker}  {destination}")
    if comparison.equal:
        lines.append("equal: the package lists exactly the registry's destinations")
    elif comparison.only_in_registry or comparison.only_in_package:
        lines.append(
            f"differ: {len(comparison.only_in_registry)} only in the registry, "
            f"{len(comparison.only_in_package)} only in the package"
        )
    else:
        lines.append("differ: the same destinations, but the package's list isn't sorted once each")
    return lines
