"""Finding cargo, and asking cargo what it installed. Both answers are measured, not assumed.

**THE TOOLCHAIN IS NOT ON A SCHEDULED JOB'S `PATH`, AND NEITHER IS `rustup`.** Measured
2026-09-06 under `env -i PATH=/usr/bin:/bin HOME=$HOME`: `command -v rustup` finds nothing, while
`/opt/homebrew/bin/rustup` exists and works. `~/.cargo/bin` holds `rtk` alone -- there is no
`cargo` and no `rustup` in it. This is the `uv` trap one tool over, and a lane that looked for
`cargo` on `PATH` would report unmeasurable every morning while working perfectly from a shell.

**`rustup which cargo` IS NECESSARY AND NOT SUFFICIENT, AND THAT IS THE HALF THAT IS EASY TO
MISS.** The resolved cargo runs and answers `--version`, and then cannot build:

    $ env -i PATH=/usr/bin:/bin HOME=$HOME sh -c '"$(rustup which cargo)" build'
    error: could not execute process `rustc -vV` (never executed)
    Caused by: No such file or directory (os error 2)

`cargo` shells out to `rustc`, which lives beside it in the toolchain directory and is likewise
not on `PATH`. Measured the same day, with clean exit codes, on a scratch crate:

    rustup run stable cargo build   ->  rc 101   (fails identically)
    PATH=<toolchain bin>:$PATH cargo build  ->  rc 0

So `rustup run` is NOT the fix, which is the answer a reader would reach for first. What works is
prepending the toolchain's own `bin/` to the subprocess environment, which is what
`cargo_environment` does. A probe that stops at `cargo --version` proves the wrong thing; the
control for this module is a real `cargo build`, and it is in the acceptance record rather than
in the suite, because a build takes longer than a test should.

**ASKING CARGO WHAT IS INSTALLED, RATHER THAN KEEPING A STATE FILE.** `cargo install --git`
records the exact commit it built from, in the install root's own `.crates.toml`:

    "rtk 0.48.0 (git+https://github.com/AlobarQuest/rtk?branch=main#4be91ebe...)" = ["rtk"]

That is cargo's record, written by the act itself, so it cannot drift from the binary the way a
file this lane maintained separately could. It is why "is the installed artifact the fork's head"
is answerable at all without rebuilding anything.

**THREE FILES MAKE UP AN INSTALL, AND A ROLLBACK THAT RESTORES ONE OF THEM IS WORSE THAN NO
ROLLBACK.** `cargo install --force` writes the binary AND `.crates.toml` AND `.crates2.json`, and
it writes the metadata on a successful BUILD -- which is before this lane has probed anything. So
a build that succeeds and a probe that fails leaves metadata claiming the new revision is
installed. Restoring only the binary would leave the machine running the old tool while cargo's
record says otherwise, and the next pass would compare that record against the head, find them
equal, report nothing to do, and never retry -- a rollback that becomes permanent and invisible.
`backup` and `restore` therefore cover all three, and after a restore the next pass sees an
installed revision behind the head and reports it, every pass, until somebody acts. A standing
finding is the honest shape; silence is not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Where `rustup` is looked for, in order, when it is not on `PATH`. Homebrew first because that is
# where it is on this machine (`/opt/homebrew/bin/rustup -> ../Cellar/rustup/1.29.0_2/bin/rustup`,
# measured 2026-09-06); the cargo home second because that is where a `rustup-init` install puts
# it, which is the arrangement any other machine is likelier to have.
RUSTUP_CANDIDATES = (
    Path("/opt/homebrew/bin/rustup"),
    Path("/usr/local/bin/rustup"),
    Path.home() / ".cargo" / "bin" / "rustup",
)

DEFAULT_INSTALL_ROOT = Path.home() / ".cargo"

# WHERE THE OUTGOING ARTIFACT IS KEPT, and it is DURABLE rather than temporary. The precedent is
# already on this machine: `~/.local/bin-backups/rtk-0.42.4`, 6.1M, put there by hand in June and
# still there. What it buys is the case the probe cannot cover -- a new binary that passes every
# check and then misbehaves in use hours later. The in-pass rollback handles a failed proof; this
# handles a bad artifact nobody has noticed yet, and it is only useful if it survives the pass.
# A directory per version rather than a file, because an install is three artifacts and a rollback
# that restored one of them would be worse than none (see this module's docstring).
DEFAULT_BACKUP_ROOT = Path.home() / ".local" / "bin-backups"

# `cargo install --root <dir>` puts the binary at `<dir>/bin/<name>` and its record at
# `<dir>/.crates.toml`, with a second copy of the record in `<dir>/.crates2.json`. Naming all
# three here is what lets `backup`/`restore` be complete by construction rather than by memory.
CRATES_TOML = ".crates.toml"
CRATES_JSON = ".crates2.json"

# `"rtk 0.48.0 (git+https://github.com/AlobarQuest/rtk?branch=main#<40 hex>)" = ["rtk"]`
_INSTALL_LINE = re.compile(
    r'^"(?P<crate>\S+)\s+(?P<version>\S+)\s+\(git\+(?P<url>[^#)]+)#(?P<revision>[0-9a-f]{40})\)"'
)


class ToolchainError(RuntimeError):
    """The toolchain cannot be used, which is this lane failing to read its own inputs."""


@dataclass(frozen=True)
class Cargo:
    """A usable cargo: the executable, and the environment it must be run in to work."""

    executable: Path
    toolchain_bin: Path

    def environment(self) -> dict[str, str]:
        """The subprocess environment, with the toolchain's `bin/` at the FRONT of `PATH`.

        At the front rather than appended: an install root's `bin/` may hold a shim with the same
        name, and the toolchain that resolved `cargo` is the one that must supply its `rustc`.
        """
        env = dict(os.environ)
        env["PATH"] = f"{self.toolchain_bin}{os.pathsep}{env.get('PATH', '')}".rstrip(os.pathsep)
        return env


@dataclass(frozen=True)
class Installed:
    """What cargo says is installed for one crate under one root."""

    crate: str
    version: str
    revision: str
    url: str


def find_rustup(candidates: tuple[Path, ...] = RUSTUP_CANDIDATES) -> Path:
    """`PATH` first, then the known locations. Never `PATH` alone -- see the module docstring."""
    found = shutil.which("rustup")
    if found:
        return Path(found)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ToolchainError(
        f"rustup is not on PATH and is at none of {', '.join(str(path) for path in candidates)}"
    )


def resolve_cargo(rustup: Path | None = None) -> Cargo:
    """Ask rustup for cargo, and keep the directory it came from -- `rustc` lives there too."""
    binary = rustup or find_rustup()
    try:
        completed = subprocess.run(  # noqa: S603
            [str(binary), "which", "cargo"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ToolchainError(f"rustup could not be run: {type(error).__name__}") from error
    if completed.returncode != 0:
        raise ToolchainError(f"`rustup which cargo` exited {completed.returncode}")
    path = Path(completed.stdout.strip())
    if not path.is_file():
        raise ToolchainError(f"rustup named a cargo that is not there: {path}")
    return Cargo(executable=path, toolchain_bin=path.parent)


def read_installed(install_root: Path, crate: str) -> Installed | None:
    """Cargo's own record for one crate, or `None` when it has never installed it.

    `None` is a first install, which is allowed -- deliberately not an error, and deliberately not
    confused with a record that exists and names a different revision.
    """
    record = install_root / CRATES_TOML
    try:
        text = record.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = _INSTALL_LINE.match(line.strip())
        if match and match.group("crate") == crate:
            return Installed(
                crate=crate,
                version=match.group("version"),
                revision=match.group("revision"),
                url=match.group("url"),
            )
    return None


def install_artifacts(install_root: Path, tool_name: str) -> tuple[Path, ...]:
    """Every path an install writes. The completeness of this tuple IS the rollback's soundness."""
    return (
        install_root / "bin" / tool_name,
        install_root / CRATES_TOML,
        install_root / CRATES_JSON,
    )


def backup(paths: tuple[Path, ...], destination: Path) -> dict[str, Path]:
    """Copy each artifact that EXISTS into `destination`, returning what was captured.

    A missing artifact is omitted rather than recorded as empty, so `restore` can tell "there was
    nothing here" from "there was something and it was empty" -- the first is a first install and
    must leave nothing behind on a rollback.
    """
    destination.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Path] = {}
    for path in paths:
        if not path.is_file():
            continue
        target = destination / path.name
        shutil.copy2(path, target)
        captured[str(path)] = target
    return captured


def restore(captured: dict[str, Path]) -> None:
    """Put every captured artifact back. THIS IS HALF OF A ROLLBACK, NOT A ROLLBACK.

    The other half is `remove_uncaptured`, and an earlier version of this docstring claimed both
    happened here -- which is the failure this file warns about one paragraph up, in a docstring
    rather than in code. A caller that trusted it would perform exactly the partial rollback
    ADR-0042 calls worse than none: on a first install there is no outgoing binary, so restoring
    alone leaves behind an unproven one plus cargo metadata claiming it.

    Call `install.py::_undo`, which runs both halves under one guard, rather than either of these
    directly.
    """
    for original, copy in captured.items():
        shutil.copy2(copy, original)


def remove_uncaptured(paths: tuple[Path, ...], captured: dict[str, Path]) -> None:
    """Delete artifacts an install created that were not there before it ran.

    The other half of a rollback; see `restore`. Both are driven from `install_artifacts`, so
    neither can fall behind the other as the set of artifacts changes.
    """
    for path in paths:
        if str(path) in captured:
            continue
        if path.is_file():
            path.unlink()
