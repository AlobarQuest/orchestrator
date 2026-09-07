"""The plugin act, against a scratch estate: real git repositories and a stand-in `claude`.

**WHAT IS REAL HERE AND WHAT IS NOT, stated once so no assertion below is read as more than it
is.** The two git repositories are real: a bare "fork", a serving clone of it, a bare hub origin
and a hub clone, so the fast-forward rule, the refusals and the rollback's `reset --hard` are
exercised by git itself rather than by a mock. The `claude` executable is a STAND-IN, and it has
to be: driving the real one would write to the operator's own `~/.claude/plugins`, which is the
one directory this lane must never touch from a test. So these prove the LANE'S sequencing --
what it reads, what it refuses, what it puts back -- and they prove nothing about Claude Code's
own behaviour. The stand-in is written to behave the way the real command was measured to behave
(it installs the version the marketplace PINS, from the serving clone), and it can be told to
misbehave in exactly the way the 2026-06-17 defect did.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_ROLLED_BACK,
    RollbackFailed,
)
from tool_installer.plugin import (
    MARKETPLACE_MANIFEST,
    PLUGIN_MANIFEST,
    PluginError,
    Sites,
    _Act,
    _install_still_moved,
    _version_key,
    bump_marketplace_version,
    default_sites,
    install_and_prove_plugin,
    prune,
    read_entry,
    resolve_claude,
    verify,
)
from tool_installer.tools import OCTO

MARKETPLACE = "devon-plugins"

# THE COLLIDING VERSION IS THE POINT OF THIS LITERAL. The real manifest carries the marketplace's
# own `metadata.version` AND a second plugin's, so the naive fix -- replacing the string
# `"version": "<old>"` -- moves whichever of them happens to share a value. Pinning all three at
# `1.0.0` makes that collision live in every test below rather than hypothetical, which is the
# only arrangement in which a global replace can be shown to be wrong.
MANIFEST = """{
  "name": "devon-plugins",
  "metadata": {
    "description": "a marketplace",
    "version": "1.0.0"
  },
  "plugins": [
    {
      "name": "octo",
      "source": "./octo",
      "version": "1.0.0",
      "keywords": ["multi-ai", "orchestration"]
    },
    {
      "name": "n8n-as-code",
      "source": "./n8n-as-code/plugins/claude/n8n-as-code",
      "version": "1.0.0",
      "keywords": ["n8n"]
    }
  ]
}
"""

# The stand-in for `claude`. It reproduces the one behaviour the lane depends on: `plugin update`
# copies the SERVING CLONE into the cache directory named by the version the MARKETPLACE pins, and
# rewrites Claude Code's own record. `SDS_FAKE_CLAUDE_STALE` makes it bump the catalog and leave
# the install alone -- which IS the 2026-06-17 defect, reproduced so the verification below can be
# shown to catch it rather than asserted to.
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path

hub = Path(os.environ["SDS_FAKE_HUB"])
plugins = Path(os.environ["SDS_FAKE_PLUGINS"])
argv = sys.argv[1:]
Path(os.environ["SDS_FAKE_LOG"]).open("a").write(" ".join(argv) + "\\n")

if os.environ.get("SDS_FAKE_CLAUDE_FAIL") == "1":
    sys.stderr.write("refused\\n")
    sys.exit(1)
if argv[:3] == ["plugin", "marketplace", "update"]:
    sys.exit(0)
if argv[:2] != ["plugin", "update"]:
    sys.exit(2)

name, marketplace = argv[2].split("@")
manifest = json.loads((hub / ".claude-plugin" / "marketplace.json").read_text())
pinned = next(e["version"] for e in manifest["plugins"] if e["name"] == name)
if os.environ.get("SDS_FAKE_CLAUDE_STALE") == "1":
    sys.exit(0)

clone = hub / name
head = subprocess.run(
    ["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
).stdout.strip()
cache = plugins / "cache" / marketplace / name / pinned
cache.mkdir(parents=True, exist_ok=True)
(cache / "plugin.json").write_text((clone / ".claude-plugin" / "plugin.json").read_text())

record = plugins / "installed_plugins.json"
document = json.loads(record.read_text()) if record.exists() else {"plugins": {}}
document["plugins"][f"{name}@{marketplace}"] = [
    {"scope": "user", "installPath": str(cache), "version": pinned, "gitCommitSha": head}
]
record.write_text(json.dumps(document, indent=2))
"""


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _identify(root: Path) -> None:
    _git(root, "config", "user.email", "sds@example.invalid")
    _git(root, "config", "user.name", "sds")
    _git(root, "config", "commit.gpgsign", "false")


def _plugin_manifest(version: str) -> str:
    return json.dumps({"name": "octo", "version": version}, indent=2) + "\n"


@dataclass
class Estate:
    """A whole scratch machine: two git repositories, a plugins root, and a stand-in `claude`."""

    sites: Sites
    clone: Path
    hub: Path
    claude: Path
    old_head: str
    new_head: str
    log: Path

    def manifest(self) -> dict[str, object]:
        return json.loads((self.hub / MARKETPLACE_MANIFEST).read_text())

    def pinned(self, plugin: str) -> str:
        entries = self.manifest()["plugins"]
        assert isinstance(entries, list)
        return next(e["version"] for e in entries if e["name"] == plugin)

    def entry(self) -> object:
        return read_entry(self.sites, OCTO, MARKETPLACE)

    def cache_versions(self) -> list[str]:
        root = self.sites.plugins_root / "cache" / MARKETPLACE / "octo"
        return sorted(path.name for path in root.iterdir()) if root.is_dir() else []

    def commands(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def estate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Estate:
    """A serving clone one commit behind its fork, a published hub, and an installed old plugin."""
    fork = tmp_path / "fork.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(fork)], check=True, capture_output=True
    )

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(fork), str(seed)], check=True, capture_output=True)
    _identify(seed)
    (seed / ".claude-plugin").mkdir()
    (seed / PLUGIN_MANIFEST).write_text(_plugin_manifest("1.0.0"))
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "v1")
    _git(seed, "push", "origin", "main")
    old_head = _git(seed, "rev-parse", "HEAD").strip()

    hub = tmp_path / "devon-plugins"
    hub_origin = tmp_path / "hub.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(hub_origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "clone", str(hub_origin), str(hub)], check=True, capture_output=True)
    _identify(hub)
    (hub / ".claude-plugin").mkdir()
    (hub / MARKETPLACE_MANIFEST).write_text(MANIFEST)
    # THE SERVING CLONE IS GITIGNORED IN THE HUB, exactly as it is on the machine -- it is a clone
    # of a repository already on GitHub and is never committed here. Without this line the hub is
    # permanently dirty and every pass refuses before it starts, which is a fixture that measures
    # the refusal rather than the act.
    (hub / ".gitignore").write_text("octo/\n")
    _git(hub, "add", "-A")
    _git(hub, "commit", "-m", "seed")
    _git(hub, "push", "origin", "main")

    # The SERVING CLONE is taken while the fork is still at v1, then the fork advances -- so the
    # clone is genuinely one fast-forward behind, which is the state a merged sync leaves.
    subprocess.run(["git", "clone", str(fork), str(hub / "octo")], check=True, capture_output=True)
    _identify(hub / "octo")
    (seed / PLUGIN_MANIFEST).write_text(_plugin_manifest("2.0.0"))
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "v2")
    _git(seed, "push", "origin", "main")
    new_head = _git(seed, "rev-parse", "HEAD").strip()

    plugins = tmp_path / "claude-plugins"
    cache = plugins / "cache" / MARKETPLACE / "octo" / "1.0.0"
    cache.mkdir(parents=True)
    (cache / "plugin.json").write_text(_plugin_manifest("1.0.0"))
    (plugins / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "octo@devon-plugins": [
                        {
                            "scope": "user",
                            "installPath": str(cache),
                            "version": "1.0.0",
                            "gitCommitSha": old_head,
                        }
                    ]
                }
            },
            indent=2,
        )
    )

    claude = tmp_path / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("SDS_FAKE_HUB", str(hub))
    monkeypatch.setenv("SDS_FAKE_PLUGINS", str(plugins))
    monkeypatch.setenv("SDS_FAKE_LOG", str(log))

    return Estate(
        sites=Sites(hub_root=hub, plugins_root=plugins),
        clone=hub / "octo",
        hub=hub,
        claude=claude,
        old_head=old_head,
        new_head=new_head,
        log=log,
    )


def _act(estate: Estate, head: str | None = None):
    return install_and_prove_plugin(
        tool=OCTO,
        claude=estate.claude,
        sites=estate.sites,
        head_revision=head or estate.new_head,
    )


# ------------------------------------------------------------------------------------------------
# ACCEPTANCE 4. The marketplace bump moves ONE entry, PROVEN by a control rather than asserted.
# ------------------------------------------------------------------------------------------------


def test_the_bump_moves_only_the_named_plugin() -> None:
    """The positive half: octo moves and every other value in the document is byte-identical."""
    after = json.loads(bump_marketplace_version(MANIFEST, "octo", "2.0.0"))
    before = json.loads(MANIFEST)
    assert after["plugins"][0]["version"] == "2.0.0"
    assert after["plugins"][1] == before["plugins"][1]
    assert after["metadata"] == before["metadata"]


def test_the_NAIVE_replacement_would_move_two_entries() -> None:
    """THE CONTROL, and without it the test above proves nothing.

    A global replace of `"version": "<old>"` is the obvious implementation. Here it moves the
    marketplace's own metadata version and the SECOND PLUGIN'S as well, because all three share
    `1.0.0` -- which is exactly the collision the real manifest can produce and exactly what the
    spec says must not be asserted away.
    """
    naive = MANIFEST.replace('"version": "1.0.0"', '"version": "2.0.0"')
    moved = json.loads(naive)
    assert moved["plugins"][1]["version"] == "2.0.0"
    assert moved["metadata"]["version"] == "2.0.0"


def test_the_bump_REFUSES_when_it_cannot_move_exactly_one_entry() -> None:
    """The guard is the selection rule, so a manifest naming the plugin twice is a refusal.

    Not a preference for refusing: with two entries called `octo` there is no single document that
    is "the original with one entry changed", so every candidate is rejected and the pass says so
    rather than picking one.
    """
    doubled = json.loads(MANIFEST)
    doubled["plugins"].append(dict(doubled["plugins"][0]))
    with pytest.raises(PluginError, match="2 times"):
        bump_marketplace_version(json.dumps(doubled), "octo", "2.0.0")


def test_the_bump_is_a_no_op_when_the_pin_is_already_there() -> None:
    """Replay: a pass that stopped after bumping must not produce a second diff on its next run."""
    assert bump_marketplace_version(MANIFEST, "octo", "1.0.0") == MANIFEST


def test_the_bump_preserves_the_hand_authored_formatting() -> None:
    """One line differs, so the machine's commit is readable and the file stays a person's."""
    after = bump_marketplace_version(MANIFEST, "octo", "2.0.0")
    changed = [
        (a, b) for a, b in zip(MANIFEST.splitlines(), after.splitlines(), strict=True) if a != b
    ]
    assert changed == [('      "version": "1.0.0",', '      "version": "2.0.0",')]


def test_the_live_bump_leaves_the_sibling_plugin_where_it_was(estate: Estate) -> None:
    """The same control at the END of a real pass, not only over the editing function.

    The unit test above proves the editor; this proves that what the pass actually WROTE to disk,
    committed and pushed carries the sibling unchanged -- which is the claim the spec asks for.
    """
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALLED
    assert estate.pinned("octo") == "2.0.0"
    assert estate.pinned("n8n-as-code") == "1.0.0"
    assert estate.manifest()["metadata"] == json.loads(MANIFEST)["metadata"]


# ------------------------------------------------------------------------------------------------
# ACCEPTANCE 5. A non-fast-forward pull REFUSES rather than merging.
# ------------------------------------------------------------------------------------------------


def test_the_serving_clone_fast_forwards_when_it_can(estate: Estate) -> None:
    """The positive control: without it, a refusal below could be a pull that never works."""
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALLED
    assert _git(estate.clone, "rev-parse", "HEAD").strip() == estate.new_head


def test_a_non_fast_forward_is_REFUSED_and_the_clone_is_not_merged(estate: Estate) -> None:
    """A history that will not fast-forward means the fork was rewritten or somebody committed
    here, and either is a question for a person rather than something a scheduled pass resolves by
    merging. The clone must be on its own commit afterwards, with a clean tree and no merge."""
    (estate.clone / "LOCAL.md").write_text("a local commit the fork does not have\n")
    _git(estate.clone, "add", "-A")
    _git(estate.clone, "commit", "-m", "diverge")
    diverged = _git(estate.clone, "rev-parse", "HEAD").strip()

    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "fast-forward" in outcome.detail
    assert _git(estate.clone, "rev-parse", "HEAD").strip() == diverged
    assert _git(estate.clone, "status", "--porcelain").strip() == ""
    assert _git(estate.clone, "rev-list", "--count", "HEAD").strip() == "2"
    # Nothing downstream happened either: the pin never moved and `claude` was never asked to run.
    assert estate.pinned("octo") == "1.0.0"
    assert estate.commands() == []


# ------------------------------------------------------------------------------------------------
# ACCEPTANCE 6. Rollback, end to end, with the restore verified.
# ------------------------------------------------------------------------------------------------


def test_a_failed_verification_restores_all_three_sites(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE 2026-06-17 DEFECT, REPRODUCED AND THEN CAUGHT.

    The stand-in bumps the catalog and leaves the install alone, which is precisely what a `git
    pull` plus a marketplace bump does on its own. Everything else about the pass succeeds -- the
    pull is clean, the pin moves, the commands exit zero -- so this is the case where every surface
    except the installed record says the pass worked.

    All three sites must come back: the serving clone to the commit it was on, the marketplace pin
    to the version it carried, and the install cache to the version it had. Nothing may be
    published, because the publish happens only after the verification this one fails.
    """
    monkeypatch.setenv("SDS_FAKE_CLAUDE_STALE", "1")
    hub_head = _git(estate.hub, "rev-parse", "HEAD").strip()

    outcome = _act(estate)

    assert outcome.action == ACTION_ROLLED_BACK
    assert [result.passed for result in outcome.probes] == [False, False, False, True]
    # The serving clone is back on the commit it was found at.
    assert _git(estate.clone, "rev-parse", "HEAD").strip() == estate.old_head
    # The marketplace pin is back, and the working tree is clean rather than merely reverted.
    assert estate.pinned("octo") == "1.0.0"
    assert _git(estate.hub, "status", "--porcelain").strip() == ""
    assert _git(estate.hub, "rev-parse", "HEAD").strip() == hub_head
    # Nothing was published.
    assert _git(estate.hub, "rev-list", "--count", "origin/main..HEAD").strip() == "0"
    # The installed plugin is the one that was there.
    entry = estate.entry()
    assert entry is not None
    assert (entry.version, entry.revision) == ("1.0.0", estate.old_head)  # type: ignore[attr-defined]


def _break_claude_after_the_forward_pass(
    estate: Estate, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """Let the forward pass run normally and make only the ROLLBACK's `claude` calls misbehave.

    The forward pass issues exactly two, so anything after the second belongs to `_undo`. Without
    this split the install never happens either, and a test meaning to say something about the
    restore would be satisfied by a pass that had nothing to restore.
    """
    calls = {"n": 0}
    real = subprocess.run

    def wrapped(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        if argv and str(argv[0]) == str(estate.claude):
            calls["n"] += 1
            if calls["n"] > 2:
                os.environ[variable] = "1"
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", wrapped)


def _refuse_pushes(estate: Estate) -> None:
    """Make the hub's origin FETCH cleanly and REFUSE the push.

    An unreachable remote no longer reaches the publish -- since 2026-09-07 the pass fetches and
    refuses up front, which is the whole point of that change. A `pre-receive` hook is both the
    only way left to exercise the publish-failure path and the more realistic shape of it: the
    remote is there, the comparison succeeds, and the server declines the write.
    """
    origin = Path(_git(estate.hub, "remote", "get-url", "origin").strip())
    hooks = origin / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'refused by the test' >&2\nexit 1\n")
    hook.chmod(0o755)


def test_a_restore_that_CANNOT_RUN_is_named_rather_than_a_bare_traceback(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollback that cannot complete is the worst state this lane can produce, so it is raised as
    this module's own error rather than escaping as whatever the failing step happened to throw --
    a partially restored machine is the one state most needing a record."""
    _refuse_pushes(estate)
    _break_claude_after_the_forward_pass(estate, monkeypatch, "SDS_FAKE_CLAUDE_FAIL")
    try:
        with pytest.raises(RollbackFailed, match="could not be restored"):
            _act(estate)
    finally:
        os.environ.pop("SDS_FAKE_CLAUDE_FAIL", None)


def test_a_restore_that_RUNS_AND_DOES_NOT_LAND_is_still_a_rollback_failure(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE CASE A CHECK ON THE COMMANDS' EXIT STATUS CANNOT SEE, and the reason the restore is
    verified rather than assumed.

    Here every rollback command exits ZERO and the machine is still left on the version the pass
    was putting back -- the same shape as the defect the forward verification exists to catch,
    arriving on the way out instead. A mutation review found that the test above kills only the
    error wrapper: it makes the commands FAIL, so the raise comes from `_refresh_install` and the
    verification clause could be deleted with nothing noticing.
    """
    _refuse_pushes(estate)
    _break_claude_after_the_forward_pass(estate, monkeypatch, "SDS_FAKE_CLAUDE_STALE")
    try:
        with pytest.raises(RollbackFailed, match="between two versions"):
            _act(estate)
    finally:
        os.environ.pop("SDS_FAKE_CLAUDE_STALE", None)


def _pin_by_hand(estate: Estate, version: str) -> None:
    """Move the marketplace pin the way a PERSON does, and publish it, so the pass finds a hub it
    will admit and a pin it has no reason to touch.

    This is the 2026-06-17 shape exactly: the pin already names what the clone will carry and the
    INSTALL is the only thing behind. A pass over it bumps nothing -- and still runs the install
    commands, which is the whole reason the undo cannot key on the pin having moved.
    """
    manifest = estate.hub / MARKETPLACE_MANIFEST
    manifest.write_text(bump_marketplace_version(manifest.read_text(), "octo", version))
    _git(estate.hub, "add", "-A")
    _git(estate.hub, "commit", "-m", "pin by hand")
    _git(estate.hub, "push", "origin", "main")


def test_a_pass_over_a_HAND_MOVED_PIN_still_repairs_the_stale_install(estate: Estate) -> None:
    """THE POSITIVE CONTROL for the pair below, and the 2026-06-17 repair working.

    The pin is already right and the install is behind, so the pass bumps nothing, publishes
    nothing, and is worth running anyway -- which is the entire claim of this module's opening
    paragraph. Without this row the refusal below would be satisfied by a pass that never got
    far enough to install anything.
    """
    _pin_by_hand(estate, "2.0.0")
    hub_head = _git(estate.hub, "rev-parse", "HEAD").strip()

    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALLED
    assert all(result.passed for result in outcome.probes)
    entry = estate.entry()
    assert entry is not None
    assert (entry.version, entry.revision) == ("2.0.0", estate.new_head)  # type: ignore[attr-defined]
    # Nothing was committed: the pin did not move, so there was nothing to publish.
    assert _git(estate.hub, "rev-parse", "HEAD").strip() == hub_head


def test_a_pass_that_moved_the_install_with_the_pin_ALREADY_RIGHT_says_it_cannot_put_it_back(
    estate: Estate,
) -> None:
    """THE STATE THE UNDO CANNOT REPAIR, AND MUST THEREFORE REPORT.

    With the pin unmoved there is nothing for the rollback to reset the install to: the install
    commands install whatever the manifest names, and the manifest names the NEW version, which
    is what this pass just put on the machine. So the undo does not run them -- re-asserting the
    pin is not a restore -- and the only honest thing left is to say the machine is not where it
    was found.

    Keyed on `refreshed` rather than on `bumped` for exactly this row: the pin never moved, so a
    check gated on the pin says the previous plugin was left in place, about a cache it never
    looked at. Here the previous plugin was NOT left in place.
    """
    _pin_by_hand(estate, "2.0.0")

    with pytest.raises(RollbackFailed, match="between two versions"):
        _act(estate, head=estate.old_head)

    entry = estate.entry()
    assert entry is not None
    assert entry.version == "2.0.0"  # type: ignore[attr-defined]


def test_a_commit_that_fails_AFTER_git_add_does_not_reinstate_the_bump_from_the_index(
    estate: Estate,
) -> None:
    """`git checkout -- <path>` RESTORES FROM THE INDEX, which is why the hub goes back with
    `reset --hard` instead.

    `git add` succeeds and `git commit` does not -- an identity, a hook, a full disk. The bump is
    then sitting in the index, and a checkout of the manifest would reinstate the very change the
    rollback exists to undo; the refresh that follows would install the NEW version and a
    recoverable failure would be reported as a rollback failure. The hub was verified clean before
    anything moved, so resetting to the captured commit is exact and loses nothing.
    """
    hook = estate.hub / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    hub_head = _git(estate.hub, "rev-parse", "HEAD").strip()

    outcome = _act(estate)

    assert outcome.action == ACTION_ROLLED_BACK
    assert estate.pinned("octo") == "1.0.0"
    assert _git(estate.hub, "rev-parse", "HEAD").strip() == hub_head
    # The INDEX is clean too, which `checkout --` would not have achieved.
    assert _git(estate.hub, "status", "--porcelain").strip() == ""
    assert _git(estate.hub, "diff", "--cached", "--name-only").strip() == ""
    entry = estate.entry()
    assert entry is not None
    assert (entry.version, entry.revision) == ("1.0.0", estate.old_head)  # type: ignore[attr-defined]


def test_a_publish_that_fails_rolls_the_whole_act_back(estate: Estate) -> None:
    """`installed == pinned == clone`, PUBLISHED, is the hub's own invariant, so a machine running
    a plugin whose pin no repository holds satisfies three quarters of it. The act is put back and
    the next pass tries again, rather than inventing an outcome for a state the invariant forbids.
    """
    _refuse_pushes(estate)
    hub_head = _git(estate.hub, "rev-parse", "HEAD").strip()

    outcome = _act(estate)

    assert outcome.action == ACTION_ROLLED_BACK
    assert "could not be published" in outcome.detail
    # Every probe PASSED -- this is a rollback of a verified-good install, which is the whole point.
    assert all(result.passed for result in outcome.probes)
    assert _git(estate.hub, "rev-parse", "HEAD").strip() == hub_head
    assert estate.pinned("octo") == "1.0.0"
    assert _git(estate.clone, "rev-parse", "HEAD").strip() == estate.old_head
    entry = estate.entry()
    assert entry is not None
    assert entry.version == "1.0.0"  # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------------
# The successful pass, and what it publishes.
# ------------------------------------------------------------------------------------------------


def test_a_successful_pass_installs_publishes_and_refreshes_the_cache(estate: Estate) -> None:
    """The whole act, and the ORDER is asserted: the catalog is refreshed before the install, and
    the install happens before anything is published."""
    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALLED
    assert all(result.passed for result in outcome.probes)
    assert estate.commands() == [
        "plugin marketplace update devon-plugins",
        "plugin update octo@devon-plugins -y",
    ]
    entry = estate.entry()
    assert entry is not None
    assert (entry.version, entry.revision) == ("2.0.0", estate.new_head)  # type: ignore[attr-defined]
    assert "2.0.0" in estate.cache_versions()
    # Published, and the commit says what it did.
    assert _git(estate.hub, "rev-list", "--count", "origin/main..HEAD").strip() == "0"
    assert "bump octo to 2.0.0" in _git(estate.hub, "log", "-1", "--format=%s")


def test_the_install_command_carries_the_flag_a_scheduled_pass_needs(estate: Estate) -> None:
    """`-y` is not decoration. Claude Code 2.1.263's own help says it is required when stdin or
    stdout is not a TTY, and a scheduled pass has neither -- so the routine a person follows,
    which omits it, cannot be transcribed verbatim into a lane."""
    _act(estate)
    assert estate.commands()[-1].endswith(" -y")


# ------------------------------------------------------------------------------------------------
# The refusals that protect the rollback, each with the state it protects.
# ------------------------------------------------------------------------------------------------


def test_a_dirty_serving_clone_is_refused_before_anything_moves(estate: Estate) -> None:
    """The rollback is `git reset --hard`, which would destroy uncommitted work -- so a tree
    carrying any is refused rather than reset."""
    (estate.clone / "WIP.md").write_text("someone's work in progress\n")
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "uncommitted changes" in outcome.detail
    assert (estate.clone / "WIP.md").exists()
    assert estate.commands() == []


def test_a_serving_clone_on_another_branch_is_refused(estate: Estate) -> None:
    """A clone on a stray branch still pulls, still yields a manifest and still bumps the pin --
    from a history that is not the one whose head this pass measured `behind` against."""
    _git(estate.clone, "checkout", "-b", "elsewhere")
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "rather than main" in outcome.detail


def test_a_hub_with_a_modified_TRACKED_file_is_refused(estate: Estate) -> None:
    """This lane commits what it writes and will not commit somebody else's.

    A MODIFIED TRACKED file, because that is what the refusal's two stated reasons actually
    reach: `git add <manifest>` then `git commit` could sweep a staged change, and the
    rollback's `reset --hard` would destroy an uncommitted one. See the control below for the
    case they do not reach.
    """
    tracked = estate.hub / "README.md"
    tracked.write_text("edited by a person\n")
    _git(estate.hub, "add", "-A")
    _git(estate.hub, "commit", "-m", "seed a tracked file")
    _git(estate.hub, "push", "origin", "main")
    tracked.write_text("edited again, uncommitted\n")

    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "uncommitted changes to tracked files" in outcome.detail


def test_a_hub_with_only_an_UNTRACKED_file_still_updates(estate: Estate) -> None:
    """The control, and the reason the check narrowed to `-uno` on 2026-09-07.

    Neither stated reason for the refusal reaches an untracked file: `git add <manifest>` names
    one path and cannot sweep it, and `reset --hard` does not delete it. Without the narrowing, a
    single stray scratch file in a repository Devon works in blocked every octo update, nightly,
    reported as a critical install failure -- a refusal with no reason behind it.
    """
    (estate.hub / "NOTES.md").write_text("a person's scratch note\n")

    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALLED
    assert (estate.hub / "NOTES.md").exists(), "the pass must not have swept it away either"


def test_a_hub_on_another_branch_is_refused(estate: Estate) -> None:
    """The publish pushes `main` BY NAME, so from any other branch the commit this pass writes
    lands off `main`, the push reports everything up to date and exits 0, and the pass would go on
    to report as published a commit only this machine holds."""
    _git(estate.hub, "checkout", "-b", "topic")
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "rather than main" in outcome.detail


def test_a_hub_carrying_an_unpublished_commit_is_refused(estate: Estate) -> None:
    """THE RESIDUE OF A PASS WHOSE PUBLISH WAS REFUSED IS REFUSED AGAIN, AT THE TOP.

    Without this the finding is reported once and then goes quiet: the next pass would find the
    pin already correct, take the replay path, never reach the publish, and report a clean run
    while the commit sat local forever. It also stops this lane pushing somebody else's unpushed
    work, which publishing the branch would do.
    """
    (estate.hub / "OTHER.md").write_text("an unpushed commit\n")
    _git(estate.hub, "add", "-A")
    _git(estate.hub, "commit", "-m", "unpublished")
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "origin does not" in outcome.detail
    assert estate.commands() == []


def test_an_unreachable_origin_is_refused_BEFORE_the_machine_is_touched(estate: Estate) -> None:
    """REVERSED 2026-09-07, deliberately, and the reversal is the point of this test.

    It previously asserted the opposite -- that the check reads no network, reasoning that going
    to it would make the refusal depend on somebody else's landings rather than on this lane's own
    unfinished act. That is right about the COMMIT and backwards about the PUSH: the push depends
    on origin whether or not this function looks, so not looking did not remove the dependency, it
    removed the ability to report it.

    Measured cost of the old behaviour: one commit landing on the hub from anywhere else made
    every subsequent pass pull, bump, commit, install, verify CLEAN, have the push rejected as a
    non-fast-forward, and then uninstall the new plugin and reinstall the old one -- nightly,
    filed `critical`, with no self-heal, because a rejected push does not advance `origin/main` so
    the next pass was identical.

    Refusing at the top makes it an INPUT problem reported before anything moves, instead of an
    install failure discovered after the machine was changed and changed back.
    """
    _git(estate.hub, "remote", "set-url", "origin", str(estate.hub.parent / "gone.git"))
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "could not be compared with origin" in outcome.detail
    # The machine is untouched: no install command ran at all.
    assert estate.commands() == []


# ------------------------------------------------------------------------------------------------
# Verification: each check, and what it is blind to.
# ------------------------------------------------------------------------------------------------


def test_every_verification_check_holds_on_a_healthy_machine(estate: Estate) -> None:
    _act(estate)
    results = verify(estate.sites, OCTO, MARKETPLACE, estate.new_head)
    assert [result.passed for result in results] == [True, True, True, True]


@pytest.mark.parametrize(
    ("index", "break_it"),
    [
        (0, "pin"),
        (1, "clone"),
        (2, "head"),
        (3, "cache"),
    ],
)
def test_each_verification_check_can_fail_on_its_own(
    estate: Estate, index: int, break_it: str
) -> None:
    """Each check is shown to discriminate SEPARATELY, so none of the four is decoration riding on
    another's answer. A check that could only fail alongside its neighbours would pass every test
    a healthy machine produces and would report nothing new when the machine was not."""
    _act(estate)
    if break_it == "pin":
        path = estate.hub / MARKETPLACE_MANIFEST
        path.write_text(bump_marketplace_version(path.read_text(), "octo", "9.9.9"))
    elif break_it == "clone":
        (estate.clone / PLUGIN_MANIFEST).write_text(_plugin_manifest("9.9.9"))
    elif break_it == "cache":
        entry = estate.entry()
        assert entry is not None
        for child in Path(entry.install_path).iterdir():  # type: ignore[attr-defined]
            child.unlink()

    head = "c" * 40 if break_it == "head" else estate.new_head
    results = verify(estate.sites, OCTO, MARKETPLACE, head)
    assert [result.passed for result in results].index(False) == index
    assert sum(1 for result in results if not result.passed) == 1


def test_an_unreadable_manifest_fails_the_check_rather_than_raising(estate: Estate) -> None:
    """Verification reports; it must not be the thing that ends the pass with a traceback."""
    _act(estate)
    (estate.hub / MARKETPLACE_MANIFEST).write_text("{not json")
    results = verify(estate.sites, OCTO, MARKETPLACE, estate.new_head)
    assert results[0].passed is False
    assert "unreadable" in results[0].detail


# ------------------------------------------------------------------------------------------------
# Pruning.
# ------------------------------------------------------------------------------------------------


def test_pruning_keeps_the_installed_version_and_the_single_newest_other(tmp_path: Path) -> None:
    """The newest other is what a still-running session is loaded from AND the one-step rollback
    buffer, so it survives; everything below it goes. Version-ordered rather than string-ordered,
    because `9.45.0` is older than `11.0.1` and a string sort says the opposite."""
    cache = tmp_path / "plugins" / "devon-plugins" / "octo"
    for version in ("9.45.0", "10.1.0", "11.0.1", "8.0.0"):
        (cache / version).mkdir(parents=True)
        (cache / version / "plugin.json").write_text("{}")

    removed = prune(cache / "11.0.1", "11.0.1", plugins_root=tmp_path / "plugins")

    assert sorted(path.name for path in cache.iterdir()) == ["10.1.0", "11.0.1"]
    assert sorted(removed) == ["removed stale cache 8.0.0", "removed stale cache 9.45.0"]


def test_pruning_two_directories_removes_nothing(tmp_path: Path) -> None:
    """The state the machine is actually in, and the control for the test above: with exactly the
    installed version and one other there is nothing stale, and a prune that removed anything here
    would be taking the rollback buffer."""
    cache = tmp_path / "plugins" / "devon-plugins" / "octo"
    for version in ("9.45.0", "11.0.1"):
        (cache / version).mkdir(parents=True)

    assert prune(cache / "11.0.1", "11.0.1", plugins_root=tmp_path / "plugins") == []
    assert sorted(path.name for path in cache.iterdir()) == ["11.0.1", "9.45.0"]


def test_a_prune_that_cannot_read_the_cache_reports_rather_than_raises(tmp_path: Path) -> None:
    """A directory that could not be pruned is untidy, not broken -- and failing the pass over it
    would roll back an install that has already been verified."""
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    assert prune(plugins / "octo" / "11.0.1", "11.0.1", plugins_root=plugins) == [
        "the install cache could not be listed: FileNotFoundError"
    ]


def test_a_successful_pass_prunes_and_says_what_it_removed(estate: Estate) -> None:
    stale = estate.sites.plugins_root / "cache" / MARKETPLACE / "octo" / "0.9.0"
    stale.mkdir(parents=True)
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALLED
    assert outcome.detail == "removed stale cache 0.9.0"
    assert estate.cache_versions() == ["1.0.0", "2.0.0"]


# ------------------------------------------------------------------------------------------------
# Resolution and defaults.
# ------------------------------------------------------------------------------------------------


def test_claude_is_found_at_a_known_location_when_it_is_not_on_PATH(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is NOT on a scheduled job's PATH -- measured under `env -i PATH=/usr/bin:/bin` -- so a
    lane that looked there alone would report unmeasurable every morning while working from a
    shell."""
    monkeypatch.setenv("PATH", "/nonexistent")
    candidate = tmp_path / "claude"
    candidate.write_text("#!/bin/sh\n")
    assert resolve_claude((tmp_path / "absent", candidate)) == candidate


def test_an_absent_claude_is_the_TOOLCHAIN_error_the_pass_already_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately the cargo side's error type: "this lane cannot reach the thing that would act"
    is one condition with one exit code, and a second exception would be a second branch to keep
    agreeing with the first."""
    from tool_installer.toolchain import ToolchainError

    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ToolchainError, match="claude is not on PATH"):
        resolve_claude((tmp_path / "absent",))


def test_the_default_sites_are_the_ones_this_machine_uses() -> None:
    """The hub is a real git clone of the marketplace repository and the plugins root is Claude
    Code's own; a wrong default here is a lane that measures a directory nobody uses."""
    sites = default_sites()
    assert sites.hub_root == Path.home() / "Developer" / "devon-plugins"
    assert sites.plugins_root == Path.home() / ".claude" / "plugins"


def test_an_absent_installed_record_reads_as_a_first_install(tmp_path: Path) -> None:
    """`None` is a first install, which is allowed -- and deliberately not confused with a record
    that exists and names a different revision."""
    assert read_entry(Sites(tmp_path, tmp_path), OCTO, MARKETPLACE) is None


def test_a_record_that_cannot_say_what_it_installed_reads_as_absent(tmp_path: Path) -> None:
    """A row missing its version or its revision is not evidence that anything is installed."""
    (tmp_path / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"octo@devon-plugins": [{"version": "1.0.0"}]}})
    )
    assert read_entry(Sites(tmp_path, tmp_path), OCTO, MARKETPLACE) is None


# ---------------------------------------------------------------------------------------------
# 2026-09-07 review fixes. Each of these is a defect two independent adversarial reviews
# demonstrated against the branch, so each test names the state it reproduces.
# ---------------------------------------------------------------------------------------------


def test_the_version_key_orders_names_that_do_not_start_with_a_digit() -> None:
    """The sort must be TOTAL. The first version returned `int` for a numeric run and `str` for
    the rest, so one directory whose name did not start with a digit raised `TypeError` out of
    `sorted` -- escaping `prune`, which catches only `OSError`, and killing the pass AFTER the
    commit had been pushed. Claude Code demonstrably produces such names: this machine's
    playwright cache holds `008fef3972d6` beside `ed404106fcd8`."""
    names = ["11.0.1", "9.45.0", "latest", "ed404106fcd8", "008fef3972d6", "tmp-partial"]

    ordered = sorted(names, key=_version_key)

    assert ordered  # did not raise, which is the whole assertion
    assert sorted(["8.0.0", "9.45.0", "10.1.0", "11.0.1"], key=_version_key) == [
        "8.0.0",
        "9.45.0",
        "10.1.0",
        "11.0.1",
    ]


def test_prune_refuses_a_cache_root_outside_the_plugins_root(tmp_path) -> None:
    """Reading the path from a record is not containment. Pointed one level up, the first version
    removed every sibling PLUGIN in the marketplace, `octo` included."""
    plugins = tmp_path / "plugins"
    elsewhere = tmp_path / "elsewhere" / "11.0.1"
    elsewhere.mkdir(parents=True)
    plugins.mkdir()

    reported = prune(elsewhere, "11.0.1", plugins_root=plugins)

    assert reported and "not under" in reported[0]
    assert elsewhere.exists()


def test_prune_refuses_a_cache_root_not_named_for_the_installed_version(tmp_path) -> None:
    """The record and the disk already disagree on this machine -- `n8n-as-code`'s recorded
    `installPath` does not exist -- so the record's word is a source, not a guarantee."""
    plugins = tmp_path / "plugins"
    cache = plugins / "devon-plugins" / "octo"
    (cache / "11.0.1").mkdir(parents=True)
    (cache / "9.45.0").mkdir()

    reported = prune(cache, "11.0.1", plugins_root=plugins)

    assert reported and "not named for the installed version" in reported[0]
    assert (cache / "9.45.0").exists()


def test_prune_removes_stale_versions_when_the_cache_root_is_the_installed_one(tmp_path) -> None:
    """The positive control: with a well-formed root the prune still does its job."""
    plugins = tmp_path / "plugins"
    cache = plugins / "devon-plugins" / "octo"
    for name in ("8.0.0", "9.45.0", "10.1.0", "11.0.1"):
        (cache / name).mkdir(parents=True)

    reported = prune(cache / "11.0.1", "11.0.1", plugins_root=plugins)

    assert (cache / "11.0.1").exists() and (cache / "10.1.0").exists()
    assert not (cache / "8.0.0").exists() and not (cache / "9.45.0").exists()
    assert any("removed stale cache 8.0.0" in line for line in reported)


def test_a_malformed_manifest_is_this_modules_error_and_not_a_bare_ValueError() -> None:
    """A bare `json.loads` raises `ValueError`, which the caller does not catch -- so a corrupt
    manifest escaped the rollback entirely, leaving the clone advanced with nothing put back."""
    with pytest.raises(PluginError, match="not readable JSON"):
        bump_marketplace_version("{not json", "octo", "2.0.0")


def test_a_rollback_that_SUCCEEDS_is_not_reported_as_a_rollback_failure(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE DEFECT TWO REVIEWS DEMONSTRATED, and the reason `_undo` asks the disk.

    `_undo` used to re-run `_refresh_install` whenever the pin had moved -- re-running the exact
    command whose failure brought it there. The two `git reset --hard`s above it had already put
    everything back, so a COMPLETE, SUCCESSFUL rollback raised `RollbackFailed`, which nothing
    caught: traceback, exit 1, no observation for this row AND none for the cargo row, because
    the rows are filed after the loop.

    Here the forward install fails outright, so the cache never moved and there is nothing to put
    back. The pass must report `install_failed` and leave the machine exactly as it found it.
    """
    before = read_entry(estate.sites, OCTO, MARKETPLACE)
    monkeypatch.setenv("SDS_FAKE_CLAUDE_FAIL", "1")

    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALL_FAILED
    after = read_entry(estate.sites, OCTO, MARKETPLACE)
    assert before is not None and after is not None
    assert (after.version, after.revision) == (before.version, before.revision)


def test_a_hub_BEHIND_origin_is_refused_before_the_machine_is_touched(estate: Estate) -> None:
    """THE STATE THAT MADE EVERY NIGHT A FALSE CRITICAL, and it is not the ahead case.

    Reproduced: a commit lands on the hub's origin from anywhere else. The old check asked only
    whether the hub was AHEAD, so it passed; the pass then pulled, bumped, committed, installed,
    verified CLEAN, had the push rejected as a non-fast-forward, and uninstalled the new plugin to
    reinstall the old one. Filed `critical`, nightly, with no self-heal -- a rejected push does not
    advance `origin/main`, so the next pass was identical.

    Behind is now as disqualifying as ahead, and it is refused BEFORE anything moves: an input
    problem reported up front rather than an install failure found after the machine was changed
    and changed back. `estate.commands()` empty is the half that says "nothing moved".
    """
    origin = Path(_git(estate.hub, "remote", "get-url", "origin").strip())
    elsewhere = estate.hub.parent / "someone-else"
    subprocess.run(["git", "clone", str(origin), str(elsewhere)], check=True, capture_output=True)
    # EVERY FIXTURE REPO NEEDS THIS. Locally git derives an identity from user+hostname and
    # commits with a warning, so omitting it passes on this machine and fails on CI with exit
    # 128 -- which is exactly what happened. A review flagged it as cosmetic; it was not.
    _identify(elsewhere)
    (elsewhere / "FROM_ELSEWHERE.md").write_text("a commit this machine has never seen\n")
    _git(elsewhere, "add", "-A")
    _git(elsewhere, "commit", "-m", "landed from another machine")
    _git(elsewhere, "push", "origin", "main")

    outcome = _act(estate)

    assert outcome.action == ACTION_INSTALL_FAILED
    assert "behind" in outcome.detail
    assert estate.commands() == [], "the machine was touched before the refusal"


def test_a_rollback_with_nothing_recorded_either_side_does_not_re_run_the_install() -> None:
    """FIX 3'S OWN `None` CASE, which bypassed fix 3 entirely.

    `_install_still_moved` returned `True` whenever `previous` was `None`, so `_undo`'s guard was
    satisfied unconditionally and `_refresh_install` re-ran -- the exact command whose failure
    brought us there, which is the defect fix 3 claims to close, reached through its own hole.

    THE TRIGGER IS ON THIS MACHINE TODAY. `read_entry` returns `None` for a plugin whose record
    carries no `gitCommitSha`, and `superpowers@claude-plugins-official` is installed right now
    with no such key. So this is not the first-install case alone.

    Nothing recorded before and nothing recorded now means there is nothing to put back.
    """
    sites = Sites(hub_root=Path("/nowhere/hub"), plugins_root=Path("/nowhere/plugins"))
    act = _Act(hub_head="a" * 40, clone_head="b" * 40, previous=None)

    # `read_entry` on a plugins root that does not exist returns None, which is the state under
    # test: unreadable/absent on both sides.
    assert _install_still_moved(sites, OCTO, MARKETPLACE, act) is False


def test_read_entry_takes_the_USER_scope_row_not_whichever_is_first(tmp_path: Path) -> None:
    """Claude Code records one row per scope, and this lane installs at user scope.

    `n8n-as-code@n8nac-marketplace` already carries a `project`-scoped row on this machine, so
    `rows[0]` is a coin toss the moment a plugin is installed twice -- and the wrong row's
    revision would make the lane think the machine is behind, or current, when it is not.
    """
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    f"octo@{MARKETPLACE}": [
                        {
                            "scope": "project",
                            "version": "0.0.1",
                            "gitCommitSha": "p" * 40,
                            "installPath": "/wrong",
                        },
                        {
                            "scope": "user",
                            "version": "11.0.1",
                            "gitCommitSha": "u" * 40,
                            "installPath": "/right/11.0.1",
                        },
                    ]
                }
            }
        )
    )
    sites = Sites(hub_root=tmp_path / "hub", plugins_root=plugins)

    entry = read_entry(sites, OCTO, MARKETPLACE)

    assert entry is not None
    assert (entry.version, entry.revision) == ("11.0.1", "u" * 40)


def test_read_entry_is_None_when_no_row_is_user_scoped(tmp_path: Path) -> None:
    """A record with rows but none this lane installed is not evidence of what it installed."""
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    f"octo@{MARKETPLACE}": [
                        {"scope": "project", "version": "0.0.1", "gitCommitSha": "p" * 40}
                    ]
                }
            }
        )
    )
    sites = Sites(hub_root=tmp_path / "hub", plugins_root=plugins)

    assert read_entry(sites, OCTO, MARKETPLACE) is None
