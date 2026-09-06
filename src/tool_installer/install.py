"""The act: back up, install, prove, and put it back if the proof fails.

**THE ORDER IS THE SAFETY PROPERTY.** Nothing is touched before the outgoing artifacts are
captured, and nothing is trusted before it is probed. `cargo install --force` contributes the
half this program cannot: it replaces the binary only on a successful build, so a build that
fails leaves the working tool in place and this module never reaches its rollback.

**THE PROBE IS THE BAR, AND `--version` ALONE IS NOT IT.** A build that succeeded and a binary
that answers `--version` proves almost nothing about a tool that filters every command on this
machine. So the version is checked by VALUE against what cargo recorded -- catching a build that
succeeded while writing some other binary -- and then the tool is made to do its job: `gain` reads
its own store, and `proxy` runs a command THROUGH the filter. All must pass.

**EVERY PROBE ADDRESSES THE INSTALLED PATH, NEVER THE NAME.** A probe that ran `rtk --version`
would exercise whatever `PATH` resolved, which during a rollback demo is the live binary and not
the one under test -- a probe that cannot fail for the reason it exists to catch.

**A ROLLBACK RESTORES THREE FILES AND DELETES WHAT WAS NOT THERE.** See `toolchain`'s docstring:
cargo writes its record on a successful BUILD, which is before this module has probed anything, so
restoring only the binary would leave the machine running the old tool while cargo's record
claimed the new revision -- after which every later pass would compare that record to the head,
find them equal, and never retry. The rollback is complete or it is worse than none.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tool_installer.toolchain import (
    Cargo,
    Installed,
    backup,
    install_artifacts,
    read_installed,
    remove_uncaptured,
    restore,
)
from tool_installer.tools import Tool

# What a pass did to the artifact. A closed vocabulary because it is written into an observation,
# and a value invented at a call site would be false provenance in a table with no supersession
# model and no delete route.
ACTION_NONE = "none"
ACTION_INSTALLED = "installed"
ACTION_ROLLED_BACK = "rolled_back"
ACTION_INSTALL_FAILED = "install_failed"
ACTION_NOT_PERMITTED = "not_permitted"

# Long enough for a cold `cargo install` of a real crate on this machine, and bounded so a hung
# build cannot hold the window open until somebody is back at the keyboard.
INSTALL_TIMEOUT_SECONDS = 1800
PROBE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ProbeResult:
    command: tuple[str, ...]
    passed: bool
    detail: str

    def as_facts(self) -> dict[str, object]:
        return {"command": list(self.command), "passed": self.passed, "detail": self.detail}


@dataclass
class InstallOutcome:
    """What the act did, in the vocabulary the record writes."""

    action: str
    installed: Installed | None
    probes: list[ProbeResult] = field(default_factory=list)
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.action == ACTION_INSTALLED


def _run(argv: list[str], *, env: dict[str, str] | None, timeout: int) -> tuple[int, str, str]:
    """Run and return `(returncode, stdout, last useful line)`. Never raises for a non-zero exit.

    **STDOUT IS RETURNED SEPARATELY FROM THE DIAGNOSTIC LINE, and the separation is load-bearing
    rather than tidy.** The diagnostic prefers stderr, which is right for a failing build; the
    version check reads a value and must read the stream the value is on. A single return that
    collapsed them would make the check read stderr the moment the tool wrote ANYTHING there -- an
    update notice, a deprecation warning -- after which a correct binary fails its value check and
    the lane rolls the machine back every night. Fail-closed, and wrong. (`rtk --version` writes to
    stdout alone today, measured 2026-09-06; the point is that nothing here should depend on it
    continuing to.)

    Only the tail of the diagnostic is kept. A build's full output is large and is not this
    program's to relay; the exit code is the assertion and the line is the hint.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as error:
        return 127, "", f"could not be run: {type(error).__name__}"
    tail = (completed.stderr or completed.stdout or "").strip().splitlines()
    return completed.returncode, completed.stdout, tail[-1][:200] if tail else ""


class RollbackFailed(RuntimeError):
    """The rollback itself could not complete. The worst state this lane can produce."""


def _undo(artifacts: tuple[Path, ...], captured: dict[str, Path]) -> None:
    """Put the root back exactly as it was found: restore what was there, remove what was not.

    THE TWO HALVES ARE ONE OPERATION and are called through this single primitive rather than
    pairwise at each site, because a caller that ran only the first would produce the partial
    rollback ADR-0042 calls worse than none -- a first install would leave an unproven binary
    plus cargo metadata claiming it, after which every later pass compares that record to the
    head, finds them equal, and never retries.

    An `OSError` here -- a full disk, a permissions change mid-rollback -- is raised as this
    module's own error rather than escaping as a bare traceback, so the pass can still say what
    happened. A partially restored install root with no record is the one state most needing one.
    """
    try:
        restore(captured)
        remove_uncaptured(artifacts, captured)
    except OSError as error:
        raise RollbackFailed(
            f"the previous artifact could not be restored: {type(error).__name__}"
        ) from error


def probe(tool: Tool, binary: Path, expected_version: str) -> list[ProbeResult]:
    """Prove the installed binary. Every result is returned, including after the first failure.

    Every probe runs even once one has failed, deliberately: the record is more useful saying
    which two of three worked than saying only that something did not, and nothing here mutates,
    so there is no reason to stop early.
    """
    results: list[ProbeResult] = []
    for arguments in tool.probe_commands:
        code, _, detail = _run([str(binary), *arguments], env=None, timeout=PROBE_TIMEOUT_SECONDS)
        results.append(
            ProbeResult(command=arguments, passed=code == 0, detail=detail if code else "")
        )
    # The version is checked by VALUE, separately from the exit statuses above. `cargo install`
    # reports the version it built; a binary on disk answering a different one means the artifact
    # is not what cargo says it is, which no exit status would catch.
    code, reported, _ = _run([str(binary), "--version"], env=None, timeout=PROBE_TIMEOUT_SECONDS)
    matches = code == 0 and expected_version in reported
    results.append(
        ProbeResult(
            command=("--version", "== ", expected_version),
            passed=matches,
            detail="" if matches else f"reported {reported!r}",
        )
    )
    return results


def install_and_prove(
    *,
    tool: Tool,
    cargo: Cargo,
    install_root: Path,
    backup_root: Path,
) -> InstallOutcome:
    """Back up, install, probe, and restore on any probe failure.

    `install_root` is a parameter rather than a constant so the rollback path can be exercised
    end to end against a scratch root -- proving it against the live `~/.cargo` would mean
    breaking the tool that filters every command in the session doing the proving.
    """
    artifacts = install_artifacts(install_root, tool.name)
    captured = backup(artifacts, backup_root)

    argv = [
        str(cargo.executable),
        "install",
        "--git",
        f"https://github.com/{tool.repository}",
        "--branch",
        tool.branch,
        "--locked",
        "--force",
        "--root",
        str(install_root),
        tool.crate,
    ]
    code, _, detail = _run(argv, env=cargo.environment(), timeout=INSTALL_TIMEOUT_SECONDS)
    if code != 0:
        # RESTORE HERE TOO, and the reason is the TIMEOUT rather than the ordinary build failure.
        # `cargo install --force` builds to a temporary directory and moves on success, so a build
        # that fails writes nothing and this restore copies identical bytes back -- harmless. But
        # `_run` also returns non-zero when it KILLS cargo at `INSTALL_TIMEOUT_SECONDS`, and that
        # can land after the new binary has been moved into place and before `.crates.toml` is
        # written. An earlier version skipped the restore on the premise that a failure never
        # swaps anything, and then reported "the working artifact was left in place" -- an
        # assertion about the machine that is false on exactly that path. Restoring
        # unconditionally makes the premise true rather than leaving it asserted and unchecked.
        _undo(artifacts, captured)
        return InstallOutcome(
            action=ACTION_INSTALL_FAILED,
            installed=read_installed(install_root, tool.crate),
            detail=f"cargo install exited {code}: {detail}",
        )

    installed = read_installed(install_root, tool.crate)
    if installed is None:
        # cargo reported success and left no record, which is a state this program cannot
        # describe. Treated as a failed proof and rolled back, because the alternative is to
        # walk away from an unproven binary.
        _undo(artifacts, captured)
        return InstallOutcome(
            action=ACTION_ROLLED_BACK,
            installed=read_installed(install_root, tool.crate),
            detail="cargo install succeeded and recorded nothing",
        )

    results = probe(tool, install_root / "bin" / tool.name, installed.version)
    if all(result.passed for result in results):
        return InstallOutcome(action=ACTION_INSTALLED, installed=installed, probes=results)

    _undo(artifacts, captured)
    return InstallOutcome(
        action=ACTION_ROLLED_BACK,
        installed=read_installed(install_root, tool.crate),
        probes=results,
        detail="the installed artifact failed its probe and the previous one was restored",
    )
