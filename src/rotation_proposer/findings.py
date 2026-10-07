"""What is due for rotation, read from infraops -- the one place the due rule lives.

**INFRAOPS COMPUTES "DUE", AND THIS PROGRAM ASKS IT RATHER THAN DECIDING AGAIN.** The rule is
infraops' `credFindings` (`src/security-drift/cred-rotation.ts`): an open exposure supersedes
everything, a credential past its class's maximum age is due, and since ADR-0054 increment 2 a
`rotate_requested` date later than the last rotation is due too. Re-implementing it here would mean
a second copy of `CLASS_POLICY`'s per-class maximum ages, of the exposure-resolution keys, and of
the rule that a revoked credential stops aging -- and that rule was still moving the week this was
written. This repository has paid for N copies of one vocabulary often enough (the capability list,
the dependency-update budget, BWS UUIDs) to know that only the copies that run get corrected.

**SO IT RUNS infraops' OWN CLI**, `security-drift-cli.js cred-findings`, which loads the listed
registries and the rotation state exactly as the 03:00 scan does and prints the findings as JSON.
That subcommand reads two files and writes nothing; this program opens neither file itself.

**THE WIRE IS STRUCTURED, NEVER PROSE.** A scan finding's `detail` is English with the values
embedded in it. Each value this program keys anything on -- the exposure id, the requested date,
the date the credential last rotated -- arrives as its own field, so nothing here parses a sentence.
`tests/fixtures/rotation_findings.json` pins the shape.

**WHICH TRIGGER WINS.** infraops can report both an age and a request for one credential. One
rotation answers both, so each credential yields ONE `Due`, by `PRECEDENCE`: an exposure, then a
request, then age. An exposure is the urgent one and infraops already lets it supersede age; a
request is a date a person wrote down, so it names the rotation better than an age does.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
SUBCOMMAND: Final = "cred-findings"
DEFAULT_INFRAOPS_CHECKOUT: Final = Path.home() / "Projects" / "infraops-mcp-server"
CLI_SCRIPT: Final = Path("dist") / "cli" / "security-drift-cli.js"
TIMEOUT_SECONDS: Final = 60.0

# launchd's PATH names only system directories, so `node` from a Homebrew install is not on it.
# The same trap `uv` is resolved around (CLAUDE.md): which first, then where installs put it.
NODE_FALLBACKS: Final = (Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node"))

# infraops' check -> this program's trigger word, which is the last part of the occurrence.
TRIGGERS: Final = {
    "cred.exposure-rotate": "exposure",
    "cred.rotation-requested": "requested",
    "cred.rotation-age": "age",
}
# Checks infraops reports about a credential that are NOT a reason to rotate it. Named rather than
# ignored by default: a check this program has never heard of is a finding, because the day
# infraops adds a new trigger, silently skipping it would leave that rotation unproposed.
NOT_ROTATIONS: Final = frozenset({"cred.unknown-class", "cred.invalid-rotate-requested"})
PRECEDENCE: Final = ("exposure", "requested", "age")

# Every value written into a package or a reference. Narrow on purpose: the occurrence is written
# into YAML between single quotes and into an observation's reference, and a quote, a colon or a
# newline in either is a corruption rather than a value.
_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")


class FindingsError(Exception):
    """infraops could not be asked, or answered something this program cannot read.

    Always a whole-pass refusal: a pass that cannot read what is due cannot say what is not due.
    """


@dataclass(frozen=True)
class Due:
    """One credential that is due for rotation, and the trigger that makes it so."""

    credential_id: str
    credential_class: str
    trigger: str
    # What identifies THIS rotation among the credential's rotations: the exposure id, the
    # requested date, or the date the credential last rotated. Never today's date -- a value that
    # moved every day would make every daily pass see a new rotation and revise again.
    basis: str
    # The date the trigger is dated by, for the observation's `observed_at`.
    dated: str

    @property
    def occurrence(self) -> str:
        """What a standing rotation package's revision carries in `profile_fields.occurrence`."""
        return f"{self.basis}-{self.trigger}"


@dataclass(frozen=True)
class Unrecognised:
    """A check infraops reported that this program does not know how to read. A finding."""

    credential_id: str
    check: str


def _text(entry: dict[str, Any], key: str, pattern: re.Pattern[str]) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise FindingsError(f"a finding's {key!r} is missing or malformed")
    return value


def _date(entry: dict[str, Any], key: str) -> str:
    """The calendar date at the head of an ISO date or timestamp, and nothing after it."""
    value = entry.get(key)
    if not isinstance(value, str) or _DATE.match(value) is None:
        raise FindingsError(f"a finding's {key!r} is missing or not an ISO date")
    return value[:10]


def _due(entry: dict[str, Any], trigger: str) -> Due:
    credential_id = _text(entry, "id", _TOKEN)
    credential_class = _text(entry, "class", _TOKEN)
    if trigger == "exposure":
        basis = _text(entry, "exposure_id", _TOKEN)
        dated = _date(entry, "exposure_date")
    elif trigger == "requested":
        basis = dated = _date(entry, "rotate_requested")
    else:
        basis = dated = _date(entry, "anchor")
    return Due(credential_id, credential_class, trigger, basis, dated)


def parse(text: str) -> tuple[list[Due], list[Unrecognised]]:
    """infraops' answer, as one `Due` per credential and every check this program cannot read."""
    try:
        document = json.loads(text)
    except ValueError as error:
        raise FindingsError("infraops answered something that is not JSON") from error
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise FindingsError(
            f"infraops answered a findings document that is not schema version {SCHEMA_VERSION}"
        )
    entries = document.get("findings")
    if not isinstance(entries, list):
        raise FindingsError("infraops answered no findings list")

    candidates: dict[str, list[Due]] = {}
    unrecognised: list[Unrecognised] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FindingsError("a finding is not an object")
        check = entry.get("check")
        if check in NOT_ROTATIONS:
            continue
        trigger = TRIGGERS.get(check) if isinstance(check, str) else None
        if trigger is None:
            unrecognised.append(Unrecognised(_text(entry, "id", _TOKEN), str(check)[:64]))
            continue
        due = _due(entry, trigger)
        candidates.setdefault(due.credential_id, []).append(due)

    chosen = [
        min(dues, key=lambda due: PRECEDENCE.index(due.trigger))
        for _, dues in sorted(candidates.items())
    ]
    return chosen, unrecognised


def infraops_checkout() -> Path:
    return Path(os.environ.get("ROTATION_PROPOSER_INFRAOPS_CHECKOUT") or DEFAULT_INFRAOPS_CHECKOUT)


def node_binary() -> Path:
    found = shutil.which("node")
    if found:
        return Path(found)
    for candidate in NODE_FALLBACKS:
        if candidate.is_file():
            return candidate
    raise FindingsError("node is not on this machine, so infraops cannot be asked what is due")


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise FindingsError("infraops did not answer what is due in time") from error
    except OSError as error:
        raise FindingsError(f"infraops could not be run: {type(error).__name__}") from error


def read_due(
    checkout: Path | None = None, *, run: Runner = _run
) -> tuple[list[Due], list[Unrecognised]]:
    """Ask infraops what is due. Every failure is a `FindingsError`, which stops the pass."""
    script = (checkout or infraops_checkout()) / CLI_SCRIPT
    if not script.is_file():
        raise FindingsError(f"no infraops security-drift CLI at {script}")
    completed = run([str(node_binary()), str(script), SUBCOMMAND])
    if completed.returncode != 0:
        # The LAST line only, bounded: infraops names its refusal there (an unparseable registry,
        # an unknown subcommand on an infraops that predates this one), and nothing it prints
        # carries a value -- the registries hold names, UUIDs and fingerprints, never secrets.
        lines = (completed.stderr or "").strip().splitlines()
        reason = lines[-1][:300] if lines else f"exit {completed.returncode}"
        raise FindingsError(f"infraops refused {SUBCOMMAND}: {reason}")
    return parse(completed.stdout)
