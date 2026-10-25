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

**A WORKSPACE PACKAGE INSTALLS THE WHOLE BUILT CHECKOUT.** `n8nac` is `packages/cli` of an npm
workspace monorepo, and its runtime dependencies are sibling workspaces the ROOT lockfile records
as links. Packing that one directory and installing it would fetch the siblings from the registry
by name -- upstream's code, defeating the fork -- so the pack-then-stage path above cannot serve
it, and `npm ci --workspace=packages/cli` was measured leaving 5 packages missing. What was
measured to work at n8nac 2.7.0: the same fetch, `npm ci` and build at the checkout's root, then a
production-only `npm ci` at the root. The built `dist/` directories sit outside `node_modules` and
survive it, the siblings are relative symlinks into `packages/` that survive the rename into
place, and `npm ls --omit=dev --all` reports nothing invalid or missing. The checkout's `.git` is
removed, and `gitHead` is written into the WORKSPACE's manifest after the prune, which is where
the record is then read from.

**PUBLISHED DATA, NEVER PUBLISHED CODE.** The CLI's knowledge commands read JSON from
`packages/skills/dist/assets/` that only upstream's full build produces, and that build builds n8n
itself. So those files -- and only those -- are taken from upstream's published package at the
version the fork's own workspace names: `npm pack` downloads it (npm checks the registry's
integrity), and the tarball is opened here rather than extracted. Only regular `.json` files
directly inside the named directory land, each created fresh; a link, a nested path or any other
member in that directory refuses the pass, and the code beside it in `dist/` is never read.
The workspace manifest records the package, version, integrity and file names it received.

**THE NPM PREFIX'S LINK, LAST.** The n8n-as-code plugin runs `npx --yes n8nac`, and npx resolves
that from `<npm prefix -g>/bin/n8nac` -- measured: a prefix holding only `bin/n8nac` resolves, one
holding only `lib/node_modules/n8nac` does not, and with neither npx downloads upstream from the
registry. So that row also points the prefix's link at this lane's own `bin/n8nac`, by an atomic
rename, only after every probe has passed, and then proves the version once more THROUGH that
link, because that path is the one npx runs.

The link is taken over only from three states: absent, already this lane's, or the shape `npm
link` leaves -- `lib/node_modules/n8nac` a symlink (or gone) and `bin/n8nac` pointing through it.
Anything else is someone else's install -- a real `npm install -g` puts a DIRECTORY at
`lib/node_modules/n8nac` -- and the pass refuses before building anything. The build takes
minutes, so the link is read again just before it is written, and a rollback puts back only the
link this pass wrote: one that changed meanwhile belongs to whoever changed it. A rollback
restores the prefix's link FIRST, since it is the path npx reads, so a rollback that fails partway
cannot leave npx pointing into a removed tree. The `npm link` package is removed last, only once
the new link has proven itself, and only when it is a symlink.
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
from tool_installer.tools import NpmInstall, PublishedAssets, Tool

# A prefix of its own rather than Homebrew's global one, which belongs to Homebrew's npm and to
# whatever a person installs there by hand. Laid out as `npm install -g --prefix` would lay it out.
DEFAULT_NPM_ROOT: Final = Path.home() / ".local" / "share" / "sds-tools" / "npm"

REVISION_FIELD: Final = "gitHead"
BUILD_PREFIX: Final = ".sds-build-"
_REVISION: Final = re.compile(r"[0-9a-f]{40}")
# A plain release version: the published data is fetched by exactly this string, so a range, a tag
# or anything else npm would resolve to some other version is refused.
_VERSION: Final = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
PUBLISHED_ASSETS_FIELD: Final = "sdsPublishedAssets"

MCP_PROTOCOL_VERSION: Final = "2025-06-18"


class NpmError(RuntimeError):
    """A step of the build refused. Always a finding, never a default."""


@dataclass(frozen=True)
class NpmInstalled:
    """What the installed package's own manifest says it is."""

    version: str
    revision: str


@dataclass(frozen=True)
class _PrefixLink:
    """The npm prefix's link for the tool: where it pointed when the pass read it (`None`: it was
    absent), and the lane's own `bin/` entry the pass points it at."""

    path: Path
    previous: str | None
    own: str


@dataclass(frozen=True)
class _Outgoing:
    """What a rollback must put back: where the old package went, and where the old link pointed.

    `prefix` is restored only while it still holds what this pass wrote, so a rollback before the
    write, or after someone else changed it, leaves it exactly as it is.
    """

    package: Path | None
    link: str | None
    prefix: _PrefixLink | None = None


def package_dir(install_root: Path, package: str) -> Path:
    return install_root / "lib" / "node_modules" / package


def read_installed_package(
    install_root: Path, package: str, workspace: str | None = None
) -> NpmInstalled | None:
    """The installed manifest's version and `gitHead`; `None` when this lane installed nothing.

    For a workspace package the manifest is the workspace's, where the pass recorded `gitHead`.
    """
    manifest = package_dir(install_root, package) / (workspace or "") / "package.json"
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
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


def _read_manifest(manifest: Path) -> dict[str, object]:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise NpmError(f"{manifest.name} is not an object")
    return document


def _write_manifest(manifest: Path, document: dict[str, object]) -> None:
    manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _bin_entry(tool: Tool, row: NpmInstall, manifest: Path) -> str:
    bins = _read_manifest(manifest).get("bin")
    entry = bins.get(tool.name) if isinstance(bins, dict) else None
    if not isinstance(entry, str):
        raise NpmError(f"{row.package} declares no `{tool.name}` in its bin map")
    return entry.removeprefix("./")


def _build(
    tool: Tool, row: NpmInstall, node: Node, work: Path, revision: str, source: str
) -> tuple[Path, str]:
    """Fetch, build, pack and stage. Returns the staged package directory and its `bin` entry,
    relative to that directory."""
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
    if row.workspace is not None:
        return _stage_workspace(tool, row, npm, env, work, src, revision)

    manifest = src / "package.json"
    document = _read_manifest(manifest)
    document[REVISION_FIELD] = revision
    _write_manifest(manifest, document)

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
    return staged, _bin_entry(tool, row, staged / "package.json")


def _stage_workspace(
    tool: Tool, row: NpmInstall, npm: str, env: dict[str, str], work: Path, src: Path, revision: str
) -> tuple[Path, str]:
    """The built checkout itself is the staged tree -- see the module docstring for why."""
    workspace = row.workspace
    if workspace is None:
        raise TypeError(f"{row.package} is not a workspace package")
    _step([npm, "ci", "--omit=dev"], cwd=src, env=env)
    shutil.rmtree(src / ".git")
    if row.published_assets is not None:
        _place_published_assets(row.published_assets, npm, env, work, src)
    # After the prune, so nothing the production install rewrites can drop the record.
    manifest = src / workspace / "package.json"
    document = _read_manifest(manifest)
    document[REVISION_FIELD] = revision
    _write_manifest(manifest, document)
    return src, f"{workspace}/{_bin_entry(tool, row, manifest)}"


def _assets_destination(assets: PublishedAssets, checkout: Path) -> Path:
    """The directory the data lands in, made and checked to be inside the checkout and empty."""
    destination = checkout / assets.workspace / assets.directory
    # Every component is checked BEFORE anything is made: a symlinked parent would carry the
    # files -- and `mkdir` -- somewhere outside the tree being installed.
    component = checkout
    for part in destination.relative_to(checkout).parts:
        component = component / part
        if component.is_symlink():
            raise NpmError(
                f"{component.relative_to(checkout)} is a symlink; the data could land "
                "outside the checkout"
            )
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.resolve().is_relative_to(checkout.resolve()):
        raise NpmError(f"{assets.directory} resolves outside the checkout")
    present = sorted(path.name for path in destination.iterdir())
    if present:
        raise NpmError(
            f"{destination.relative_to(checkout)} already holds {', '.join(present)}: the fork's "
            "build and the published data both wrote there"
        )
    return destination


def _place_published_assets(
    assets: PublishedAssets, npm: str, env: dict[str, str], work: Path, checkout: Path
) -> None:
    """Fetch upstream's published package and copy ONLY its data files into the checkout."""
    manifest = checkout / assets.workspace / "package.json"
    document = _read_manifest(manifest)
    version = document.get("version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise NpmError(f"{assets.workspace} names no plain version to fetch: {version!r}")
    destination = _assets_destination(assets, checkout)

    # An empty directory as the working directory, so no project or `.npmrc` of the checkout's
    # is in effect for the download.
    download = work / "published"
    download.mkdir()
    spec = f"{assets.package}@{version}"
    code, stdout, detail = _run(
        [npm, "pack", spec, "--json", "--pack-destination", str(download)],
        env=env,
        timeout=INSTALL_TIMEOUT_SECONDS,
        cwd=download,
    )
    if code != 0:
        raise NpmError(f"`npm pack {spec}` exited {code}: {detail}")
    try:
        packed = json.loads(stdout)[0]
        filename, integrity = packed["filename"], packed["integrity"]
    except (ValueError, LookupError, TypeError) as error:
        raise NpmError(f"`npm pack {spec}` reported no tarball") from error
    if not isinstance(filename, str) or "/" in filename or not isinstance(integrity, str):
        raise NpmError(f"`npm pack {spec}` reported no usable tarball")

    prefix = f"package/{assets.directory}/"
    landed: list[str] = []
    with tarfile.open(download / filename) as archive:
        for member in archive.getmembers():
            if not member.name.startswith(prefix):
                continue
            name = member.name.removeprefix(prefix)
            # A link could point anywhere, and a nested or dotted name could climb out of the
            # directory; neither is what the data is, so either refuses the whole pass. (`""`,
            # `.` and `..` need no test of their own: none of them ends `.json`.)
            if not member.isfile() or "/" in name or not name.endswith(".json"):
                raise NpmError(f"{spec} holds {member.name!r}, which is not a data file")
            data = archive.extractfile(member)
            if data is None:
                raise NpmError(f"{spec} holds {member.name!r} with no content")
            # `x`: a name the tarball repeats refuses rather than overwriting.
            with open(destination / name, "xb") as written:
                shutil.copyfileobj(data, written)
            landed.append(name)
    if not landed:
        raise NpmError(f"{spec} holds no data files under {assets.directory}")

    document[PUBLISHED_ASSETS_FIELD] = {
        "package": assets.package,
        "version": version,
        "integrity": integrity,
        "files": sorted(landed),
    }
    _write_manifest(manifest, document)


def _move_aside(target: Path, backup_root: Path) -> Path | None:
    if not target.exists():
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    moved = backup_root / target.name
    # Only when the outgoing VERSION was backed up once already, from a different revision; the
    # newer copy is the one worth keeping.
    if moved.exists():
        shutil.rmtree(moved)
    # A RENAME, never `shutil.move`: across volumes that is copy-then-delete, and a failure partway
    # through the delete leaves the live package half gone. A rename is all or nothing, and a
    # backup root on another volume refuses here with the live package untouched.
    target.rename(moved)
    return moved


def _place(staged: Path, target: Path, link: Path, link_target: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(link_target)
    # npm's own bin-linking sets this; the lane links by hand, so it sets it too.
    entry = link.resolve()
    entry.chmod(entry.stat().st_mode | 0o111)


def _undo(target: Path, link: Path, outgoing: _Outgoing) -> None:
    """Both halves, under one guard -- see the module docstring and `install._undo`."""
    try:
        # The prefix's link FIRST: it is the path npx reads, and restoring it last would leave it
        # pointing into a removed tree whenever a later step of this rollback failed.
        if outgoing.prefix is not None:
            _restore_prefix_link(outgoing.prefix)
        if target.exists():
            shutil.rmtree(target)
        if outgoing.package is not None:
            outgoing.package.rename(target)
        if link.is_symlink() or link.exists():
            link.unlink()
        if outgoing.link is not None:
            link.symlink_to(outgoing.link)
    except OSError as error:
        raise RollbackFailed(
            f"the previous package could not be restored: {type(error).__name__}"
        ) from error


def _read_link(path: Path) -> str | None:
    """Where the symlink `path` points, `None` when nothing is there; refuses anything else."""
    if path.is_symlink():
        return os.readlink(path)
    if path.exists():
        raise NpmError(f"{path} is not a symlink, so it is not this lane's to replace")
    return None


def _linked_package(prefix: Path, package: str) -> Path:
    return prefix / "lib" / "node_modules" / package


def _npm_prefix_link(
    tool: Tool, row: NpmInstall, node: Node, env: dict[str, str], own: Path
) -> _PrefixLink:
    """Where `npx` will look for the tool, and what is there now -- refused unless it is absent,
    already `own`, or what `npm link` leaves. See the module docstring."""
    code, stdout, detail = _run(
        [str(node.npm), "prefix", "-g"], env=env, timeout=PROBE_TIMEOUT_SECONDS
    )
    prefix = Path(stdout.strip())
    if code != 0 or not prefix.is_absolute() or not prefix.is_dir():
        raise NpmError(f"`npm prefix -g` exited {code} with no usable prefix: {detail}")
    linked = _linked_package(prefix, row.package)
    if linked.exists() and not linked.is_symlink():
        raise NpmError(f"{linked} is a real install, not an `npm link`; it is not this lane's")
    path = prefix / "bin" / tool.name
    previous = _read_link(path)
    if previous is not None and previous != str(own):
        # Resolved as text, not through the filesystem: the question is whether the link goes
        # THROUGH the linked package, which may be a dangling symlink by now.
        through = os.path.normpath(os.path.join(path.parent, previous))
        if not through.startswith(f"{linked}{os.sep}"):
            raise NpmError(
                f"{path} points at {previous!r}, which is neither this lane's nor an `npm link`"
            )
    return _PrefixLink(path=path, previous=previous, own=str(own))


def _repoint(path: Path, target: str | None) -> None:
    """Make the symlink `path` point at `target` (`None`: no link), changing nothing if it does.

    By a rename over the old entry, so at no instant is there no link for npx to find. A link
    already pointing where it should is left alone, which is what lets a rollback after a write
    that never happened succeed in a directory it could not write either.
    """
    current = os.readlink(path) if path.is_symlink() else None
    if current == target:
        return
    if target is None:
        path.unlink()
        return
    staged = path.with_name(f".{path.name}{BUILD_PREFIX}{os.getpid()}")
    if staged.is_symlink():
        staged.unlink()
    staged.symlink_to(target)
    os.replace(staged, path)


def _restore_prefix_link(prefix: _PrefixLink) -> None:
    # Only the link this pass wrote is put back; one changed since belongs to whoever changed it.
    if prefix.path.is_symlink() and os.readlink(prefix.path) == prefix.own:
        _repoint(prefix.path, prefix.previous)


def _write_prefix_link(prefix: _PrefixLink) -> None:
    """Point the prefix's link at the lane's own, if it still holds what the pass first read."""
    if _read_link(prefix.path) != prefix.previous:
        raise NpmError(f"{prefix.path} changed during the pass, so it is left as it is now")
    _repoint(prefix.path, prefix.own)


def _probe_through(path: Path, expected_version: str, env: dict[str, str]) -> ProbeResult:
    """The version by VALUE, through the path npx runs rather than the lane's own."""
    code, reported, _ = _run([str(path), "--version"], env=env, timeout=PROBE_TIMEOUT_SECONDS)
    passed = code == 0 and expected_version in reported
    return ProbeResult(
        command=(str(path), "--version", "== ", expected_version),
        passed=passed,
        detail="" if passed else f"reported {reported!r}",
    )


def _take_npm_prefix(
    prefix: _PrefixLink, row: NpmInstall, expected_version: str, env: dict[str, str]
) -> ProbeResult:
    """Write the prefix's link, prove it, and only then drop the `npm link` package.

    The package goes LAST: the previous link may resolve through it, so removing it first could
    leave a rollback restoring a link pointing at nothing.
    """
    _write_prefix_link(prefix)
    result = _probe_through(prefix.path, expected_version, _probe_environment(row, env))
    if result.passed:
        linked = _linked_package(prefix.path.parent.parent, row.package)
        if linked.is_symlink():
            linked.unlink()
    return result


def probe_mcp(
    arguments: tuple[str, ...],
    binary: Path,
    expected_version: str,
    env: dict[str, str],
    *,
    exit_code: int = 0,
) -> ProbeResult:
    """Start the MCP server, send one `initialize`, and check the reported version by VALUE.

    The server reads stdin and exits when it closes, so one request and EOF is the whole
    exchange: measured at 6.7.0, 0.5-0.7s with network denied and nothing written under `HOME`.
    `exit_code` is how the row says its server ends on EOF; anything else fails.
    """
    command = (*arguments, "initialize", "serverInfo.version ==", expected_version)
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
            [str(binary), *arguments],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return ProbeResult(command=command, passed=False, detail=type(error).__name__)
    # The response is found by its `id`, not by position: a server may emit a notification first.
    reported = None
    for line in completed.stdout.splitlines():
        try:
            message = json.loads(line)
            if message.get("id") == request["id"]:
                reported = message["result"]["serverInfo"]["version"]
                break
        except ValueError, KeyError, TypeError, AttributeError:
            continue
    passed = completed.returncode == exit_code and reported == expected_version
    detail = "" if passed else f"exited {completed.returncode}, reported {reported!r}"
    return ProbeResult(command=command, passed=passed, detail=detail)


def _probe_environment(row: NpmInstall, env: dict[str, str]) -> dict[str, str]:
    return {**env, **dict(row.probe_environment)}


def _prove(
    row: NpmInstall, binary: Path, expected_version: str, env: dict[str, str]
) -> list[ProbeResult]:
    """Every probe the row declares, plus the MCP exchange when it serves one."""
    env = _probe_environment(row, env)
    results = probe(row, binary, expected_version, env=env)
    if row.mcp_arguments is not None:
        version = row.mcp_version or expected_version
        results.append(
            probe_mcp(row.mcp_arguments, binary, version, env, exit_code=row.mcp_exit_code)
        )
    return results


def _rolled_back(
    install_root: Path, row: NpmInstall, detail: str, probes: list[ProbeResult]
) -> InstallOutcome:
    return InstallOutcome(
        action=ACTION_ROLLED_BACK,
        installed=read_installed_package(install_root, row.package, row.workspace),
        probes=probes,
        detail=detail,
    )


def _prove_in_place(
    tool: Tool,
    row: NpmInstall,
    install_root: Path,
    outgoing: _Outgoing,
    revision: str,
    env: dict[str, str],
) -> InstallOutcome:
    """Everything after the swap: check the record, probe, and only then write the prefix link."""
    prefix = outgoing.prefix
    target = package_dir(install_root, row.package)
    link = install_root / "bin" / tool.name
    installed = read_installed_package(install_root, row.package, row.workspace)
    if installed is None or installed.revision != revision:
        _undo(target, link, outgoing)
        return _rolled_back(
            install_root,
            row,
            "the installed package does not record the revision it was built from",
            [],
        )

    results = _prove(row, link, installed.version, env)
    if not all(result.passed for result in results):
        _undo(target, link, outgoing)
        return _rolled_back(
            install_root,
            row,
            "the installed artifact failed its probe and the previous one was restored",
            results,
        )

    if prefix is not None:
        try:
            through = _take_npm_prefix(prefix, row, installed.version, env)
        except NpmError as error:
            _undo(target, link, outgoing)
            return _rolled_back(install_root, row, str(error), results)
        except OSError as error:
            _undo(target, link, outgoing)
            return _rolled_back(
                install_root,
                row,
                f"the npm prefix's link could not be written: {type(error).__name__}",
                results,
            )
        results.append(through)
        if not through.passed:
            _undo(target, link, outgoing)
            return _rolled_back(
                install_root,
                row,
                "the tool failed its probe through the npm prefix's link, and both were restored",
                results,
            )
    return InstallOutcome(action=ACTION_INSTALLED, installed=installed, probes=results)


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

    def failed(detail: str) -> InstallOutcome:
        return InstallOutcome(
            action=ACTION_INSTALL_FAILED,
            installed=read_installed_package(install_root, row.package, row.workspace),
            detail=detail,
        )

    try:
        # Read before anything is built or moved, so a prefix that is someone else's refuses the
        # pass with the machine exactly as it was found.
        prefix = (
            _npm_prefix_link(tool, row, node, env, link.absolute())
            if row.link_into_npm_prefix
            else None
        )
        install_root.mkdir(parents=True, exist_ok=True)
        # A pass killed mid-build (a launchd timeout, a reboot) never reaches the cleanup below,
        # and each such workspace holds two `node_modules` trees.
        for stale in install_root.glob(f"{BUILD_PREFIX}*"):
            shutil.rmtree(stale)
        workspace = tempfile.TemporaryDirectory(
            dir=install_root, prefix=BUILD_PREFIX, ignore_cleanup_errors=True
        )
    except NpmError as error:
        return failed(str(error))
    except OSError as error:
        return failed(f"the install root is not usable: {type(error).__name__}")

    with workspace as work:
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
            outgoing = _Outgoing(
                package=_move_aside(target, backup_root), link=previous_link, prefix=prefix
            )
        except OSError as error:
            return failed(f"the previous package could not be moved aside: {type(error).__name__}")
        try:
            _place(staged, target, link, f"../lib/node_modules/{row.package}/{entry}")
        except OSError as error:
            _undo(target, link, outgoing)
            return failed(f"the new package could not be put in place: {type(error).__name__}")

    return _prove_in_place(tool, row, install_root, outgoing, revision, env)
