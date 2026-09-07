"""The plugin act: pull the serving clone, move the pin, REFRESH WHAT CLAUDE CODE ACTUALLY LOADS.

**THE DEFECT THIS EXISTS TO PREVENT, AND IT IS THE WHOLE REASON THIS MODULE IS NOT THREE LINES.**
Claude Code does not load the serving clone. It loads a SEPARATE VERSIONED COPY under
`~/.claude/plugins/cache/<marketplace>/<name>/<version>/`, which a `git pull` does not touch. A
lane that pulls the clone and moves the marketplace pin has changed nothing about what runs, and
every surface it could check -- a clean pull, a bumped pin, a pushed commit -- would say it
succeeded. That was caught by hand on 2026-06-17, after the running plugin had drifted to 9.42.3
while the clone and the pin both read 9.44.0. `installed == pinned == clone` is the invariant the
hub repository states, and the installed half is the one nothing else asserts.

**THE PUSH IS NEW AUTHORITY, AND IT IS NAMED RATHER THAN SLID IN.** Until this row the lane
touched `~/.cargo` and nothing else. It now publishes a commit to `AlobarQuest/devon-plugins`,
which is why this file carries an entry in the repository-wide merge guard's own register --
taken openly, in that guard's register, with the same discipline ADR-0033 imposed on the only
other producer here that publishes to a default branch. The alternative, committing locally and
never pushing, was rejected: an uncommitted edit every night is exactly the dirty-working-copy
state the activation sweep exists to report, and it would leave the machine's own marketplace
repository permanently behind what the machine runs.

**THE PUSH HAPPENS AFTER VERIFICATION, WHICH IS NOT THE ORDER THE ROUTINE A PERSON FOLLOWS USES.**
A person pushes before running the update because they are following a list. A scheduled pass has
to be able to put everything back, and the marketplace is a LOCAL DIRECTORY -- `claude plugin
marketplace update` reads the hub's own filesystem, so the install does not depend on the push at
all. Publishing first would mean a rollback had to publish a second commit undoing the first,
over a network, at the one moment the pass has already established that something is wrong. Doing
it last means a rollback is entirely local: reset the hub to the commit it was on, reset the
clone, put the cache back. The cost is the case where everything installs and the push fails, and
that case is handled the same way as any other failure -- the pass rolls the whole act back, so
the machine is left in the state that IS published. A new outcome for "installed but unpublished"
was considered and rejected: it would be a fifth vocabulary member describing a state the hub's
own invariant forbids.

**WHAT VERIFICATION IS HERE, AND HOW MUCH WEAKER IT IS THAN `rtk`'s.** `rtk`'s probe runs the
binary through its own filter -- the tool doing the job it exists to do. A plugin is not
executable, and the functional check (`/octo:doctor`) needs a Claude Code session, which a
scheduled pass does not have and must not pretend to. So what is checkable is AGREEMENT across
the sites: the installed version equals the marketplace pin, equals the serving clone's manifest;
the installed revision equals the fork head this pass acted on; and the cache directory that
version names exists and is not empty. That is a real check -- it is precisely what the
2026-06-17 defect would have failed -- and it is not a check that the plugin works.

**THE RESTART GAP, WHICH THIS LANE CANNOT CLOSE AND MUST NOT PRETEND TO.** Claude Code loads
plugins at session start. After a successful pass the INSTALLED plugin is the new one and the
RUNNING one is whatever the live session loaded, which may be the old one for hours. Nothing here
says the machine is running the new plugin, because nothing here checked it; the record says what
is installed, and the summary says it loads at the next start. This is the same reason the prune
keeps the newest version other than the installed one: that directory is what a still-running
session is loaded from, and deleting it can break a session that is in use.

**IT REFUSES TO START ON A TREE SOMEBODY ELSE HAS TOUCHED**, in both repositories and for two
different reasons. The rollback is `git reset --hard`, which would destroy uncommitted work, so a
dirty tree is refused rather than reset. And a clone that is not on the tracked branch, or a hub
that is not on the branch the publish names, would have this pass read a manifest from the wrong
history or report a commit as published that is not -- both silent, both wearing the shape of
success.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    InstallOutcome,
    ProbeResult,
    RollbackFailed,
)
from tool_installer.toolchain import ToolchainError
from tool_installer.tools import PluginInstall, Tool

DEFAULT_HUB_ROOT: Final = Path.home() / "Developer" / "devon-plugins"
DEFAULT_PLUGINS_ROOT: Final = Path.home() / ".claude" / "plugins"

# WHERE `claude` IS LOOKED FOR WHEN IT IS NOT ON `PATH`, and it is not on a scheduled job's PATH.
# Measured 2026-09-07 under `env -i PATH=/usr/bin:/bin HOME=$HOME`: `command -v claude` finds
# nothing, while both paths below exist. This is the same trap `rustup` and `uv` set one tool
# over, and a lane that looked on `PATH` alone would report unmeasurable every morning while
# working perfectly from a shell.
#
# THE ORDER MATTERS BECAUSE THE TWO ARE DIFFERENT INSTALLS. Measured the same day:
# `~/.local/bin/claude` is 2.1.263 and `/opt/homebrew/bin/claude` is 2.1.258. The first is the one
# the operator's own shell resolves, so it is the one whose behaviour a person would be describing
# if they ran the routine by hand.
CLAUDE_CANDIDATES: Final = (
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)

# THE ACT, SPELLED THE WAY THE GUARD SCANS FOR IT, and a STRING rather than a list of four words
# on purpose -- the discipline `bump_proposer/standing.py` records. `MERGE_EXEMPT_PATHS` in
# `tests/architecture/test_wsp21_invariant_scan.py` names this file, and the rot check beside it
# removes an entry whose file no longer contains one of `MERGE_ACTIONS`. That scan reads this
# file's TEXT, where a list literal matches nothing -- so writing the command as
# `["git", "push", ...]` would pass the guard without an exemption, and would then have the
# exemption withdrawn as unneeded, leaving the act in place with nothing watching it. Not evasion
# by rewording, but by tokenisation, which amounts to the same thing. Written this way the bytes
# the guard scans and the command that runs are ONE value.
PUBLISH_COMMAND: Final = "git push origin main"

# The branch that command publishes, READ OUT OF IT rather than written again. The push names the
# branch by name, so a hub on any other branch is one this lane must refuse; a second spelling of
# `main` here would be the one place that refusal could silently stop describing the command it
# guards.
_PUBLISHED_BRANCH: Final = PUBLISH_COMMAND.split()[-1]
_PUBLISHED_REMOTE: Final = PUBLISH_COMMAND.split()[-2]

MARKETPLACE_MANIFEST: Final = Path(".claude-plugin") / "marketplace.json"
PLUGIN_MANIFEST: Final = Path(".claude-plugin") / "plugin.json"
INSTALLED_RECORD: Final = "installed_plugins.json"

GIT_TIMEOUT_SECONDS: Final = 600
CLAUDE_TIMEOUT_SECONDS: Final = 300

# `"version"` and its string value, with the KEY AND SEPARATOR CAPTURED so a rewrite preserves the
# file's own spacing. The marketplace manifest is hand-authored -- a round trip through a JSON
# dumper would reflow every inline array in it.
_VERSION_VALUE: Final = re.compile(r'("version"\s*:\s*)"(?:[^"\\]|\\.)*"')


class PluginError(RuntimeError):
    """A step of the plugin routine refused. Always a finding, never a default."""


@dataclass(frozen=True)
class Sites:
    """The two places on this machine a plugin install lives.

    A parameter rather than a constant for the reason `install_root` is one on the cargo side:
    the rollback path can only be exercised end to end against scratch copies, and proving it
    against the real hub would mean breaking the plugin the operator is using.
    """

    hub_root: Path
    plugins_root: Path

    def clone_root(self, tool: Tool) -> Path:
        return self.hub_root / tool.name

    def marketplace_path(self) -> Path:
        return self.hub_root / MARKETPLACE_MANIFEST

    def installed_record_path(self) -> Path:
        return self.plugins_root / INSTALLED_RECORD


def default_sites() -> Sites:
    return Sites(hub_root=DEFAULT_HUB_ROOT, plugins_root=DEFAULT_PLUGINS_ROOT)


@dataclass(frozen=True)
class PluginEntry:
    """What Claude Code's own record says is installed for one plugin.

    `version` and `revision` are named for the cargo side's `Installed`, deliberately: the pass
    reads exactly those two off whichever row it is measuring, so the measurement code has one
    shape rather than two.
    """

    version: str
    revision: str
    install_path: str


def _run(argv: tuple[str, ...], *, cwd: Path | None, timeout: int) -> tuple[int, str, str]:
    """Run and return `(returncode, stdout, last useful line)`. Never raises for a non-zero exit.

    The diagnostic prefers stderr, which is right for a refused git command, and only its tail is
    kept: a failing pull's full output is large and is not this program's to relay.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as error:
        return 127, "", f"could not be run: {type(error).__name__}"
    tail = (completed.stderr or completed.stdout or "").strip().splitlines()
    return completed.returncode, completed.stdout, tail[-1][:200] if tail else ""


def _git(root: Path, *args: str) -> str:
    code, out, detail = _git_maybe(root, *args)
    if code != 0:
        raise PluginError(f"`git {' '.join(args[:3])}` in {root.name} exited {code}: {detail}")
    return out


def _git_maybe(root: Path, *args: str) -> tuple[int, str, str]:
    return _run(("git", "-C", str(root), *args), cwd=None, timeout=GIT_TIMEOUT_SECONDS)


def resolve_claude(candidates: tuple[Path, ...] = CLAUDE_CANDIDATES) -> Path:
    """`PATH` first, then the known locations. Never `PATH` alone -- see `CLAUDE_CANDIDATES`.

    Raises the CARGO side's error type on purpose. "This lane cannot reach the thing that would
    do the install" is one condition with one exit code, and giving the plugin row its own
    exception would make the entry point carry two branches that must never diverge.
    """
    found = shutil.which("claude")
    if found:
        return Path(found)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ToolchainError(
        f"claude is not on PATH and is at none of {', '.join(str(path) for path in candidates)}"
    )


def read_entry(sites: Sites, tool: Tool, marketplace: str) -> PluginEntry | None:
    """Claude Code's own record for one plugin, or `None` when it has never installed it.

    `None` is a first install, which is allowed -- deliberately not an error, and deliberately not
    confused with a record that exists and names a different revision. An entry missing either
    field is also `None`: a record that cannot say what it installed is not evidence that
    anything is.
    """
    try:
        document = json.loads(sites.installed_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    plugins = document.get("plugins")
    if not isinstance(plugins, dict):
        return None
    rows = plugins.get(f"{tool.name}@{marketplace}")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    row: dict[str, Any] = rows[0]
    version = row.get("version")
    revision = row.get("gitCommitSha")
    install_path = row.get("installPath")
    if not isinstance(version, str) or not isinstance(revision, str):
        return None
    return PluginEntry(
        version=version,
        revision=revision,
        install_path=install_path if isinstance(install_path, str) else "",
    )


def _pinned_version(sites: Sites, tool: Tool) -> str | None:
    """The version the marketplace manifest pins for this plugin."""
    try:
        document = json.loads(sites.marketplace_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PluginError(f"the marketplace manifest could not be read: {error!s:.120}") from error
    entries = _entries_named(document, tool.name)
    version = entries[0].get("version")
    return version if isinstance(version, str) else None


def _clone_version(sites: Sites, tool: Tool) -> str:
    """The version the serving clone's own manifest declares, which is what a bump copies."""
    path = sites.clone_root(tool) / PLUGIN_MANIFEST
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PluginError(f"{path.name} in the serving clone could not be read") from error
    version = document.get("version") if isinstance(document, dict) else None
    if not isinstance(version, str) or not version:
        raise PluginError(f"{path.name} in the serving clone declares no version")
    return version


def _entries_named(document: object, plugin: str) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise PluginError("the marketplace manifest is not an object")
    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        raise PluginError("the marketplace manifest carries no plugins array")
    entries = [
        entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == plugin
    ]
    if len(entries) != 1:
        raise PluginError(
            f"the marketplace manifest names {plugin} {len(entries)} times; expected exactly one"
        )
    return entries


def bump_marketplace_version(text: str, plugin: str, version: str) -> str:
    """Move ONE plugin's version in the manifest text, PROVING no other entry moved with it.

    **GENERATE AND VERIFY, RATHER THAN SCOPE A REPLACEMENT.** The obvious implementation is a
    string replace of `"version": "<old>"`, and it is wrong in a way nothing would report: the
    manifest also carries the marketplace's own `metadata.version` and a second plugin's, so two
    entries holding one version string would both move. Locating the target object's span instead
    means writing a brace scanner whose bugs are silent in the same direction.

    So every `"version"` value in the file is tried in turn, and the candidate KEPT is the one
    whose parsed document equals the original with exactly the target entry's version changed.
    The assertion the spec asks for is not a clause added beside the edit -- it IS the edit's
    selection rule, so an edit that moved anything else could not be returned. Zero or more than
    one surviving candidate is a refusal rather than a guess.

    The key and its separator are preserved from the match, so a manifest spaced any other way
    keeps its spacing; only the quoted value is rewritten, through `json.dumps` so a version
    needing an escape gets one.
    """
    document = json.loads(text)
    entries = _entries_named(document, plugin)
    if entries[0].get("version") == version:
        # Already there. Returning the text unchanged rather than rewriting it keeps a re-run
        # over unchanged reality from producing a diff, which is what makes the whole act
        # replayable after a pass that stopped half way.
        return text

    expected = json.loads(text)
    _entries_named(expected, plugin)[0]["version"] = version

    survivors = [
        candidate
        for candidate in (
            text[: match.start()] + match.group(1) + json.dumps(version) + text[match.end() :]
            for match in _VERSION_VALUE.finditer(text)
        )
        if _parses_to(candidate, expected)
    ]
    if len(survivors) != 1:
        raise PluginError(
            f"rewriting {plugin}'s version to {version} produced {len(survivors)} candidate "
            "manifests that change nothing else; refusing to guess which"
        )
    return survivors[0]


def _parses_to(candidate: str, expected: object) -> bool:
    try:
        return json.loads(candidate) == expected
    except ValueError:
        return False


def _require_usable_clone(sites: Sites, tool: Tool) -> str:
    """Refuse a serving clone this pass cannot safely pull into or reset, and answer its head.

    The branch check is the half that fails silently. A clone on a stray branch or a detached
    head still pulls, still yields a manifest, and still bumps the pin -- from a history that is
    not the one whose head this pass measured `behind` against.
    """
    root = sites.clone_root(tool)
    if not (root / ".git").exists():
        raise PluginError(f"the serving clone at {root} is not a git checkout")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != tool.branch:
        raise PluginError(
            f"the serving clone at {root} is on {branch or 'no branch'} rather than "
            f"{tool.branch}; this pass would read a manifest from the wrong history"
        )
    if _git(root, "status", "--porcelain").strip():
        raise PluginError(
            f"the serving clone at {root} has uncommitted changes; putting it back is "
            "`git reset --hard`, which would destroy them"
        )
    return _git(root, "rev-parse", "HEAD").strip()


def _require_publishable_hub(sites: Sites) -> str:
    """Refuse a hub this pass could not publish from, and answer its head.

    THREE REFUSALS, AND EACH COVERS A DIFFERENT SILENT FAILURE. A hub on another branch would
    have the publish push a branch this pass never wrote to, reporting `Everything up-to-date`
    and exiting 0 -- measured on the sibling producer, not reasoned. A dirty hub would have the
    commit sweep somebody else's work in, and the rollback destroy it. And a hub already carrying
    a commit `origin` does not is the residue of a pass whose publish was refused; left
    unexamined it sits local forever while every later pass reports a clean run, so it is
    refused here, at the top, where a person is told about it every night until it is reconciled.

    The unpublished check needs no fetch and must not make one: `origin/<branch>` is the
    remote-tracking ref, which a successful push advances and a refused push does not, so it
    answers this question from disk. Going to the network would make the refusal depend on
    somebody else's landings rather than on this lane's own unfinished act.
    """
    root = sites.hub_root
    if not (root / ".git").exists():
        raise PluginError(f"the marketplace hub at {root} is not a git checkout")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != _PUBLISHED_BRANCH:
        raise PluginError(
            f"the marketplace hub at {root} is on {branch or 'no branch'} rather than "
            f"{_PUBLISHED_BRANCH}; publishing pushes that branch by name, so a commit written "
            "here would be reported as published and would not be"
        )
    if _git(root, "status", "--porcelain").strip():
        raise PluginError(
            f"the marketplace hub at {root} has uncommitted changes; this lane commits what it "
            "writes and will not commit somebody else's"
        )
    ahead = _git(
        root, "rev-list", "--count", f"{_PUBLISHED_REMOTE}/{_PUBLISHED_BRANCH}..HEAD"
    ).strip()
    if not ahead.isdigit():
        raise PluginError(f"the hub at {root} would not say how far ahead of origin it is")
    if int(ahead) > 0:
        raise PluginError(
            f"the marketplace hub at {root} carries {ahead} commit(s) origin does not; "
            "publishing pushes the branch, so this pass would publish them too - reconcile "
            "the hub first"
        )
    return _git(root, "rev-parse", "HEAD").strip()


def _refresh_install(claude: Path, marketplace: str, plugin: str) -> None:
    """Re-copy the serving clone into the versioned cache Claude Code actually loads.

    THE TWO COMMANDS ARE ONE STEP. The first re-reads the local directory-marketplace's catalog
    so it sees the pin this pass moved; the second copies the clone into the cache directory for
    that version and rewrites Claude Code's own installed record. Running only the first leaves
    the catalog current and the machine stale, which is the 2026-06-17 defect with an extra
    command in front of it.

    `-y` IS REQUIRED AND THE ROUTINE A PERSON FOLLOWS DOES NOT CARRY IT. Measured 2026-09-07 on
    Claude Code 2.1.263: `claude plugin update` accepts the marketplace-declared command without
    a prompt only under `-y`, which its own help says is "required when stdin or stdout is not a
    TTY" -- and a scheduled pass has neither.
    """
    for argv in (
        (str(claude), "plugin", "marketplace", "update", marketplace),
        (str(claude), "plugin", "update", f"{plugin}@{marketplace}", "-y"),
    ):
        code, _, detail = _run(argv, cwd=None, timeout=CLAUDE_TIMEOUT_SECONDS)
        if code != 0:
            raise PluginError(f"`{' '.join(argv[1:5])}` exited {code}: {detail}")


def verify(sites: Sites, tool: Tool, marketplace: str, head_revision: str) -> list[ProbeResult]:
    """Prove the three sites agree and that the cache holds something. Every result is returned.

    Every check runs even once one has failed: the record is more useful saying which three of
    four held than saying only that something did not, and nothing here mutates.

    THIS IS NOT A FUNCTIONAL CHECK and the module docstring says so. It is the agreement the
    2026-06-17 defect broke, and nothing more.
    """
    entry = read_entry(sites, tool, marketplace)
    installed = entry.version if entry else ""
    try:
        pinned = _pinned_version(sites, tool) or ""
    except PluginError as error:
        pinned = f"unreadable: {error}"
    try:
        clone = _clone_version(sites, tool)
    except PluginError as error:
        clone = f"unreadable: {error}"

    cache = Path(entry.install_path) if entry and entry.install_path else None
    populated = bool(cache and cache.is_dir() and any(cache.iterdir()))

    return [
        ProbeResult(
            command=("installed", "==", "pinned"),
            passed=bool(installed) and installed == pinned,
            detail=f"installed {installed!r}, pinned {pinned!r}",
        ),
        ProbeResult(
            command=("installed", "==", "clone"),
            passed=bool(installed) and installed == clone,
            detail=f"installed {installed!r}, clone {clone!r}",
        ),
        ProbeResult(
            command=("installed", "revision", "==", "head"),
            passed=bool(entry) and entry.revision == head_revision,
            detail=f"installed {(entry.revision if entry else '')[:7]}, head {head_revision[:7]}",
        ),
        ProbeResult(
            command=("cache", "is", "populated"),
            passed=populated,
            detail=str(cache) if cache else "the installed record names no path",
        ),
    ]


def _version_key(name: str) -> tuple[object, ...]:
    """A `sort -V`-shaped key, so `9.45.0` sorts below `11.0.1` where a string sort would not."""
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name) if part)


def prune(cache_root: Path, installed_version: str) -> list[str]:
    """Keep exactly the installed version and the single newest other; report every outcome.

    THE NEWEST OTHER IS NOT SPARE CAPACITY. Claude Code loads plugins at session start, so a
    session running right now is loaded from the directory the previous install wrote -- deleting
    it can break a session that is in use, and it is also the one-step rollback buffer. Keeping
    it is the same restart gap the module docstring names, seen from the other side.

    A FAILURE HERE IS REPORTED AND NEVER FATAL. The install is verified by the time this runs; a
    directory that could not be removed is untidy, not broken, and failing the pass over it would
    roll back a good install. `permissions.deny` on this machine refuses `rm -rf` in an
    interactive shell, which is a fact about a shell rather than about this program -- but the
    reporting path is the one that has to work either way, so it is the one that is exercised.
    """
    reported: list[str] = []
    try:
        present = sorted(path.name for path in cache_root.iterdir() if path.is_dir())
    except OSError as error:
        return [f"the install cache could not be listed: {type(error).__name__}"]
    others = sorted((name for name in present if name != installed_version), key=_version_key)
    keep_other = others[-1] if others else None
    for name in others[:-1] if keep_other else []:
        try:
            shutil.rmtree(cache_root / name)
        except OSError as error:
            reported.append(f"stale cache {name} could not be removed: {type(error).__name__}")
        else:
            reported.append(f"removed stale cache {name}")
    return reported


@dataclass
class _Act:
    """What the pass has changed so far, so putting it back is driven by facts rather than memory.

    The two `head` values are the commits each repository was on BEFORE anything moved, captured
    before the first mutation. `bumped` is what decides whether the cache has to be refreshed on
    the way back: a pass that refused at the pull changed nothing at all, and running the install
    commands to undo nothing could turn a clean refusal into a rollback failure.
    """

    hub_head: str
    clone_head: str
    previous: PluginEntry | None
    version: str = ""
    bumped: bool = False
    committed: bool = False


def install_and_prove_plugin(
    *,
    tool: Tool,
    claude: Path,
    sites: Sites,
    head_revision: str,
) -> InstallOutcome:
    """Pull, pin, refresh the install cache, prove the sites agree, publish -- or put it all back.

    The order is the safety property, and the two halves of it are stated in the module
    docstring: nothing is touched before the outgoing state is captured, and nothing is published
    before it is proven.
    """
    if not isinstance(tool.install, PluginInstall):
        raise PluginError(f"{tool.name} is not a plugin row")
    marketplace = tool.install.marketplace

    try:
        act = _Act(
            hub_head=_require_publishable_hub(sites),
            clone_head=_require_usable_clone(sites, tool),
            previous=read_entry(sites, tool, marketplace),
        )
    except PluginError as error:
        # Nothing has moved, so there is nothing to put back. The refusal is the whole outcome.
        return InstallOutcome(
            action=ACTION_INSTALL_FAILED,
            installed=read_entry(sites, tool, marketplace),
            detail=str(error),
        )

    try:
        _advance(tool=tool, claude=claude, sites=sites, marketplace=marketplace, act=act)
    except PluginError as error:
        _undo(claude=claude, sites=sites, tool=tool, marketplace=marketplace, act=act)
        return InstallOutcome(
            action=ACTION_INSTALL_FAILED,
            installed=read_entry(sites, tool, marketplace),
            detail=str(error),
        )

    results = verify(sites, tool, marketplace, head_revision)
    if not all(result.passed for result in results):
        _undo(claude=claude, sites=sites, tool=tool, marketplace=marketplace, act=act)
        return InstallOutcome(
            action=ACTION_ROLLED_BACK,
            installed=read_entry(sites, tool, marketplace),
            probes=results,
            detail="the installed plugin did not agree with the pin, the clone or the head",
        )

    try:
        _publish(sites, tool, act)
    except PluginError as error:
        # THE ACT WAS GOOD AND IS STILL PUT BACK. `installed == pinned == clone`, published, is
        # the hub repository's own invariant; a machine running a plugin whose pin no repository
        # holds satisfies the first three and not the fourth. Rolling back leaves the machine in
        # the state that IS published, and the next pass tries the whole act again.
        _undo(claude=claude, sites=sites, tool=tool, marketplace=marketplace, act=act)
        return InstallOutcome(
            action=ACTION_ROLLED_BACK,
            installed=read_entry(sites, tool, marketplace),
            probes=results,
            detail=str(error),
        )

    entry = read_entry(sites, tool, marketplace)
    # THE CACHE ROOT COMES FROM THE INSTALLED RECORD'S OWN PATH, never from a path this module
    # composes out of the plugins root. It is the tool's own answer to where it put things, so
    # pruning cannot wander into a directory unrelated to what is installed -- and the two are
    # NOT reliably the same: this machine's `n8n-as-code` entry records an `installPath` that
    # does not exist, which is exactly the drift the cache check above exists to catch.
    pruned = (
        prune(Path(entry.install_path).parent, entry.version)
        if entry and entry.install_path
        else ["the installed record names no path, so nothing was pruned"]
    )
    return InstallOutcome(
        action=ACTION_INSTALLED,
        installed=entry,
        probes=results,
        detail="; ".join(pruned),
    )


def _advance(*, tool: Tool, claude: Path, sites: Sites, marketplace: str, act: _Act) -> None:
    """Everything between the capture and the verification. Raises `PluginError` on any refusal."""
    clone = sites.clone_root(tool)
    code, _, detail = _git_maybe(clone, "pull", "--ff-only", _PUBLISHED_REMOTE, tool.branch)
    if code != 0:
        # A NON-FAST-FORWARD IS A REFUSAL, NEVER A RECONCILIATION. The serving clone is a mirror
        # of a reviewed fork; a history that will not fast-forward means the fork was rewritten
        # or somebody committed here, and either is a question for a person rather than something
        # for a scheduled pass to resolve by merging.
        raise PluginError(f"the serving clone would not fast-forward to {tool.branch}: {detail}")

    act.version = _clone_version(sites, tool)
    manifest = sites.marketplace_path()
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as error:
        raise PluginError(f"the marketplace manifest could not be read: {error}") from error
    bumped = bump_marketplace_version(text, tool.name, act.version)
    if bumped != text:
        manifest.write_text(bumped, encoding="utf-8")
        act.bumped = True
    _refresh_install(claude, marketplace, tool.name)


def _publish(sites: Sites, tool: Tool, act: _Act) -> None:
    """Commit the pin and push it. Nothing is committed when the pin did not move."""
    if not act.bumped:
        return
    root = sites.hub_root
    _git(root, "add", str(MARKETPLACE_MANIFEST))
    _git(root, "commit", "-m", f"chore: bump {tool.name} to {act.version}")
    act.committed = True
    code, _, detail = _git_maybe(root, *PUBLISH_COMMAND.split()[1:])
    if code != 0:
        raise PluginError(f"the marketplace bump could not be published: {detail}")


def _undo(*, claude: Path, sites: Sites, tool: Tool, marketplace: str, act: _Act) -> None:
    """Put both repositories and the install cache back exactly as they were found.

    ONE PRIMITIVE, THREE SITES, and the completeness is what makes it worth having: a rollback
    that restored the clone and left the pin, or restored both and left the cache, would leave
    the machine running a version no repository claims -- the same class of half-rollback the
    cargo side documents as worse than none.

    The cache is refreshed only when the pin actually moved. A pass that refused at the pull
    changed nothing, and running the install commands to undo nothing could turn a clean refusal
    into a rollback failure.

    THE RESTORE IS VERIFIED. An `OSError` or a refused git command is raised as this module's own
    error rather than escaping as a bare traceback, and so is a restore that ran and did not
    land -- the one state most needing a record is a machine left between two versions.
    """
    try:
        if act.committed:
            _git(sites.hub_root, "reset", "--hard", act.hub_head)
        _git(sites.clone_root(tool), "reset", "--hard", act.clone_head)
        if act.bumped:
            if not act.committed:
                _git(sites.hub_root, "checkout", "--", str(MARKETPLACE_MANIFEST))
            _refresh_install(claude, marketplace, tool.name)
    except (PluginError, OSError) as error:
        raise RollbackFailed(f"the previous plugin could not be restored: {error}") from error

    if not act.bumped:
        return
    restored = read_entry(sites, tool, marketplace)
    previous = act.previous
    if previous is None:
        return
    if restored is None or (restored.version, restored.revision) != (
        previous.version,
        previous.revision,
    ):
        raise RollbackFailed(
            f"{tool.name} was restored to "
            f"{(restored.version if restored else 'nothing')!r} rather than "
            f"{previous.version!r}; the machine is between two versions"
        )
