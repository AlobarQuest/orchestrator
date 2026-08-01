"""The policy artifact: one versioned document, keyed on reach (WS-P2.18 Increment 2, ADR-0010).

WS-P2.18 replaces four scattered policies -- the authority known-good pattern, the change window,
the per-key lease, and self-update -- with a single versioned document keyed on what a package's
work touches when it runs (``reach``, ADR-0009). This module is that document's loader. The
document itself is ``factory-policy.toml``, next to this file.

**Its only expressible output is a refusal.** There is no permission in this schema and no boolean
in this module: :meth:`FactoryPolicy.refusals_for` returns the reasons policy objects, and an empty
tuple means "this policy raises no objection", which is a strictly weaker claim than "go ahead".
Permission is the conjunction of every admission check, of which policy is one term and the hard
off-switch is another. Policy can only ever lengthen the list of objections, so it cannot widen
what that switch allows -- not by convention or by check ordering, but because a widening is
unwritable. This module deliberately imports nothing from ``orchestrator.config``: it cannot read
the off-switch, so it cannot overrule it.

**Nothing is cached.** The artifact is read and parsed on every call. A cache is the mechanism by
which a policy change silently fails to take effect, and this surface has no volume that would
justify one -- so noticing an edit costs no restart. Getting new bytes onto a running process is a
separate question, answered by how the image ships (see ADR-0010).

**Unknown resolves restrictively, in all four directions.** An undeclared reach, a member outside
the vocabulary, and a member with no row each produce a refusal rather than an absence of one; and
a malformed document or an unrecognised schema version raises a named error rather than yielding an
empty policy, because an empty policy is the permissive reading of a broken file.

**Total coverage, no implicit default.** Every member of ``REACH_VOCABULARY`` must have exactly one
row, and a row for a member the vocabulary does not know is an error. That makes the artifact a
pinned projection of the vocabulary rather than a second copy of it: a new member added without a
row does not fall through to something lenient, it stops the document loading.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from orchestrator.errors import DomainError
from orchestrator.reach_vocabulary import REACH_VOCABULARY

PACKAGED_ARTIFACT: Final[Path] = Path(__file__).parent / "factory-policy.toml"

# The schema versions this loader understands. A version outside this set is a named failure, never
# a best-effort read: an older process meeting a newer document would silently ignore whatever
# narrowing the new version introduced, which is the permissive reading of a version skew. A new
# version is therefore a coordinated change -- the loader learns it in the same commit that ships
# the document at it and the code that reads its new field.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})

# Why policy objects. These are the whole output vocabulary of this module.
REACH_UNDECLARED: Final = "reach_undeclared"
REACH_UNRECOGNISED: Final = "reach_unrecognised"
REACH_NOT_IN_POLICY: Final = "reach_not_in_policy"

_ROW_FIELDS = frozenset({"rationale", "decided"})
_TOP_LEVEL_FIELDS = frozenset({"version", "reach"})


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "factory_policy_invalid",
        f"the policy artifact is invalid: {detail}",
        "correct factory-policy.toml; a document that does not load permits nothing",
    )


@dataclass(frozen=True)
class ReachPolicy:
    """One row -- everything policy currently says about a single reach member."""

    member: str
    rationale: str
    decided: date


@dataclass(frozen=True)
class FactoryPolicy:
    """A loaded artifact. Answers only in refusals."""

    version: int
    source: str
    rows: Mapping[str, ReachPolicy]

    def refusals_for(self, reach: Sequence[str] | None) -> tuple[str, ...]:
        """Why this policy objects to work of the given reach; empty means it does not object.

        Composition over a reach set is the union of its members' refusals, which is the same
        thing as intersection-of-permission (ADR-0009): adding a member can only lengthen the
        result, never shorten it.
        """
        if not reach:
            return (REACH_UNDECLARED,)
        refusals: set[str] = set()
        for member in reach:
            if member not in REACH_VOCABULARY:
                refusals.add(REACH_UNRECOGNISED)
            elif member not in self.rows:
                refusals.add(REACH_NOT_IN_POLICY)
        return tuple(sorted(refusals))

    def report(self) -> dict[str, Any]:
        """What this process is enforcing, for an operator reading the running instance."""
        return {
            "version": self.version,
            "source": self.source,
            "reach": [
                {"member": row.member, "rationale": row.rationale, "decided": row.decided}
                for row in self.rows.values()
            ],
        }


def load_factory_policy(path: Path = PACKAGED_ARTIFACT) -> FactoryPolicy:
    """The artifact at ``path``, validated. Raises rather than returning a degraded policy."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _invalid(f"{path.name} could not be read") from error
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise _invalid(f"{path.name} is not valid TOML: {error}") from error
    return FactoryPolicy(
        version=_schema_version(document),
        source=path.name,
        rows=_rows(document),
    )


def _schema_version(document: Mapping[str, Any]) -> int:
    unknown = set(document) - _TOP_LEVEL_FIELDS
    if unknown:
        raise _invalid("it declares unknown top-level keys: " + ", ".join(sorted(unknown)))
    version = document.get("version")
    # `bool` is an `int` in Python, and `version = true` is a shape error rather than version 1.
    if not isinstance(version, int) or isinstance(version, bool):
        raise _invalid("`version` must be present and an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise DomainError(
            "factory_policy_version_unsupported",
            f"the policy artifact declares schema version {version}, which this build does "
            "not understand",
            "ship a build that knows this schema version, or restore the artifact to one it does",
        )
    return version


def _rows(document: Mapping[str, Any]) -> Mapping[str, ReachPolicy]:
    table = document.get("reach")
    if not isinstance(table, dict):
        raise _invalid("it must declare a `[reach]` table")
    missing = sorted(set(REACH_VOCABULARY) - set(table))
    extra = sorted(set(table) - set(REACH_VOCABULARY))
    if missing or extra:
        raise _invalid(
            "every reach member needs exactly one row and no others are allowed"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unknown: {', '.join(extra)}" if extra else "")
        )
    return {member: _row(member, table[member]) for member in sorted(table)}


def _row(member: str, value: object) -> ReachPolicy:
    if not isinstance(value, dict):
        raise _invalid(f"`[reach.{member}]` must be a table")
    if set(value) != _ROW_FIELDS:
        raise _invalid(
            f"`[reach.{member}]` must declare exactly "
            + ", ".join(sorted(_ROW_FIELDS))
            + f"; it declares {', '.join(sorted(value)) or 'nothing'}"
        )
    rationale = value["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise _invalid(f"`[reach.{member}].rationale` must be a non-empty string")
    return ReachPolicy(
        member=member,
        # Wrapped in the file for review; one sentence on the wire.
        rationale=" ".join(rationale.split()),
        decided=_decided(member, value["decided"]),
    )


def _decided(member: str, value: object) -> date:
    # A quoted ISO date, following routing-policy.toml. A bare TOML date is a different shape and
    # is rejected rather than accepted alongside -- one shape, so there is nothing to coerce.
    if not isinstance(value, str):
        raise _invalid(f'`[reach.{member}].decided` must be a quoted ISO date, e.g. "2026-08-01"')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _invalid(f"`[reach.{member}].decided` is not an ISO date: {value!r}") from error
