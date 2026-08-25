"""What a machine-local activation asserts, measured on the machine and nowhere else.

ADR-0030's binding lane files the artifact: a content digest over the working copy, with the
landing commit proven to be in its history. This module answers the question that comes after it
-- is that artifact actually what the next start will execute -- and files the answer as a
deployment observation, the sixth traceability hop.

THREE FACTS, KEPT SEPARATE. "Not current" for three different reasons is exactly the state
collapse this estate has paid for repeatedly, so each is measured and reported on its own.

1. **The landing commit is in the history this working copy holds.** The binding lane proved it
   once, at whatever moment it wrote the binding; this measures it again at the moment of the
   check, because carrying the earlier proof forward would be the producer attesting to its own
   act, and a working copy that has since been reset would be recorded as holding a change it
   does not.
2. **Every declared console entry point is installed.** This is the one with a recorded failure.
   The editable install is a `.pth` file pointing at the source tree, so an ordinary module change
   is live the moment the pull lands and needs no sync -- console entry points are the exception,
   the launchers invoke them by absolute path, and a job whose entry point was never installed
   dies at a missing binary. "The code is at the right commit" is not the same statement as "the
   program that will run is the new one".
3. **The environment matches the lockfile.** `uv sync --frozen --check` answers it, and a
   dependency-update unit is precisely the case where an unsynced environment means the change is
   not live at all.

TRI-STATE, NOT BOOLEAN. One of the four enrolled working copies is a TypeScript project with no
`pyproject.toml`, no lockfile and no virtual environment, so facts 2 and 3 genuinely do not apply
to it -- and this estate's standing ruling is that "not applicable" is a distinct answer from
"not met". Fact 1 is never excused: every working copy either holds the commit or does not.

WHAT REMAINS UNOBSERVED, and it is a real bound rather than a caveat. A process that started
BEFORE the pull is still running old code until it restarts. This answers what the NEXT start will
execute, never what is executing now. That is narrow for this population -- launchers fire on a
schedule, CLIs run per invocation, the MCP server restarts each session -- and the named path to
closing it is self-reporting consumers, as `brain` already does by serving its revision on its own
health endpoint.

THE NPM HALF IS DELIBERATELY ABSENT. `infraops-mcp-server` runs `node dist/index.js`, so a stale
`dist/` is fact 2 one toolchain over, and nothing here checks it. Reporting `not_applicable` says
so rather than implying the repository passed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

# THE VOCABULARY THE ORCHESTRATOR VALIDATES, transcribed. `src/activation_sweep` is a separate
# program that imports nothing from `orchestrator.*` (`tests/architecture` enforces it), so these
# are a copy, and `tests/contract/test_activation_summary_contract.py` is what keeps the two
# agreeing. A rename on either side without the other would have the orchestrator refuse every
# observation this lane composes -- loudly, at least, since the summary's key set is exact.
MERGE_COMMIT_PRESENT = "merge_commit_present"
CONSOLE_ENTRY_POINTS_PRESENT = "console_entry_points_present"
ENVIRONMENT_MATCHES_LOCK = "environment_matches_lock"
ACTIVATION_FACTS = (MERGE_COMMIT_PRESENT, CONSOLE_ENTRY_POINTS_PRESENT, ENVIRONMENT_MATCHES_LOCK)

YES = "yes"
NO = "no"
NOT_APPLICABLE = "not_applicable"
ACTIVATION_RESULTS = (YES, NO, NOT_APPLICABLE)

# The one environment a machine-local observation may name, pinned by the orchestrator's own CHECK
# constraint. Transcribed here for the same reason the facts are.
OPERATOR_MACHINE_ENVIRONMENT = "operator_machine"

# THE ONE EXTERNAL COMMAND THIS MODULE RUNS, and it is bounded the way `checkout.py` bounds git.
# `--frozen` is what makes it a measurement: without it `uv sync --check` may re-resolve and
# rewrite `uv.lock`, so a checker would mutate the repository it was reading. Measured 2026-08-25
# across three working copies: the tree is byte-identical before and after, exit 0 means
# synchronized and exit 1 means it is not.
UV_CHECK = ("sync", "--frozen", "--check")
UV_TIMEOUT_SECONDS = 120

# uv's standard install location, tried when it is not on PATH. Under `launchd` the job inherits
# only the PATH its plist names, and `uv` lives in the user's `~/.local/bin` rather than in any
# system directory -- so without this fallback every Python working copy would report unmeasurable
# on every scheduled pass while working perfectly from an operator's shell.
UV_FALLBACK = Path.home() / ".local" / "bin" / "uv"

PYPROJECT = "pyproject.toml"
LOCKFILE = "uv.lock"
VENV_BIN = Path(".venv") / "bin"


class ActivationError(RuntimeError):
    """This working copy could not answer. The answer is missing, never negative."""


@dataclass(frozen=True)
class RepositoryFacts:
    """The two facts that are properties of the working copy rather than of any one unit.

    Measured once per pass: `uv sync --check` and a `pyproject.toml` read do not vary between the
    units of one repository, and running them per candidate would multiply the cost by the number
    of landings the machine has pulled.
    """

    console_entry_points_present: str
    environment_matches_lock: str


@dataclass(frozen=True)
class ActivationFacts:
    """The three facts, and whether they permit recording an activation.

    `recordable` is deliberately NOT one of the facts. A fact is what was measured; whether to
    file a row is a decision about what a `deployment` hop means -- and it means the artifact is
    what the next start executes, which a `no` contradicts. A `no` is reported until a person
    acts, and the row lands clean afterwards rather than freezing a false that nothing can amend:
    the ingest refuses a second observation carrying different facts, so a row written wrong is
    written wrong forever.
    """

    merge_commit_present: str
    console_entry_points_present: str
    environment_matches_lock: str

    @classmethod
    def of(cls, repository: RepositoryFacts, *, merge_commit_present: str) -> ActivationFacts:
        """Assemble one unit's answer from the shared pair plus its own commit fact.

        The commit fact is MEASURED per unit rather than assumed from the binding's existence.
        Every candidate reaching this point was proven activated at some earlier moment, so
        asserting `yes` would be the producer attesting to its own act -- and a working copy that
        has since been reset would then be recorded as carrying a change it no longer holds.
        """
        return cls(
            merge_commit_present=merge_commit_present,
            console_entry_points_present=repository.console_entry_points_present,
            environment_matches_lock=repository.environment_matches_lock,
        )

    @property
    def summary(self) -> dict[str, str]:
        return {
            MERGE_COMMIT_PRESENT: self.merge_commit_present,
            CONSOLE_ENTRY_POINTS_PRESENT: self.console_entry_points_present,
            ENVIRONMENT_MATCHES_LOCK: self.environment_matches_lock,
        }

    @property
    def unsatisfied(self) -> tuple[str, ...]:
        return tuple(fact for fact, result in self.summary.items() if result == NO)

    @property
    def recordable(self) -> bool:
        return not self.unsatisfied


def console_entry_points_present(path: Path) -> str:
    """Whether every `[project.scripts]` entry has a file in `.venv/bin`.

    A repository with no `pyproject.toml`, or one declaring no console entry points, has nothing
    to check and says so. A repository that declares them and has no `.venv` at all answers `no`:
    its launchers invoke by absolute path and would die at a missing binary, which is exactly the
    condition this fact exists for.
    """
    declared = _declared_entry_points(path)
    if declared is None:
        return NOT_APPLICABLE
    binaries = path / VENV_BIN
    missing = [name for name in declared if not (binaries / name).is_file()]
    return NO if missing else YES


def environment_matches_lock(path: Path) -> str:
    """Whether the installed environment matches `uv.lock`, via uv's own check.

    Exit 0 is synchronized and exit 1 is not; anything else is uv failing rather than answering,
    which is unmeasurable and must not read as either result.
    """
    if not (path / PYPROJECT).is_file() or not (path / LOCKFILE).is_file():
        return NOT_APPLICABLE
    executable = _uv_executable()
    try:
        completed = subprocess.run(
            [executable, *UV_CHECK],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=UV_TIMEOUT_SECONDS,
            env=dict(os.environ),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ActivationError(f"uv could not run in {path}: {type(error).__name__}") from error
    if completed.returncode == 0:
        return YES
    if completed.returncode == 1:
        return NO
    # Only the exit status. uv's stderr can name an index URL and its credentials, and a
    # diagnostic that prints what it was given is how a value that should not be in a transcript
    # gets into one.
    raise ActivationError(f"uv sync --check exited {completed.returncode} in {path}")


def repository_facts(path: Path) -> RepositoryFacts:
    """The shared pair, for one working copy. Raises only when a fact could not be measured."""
    return RepositoryFacts(
        console_entry_points_present=console_entry_points_present(path),
        environment_matches_lock=environment_matches_lock(path),
    )


def _declared_entry_points(path: Path) -> tuple[str, ...] | None:
    manifest = path / PYPROJECT
    if not manifest.is_file():
        return None
    try:
        parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ActivationError(
            f"{PYPROJECT} could not be read in {path}: {type(error).__name__}"
        ) from error
    project = parsed.get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if not isinstance(scripts, dict) or not scripts:
        return None
    return tuple(sorted(str(name) for name in scripts))


def _uv_executable() -> str:
    found = shutil.which("uv")
    if found is not None:
        return found
    if UV_FALLBACK.is_file() and os.access(UV_FALLBACK, os.X_OK):
        return str(UV_FALLBACK)
    raise ActivationError("uv is not on PATH and is not at its standard install location")
