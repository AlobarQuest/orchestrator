"""The npm act, and above all its ROLLBACK -- demonstrated end to end against scratch roots.

The fetch is real git against a local repository, fetched by SHA exactly as the act fetches the
fork. npm is a fake that writes what the real one writes -- `node_modules/`, a built `dist/`, a
tarball with a `package/` prefix -- and logs every invocation, so the order of the build and the
absence of lifecycle scripts are asserted rather than assumed. Nothing here reaches the network or
the operator's real npm prefix.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
)
from tool_installer.npm import (
    install_and_prove_npm,
    package_dir,
    read_installed_package,
)
from tool_installer.toolchain import Node, ToolchainError, resolve_node
from tool_installer.tools import NpmInstall, Tool

OLD_REVISION = "a" * 40

FAKE = Tool(
    name="faketool",
    repository="AlobarQuest/faketool",
    branch="main",
    install=NpmInstall(
        package="fake-agents",
        build_commands=(("run", "compile"),),
        probe_commands=(("--help",),),
        mcp_arguments=("mcp",),
    ),
)

# The tool's entry point. It answers `--help`, `--version` and one MCP `initialize`, reporting
# `REPORTS`, which a test may set to something other than the package's version -- the binary
# that lies about what it is, which only a by-value check catches.
_ENTRY = """#!/bin/sh
case "$1" in
  --help) echo "usage: faketool" ;;
  --version) echo "REPORTS" ;;
  mcp) read -r _; printf '{"result":{"serverInfo":{"name":"faketool","version":"REPORTS"}}}\\n' ;;
  *) exit 2 ;;
esac
"""

# npm, as far as the act uses it. Every call is appended to `$NPM_LOG` with the lifecycle-script
# switch it saw, so a test can assert no call ran without it.
_NPM = """#!/bin/sh
echo "$* ignore_scripts=$npm_config_ignore_scripts" >> "$NPM_LOG"
if [ -n "$NPM_FAIL" ] && [ "$1 $2" = "$NPM_FAIL" ]; then echo "npm ERR! $NPM_FAIL" >&2; exit 1; fi
case "$1" in
  ci) mkdir -p node_modules ;;
  run) [ "$2" = "compile" ] && mkdir -p dist && echo built > dist/main.js ;;
  pack)
    staging="$(mktemp -d)"
    mkdir "$staging/package"
    cp -R package.json scripts dist "$staging/package/"
    tar -czf "$3/fake-agents-1.0.0.tgz" -C "$staging" package
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


def _fork(tmp_path: Path, *, version: str = "1.0.0", reports: str | None = None) -> tuple[str, str]:
    """A local repository holding one revision of the package. Returns `(source, revision)`."""
    repo = tmp_path / "fork"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({"name": "fake-agents", "version": version, "bin": {"faketool": "./scripts/t"}})
    )
    (repo / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3}))
    _executable(repo / "scripts" / "t", _ENTRY.replace("REPORTS", reports or version))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "release")
    return str(repo), _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Node:
    bin_dir = tmp_path / "nodebin"
    _executable(bin_dir / "node", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "npm", _NPM)
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    monkeypatch.delenv("NPM_FAIL", raising=False)
    return Node(executable=bin_dir / "node")


def _seed_installed(root: Path, revision: str = OLD_REVISION) -> None:
    """The outgoing install: a package directory recording its revision, and the bin link."""
    package = package_dir(root, "fake-agents")
    _executable(package / "scripts" / "t", _ENTRY.replace("REPORTS", "0.9.0"))
    (package / "package.json").write_text(
        json.dumps({"name": "fake-agents", "version": "0.9.0", "gitHead": revision})
    )
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "faketool").symlink_to("../lib/node_modules/fake-agents/scripts/t")


def _act(node: Node, tmp_path: Path, source: str, revision: str):
    return install_and_prove_npm(
        tool=FAKE,
        node=node,
        install_root=tmp_path / "root",
        backup_root=tmp_path / "backups" / "faketool-0.9.0",
        revision=revision,
        source=source,
    )


def test_a_first_install_is_built_staged_linked_and_proven(node: Node, tmp_path: Path) -> None:
    source, revision = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    root = tmp_path / "root"
    assert read_installed_package(root, "fake-agents") == outcome.installed
    assert outcome.installed is not None
    assert outcome.installed.revision == revision
    assert (root / "bin" / "faketool").resolve() == (
        package_dir(root, "fake-agents") / "scripts" / "t"
    ).resolve()
    assert [probe.command[0] for probe in outcome.probes] == ["--help", "--version", "mcp"]
    # Nothing of the build is left in the root: only the installed tree and the link.
    assert sorted(path.name for path in root.iterdir()) == ["bin", "lib"]


def test_the_build_runs_in_order_and_NEVER_with_lifecycle_scripts(
    node: Node, tmp_path: Path
) -> None:
    """`npm ci`, the row's build, `npm pack`, then a production-only `npm ci` against the lockfile
    -- and every one of them with lifecycle scripts refused."""
    source, revision = _fork(tmp_path)
    _act(node, tmp_path, source, revision)
    calls = (tmp_path / "npm.log").read_text().splitlines()
    assert [
        call.split(" ignore_scripts=")[0].split(" --pack-destination")[0] for call in calls
    ] == [
        "ci",
        "run compile",
        "pack",
        "ci --omit=dev",
    ]
    assert all(call.endswith("ignore_scripts=true") for call in calls)


def test_the_installed_lockfile_is_the_forks(node: Node, tmp_path: Path) -> None:
    """The production install resolves against the reviewed lockfile, not against the registry."""
    source, revision = _fork(tmp_path)
    _act(node, tmp_path, source, revision)
    installed = package_dir(tmp_path / "root", "fake-agents") / "package-lock.json"
    assert json.loads(installed.read_text()) == {"lockfileVersion": 3}


def test_an_upgrade_moves_the_outgoing_package_into_the_backup_root(
    node: Node, tmp_path: Path
) -> None:
    source, revision = _fork(tmp_path)
    _seed_installed(tmp_path / "root")
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    kept = tmp_path / "backups" / "faketool-0.9.0" / "fake-agents" / "package.json"
    assert json.loads(kept.read_text())["gitHead"] == OLD_REVISION


def test_a_binary_that_lies_about_its_version_is_ROLLED_BACK_to_the_previous_one(
    node: Node, tmp_path: Path
) -> None:
    """Every exit status is clean; only the by-value checks catch it. The package directory AND
    the link must both be the outgoing ones afterwards -- restoring either alone would leave the
    record or the link describing something that is not there."""
    source, revision = _fork(tmp_path, reports="9.9.9")
    root = tmp_path / "root"
    _seed_installed(root)
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_ROLLED_BACK
    assert {probe.command[0]: probe.passed for probe in outcome.probes} == {
        "--help": True,
        "--version": False,
        "mcp": False,
    }
    restored = read_installed_package(root, "fake-agents")
    assert restored is not None
    assert restored.revision == OLD_REVISION
    assert os.readlink(root / "bin" / "faketool") == "../lib/node_modules/fake-agents/scripts/t"


def test_a_failed_FIRST_install_leaves_nothing_behind(node: Node, tmp_path: Path) -> None:
    """No outgoing package means nothing to restore, so the rollback is a removal -- of the
    package directory and of the link, or the next pass reads an unproven install as current."""
    source, revision = _fork(tmp_path, reports="9.9.9")
    outcome = _act(node, tmp_path, source, revision)

    root = tmp_path / "root"
    assert outcome.action == ACTION_ROLLED_BACK
    assert not package_dir(root, "fake-agents").exists()
    assert not (root / "bin" / "faketool").is_symlink()
    assert read_installed_package(root, "fake-agents") is None


def test_a_failed_build_touches_nothing_live(
    node: Node, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build is staged, so a failure is reported and the working install is never moved."""
    monkeypatch.setenv("NPM_FAIL", "run compile")
    source, revision = _fork(tmp_path)
    root = tmp_path / "root"
    _seed_installed(root)
    outcome = _act(node, tmp_path, source, revision)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "`npm run compile` exited 1" in outcome.detail
    assert outcome.installed is not None
    assert outcome.installed.revision == OLD_REVISION
    assert not (tmp_path / "backups").exists()


def test_a_revision_the_fork_does_not_hold_is_an_install_failure(
    node: Node, tmp_path: Path
) -> None:
    source, _ = _fork(tmp_path)
    outcome = _act(node, tmp_path, source, "c" * 40)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "git -C" in outcome.detail


def test_a_manifest_without_a_revision_was_not_installed_by_this_lane(tmp_path: Path) -> None:
    """Read as nothing installed, so the lane installs over it rather than trusting it."""
    package = package_dir(tmp_path, "fake-agents")
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "fake-agents", "version": "1.0.0"}))
    assert read_installed_package(tmp_path, "fake-agents") is None


def test_node_without_npm_beside_it_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    _executable(tmp_path / "node", "#!/bin/sh\n")
    with pytest.raises(ToolchainError, match="no npm beside it"):
        resolve_node((tmp_path / "node",))


def test_node_is_found_off_PATH_and_leads_the_subprocess_PATH(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launchd's PATH has no node. The known location is used, and its directory is put first,
    because npm and every package `bin` are `#!/usr/bin/env node` scripts."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    _executable(tmp_path / "node", "#!/bin/sh\n")
    _executable(tmp_path / "npm", "#!/bin/sh\n")
    found = resolve_node((tmp_path / "missing", tmp_path / "node"))
    assert found.executable == tmp_path / "node"
    assert found.environment()["PATH"].split(os.pathsep)[0] == str(tmp_path)
