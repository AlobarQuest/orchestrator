"""The registry act -- upstream's published package at the fork's version, holding the fork's files.

The fork is a real local repository with a `main` commit and a `hardened` commit on top of it,
fetched by SHA exactly as the act fetches the fork. npm is a fake that answers `prefix -g` and
`pack <spec>` with a tarball a test builds, and logs every call. node is a fake that runs the
package's entry point as a shell script, and that entry point logs the telemetry variable it was
started with, so the wrapper is proven to set it. Nothing reaches the network or the operator's
real npm prefix.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_NONE,
    ACTION_NOT_PERMITTED,
    ACTION_ROLLED_BACK,
)
from tool_installer.npm import package_dir, read_installed_package
from tool_installer.record import Installation, summary_of
from tool_installer.registry import REGISTRY_FIELD, WRAPPER_DIRECTORY, install_and_prove_registry
from tool_installer.toolchain import Node
from tool_installer.tools import (
    MODERN_WEB_GUIDANCE,
    MODERN_WEB_GUIDANCE_CLI,
    TOOLS,
    PluginInstall,
    RegistryInstall,
    Tool,
)

PACKAGE = "fake-guidance"
SKILL = "skills/g/SKILL.md"
STAMP = "2026_11_10-47b4c80e"
OLD_REVISION = "a" * 40
OLD_TARGET = f"../lib/node_modules/{PACKAGE}/{WRAPPER_DIRECTORY}/mwg"
INTEGRITY = "sha512-fake"

FAKE = Tool(
    name="mwg-cli",
    repository="AlobarQuest/fake-guidance",
    branch="hardened",
    install=RegistryInstall(
        package=PACKAGE,
        binary="mwg",
        environment=(("DISABLE_TELEMETRY", "1"),),
        skill=SKILL,
        search="light dismiss dialog",
    ),
)

MANIFEST = json.dumps({"name": PACKAGE, "version": "1.0.1", "bin": {"mwg": "skills/g/cli.mjs"}})
UPSTREAM_SKILL = f'Run `npx -y mwg@latest search "<q>" --skill-version {STAMP}`.\n'
HARDENED_SKILL = f'Run `mwg search "<q>" --skill-version {STAMP}`.\n'
UPSTREAM_GUIDE = "# Dialog\n\nSee `npx -y mwg@latest retrieve html`.\n"
HARDENED_GUIDE = "# Dialog\n\nSee `mwg retrieve html`.\n"

# The CLI, as far as the probes use it. It logs the telemetry variable it started with; `search`
# writes `$CLI_STDERR` to stderr when set; `retrieve` prints the INSTALLED guides beside it.
CLI = """#!/bin/sh
here="$(dirname "$0")"
echo "DISABLE_TELEMETRY=$DISABLE_TELEMETRY" >> "$CLI_LOG"
case "$1" in
  --version) echo "${CLI_REPORTS:-1.0.1}" ;;
  search) [ -n "$CLI_STDERR" ] && echo "$CLI_STDERR" >&2
          echo '[{"id":"dialog","description":"A dialog"}]' ;;
  retrieve) for id in $(echo "$2" | tr , ' '); do
              echo "--- Guide for $id ---"; cat "$here/guides/$id.md"
            done ;;
  *) exit 2 ;;
esac
"""

# npm, as far as the act uses it: `prefix -g` answers `$NPM_PREFIX`, and `pack <spec>` copies
# `$NPM_PUBLISHED` into the destination and reports it as npm does.
NPM = """#!/bin/sh
echo "$* ignore_scripts=$npm_config_ignore_scripts" >> "$NPM_LOG"
case "$1" in
  prefix) echo "$NPM_PREFIX" ;;
  pack) cp "$NPM_PUBLISHED" "$5/fake-guidance-1.0.1.tgz"
        echo '[{"filename": "fake-guidance-1.0.1.tgz", "integrity": "sha512-fake"}]' ;;
  *) exit 1 ;;
esac
"""


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git(cwd: Path, *args: str, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, input=stdin, env=env
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], parents: list[str], message: str) -> str:
    """A commit holding exactly `files`, written from a tree and index of its own."""
    number = len(list(repo.parent.glob("tree-*")))
    tree_dir = repo.parent / f"tree-{number}"
    for path, text in files.items():
        (tree_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (tree_dir / path).write_text(text)
    env = {**os.environ, "GIT_INDEX_FILE": str(repo.parent / f"index-{number}")}
    _git(tree_dir, f"--git-dir={repo / '.git'}", "--work-tree=.", "add", "-A", env=env)
    tree = _git(tree_dir, f"--git-dir={repo / '.git'}", "write-tree", env=env)
    argv = ["-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", tree]
    for parent in parents:
        argv += ["-p", parent]
    return _git(repo, *argv, stdin=message)


def _main_files() -> dict[str, str]:
    return {
        "package.json": MANIFEST,
        "README.md": "# fake\n",
        SKILL: UPSTREAM_SKILL,
        "skills/g/guides/dialog.md": UPSTREAM_GUIDE,
    }


def _hardened_files() -> dict[str, str]:
    files = _main_files()
    files[SKILL] = HARDENED_SKILL
    files["skills/g/guides/dialog.md"] = HARDENED_GUIDE
    return files


class Fork:
    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "fork"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.main = _commit(self.repo, _main_files(), [], "release")
        self.hardened = _commit(self.repo, _hardened_files(), [self.main], "fork-harden")

    @property
    def source(self) -> str:
        return str(self.repo)


def _member(archive: tarfile.TarFile, name: str, content: str | bytes) -> None:
    data = content.encode("utf-8", "surrogateescape") if isinstance(content, str) else content
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(data))


def _published(
    tmp_path: Path,
    *,
    changed: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
    link: str | None = None,
    stamp: str = STAMP,
) -> Path:
    """Upstream's tarball: `main`'s files plus the CLI and its stamp, which `main` lacks."""
    files = {**_main_files(), "skills/g/cli.mjs": CLI, "skills/g/skill-version.txt": stamp}
    files.update(changed or {})
    files.update(extra or {})
    path = tmp_path / "published.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            _member(archive, f"package/{name}", text)
        if link:
            info = tarfile.TarInfo(f"package/{link}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
    return path


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    root = tmp_path / "prefix"
    (root / "bin").mkdir(parents=True)
    (root / "lib" / "node_modules").mkdir(parents=True)
    return root


@pytest.fixture
def node(tmp_path: Path, prefix: Path, monkeypatch: pytest.MonkeyPatch) -> Node:
    bin_dir = tmp_path / "nodebin"
    _executable(bin_dir / "node", '#!/bin/sh\nexec /bin/sh "$@"\n')
    _executable(bin_dir / "npm", NPM)
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    monkeypatch.setenv("CLI_LOG", str(tmp_path / "cli.log"))
    monkeypatch.setenv("NPM_PREFIX", str(prefix))
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path)))
    for name in ("DISABLE_TELEMETRY", "CLI_REPORTS", "CLI_STDERR"):
        monkeypatch.delenv(name, raising=False)
    return Node(executable=bin_dir / "node")


@pytest.fixture
def fork(tmp_path: Path) -> Fork:
    return Fork(tmp_path)


def _root(tmp_path: Path) -> Path:
    return tmp_path / "root"


def _act(node: Node, tmp_path: Path, source: str, revision: str, tool: Tool = FAKE):
    return install_and_prove_registry(
        tool=tool,
        node=node,
        install_root=_root(tmp_path),
        backup_root=tmp_path / "backups" / "mwg-cli-1.0.0",
        revision=revision,
        source=source,
    )


def _seed_installed(root: Path) -> None:
    """The outgoing install: a package recording its revision, with its own wrapper and link."""
    package = package_dir(root, PACKAGE)
    _executable(package / WRAPPER_DIRECTORY / "mwg", "#!/bin/sh\necho 1.0.0\n")
    (package / "package.json").write_text(
        json.dumps({"name": PACKAGE, "version": "1.0.0", "gitHead": OLD_REVISION})
    )
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "mwg").symlink_to(OLD_TARGET)


def _assert_previous_in_place(tmp_path: Path, prefix: Path) -> None:
    installed = read_installed_package(_root(tmp_path), PACKAGE)
    assert installed is not None
    assert installed.revision == OLD_REVISION
    assert os.readlink(_root(tmp_path) / "bin" / "mwg") == OLD_TARGET
    assert not (prefix / "bin" / "mwg").is_symlink()


def test_a_first_install_holds_the_forks_files_and_only_the_cli_from_the_tarball(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path
) -> None:
    outcome = _act(node, tmp_path, fork.source, fork.hardened)

    assert outcome.action == ACTION_INSTALLED, outcome.detail
    assert all(result.passed for result in outcome.probes)
    package = package_dir(_root(tmp_path), PACKAGE)
    # The fork's hardened files, never the tarball's copies of them.
    assert (package / "skills/g/guides/dialog.md").read_text() == HARDENED_GUIDE
    assert (package / "skills/g/SKILL.md").read_text() == HARDENED_SKILL
    assert (package / "skills/g/cli.mjs").read_text() == CLI
    manifest = json.loads((package / "package.json").read_text())
    assert manifest["gitHead"] == fork.hardened
    assert manifest[REGISTRY_FIELD] == {
        "package": PACKAGE,
        "version": "1.0.1",
        "integrity": INTEGRITY,
        "files": ["skills/g/cli.mjs", "skills/g/skill-version.txt"],
    }
    installed = read_installed_package(_root(tmp_path), PACKAGE)
    assert installed is not None
    assert (installed.version, installed.revision) == ("1.0.1", fork.hardened)
    # The lane's entry is the wrapper inside the package; the prefix's link points at the entry.
    assert os.readlink(_root(tmp_path) / "bin" / "mwg") == OLD_TARGET
    assert os.readlink(prefix / "bin" / "mwg") == str((_root(tmp_path) / "bin" / "mwg").absolute())
    # The probe was run through the prefix's link last.
    assert outcome.probes[-1].command[:2] == (str(prefix / "bin" / "mwg"), "--version")


def test_the_wrapper_turns_telemetry_off_for_every_run(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path
) -> None:
    assert "DISABLE_TELEMETRY" not in os.environ
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_INSTALLED, outcome.detail
    runs = (tmp_path / "cli.log").read_text().splitlines()
    assert len(runs) == 4  # the version, search, retrieve, and the version through the prefix
    assert set(runs) == {"DISABLE_TELEMETRY=1"}


def test_the_tarball_is_fetched_at_the_forks_version_with_no_lifecycle_scripts(
    node: Node, tmp_path: Path, fork: Fork
) -> None:
    _act(node, tmp_path, fork.source, fork.hardened)
    calls = (tmp_path / "npm.log").read_text().splitlines()
    assert [call.split(" ")[:2] for call in calls] == [
        ["prefix", "-g"],
        ["pack", f"{PACKAGE}@1.0.1"],
    ]
    assert all(call.endswith("ignore_scripts=true") for call in calls)


def test_the_comparison_is_with_the_hardened_commits_LAST_parent(
    node: Node, tmp_path: Path, fork: Fork
) -> None:
    """fork-harden's second and later commits: the previous `hardened`, then `main`."""
    later = _commit(fork.repo, _hardened_files(), [fork.hardened, fork.main], "fork-harden")
    outcome = _act(node, tmp_path, fork.source, later)
    assert outcome.action == ACTION_INSTALLED, outcome.detail


def test_a_row_on_main_compares_and_installs_main_itself_and_the_probe_finds_the_runner(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path
) -> None:
    """Upstream's guides name a package runner, which `retrieve` serves, so the probe refuses."""
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, fork.source, fork.main, replace(FAKE, branch="main"))
    assert outcome.action == ACTION_ROLLED_BACK
    assert [result.passed for result in outcome.probes] == [True, True, True, False]
    assert "'npx'" in outcome.probes[-1].detail
    _assert_previous_in_place(tmp_path, prefix)


def test_a_tarball_that_differs_from_main_is_REFUSED_and_nothing_live_moves(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_installed(_root(tmp_path))
    monkeypatch.setenv(
        "NPM_PUBLISHED",
        str(_published(tmp_path, changed={"README.md": "# changed after the tag\n"})),
    )
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "differs from the fork's main in 1: README.md" in outcome.detail
    _assert_previous_in_place(tmp_path, prefix)


@pytest.mark.parametrize(
    "field",
    [
        {"dependencies": {"left-pad": "1.0.0"}},
        {"optionalDependencies": {"left-pad": "1.0.0"}},
        {"scripts": {"postinstall": "node x.js"}},
    ],
)
def test_a_tarball_declaring_a_dependency_or_a_script_is_REFUSED(
    node: Node,
    tmp_path: Path,
    fork: Fork,
    prefix: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: dict[str, object],
) -> None:
    _seed_installed(_root(tmp_path))
    nested = json.dumps({"name": "inner", **field})
    monkeypatch.setenv(
        "NPM_PUBLISHED", str(_published(tmp_path, extra={"skills/g/package.json": nested}))
    )
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "skills/g/package.json declares" in outcome.detail
    _assert_previous_in_place(tmp_path, prefix)


def test_a_tarball_holding_a_link_is_REFUSED(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path, link="skills/g/gen")))
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "not a regular file" in outcome.detail
    assert not package_dir(_root(tmp_path), PACKAGE).exists()


@pytest.mark.parametrize("version", ["^1.0.1", "latest", "1.0.1-beta.1"])
def test_a_fork_version_that_is_not_a_plain_release_is_REFUSED_before_any_download(
    node: Node, tmp_path: Path, version: str
) -> None:
    repo = tmp_path / "fork"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    files = {**_hardened_files(), "package.json": json.dumps({"name": PACKAGE, "version": version})}
    revision = _commit(repo, files, [], "release")
    outcome = _act(node, tmp_path, str(repo), revision, replace(FAKE, branch="main"))
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "no plain version" in outcome.detail
    assert "pack" not in (tmp_path / "npm.log").read_text()


def test_a_skill_and_a_cli_that_disagree_are_ROLLED_BACK(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_installed(_root(tmp_path))
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path, stamp="2026_11_12-0000000")))
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_ROLLED_BACK
    assert not outcome.probes[1].passed
    assert "2026_11_12-0000000" in outcome.probes[1].detail
    _assert_previous_in_place(tmp_path, prefix)


def test_a_cli_stamp_that_is_not_text_is_ROLLED_BACK_not_raised(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_installed(_root(tmp_path))
    monkeypatch.setenv("NPM_PUBLISHED", str(_published(tmp_path, stamp="\udcff")))
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_ROLLED_BACK
    assert not outcome.probes[1].passed
    _assert_previous_in_place(tmp_path, prefix)


def test_a_search_that_writes_to_stderr_is_ROLLED_BACK(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI's own warning that the skill must be updated arrives on stderr."""
    _seed_installed(_root(tmp_path))
    monkeypatch.setenv("CLI_STDERR", "Please ask the user to update the skill")
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_ROLLED_BACK
    assert [result.passed for result in outcome.probes] == [True, True, False]
    _assert_previous_in_place(tmp_path, prefix)


def test_a_cli_that_lies_about_its_version_is_ROLLED_BACK(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_installed(_root(tmp_path))
    monkeypatch.setenv("CLI_REPORTS", "9.9.9")
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_ROLLED_BACK
    assert not outcome.probes[0].passed
    _assert_previous_in_place(tmp_path, prefix)


def test_an_upgrade_moves_the_outgoing_package_into_the_backup_root(
    node: Node, tmp_path: Path, fork: Fork
) -> None:
    _seed_installed(_root(tmp_path))
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_INSTALLED, outcome.detail
    kept = tmp_path / "backups" / "mwg-cli-1.0.0" / PACKAGE / "package.json"
    assert json.loads(kept.read_text())["gitHead"] == OLD_REVISION


def test_someone_elses_install_at_the_prefix_is_REFUSED_before_anything_is_fetched(
    node: Node, tmp_path: Path, fork: Fork, prefix: Path
) -> None:
    (prefix / "lib" / "node_modules" / PACKAGE).mkdir()
    outcome = _act(node, tmp_path, fork.source, fork.hardened)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "real install" in outcome.detail
    assert (tmp_path / "npm.log").read_text().splitlines() == ["prefix -g ignore_scripts=true"]


def test_a_revision_the_fork_does_not_hold_is_an_install_failure(
    node: Node, tmp_path: Path, fork: Fork
) -> None:
    outcome = _act(node, tmp_path, fork.source, "b" * 40)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert not package_dir(_root(tmp_path), PACKAGE).exists()


def test_the_cli_row_installs_before_its_plugin_under_a_name_of_its_own() -> None:
    names = [tool.name for tool in TOOLS]
    assert len(names) == len(set(names))
    assert names.index(MODERN_WEB_GUIDANCE_CLI.name) < names.index(MODERN_WEB_GUIDANCE.name)
    assert MODERN_WEB_GUIDANCE.name == "modern-web-guidance"
    cli = MODERN_WEB_GUIDANCE_CLI.install
    assert isinstance(cli, RegistryInstall)
    assert cli.binary == MODERN_WEB_GUIDANCE.name
    assert isinstance(MODERN_WEB_GUIDANCE.install, PluginInstall)
    assert {MODERN_WEB_GUIDANCE_CLI.branch, MODERN_WEB_GUIDANCE.branch} == {"hardened"}
    assert {MODERN_WEB_GUIDANCE_CLI.repository, MODERN_WEB_GUIDANCE.repository} == {
        "AlobarQuest/modern-web-guidance"
    }
    assert cli.environment == (("DISABLE_TELEMETRY", "1"),)


def _row(action: str, installed: str | None) -> Installation:
    return Installation(
        tool=MODERN_WEB_GUIDANCE_CLI,
        install_root="/root",
        installed_revision=installed,
        installed_version="1.0.1" if installed else None,
        installed_committed_at=None,
        available_revision="c" * 40,
        available_committed_at="2026-11-16T00:00:00Z",
        action=action,
    )


@pytest.mark.parametrize(
    ("action", "installed", "says"),
    [
        (ACTION_ROLLED_BACK, OLD_REVISION, "did not prove itself"),
        (ACTION_INSTALL_FAILED, OLD_REVISION, "could not be installed at ccccccc"),
        (ACTION_NONE, "c" * 40, "holds ccccccc, which is hardened's head"),
        (ACTION_NOT_PERMITTED, OLD_REVISION, "this pass was not permitted to act"),
        (ACTION_INSTALLED, "c" * 40, "passed every probe"),
    ],
)
def test_the_registry_rows_record_never_says_it_was_built(
    action: str, installed: str | None, says: str
) -> None:
    sentence = summary_of(_row(action, installed))
    assert says in sentence
    assert "built" not in sentence
