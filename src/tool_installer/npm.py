"""The npm act: build the landed revision from the fork's source, install it, prove it.

**FROM SOURCE, NOT FROM THE REGISTRY.** The fork is the reviewed artifact; the registry holds
upstream's. So the pass fetches exactly the commit it measured -- a shallow fetch of that SHA, never
"whatever the branch is now" -- and builds it. The fork's git submodules are not fetched: none of
them is in the package's `files` list, so nothing the installed package contains comes from one.

**NO LIFECYCLE SCRIPT RUNS, ANYWHERE.** `npm_config_ignore_scripts` is set for every npm call, so
no dependency's install script runs, and neither do the package's own `prepare` (installs husky
git hooks) or `prepack` (re-runs the whole build through turbo). The build the package needs is
named on the row instead. What still runs is the fork's own build code -- `npm run compile` is
`tsc` plus the fork's `scripts/*.mjs` -- which is the same trust boundary as a crate's `build.rs`
on the cargo row, and is not pretended away. At rea-agents 6.7.0 the only production dependency
with an install script is the optional `frida`, used for dynamic instrumentation; the CLI and the
MCP server start and answer every probe without it having run.

**NOT `npm install -g <tarball>`, AND THAT WAS MEASURED, NOT ASSUMED.** A global install of the
packed tarball resolves dependencies against the registry by semver range: at 6.7.0 eleven
packages landed at versions the fork's lockfile does not name (`jose` 6.2.12 for the locked
6.2.3), and still eight with the lockfile packed as `npm-shrinkwrap.json`. The reviewed lockfile
is the thing the fork's security scan covered, so the install is a production-only `npm ci`
against it, in a staging directory: zero mismatches across all 173 runtime packages.

**NOTHING LIVE IS TOUCHED UNTIL THE BUILD HAS SUCCEEDED.** The build and the staged install happen
in a temporary directory inside the install root, and only a finished tree is renamed into place.
That is this row's counterpart of `cargo install --force` writing only on success.

**THE RECORD IS INSIDE THE ARTIFACT.** `npm pack` does not record the commit it packed (measured:
no `gitHead` in the tarball's `package.json`), so the pass writes `gitHead` into the manifest
before packing. The installed `package.json` then says which revision it is, and cannot drift from
the files beside it the way a separate state file could. A `package.json` with no `gitHead` was
not installed by this lane and reads as nothing installed.

**A ROLLBACK RESTORES THE PACKAGE DIRECTORY AND THE `bin/` LINK, TOGETHER.** Restoring the
directory alone would leave the link of a first install pointing at nothing; restoring the link
alone would leave the new package's record claiming the revision. The previous directory is MOVED
aside rather than copied -- it is about 137M -- into the same durable backup root the cargo row
uses, and stays there after a successful pass for the reason that root exists.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    INSTALL_TIMEOUT_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    InstallOutcome,
    ProbeResult,
    RollbackFailed,
    _run,
    probe,
)
from tool_installer.toolchain import Node
from tool_installer.tools import NpmInstall, Tool

# A prefix of its own rather than Homebrew's global one, which belongs to Homebrew's npm and to
# whatever a person installs there by hand. Laid out as `npm install -g --prefix` would lay it out.
DEFAULT_NPM_ROOT: Final = Path.home() / ".local" / "share" / "sds-tools" / "npm"

REVISION_FIELD: Final = "gitHead"
_REVISION: Final = re.compile(r"[0-9a-f]{40}")

MCP_PROTOCOL_VERSION: Final = "2025-06-18"


class NpmError(RuntimeError):
    """A step of the build refused. Always a finding, never a default."""


@dataclass(frozen=True)
class NpmInstalled:
    """What the installed package's own manifest says it is."""

    version: str
    revision: str


@dataclass(frozen=True)
class _Outgoing:
    """What a rollback must put back: where the old package went, and where the old link pointed."""

    package: Path | None
    link: str | None


def package_dir(install_root: Path, package: str) -> Path:
    return install_root / "lib" / "node_modules" / package


def read_installed_package(install_root: Path, package: str) -> NpmInstalled | None:
    """The installed manifest's version and `gitHead`; `None` when this lane installed nothing."""
    try:
        document = json.loads(
            (package_dir(install_root, package) / "package.json").read_text(encoding="utf-8")
        )
    except OSError, ValueError:
        return None
    if not isinstance(document, dict):
        return None
    version = document.get("version")
    revision = document.get(REVISION_FIELD)
    if isinstance(version, str) and isinstance(revision, str) and _REVISION.fullmatch(revision):
        return NpmInstalled(version=version, revision=revision)
    return None


def _environment(node: Node) -> dict[str, str]:
    """Node's environment, plus the two refusals this act depends on: no lifecycle scripts, and
    no credential prompt -- under launchd there is no terminal to answer one."""
    return {
        **node.environment(),
        "npm_config_ignore_scripts": "true",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
    }


def _step(argv: list[str], *, cwd: Path | None, env: dict[str, str]) -> None:
    code, _, detail = _run(argv, env=env, timeout=INSTALL_TIMEOUT_SECONDS, cwd=cwd)
    if code != 0:
        shown = " ".join([Path(argv[0]).name, *argv[1:3]])
        raise NpmError(f"`{shown}` exited {code}: {detail}")


def _build(
    tool: Tool, row: NpmInstall, node: Node, work: Path, revision: str, source: str
) -> tuple[Path, str]:
    """Fetch, build, pack and stage. Returns the staged package directory and its `bin` entry."""
    env = _environment(node)
    npm = str(node.npm)
    src = work / "src"
    _step(["git", "init", "-q", str(src)], cwd=None, env=env)
    _step(
        ["git", "-C", str(src), "fetch", "-q", "--depth", "1", source, revision], cwd=None, env=env
    )
    _step(["git", "-C", str(src), "checkout", "-q", "FETCH_HEAD"], cwd=None, env=env)
    _step([npm, "ci"], cwd=src, env=env)
    for command in row.build_commands:
        _step([npm, *command], cwd=src, env=env)

    manifest = src / "package.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document[REVISION_FIELD] = revision
    manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    packed = work / "packed"
    packed.mkdir()
    _step([npm, "pack", "--pack-destination", str(packed)], cwd=src, env=env)
    tarballs = list(packed.glob("*.tgz"))
    if len(tarballs) != 1:
        raise NpmError(f"npm pack produced {len(tarballs)} tarballs")
    with tarfile.open(tarballs[0]) as archive:
        archive.extractall(work / "stage", filter="data")
    staged = work / "stage" / "package"
    shutil.copy2(src / "package-lock.json", staged / "package-lock.json")
    _step([npm, "ci", "--omit=dev"], cwd=staged, env=env)

    bins = json.loads((staged / "package.json").read_text(encoding="utf-8")).get("bin")
    entry = bins.get(tool.name) if isinstance(bins, dict) else None
    if not isinstance(entry, str):
        raise NpmError(f"{row.package} declares no `{tool.name}` in its bin map")
    return staged, entry.removeprefix("./")


def _move_aside(target: Path, backup_root: Path) -> Path | None:
    if not target.exists():
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    moved = backup_root / target.name
    # Only when the outgoing VERSION was backed up once already, from a different revision; the
    # newer copy is the one worth keeping.
    if moved.exists():
        shutil.rmtree(moved)
    shutil.move(target, moved)
    return moved


def _place(staged: Path, target: Path, link: Path, link_target: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(link_target)


def _undo(target: Path, link: Path, outgoing: _Outgoing) -> None:
    """Both halves, under one guard -- see the module docstring and `install._undo`."""
    try:
        if target.exists():
            shutil.rmtree(target)
        if outgoing.package is not None:
            shutil.move(outgoing.package, target)
        if link.is_symlink() or link.exists():
            link.unlink()
        if outgoing.link is not None:
            link.symlink_to(outgoing.link)
    except OSError as error:
        raise RollbackFailed(
            f"the previous package could not be restored: {type(error).__name__}"
        ) from error


def probe_mcp(
    row: NpmInstall, binary: Path, expected_version: str, env: dict[str, str]
) -> ProbeResult:
    """Start the MCP server, send one `initialize`, and check the reported version by VALUE.

    The server reads stdin and exits when it closes, so one request and EOF is the whole
    exchange: measured at 6.7.0, 0.5-0.7s with network denied and nothing written under `HOME`.
    """
    command = (*row.mcp_arguments, "initialize", "serverInfo.version ==", expected_version)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sds-tool-installer", "version": "1"},
        },
    }
    try:
        completed = subprocess.run(  # noqa: S603
            [str(binary), *row.mcp_arguments],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return ProbeResult(command=command, passed=False, detail=type(error).__name__)
    lines = completed.stdout.strip().splitlines()
    try:
        reported = json.loads(lines[0])["result"]["serverInfo"]["version"]
    except IndexError, ValueError, KeyError, TypeError:
        reported = None
    passed = completed.returncode == 0 and reported == expected_version
    detail = "" if passed else f"exited {completed.returncode}, reported {reported!r}"
    return ProbeResult(command=command, passed=passed, detail=detail)


def install_and_prove_npm(
    *,
    tool: Tool,
    node: Node,
    install_root: Path,
    backup_root: Path,
    revision: str,
    source: str | None = None,
) -> InstallOutcome:
    """Build `revision` in staging, swap it in, probe it, and restore on any failure.

    `source` defaults to the fork on GitHub; it is a parameter so the act can be exercised end to
    end against a local repository, as `install_root` is for the same reason on the cargo row.
    """
    row = tool.install
    if not isinstance(row, NpmInstall):
        raise TypeError(f"{tool.name} is not an npm row")
    target = package_dir(install_root, row.package)
    link = install_root / "bin" / tool.name
    env = _environment(node)
    install_root.mkdir(parents=True, exist_ok=True)

    def failed(detail: str) -> InstallOutcome:
        return InstallOutcome(
            action=ACTION_INSTALL_FAILED,
            installed=read_installed_package(install_root, row.package),
            detail=detail,
        )

    with tempfile.TemporaryDirectory(
        dir=install_root, prefix=".sds-build-", ignore_cleanup_errors=True
    ) as work:
        try:
            staged, entry = _build(
                tool,
                row,
                node,
                Path(work),
                revision,
                source or f"https://github.com/{tool.repository}",
            )
        except (NpmError, OSError, ValueError, tarfile.TarError) as error:
            return failed(str(error) or type(error).__name__)

        previous_link = os.readlink(link) if link.is_symlink() else None
        try:
            outgoing = _Outgoing(package=_move_aside(target, backup_root), link=previous_link)
        except OSError as error:
            return failed(f"the previous package could not be moved aside: {type(error).__name__}")
        try:
            _place(staged, target, link, f"../lib/node_modules/{row.package}/{entry}")
        except OSError as error:
            _undo(target, link, outgoing)
            return failed(f"the new package could not be put in place: {type(error).__name__}")

    installed = read_installed_package(install_root, row.package)
    if installed is None or installed.revision != revision:
        _undo(target, link, outgoing)
        return InstallOutcome(
            action=ACTION_ROLLED_BACK,
            installed=read_installed_package(install_root, row.package),
            detail="the installed package does not record the revision it was built from",
        )

    results = probe(row, link, installed.version, env=env)
    results.append(probe_mcp(row, link, installed.version, env))
    if all(result.passed for result in results):
        return InstallOutcome(action=ACTION_INSTALLED, installed=installed, probes=results)

    _undo(target, link, outgoing)
    return InstallOutcome(
        action=ACTION_ROLLED_BACK,
        installed=read_installed_package(install_root, row.package),
        probes=results,
        detail="the installed artifact failed its probe and the previous one was restored",
    )
