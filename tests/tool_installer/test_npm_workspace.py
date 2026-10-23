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
from pathlib import Path

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    InstallOutcome,
)
from tool_installer.npm import install_and_prove_npm, package_dir, read_installed_package
from tool_installer.toolchain import Node
from tool_installer.tools import N8NAC, NpmInstall

OLD_REVISION = "a" * 40
OLD_TARGET = "../lib/node_modules/n8nac/packages/cli/dist/index.js"
# Where the hand-made `npm link` pointed: an old checkout, which need not exist for the test.
HAND_LINKED = "/nonexistent/old-checkout/packages/cli/dist/index.js"
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
_ENTRY = """#!/bin/sh
if [ "$DO_NOT_TRACK" != 1 ] || [ "$NO_UPDATE_NOTIFIER" != 1 ] || [ "$CI" != 1 ]; then
  echo "telemetry is on" >&2; exit 1
fi
checkout="$(dirname "$0")/../lib/node_modules/n8nac"
case "$1" in
  --version) echo "REPORTS" ;;
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
# `$NPM_PUBLISHED` into the destination and reports it as npm does.
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
    [ "$2" = "build:cli" ] || exit 1
    mkdir -p packages/cli/dist packages/skills/dist
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
    tmp_path: Path, *, reports: str = "2.7.0", skills_version: str = "2.4.1"
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
    _executable(repo / "packages" / "cli" / "src" / "entry.sh", _ENTRY.replace("REPORTS", reports))
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
    (root / "lib" / "node_modules" / "n8nac").symlink_to("/nonexistent/old-checkout")
    return root


@pytest.fixture
def node(tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch) -> Node:
    bin_dir = tmp_path / "nodebin"
    _executable(bin_dir / "node", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "npm", _NPM)
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    monkeypatch.setenv("NPM_PREFIX", str(prefix))
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path)))
    monkeypatch.delenv("NPM_FAIL", raising=False)
    for name in ("DO_NOT_TRACK", "NO_UPDATE_NOTIFIER", "CI"):
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
    assert (prefix / "lib" / "node_modules" / "n8nac").is_symlink()


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
    assert [probe.command[:2] for probe in outcome.probes] == [
        ("skills", "search"),
        ("skills", "node-info"),
        ("--version", "== "),
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


def test_a_real_directory_at_the_prefix_package_is_LEFT(
    node: Node, tmp_path: Path, prefix: Path
) -> None:
    """Only an `npm link` symlink is this lane's to remove; a real install there is not."""
    stale = prefix / "lib" / "node_modules" / "n8nac"
    stale.unlink()
    (stale / "dist").mkdir(parents=True)
    (stale / "package.json").write_text("{}")
    source, revision = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    assert (stale / "package.json").read_text() == "{}"
    assert os.readlink(prefix / "bin" / "n8nac") == str(
        (_root(tmp_path) / "bin" / "n8nac").absolute()
    )


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


def test_the_row_serves_no_MCP_and_is_not_asked_to(node: Node, tmp_path: Path) -> None:
    source, revision = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, revision)
    assert isinstance(N8NAC.install, NpmInstall)
    assert N8NAC.install.mcp_arguments is None
    assert outcome.action == ACTION_INSTALLED, outcome.detail
    assert not [probe for probe in outcome.probes if "initialize" in probe.command]
