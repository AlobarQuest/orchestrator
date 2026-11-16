"""The registry act: upstream's published package at the fork's version, holding the fork's files.

**FROM THE REGISTRY, AND WHY THAT IS AN EXCEPTION.** Every other row builds the fork. The
modern-web-guidance CLI can't be: upstream builds it in a repository the fork doesn't mirror and
publishes it only as the npm package, whose tarball is the plugin's tree plus the CLI, its
telemetry watchdog and its search index (`gen/`). Devon chose on 2026-11-16 to take that tarball at
exactly the version the fork names rather than fork a second repository (ADR-0042 amendment 5).
`npm pack` verifies the registry's integrity on download, and the installed manifest records it.

**THE TARBALL MUST AGREE WITH THE FORK WHEREVER BOTH HOLD A FILE, AND THE FORK'S `main` IS THE
ONE COMPARED.** Every tarball file that `main` also has must be byte-identical to `main`'s copy,
or the pass refuses: a release whose published files differ from the tag the fork reviewed is not
the release the fork reviewed. `hardened` can't be the comparison, because the hardening rule has
rewritten its skill files and they never match. The `main` compared is the hardened commit's LAST
parent, which is where `fork-harden` puts the `main` it hardened (`harden.py`: the previous
`hardened`, then `main`); a row tracking `main` itself compares that commit. Measured at 1.0.1:
every shared file is identical, and 16 files are the tarball's alone.

**THE FORK'S FILES ARE INSTALLED, NOT UPSTREAM'S.** The installed tree is the tracked revision's
tree, plus only the tarball files `main` doesn't carry, which the manifest lists. `retrieve`
serves the installed package's own `guides/*.md`, so installing upstream's copies would hand an
agent the 211 `npx -y modern-web-guidance@latest` cross-references the hardening rule removes.
Trees are read as git objects (`ls-tree` and `cat-file`), never checked out: a checkout would apply
the fork's `.gitattributes`, so the bytes installed or compared could differ from the commit's.

**NOTHING IS INSTALLED, SO NOTHING RUNS.** No `npm install` or `npm ci` touches the tarball, and a
tarball whose manifests declare a dependency or any script is refused, so what lands is complete
as published and no lifecycle script could be waiting for a later install.

**THE `bin/` ENTRY IS A WRAPPER, SO TELEMETRY IS OFF WHATEVER AN AGENT TYPES.** The CLI posts each
search query and retrieved guide ID to Google unless `DISABLE_TELEMETRY` is set. The lane's
`bin/<binary>` links to `.sds-bin/<binary>` inside the installed package, a shell script that sets
the row's `environment` and execs this pass's node on the package's `bin` target, by absolute
paths. Being inside the package, it is staged, moved aside and restored with it, so the rollback
is the npm act's. The npm prefix's link (`/opt/homebrew/bin/<binary>`) points at the lane's
entry, so the bare command the hardened skill runs resolves on `PATH`; it is written last and
restored first, as in `npm.py`.

**THE PROBE PASSES THE SKILL'S STAMP, NOT THE TARBALL'S.** `search <query> --skill-version
<the stamp in the hardened skill>` must exit 0, print a JSON array and write nothing to stderr;
every returned guide is then retrieved, with stdout to a file (the CLI then never moves a large
answer to a temporary file), and must name each guide and hold no package-runner text. The CLI
warns on stderr when the skill's stamp is older than its own `skill-version.txt`, but only from
six days apart, so the probe also requires the two stamps to be equal. Probes run from an empty
directory, since the CLI reads a project's `.mwgrc`, and with no telemetry variable of their own:
the wrapper is what turns it off.

A STATED LIMIT: the CLI's own messages for an opt-in gate (`set-allow-origin-trials`) tell a person
to run `npx modern-web-guidance@latest ...`. That text is in `modern-web.mjs`, a tarball file the
hardening rule can't reach; the hardened skill tells an agent never to run such a command.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    INSTALL_TIMEOUT_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    InstallOutcome,
    ProbeResult,
    _run,
)
from tool_installer.npm import (
    _VERSION,
    REVISION_FIELD,
    NpmError,
    _environment,
    _move_aside,
    _npm_prefix_link,
    _open_workspace,
    _Outgoing,
    _pack_published,
    _place,
    _PrefixLink,
    _probe_through,
    _step,
    _take_npm_prefix,
    _undo,
    package_dir,
    read_installed_package,
)
from tool_installer.toolchain import Node
from tool_installer.tools import RegistryInstall, Tool

REGISTRY_FIELD: Final = "sdsRegistryPackage"
WRAPPER_DIRECTORY: Final = ".sds-bin"
LANDED_BRANCH: Final = "main"
"""fork-sync's landed branch, which `fork-harden` reads; a row on it compares its own commit."""

_REGULAR_MODES: Final = {"100644", "100755"}
_DEPENDENCY_FIELDS: Final = (
    "dependencies",
    "optionalDependencies",
    "peerDependencies",
    "bundleDependencies",
    "bundledDependencies",
)
_STAMP: Final = re.compile(r"--skill-version\s+([0-9]{4}_[0-9]{2}_[0-9]{2}-[0-9a-f]+)")
_RUNNER_TEXT: Final = re.compile(
    r"\b(?:npx|pnpx|bunx)\b|\bnpm\s+(?:exec|x)\b|\b(?:pnpm|yarn)\s+dlx\b|modern-web-guidance@"
)


@dataclass(frozen=True)
class _Staged:
    """The tree to install, its `bin` target inside it, and the stamp its skill passes."""

    tree: Path
    entry: str
    version: str
    stamp: str


def _tree_files(src: Path, commit: str, env: dict[str, str]) -> dict[str, tuple[str, bytes]]:
    """Every file of `commit`'s tree, `path -> (mode, bytes)`, read as git objects.

    Refuses a symlink or a submodule: neither is a file the package could ship as written.
    """
    listed = subprocess.run(
        ["git", "-C", str(src), "ls-tree", "-r", "-z", "--full-tree", commit],
        capture_output=True,
        check=False,
        env=env,
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if listed.returncode != 0:
        raise NpmError(f"`git ls-tree` of {commit[:12]} exited {listed.returncode}")
    entries: list[tuple[str, str, str]] = []
    for record in listed.stdout.decode("utf-8").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, kind, sha = meta.split(" ")
        if kind != "blob" or mode not in _REGULAR_MODES:
            raise NpmError(f"{path} at {commit[:12]} is a {kind} (mode {mode}), not a file")
        entries.append((mode, sha, path))
    blobs = subprocess.run(
        ["git", "-C", str(src), "cat-file", "--batch"],
        input="".join(f"{sha}\n" for _, sha, _ in entries).encode(),
        capture_output=True,
        check=False,
        env=env,
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if blobs.returncode != 0:
        raise NpmError(f"`git cat-file` of {commit[:12]} exited {blobs.returncode}")
    files: dict[str, tuple[str, bytes]] = {}
    stream, offset = blobs.stdout, 0
    for mode, sha, path in entries:
        end = stream.index(b"\n", offset)
        header = stream[offset:end].split(b" ")
        if len(header) != 3 or header[0].decode() != sha or header[1] != b"blob":
            raise NpmError(f"`git cat-file` answered {stream[offset:end]!r} for {path}")
        size = int(header[2])
        files[path] = (mode, stream[end + 1 : end + 1 + size])
        offset = end + 1 + size + 1
    return files


def _landed_commit(tool: Tool, src: Path, revision: str, env: dict[str, str]) -> str:
    """The `main` commit the tarball is compared with -- see the module docstring."""
    if tool.branch == LANDED_BRANCH:
        return revision
    code, body, detail = _run(
        ["git", "-C", str(src), "cat-file", "-p", revision], env=env, timeout=PROBE_TIMEOUT_SECONDS
    )
    if code != 0:
        raise NpmError(f"`git cat-file` of {revision[:12]} exited {code}: {detail}")
    parents = [line.split(" ", 1)[1] for line in body.splitlines() if line.startswith("parent ")]
    if not parents:
        raise NpmError(f"{revision[:12]} has no parent, so it holds no `{LANDED_BRANCH}`")
    return parents[-1]


def _manifest(data: bytes, where: str) -> dict[str, object]:
    try:
        document = json.loads(data)
    except ValueError as error:
        raise NpmError(f"{where} is not JSON") from error
    if not isinstance(document, dict):
        raise NpmError(f"{where} is not an object")
    return document


def _tarball_files(tarball: Path, spec: str) -> dict[str, tuple[int, bytes]]:
    """Every regular file of the tarball, `path under package/ -> (mode, bytes)`, its mode kept
    to the executable bit. Anything else refuses."""
    files: dict[str, tuple[int, bytes]] = {}
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            parts = name.parts
            if name.is_absolute() or not parts or parts[0] != "package" or ".." in parts:
                raise NpmError(f"{spec} holds {member.name!r}, outside `package/`")
            if member.isdir():
                continue
            data = archive.extractfile(member) if member.isfile() else None
            if data is None:
                raise NpmError(f"{spec} holds {member.name!r}, which is not a regular file")
            path = "/".join(parts[1:])
            if not path or path in files:
                raise NpmError(f"{spec} holds {member.name!r} twice or with no name")
            files[path] = (0o755 if member.mode & 0o111 else 0o644, data.read())
    return files


def _refuse_scripts_and_dependencies(files: dict[str, tuple[int, bytes]], spec: str) -> None:
    """Every manifest in the tarball: no dependency and no script of any kind."""
    for path, (_, data) in files.items():
        if PurePosixPath(path).name != "package.json":
            continue
        document = _manifest(data, f"{spec}'s {path}")
        declared = [field for field in (*_DEPENDENCY_FIELDS, "scripts") if document.get(field)]
        if declared:
            raise NpmError(f"{spec}'s {path} declares {', '.join(declared)}")


def _refuse_disagreement(
    published: dict[str, tuple[int, bytes]], landed: dict[str, tuple[str, bytes]], spec: str
) -> list[str]:
    """Refuse unless every file both hold is identical. Returns the tarball's own files."""
    differing = sorted(
        path for path, (_, data) in published.items() if path in landed and landed[path][1] != data
    )
    if differing:
        shown = ", ".join(differing[:3])
        raise NpmError(
            f"{spec} differs from the fork's {LANDED_BRANCH} in {len(differing)}: {shown}"
        )
    return sorted(path for path in published if path not in landed)


def _write(tree: Path, path: str, data: bytes, mode: int) -> None:
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    # `x`: a path written twice refuses rather than overwriting.
    with open(target, "xb") as written:
        written.write(data)
    target.chmod(mode)


def _wrapper(row: RegistryInstall, node: Node, entry: Path) -> str:
    exports = "".join(f"export {name}={shlex.quote(value)}\n" for name, value in row.environment)
    return (
        "#!/bin/sh\n"
        "# Written by the SDS tool installer (orchestrator `tool_installer/registry.py`).\n"
        f"{exports}"
        f'exec {shlex.quote(str(node.executable))} {shlex.quote(str(entry))} "$@"\n'
    )


def _stamp(tree: dict[str, tuple[str, bytes]], row: RegistryInstall) -> str:
    if row.skill not in tree:
        raise NpmError(f"the fork holds no {row.skill}")
    stamps = set(_STAMP.findall(tree[row.skill][1].decode("utf-8", errors="replace")))
    if len(stamps) != 1:
        raise NpmError(f"{row.skill} names {len(stamps)} skill versions; expected exactly one")
    return stamps.pop()


def _stage(
    tool: Tool, row: RegistryInstall, node: Node, work: Path, revision: str, source: str
) -> _Staged:
    """Fetch the revision and its `main`, pack the release, compare, and stage the fork's tree."""
    env = _environment(node)
    npm = str(node.npm)
    src = work / "src"
    _step(["git", "init", "-q", "--bare", str(src)], cwd=None, env=env)
    # Depth 2 holds the revision and both of its parents, so its `main` is in the fetch.
    _step(
        ["git", "-C", str(src), "fetch", "-q", "--depth", "2", source, revision],
        cwd=None,
        env=env,
    )
    tree = _tree_files(src, revision, env)
    landed = _tree_files(src, _landed_commit(tool, src, revision, env), env)

    if "package.json" not in tree:
        raise NpmError("the fork holds no package.json")
    manifest = _manifest(tree["package.json"][1], "the fork's package.json")
    version = manifest.get("version")
    if manifest.get("name") != row.package:
        raise NpmError(f"the fork's package.json names {manifest.get('name')!r}, not {row.package}")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise NpmError(f"the fork's package.json names no plain version to fetch: {version!r}")
    stamp = _stamp(tree, row)

    spec = f"{row.package}@{version}"
    tarball, integrity = _pack_published(npm, env, work / "published", spec)
    published = _tarball_files(tarball, spec)
    _refuse_scripts_and_dependencies(published, spec)
    own = _refuse_disagreement(published, landed, spec)
    if (
        _manifest(published.get("package.json", (0, b"null"))[1], f"{spec}'s package.json").get(
            "version"
        )
        != version
    ):
        raise NpmError(f"{spec}'s package.json is not version {version}")

    staged = work / "stage"
    for path, (mode, data) in tree.items():
        _write(staged, path, data, 0o755 if mode == "100755" else 0o644)
    if clashing := sorted(set(own) & set(tree)):
        raise NpmError(
            f"the fork's tree and only the tarball both hold {', '.join(clashing[:3])}: the fork "
            f"added a file {LANDED_BRANCH} lacks"
        )
    for path in own:
        mode, data = published[path]
        _write(staged, path, data, mode)

    bins = manifest.get("bin")
    entry = bins.get(row.binary) if isinstance(bins, dict) else None
    if not isinstance(entry, str):
        raise NpmError(f"{row.package} declares no `{row.binary}` in its bin map")
    entry = os.path.normpath(entry)
    if entry.startswith(("..", "/")) or not (staged / entry).is_file():
        raise NpmError(f"{row.package}'s `{row.binary}` names {entry!r}, not a file it ships")

    manifest[REVISION_FIELD] = revision
    manifest[REGISTRY_FIELD] = {
        "package": row.package,
        "version": version,
        "integrity": integrity,
        "files": own,
    }
    (staged / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return _Staged(tree=staged, entry=entry, version=version, stamp=stamp)


def _probe_run(argv: list[str], env: dict[str, str], cwd: Path, stdout: Path) -> tuple[int, str]:
    """Run with stdout to a file; returns the exit code and stderr."""
    try:
        with open(stdout, "wb") as out:
            completed = subprocess.run(
                argv,
                stdout=out,
                stderr=subprocess.PIPE,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
                env=env,
                cwd=cwd,
            )
    except (subprocess.TimeoutExpired, OSError) as error:
        return 127, type(error).__name__
    return completed.returncode, completed.stderr


def _probe_guidance(
    row: RegistryInstall, binary: Path, stamp: str, cli_stamp: str | None, env: dict[str, str]
) -> list[ProbeResult]:
    """Search with the skill's stamp, then retrieve every guide it found. See the docstring."""
    agree = ProbeResult(
        command=(row.skill, "--skill-version ==", "skill-version.txt"),
        passed=cli_stamp == stamp,
        detail="" if cli_stamp == stamp else f"the skill passes {stamp}, the CLI has {cli_stamp}",
    )
    with tempfile.TemporaryDirectory() as empty:
        cwd = Path(empty)
        out = cwd / ".stdout"
        search = ("search", row.search, "--skill-version", stamp)
        code, stderr = _probe_run([str(binary), *search], env, cwd, out)
        ids: list[object] = []
        try:
            found = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            found = None
        if isinstance(found, list):
            ids = [item.get("id") for item in found if isinstance(item, dict)]
        named = isinstance(found, list) and len(ids) == len(found) > 0
        searched = code == 0 and not stderr and named and all(isinstance(i, str) for i in ids)
        results = [
            agree,
            ProbeResult(
                command=search,
                passed=searched,
                detail="" if searched else f"exited {code}, {len(ids)} found, stderr {stderr!r}",
            ),
        ]
        if not searched:
            return results
        retrieve = ("retrieve", ",".join(str(i) for i in ids), "--skill-version", stamp)
        code, stderr = _probe_run([str(binary), *retrieve], env, cwd, out)
        text = out.read_text(encoding="utf-8", errors="replace")
        missing = [i for i in ids if f"--- Guide for {i} ---" not in text]
        runner = _RUNNER_TEXT.search(text)
        retrieved = code == 0 and not stderr and not missing and runner is None
        detail = (
            ""
            if retrieved
            else f"exited {code}, {len(missing)} missing, runner text "
            f"{runner.group(0) if runner else None!r}, stderr {stderr[:120]!r}"
        )
        results.append(ProbeResult(command=retrieve, passed=retrieved, detail=detail))
    return results


def _cli_stamp(target: Path, entry: str) -> str | None:
    try:
        return (target / entry).parent.joinpath("skill-version.txt").read_text("utf-8").strip()
    except OSError, ValueError:
        return None


def _rolled_back(
    install_root: Path, row: RegistryInstall, detail: str, probes: list[ProbeResult]
) -> InstallOutcome:
    return InstallOutcome(
        action=ACTION_ROLLED_BACK,
        installed=read_installed_package(install_root, row.package),
        probes=probes,
        detail=detail,
    )


def install_and_prove_registry(
    *,
    tool: Tool,
    node: Node,
    install_root: Path,
    backup_root: Path,
    revision: str,
    source: str | None = None,
) -> InstallOutcome:
    """Stage `revision` with the release it names, swap it in, probe it, and restore on failure.

    `source` defaults to the fork on GitHub; it is a parameter so the act can be exercised end to
    end against a local repository, as on the npm row.
    """
    row = tool.install
    if not isinstance(row, RegistryInstall):
        raise TypeError(f"{tool.name} is not a registry row")
    target = package_dir(install_root, row.package)
    link = install_root / "bin" / row.binary
    env = _environment(node)

    def failed(detail: str) -> InstallOutcome:
        return InstallOutcome(
            action=ACTION_INSTALL_FAILED,
            installed=read_installed_package(install_root, row.package),
            detail=detail,
        )

    try:
        prefix = _npm_prefix_link(row.binary, row.package, node, env, link.absolute())
        workspace = _open_workspace(install_root)
    except NpmError as error:
        return failed(str(error))
    except OSError as error:
        return failed(f"the install root is not usable: {type(error).__name__}")

    with workspace as work:
        try:
            staged = _stage(
                tool,
                row,
                node,
                Path(work),
                revision,
                source or f"https://github.com/{tool.repository}",
            )
            _write(
                staged.tree,
                f"{WRAPPER_DIRECTORY}/{row.binary}",
                _wrapper(row, node, target / staged.entry).encode(),
                0o755,
            )
        except (
            NpmError,
            OSError,
            ValueError,
            tarfile.TarError,
            subprocess.SubprocessError,
        ) as error:
            return failed(str(error) or type(error).__name__)

        previous_link = os.readlink(link) if link.is_symlink() else None
        try:
            outgoing = _Outgoing(
                package=_move_aside(target, backup_root), link=previous_link, prefix=prefix
            )
        except OSError as error:
            return failed(f"the previous package could not be moved aside: {type(error).__name__}")
        try:
            _place(
                staged.tree,
                target,
                link,
                f"../lib/node_modules/{row.package}/{WRAPPER_DIRECTORY}/{row.binary}",
            )
        except OSError as error:
            _undo(target, link, outgoing)
            return failed(f"the new package could not be put in place: {type(error).__name__}")

    return _prove_in_place(row, install_root, outgoing, prefix, staged, revision, env)


def _prove_in_place(
    row: RegistryInstall,
    install_root: Path,
    outgoing: _Outgoing,
    prefix: _PrefixLink,
    staged: _Staged,
    revision: str,
    env: dict[str, str],
) -> InstallOutcome:
    """Everything after the swap: check the record, probe, and only then write the prefix link."""
    target = package_dir(install_root, row.package)
    link = install_root / "bin" / row.binary
    installed = read_installed_package(install_root, row.package)
    if installed is None or installed.revision != revision:
        _undo(target, link, outgoing)
        return _rolled_back(
            install_root, row, "the installed package does not record its revision", []
        )
    results = [_probe_through(link, installed.version, env)]
    try:
        results += _probe_guidance(row, link, staged.stamp, _cli_stamp(target, staged.entry), env)
    except OSError as error:
        # The probe's own directory or output file: still a probe that did not pass, and the
        # unproven package must not stay live because of it.
        results.append(
            ProbeResult(command=("search", row.search), passed=False, detail=type(error).__name__)
        )
    if not all(result.passed for result in results):
        _undo(target, link, outgoing)
        return _rolled_back(
            install_root,
            row,
            "the installed artifact failed its probe and the previous one was restored",
            results,
        )
    try:
        through = _take_npm_prefix(prefix, row.package, installed.version, env)
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
