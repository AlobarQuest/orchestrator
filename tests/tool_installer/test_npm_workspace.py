"""The npm act for a WORKSPACE package -- the `n8nac` row -- end to end against scratch roots.

As in `test_npm.py`, the fetch is real git against a local repository and npm is a fake that logs
every call. The fork here is a small npm workspace monorepo shaped like n8n-as-code's, the
published package is a tarball each test builds with exactly the members it wants, and the npm
prefix `npx` reads is a scratch directory. Nothing reaches the network, the operator's npm prefix
or this lane's real install root.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tarfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    InstallOutcome,
    RollbackFailed,
)
from tool_installer.npm import install_and_prove_npm, package_dir, read_installed_package
from tool_installer.toolchain import Node
from tool_installer.tools import N8NAC, REA, NpmInstall, PublishedAssets

OLD_REVISION = "a" * 40
OLD_TARGET = "../lib/node_modules/n8nac/packages/cli/dist/index.js"
# What `npm link` left in the prefix, as measured on this machine: `bin/n8nac` points THROUGH
# `lib/node_modules/n8nac`, itself a symlink into an old checkout (which need not exist here).
HAND_LINKED = "../lib/node_modules/n8nac/dist/index.js"
OLD_CHECKOUT = "/nonexistent/old-checkout/packages/cli"
DATA_FILES = (
    "n8n-credentials-ontology.json",
    "n8n-docs-complete.json",
    "n8n-knowledge-index.json",
    "n8n-nodes-technical.json",
    "workflows-index.json",
)
UPSTREAM_CODE = "UPSTREAM-CODE-MUST-NOT-LAND"

# The CLI's entry point. It refuses to run with telemetry or the update check left on, answers
# `--version` with `REPORTS`, and fails the knowledge commands -- as the real one does -- unless
# the published data and the sibling workspace link are both where the installed tree needs them.
# Run through the npm prefix's link, it can instead lie about its version (`LIE_THROUGH_PREFIX`),
# repoint that link (`RELINK_THROUGH_PREFIX`) or lock a directory (`LOCK_THROUGH_PREFIX`), then
# fail -- the three things a test needs to happen after the link is written.
_ENTRY = """#!/bin/sh
if [ "$DO_NOT_TRACK" != 1 ] || [ "$NO_UPDATE_NOTIFIER" != 1 ] || [ "$CI" != 1 ]; then
  echo "telemetry is on" >&2; exit 1
fi
case "$0" in
  "$NPM_PREFIX"/*)
    [ -n "$LIE_THROUGH_PREFIX" ] && { echo 0.0.0; exit 0; }
    [ -n "$RELINK_THROUGH_PREFIX" ] && { ln -sfn "$RELINK_THROUGH_PREFIX" "$0"; exit 1; }
    [ -n "$LOCK_THROUGH_PREFIX" ] && { chmod 555 "$LOCK_THROUGH_PREFIX"; exit 1; }
    ;;
esac
checkout="$(dirname "$0")/../lib/node_modules/n8nac"
case "$1" in
  --version) echo "REPORTS" ;;
  mcp)
    read -r request
    info='"serverInfo":{"name":"n8n-as-code","version":"MCP_REPORTS"}'
    printf '{"jsonrpc":"2.0","id":1,"result":{%s}}\n' "$info"
    exit "${MCP_EXIT:-13}" ;;
  skills)
    [ -f "$checkout/packages/skills/dist/assets/n8n-knowledge-index.json" ] \\
      || { echo "Knowledge index not found" >&2; exit 1; }
    [ -f "$checkout/node_modules/@n8n-as-code/skills/package.json" ] \\
      || { echo "Cannot find module @n8n-as-code/skills" >&2; exit 1; }
    echo found ;;
  *) exit 2 ;;
esac
"""

# npm, as far as the workspace act uses it. `ci` links the sibling workspace as npm does (a
# relative symlink), and a dev-only directory the production `ci` removes; the build writes the
# fork's own code into both workspaces; `prefix -g` answers `$NPM_PREFIX`; `pack <spec>` copies
# `$NPM_PUBLISHED` into the destination and reports it as npm does. The build can also repoint
# the prefix's link mid-pass (`NPM_RELINK_PREFIX`), write into the data directory itself
# (`NPM_BUILD_WRITES_ASSETS`), make the skills `dist/` a symlink (`NPM_BUILD_LINKS_DIST`), or put
# a real install where the `npm link` package was (`NPM_INSTALLS_LINKED`).
_NPM = """#!/bin/sh
echo "$* ignore_scripts=$npm_config_ignore_scripts" >> "$NPM_LOG"
if [ -n "$NPM_FAIL" ] && [ "$1 $2" = "$NPM_FAIL" ]; then echo "npm ERR! $NPM_FAIL" >&2; exit 1; fi
case "$1" in
  ci)
    mkdir -p node_modules/@n8n-as-code
    cd node_modules
    [ -L @n8n-as-code/skills ] || ln -s ../../packages/skills @n8n-as-code/skills
    if [ "$2" = "--omit=dev" ]; then rmdir dev-only; else mkdir dev-only; fi
    ;;
  run)
    [ "$2 $3" = "build --workspace=packages/mcp" ] && exit 0
    [ "$2" = "build:cli" ] || exit 1
    [ -n "$NPM_RELINK_PREFIX" ] && ln -sfn "$NPM_RELINK_PREFIX" "$NPM_PREFIX/bin/n8nac"
    [ -n "$NPM_BUILD_LINKS_DIST" ] && ln -s "$NPM_BUILD_LINKS_DIST" packages/skills/dist
    if [ -n "$NPM_INSTALLS_LINKED" ]; then
      rm "$NPM_PREFIX/lib/node_modules/n8nac" && mkdir "$NPM_PREFIX/lib/node_modules/n8nac"
      echo '{}' > "$NPM_PREFIX/lib/node_modules/n8nac/package.json"
    fi
    mkdir -p packages/cli/dist packages/skills/dist
    if [ -n "$NPM_BUILD_WRITES_ASSETS" ]; then
      mkdir -p packages/skills/dist/assets
      echo '{}' > packages/skills/dist/assets/n8n-knowledge-index.json
    fi
    cp packages/cli/src/entry.sh packages/cli/dist/index.js
    echo "the fork's code" > packages/skills/dist/index.js
    ;;
  prefix) echo "$NPM_PREFIX" ;;
  pack)
    cp "$NPM_PUBLISHED" "$5/"
    printf '[{"filename":"%s","integrity":"sha512-published"}]\\n' "$(basename "$NPM_PUBLISHED")"
    ;;
esac
"""


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _fork(
    tmp_path: Path,
    *,
    reports: str = "2.7.0",
    skills_version: str = "2.4.1",
    mcp_reports: str = "1.0.0",
) -> tuple[str, str]:
    """A local monorepo shaped like n8n-as-code's. Returns `(source, revision)`."""
    repo = tmp_path / "fork"
    (repo / "packages" / "cli").mkdir(parents=True)
    (repo / "packages" / "skills").mkdir(parents=True)
    (repo / "package.json").write_text(
        json.dumps({"name": "n8n-as-code-monorepo", "private": True, "workspaces": ["packages/*"]})
    )
    (repo / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3}))
    (repo / "packages" / "cli" / "package.json").write_text(
        json.dumps(
            {
                "name": "n8nac",
                "version": "2.7.0",
                "bin": {"n8nac": "./dist/index.js", "n8n-as-code": "./dist/index.js"},
            }
        )
    )
    entry = _ENTRY.replace("MCP_REPORTS", mcp_reports).replace("REPORTS", reports)
    _executable(repo / "packages" / "cli" / "src" / "entry.sh", entry)
    (repo / "packages" / "skills" / "package.json").write_text(
        json.dumps({"name": "@n8n-as-code/skills", "version": skills_version})
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "release")
    return str(repo), _git(repo, "rev-parse", "HEAD")


def _member(archive: tarfile.TarFile, name: str, content: bytes = b"{}") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))


def _published(tmp_path: Path, *, data: bool = True, extra: tuple[str, ...] = ()) -> Path:
    """Upstream's published tarball: the five data files, upstream's CODE beside them, and any
    `extra` member -- `link:<name>` makes a symlink, anything else a regular file."""
    path = tmp_path / "published" / "n8n-as-code-skills-2.4.1.tgz"
    path.parent.mkdir(exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        _member(archive, "package/package.json", b'{"name": "@n8n-as-code/skills"}')
        _member(archive, "package/dist/index.js", UPSTREAM_CODE.encode())
        _member(archive, "package/dist/assets.js", UPSTREAM_CODE.encode())
        if data:
            for name in DATA_FILES:
                _member(archive, f"package/dist/assets/{name}", b'{"data": true}')
        for name in extra:
            if name.startswith("link:"):
                info = tarfile.TarInfo(name.removeprefix("link:"))
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            else:
                _member(archive, name)
    return path


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    """The npm prefix `npx` reads, as the hand-made `npm link` left it."""
    root = tmp_path / "prefix"
    (root / "bin").mkdir(parents=True)
    (root / "lib" / "node_modules").mkdir(parents=True)
    (root / "bin" / "n8nac").symlink_to(HAND_LINKED)
    (root / "lib" / "node_modules" / "n8nac").symlink_to(OLD_CHECKOUT)
    return root


@pytest.fixture
def node(tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch) -> Node:
    bin_dir = tmp_path / "nodebin"
    _executable(bin_dir / "node", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "npm", _NPM)
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    monkeypatch.setenv("NPM_PREFIX", str(prefix))
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path)))
    for name in (
        "NPM_FAIL",
        "NPM_RELINK_PREFIX",
        "NPM_BUILD_WRITES_ASSETS",
        "NPM_BUILD_LINKS_DIST",
        "NPM_INSTALLS_LINKED",
        "LIE_THROUGH_PREFIX",
        "RELINK_THROUGH_PREFIX",
        "LOCK_THROUGH_PREFIX",
        "MCP_EXIT",
        "DO_NOT_TRACK",
        "NO_UPDATE_NOTIFIER",
        "CI",
    ):
        monkeypatch.delenv(name, raising=False)
    return Node(executable=bin_dir / "node")


def _root(tmp_path: Path) -> Path:
    return tmp_path / "root"


def _seed_installed(root: Path) -> None:
    """The outgoing install: a checkout recording its revision in the workspace, and the link."""
    checkout = package_dir(root, "n8nac")
    _executable(checkout / "packages" / "cli" / "dist" / "index.js", "#!/bin/sh\necho 2.6.0\n")
    (checkout / "packages" / "cli" / "package.json").write_text(
        json.dumps({"name": "n8nac", "version": "2.6.0", "gitHead": OLD_REVISION})
    )
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "n8nac").symlink_to(OLD_TARGET)


def _act(node: Node, tmp_path: Path, source: str, revision: str) -> InstallOutcome:
    return install_and_prove_npm(
        tool=N8NAC,
        node=node,
        install_root=_root(tmp_path),
        backup_root=tmp_path / "backups" / "n8nac-2.6.0",
        revision=revision,
        source=source,
    )


def _installed(tmp_path: Path):
    return read_installed_package(_root(tmp_path), "n8nac", "packages/cli")


def _assert_nothing_live_changed(tmp_path: Path, prefix: Path) -> None:
    installed = _installed(tmp_path)
    assert installed is not None
    assert installed.revision == OLD_REVISION
    assert os.readlink(_root(tmp_path) / "bin" / "n8nac") == OLD_TARGET
    assert os.readlink(prefix / "bin" / "n8nac") == HAND_LINKED
    assert os.readlink(prefix / "lib" / "node_modules" / "n8nac") == OLD_CHECKOUT


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "npm.log").read_text().splitlines()


@pytest.fixture
def read_only() -> Iterator[list[Path]]:
    """Directories a test makes unwritable, made writable again so tmp_path can be removed."""
    made: list[Path] = []
    yield made
    for path in made:
        path.chmod(0o755)


def test_a_first_workspace_install_is_built_pruned_given_its_data_and_linked(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    source, revision = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    root = _root(tmp_path)
    checkout = package_dir(root, "n8nac")
    assert _installed(tmp_path) == outcome.installed
    assert outcome.installed is not None
    assert outcome.installed.revision == revision
    assert outcome.installed.version == "2.7.0"
    # The record is the WORKSPACE's, written after the prune; the root manifest carries none.
    assert "gitHead" not in json.loads((checkout / "package.json").read_text())
    assert not (checkout / ".git").exists()
    assert not (checkout / "node_modules" / "dev-only").exists()
    assert (checkout / "node_modules" / "@n8n-as-code" / "skills").is_symlink()

    assets = checkout / "packages" / "skills" / "dist" / "assets"
    assert sorted(path.name for path in assets.iterdir()) == list(DATA_FILES)
    skills = json.loads((checkout / "packages" / "skills" / "package.json").read_text())
    assert skills["sdsPublishedAssets"] == {
        "package": "@n8n-as-code/skills",
        "version": "2.4.1",
        "integrity": "sha512-published",
        "files": list(DATA_FILES),
    }
    # Upstream's code sat beside the data in the same tarball, and none of it landed anywhere.
    assert (checkout / "packages" / "skills" / "dist" / "index.js").read_text() == (
        "the fork's code\n"
    )
    landed = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    assert not [path for path in landed if UPSTREAM_CODE in path.read_text(errors="replace")]

    assert os.readlink(root / "bin" / "n8nac") == OLD_TARGET
    assert os.readlink(prefix / "bin" / "n8nac") == str((root / "bin" / "n8nac").absolute())
    # The `npm link` package went with it, because it was a symlink.
    assert not (prefix / "lib" / "node_modules" / "n8nac").is_symlink()
    # The last probe is the one npx's path gets: the version, by value, through the prefix.
    assert [probe.command[:2] for probe in outcome.probes] == [
        ("skills", "search"),
        ("skills", "node-info"),
        ("--version", "== "),
        ("mcp", "initialize"),
        (str(prefix / "bin" / "n8nac"), "--version"),
    ]
    assert sorted(path.name for path in root.iterdir()) == ["bin", "lib"]


def test_the_workspace_build_runs_in_order_and_NEVER_with_lifecycle_scripts(
    node: Node, tmp_path: Path
) -> None:
    source, revision = _fork(tmp_path)
    _act(node, tmp_path, source, revision)
    calls = _calls(tmp_path)
    assert [
        call.split(" ignore_scripts=")[0].split(" --pack-destination")[0] for call in calls
    ] == [
        "prefix -g",
        "ci",
        "run build:cli",
        "run build --workspace=packages/mcp",
        "ci --omit=dev",
        "pack @n8n-as-code/skills@2.4.1 --json",
    ]
    assert all(call.endswith("ignore_scripts=true") for call in calls)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(("link:package/dist/assets/n8n-extra.json",), id="a symlink member"),
        pytest.param(("package/dist/assets/../escaped.json",), id="a dotted member"),
        pytest.param(("package/dist/assets/..",), id="a member named dot-dot"),
        pytest.param(("package/dist/assets/notes.txt",), id="a non-json member"),
        pytest.param(("package/dist/assets/nested/more.json",), id="a nested member"),
    ],
)
def test_a_published_tarball_with_anything_but_data_files_is_REFUSED(
    node: Node,
    tmp_path: Path,
    prefix: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: tuple[str, ...],
) -> None:
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path, extra=extra)))
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "not a data file" in outcome.detail
    _assert_nothing_live_changed(tmp_path, prefix)
    assert not (tmp_path / "backups").exists()


def test_a_published_tarball_with_no_data_files_is_REFUSED(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The knowledge commands would fail without them; better refused at the build than probed."""
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path, data=False)))
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "no data files" in outcome.detail
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_skills_version_that_is_not_a_plain_release_is_REFUSED(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    """The data is fetched by that exact string; a range would let npm pick some other version."""
    source, revision = _fork(tmp_path, skills_version="^2.4.1")
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "no plain version" in outcome.detail
    assert not [call for call in _calls(tmp_path) if call.startswith("pack")]
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_failed_probe_rolls_back_the_checkout_the_link_AND_leaves_the_prefix_link(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    source, revision = _fork(tmp_path, reports="9.9.9")
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert {probe.command[0]: probe.passed for probe in outcome.probes} == {
        "skills": True,
        "--version": False,
        "mcp": True,
    }
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_prefix_link_that_cannot_be_written_rolls_everything_back(
    node: Node, tmp_path: Path, prefix: Path, read_only: list[Path]
) -> None:
    """Every probe passed, and the last step failed: the swap is undone, not left half done."""
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    (prefix / "bin").chmod(0o555)
    read_only.append(prefix / "bin")
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert "npm prefix's link" in outcome.detail
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_failure_AFTER_the_prefix_link_is_written_restores_its_previous_target(
    node: Node, tmp_path: Path, prefix: Path, read_only: list[Path]
) -> None:
    """The stale `npm link` package cannot be removed, after the new link is already in place.
    The previous link resolves through that package, which is still there, so restoring the link
    puts the prefix back exactly as it was."""
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    (prefix / "lib" / "node_modules").chmod(0o555)
    read_only.append(prefix / "lib" / "node_modules")
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_first_install_rolled_back_after_the_prefix_link_removes_that_link(
    node: Node, tmp_path: Path, prefix: Path, read_only: list[Path]
) -> None:
    """No link was there before, so the rollback leaves none -- not one into a removed tree."""
    (prefix / "bin" / "n8nac").unlink()
    source, revision = _fork(tmp_path)
    (prefix / "lib" / "node_modules").chmod(0o555)
    read_only.append(prefix / "lib" / "node_modules")
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert not (prefix / "bin" / "n8nac").is_symlink()
    assert not package_dir(_root(tmp_path), "n8nac").exists()


def test_a_regular_file_at_the_prefix_link_is_REFUSED_before_anything_is_built(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    """Someone else's install, not this lane's to replace."""
    (prefix / "bin" / "n8nac").unlink()
    (prefix / "bin" / "n8nac").write_text("#!/bin/sh\necho installed by hand\n")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "not a symlink" in outcome.detail
    assert (prefix / "bin" / "n8nac").read_text() == "#!/bin/sh\necho installed by hand\n"
    assert [call.split(" ignore_scripts=")[0] for call in _calls(tmp_path)] == ["prefix -g"]
    installed = _installed(tmp_path)
    assert installed is not None
    assert installed.revision == OLD_REVISION


def test_a_real_install_at_the_prefix_package_is_REFUSED_before_anything_is_built(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    """A real `npm install -g n8nac` puts a DIRECTORY there, and a bin link into it. Neither is
    this lane's: taking the link over would silently replace someone's install."""
    linked = prefix / "lib" / "node_modules" / "n8nac"
    linked.unlink()
    (linked / "dist").mkdir(parents=True)
    (linked / "package.json").write_text("{}")
    source, revision = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "real install" in outcome.detail
    assert (linked / "package.json").read_text() == "{}"
    assert os.readlink(prefix / "bin" / "n8nac") == HAND_LINKED
    assert [call.split(" ignore_scripts=")[0] for call in _calls(tmp_path)] == ["prefix -g"]


def test_a_prefix_link_pointing_anywhere_else_is_REFUSED(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    """Not absent, not the lane's, and not through the `npm link` package: someone else's."""
    (prefix / "bin" / "n8nac").unlink()
    (prefix / "bin" / "n8nac").symlink_to("/usr/local/elsewhere/n8nac")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "neither this lane's nor an `npm link`" in outcome.detail
    assert os.readlink(prefix / "bin" / "n8nac") == "/usr/local/elsewhere/n8nac"
    assert [call.split(" ignore_scripts=")[0] for call in _calls(tmp_path)] == ["prefix -g"]


def test_a_prefix_link_already_the_lanes_is_taken_and_left_unwritten(
    node: Node, tmp_path: Path, prefix: Path, read_only: list[Path]
) -> None:
    """The next pass after a first install finds its own link there. Nothing in `bin/` needs to
    change, so an unwritable `bin/` does not stop it."""
    own = str((_root(tmp_path) / "bin" / "n8nac").absolute())
    (prefix / "bin" / "n8nac").unlink()
    (prefix / "bin" / "n8nac").symlink_to(own)
    (prefix / "bin").chmod(0o555)
    read_only.append(prefix / "bin")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    assert os.readlink(prefix / "bin" / "n8nac") == own


def test_a_prefix_link_changed_DURING_the_build_is_left_and_the_pass_rolled_back(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read before a build of minutes, and read again before the write: what someone did in
    between is theirs, so the swap is undone and their link is not touched."""
    monkeypatch.setenv("NPM_RELINK_PREFIX", "/somebody/else/n8nac")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert "changed during the pass" in outcome.detail
    assert os.readlink(prefix / "bin" / "n8nac") == "/somebody/else/n8nac"
    installed = _installed(tmp_path)
    assert installed is not None
    assert installed.revision == OLD_REVISION


def test_a_prefix_link_changed_AFTER_it_was_written_is_not_restored_over(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rollback puts back only the link this pass wrote."""
    monkeypatch.setenv("RELINK_THROUGH_PREFIX", "/somebody/else/n8nac")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert os.readlink(prefix / "bin" / "n8nac") == "/somebody/else/n8nac"
    installed = _installed(tmp_path)
    assert installed is not None
    assert installed.revision == OLD_REVISION


def test_a_version_that_fails_THROUGH_the_prefix_link_rolls_everything_back(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lane's own path probed clean; the path npx runs did not. Both are put back, and the
    `npm link` package the previous link resolves through is still there."""
    monkeypatch.setenv("LIE_THROUGH_PREFIX", "1")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert "through the npm prefix's link" in outcome.detail
    assert outcome.probes[-1].command[0] == str(prefix / "bin" / "n8nac")
    assert not outcome.probes[-1].passed
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_rollback_that_fails_partway_has_ALREADY_restored_the_prefix_link(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch, read_only: list[Path]
) -> None:
    """The outgoing package cannot be moved back (its backup directory is locked), so the
    rollback fails -- after the link npx reads is already back where it was, rather than left
    pointing at a package that has just been removed."""
    backup = tmp_path / "backups" / "n8nac-2.6.0"
    monkeypatch.setenv("LOCK_THROUGH_PREFIX", str(backup))
    read_only.append(backup)
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    with pytest.raises(RollbackFailed):
        _act(node, tmp_path, source, revision)

    assert os.readlink(prefix / "bin" / "n8nac") == HAND_LINKED


def test_a_real_install_that_appears_DURING_the_pass_is_never_deleted(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the `npm link` symlink is this lane's to remove, whenever the pass gets there."""
    monkeypatch.setenv("NPM_INSTALLS_LINKED", "1")
    source, revision = _fork(tmp_path)
    _act(node, tmp_path, source, revision)
    assert (prefix / "lib" / "node_modules" / "n8nac" / "package.json").read_text() == "{}\n"


def test_a_data_directory_that_climbs_out_of_the_checkout_is_REFUSED(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    """No symlink is involved, so only resolving the path catches it: a row is a table entry,
    and a `..` in one must not carry the data out of the tree being installed."""
    assert isinstance(N8NAC.install, NpmInstall)
    climbing = replace(
        N8NAC,
        install=replace(
            N8NAC.install,
            published_assets=PublishedAssets(
                package="@n8n-as-code/skills", workspace="packages/skills", directory="../../../x"
            ),
        ),
    )
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = install_and_prove_npm(
        tool=climbing,
        node=node,
        install_root=_root(tmp_path),
        backup_root=tmp_path / "backups" / "n8nac-2.6.0",
        revision=revision,
        source=source,
    )

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "resolves outside the checkout" in outcome.detail
    _assert_nothing_live_changed(tmp_path, prefix)


def test_data_the_build_already_wrote_is_a_named_REFUSAL(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sources for one file is a question for a person, not a silent overwrite either way."""
    monkeypatch.setenv("NPM_BUILD_WRITES_ASSETS", "1")
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "already holds n8n-knowledge-index.json" in outcome.detail
    assert "both wrote there" in outcome.detail
    _assert_nothing_live_changed(tmp_path, prefix)


def test_a_symlinked_parent_of_the_data_directory_is_REFUSED(
    node: Node, tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data would land wherever the link points -- here, outside the checkout."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("NPM_BUILD_LINKS_DIST", str(outside))
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "packages/skills/dist is a symlink" in outcome.detail
    assert not (outside / "assets").exists()
    _assert_nothing_live_changed(tmp_path, prefix)


def test_an_upgrade_keeps_the_outgoing_checkout_and_reads_the_new_workspace_record(
    node: Node, tmp_path: Path
) -> None:
    source, revision = _fork(tmp_path)
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    kept = tmp_path / "backups" / "n8nac-2.6.0" / "n8nac" / "packages" / "cli" / "package.json"
    assert json.loads(kept.read_text())["gitHead"] == OLD_REVISION
    assert os.readlink(_root(tmp_path) / "bin" / "n8nac") == OLD_TARGET


def test_the_rows_MCP_server_is_probed_at_its_own_version_and_exit_code(
    node: Node, tmp_path: Path
) -> None:
    """n8nac's server reports `1.0.0` and Node ends it with 13 on EOF; rea's is unchanged."""
    source, revision = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, revision)
    assert isinstance(N8NAC.install, NpmInstall)
    assert (N8NAC.install.mcp_arguments, N8NAC.install.mcp_version) == (("mcp",), "1.0.0")
    assert N8NAC.install.mcp_exit_codes == (0, 13)
    assert isinstance(REA.install, NpmInstall)
    assert (REA.install.mcp_version, REA.install.mcp_exit_codes) == (None, (0,))
    assert outcome.action == ACTION_INSTALLED, outcome.detail
    mcp = [probe for probe in outcome.probes if "initialize" in probe.command]
    assert [probe.passed for probe in mcp] == [True]
    assert "1.0.0" in mcp[0].command


def test_an_MCP_server_ending_cleanly_is_accepted(
    node: Node, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, revision = _fork(tmp_path)
    monkeypatch.setenv("MCP_EXIT", "0")
    assert _act(node, tmp_path, source, revision).action == ACTION_INSTALLED


@pytest.mark.parametrize(("mcp_reports", "exit_code"), [("2.7.0", "13"), ("1.0.0", "1")])
def test_an_MCP_server_answering_otherwise_rolls_back(
    node: Node,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mcp_reports: str,
    exit_code: str,
) -> None:
    source, revision = _fork(tmp_path, mcp_reports=mcp_reports)
    monkeypatch.setenv("MCP_EXIT", exit_code)
    outcome = _act(node, tmp_path, source, revision)
    assert outcome.action == ACTION_ROLLED_BACK, outcome.detail


def test_the_probe_environment_is_declared_on_the_row() -> None:
    """n8nac's telemetry and update check are switched off for its probes; rea's are as before."""
    assert isinstance(N8NAC.install, NpmInstall)
    assert dict(N8NAC.install.probe_environment) == {
        "DO_NOT_TRACK": "1",
        "NO_UPDATE_NOTIFIER": "1",
        "CI": "1",
    }
    assert isinstance(REA.install, NpmInstall)
    assert REA.install.probe_environment == ()
