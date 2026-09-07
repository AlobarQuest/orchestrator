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


def test_a_restore_that_CANNOT_RUN_is_named_rather_than_a_bare_traceback(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollback that cannot complete is the worst state this lane can produce, so it is raised as
    this module's own error rather than escaping as whatever the failing step happened to throw --
    a partially restored machine is the one state most needing a record."""
    _git(estate.hub, "remote", "set-url", "origin", str(estate.hub.parent / "gone.git"))
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
    _git(estate.hub, "remote", "set-url", "origin", str(estate.hub.parent / "gone.git"))
    _break_claude_after_the_forward_pass(estate, monkeypatch, "SDS_FAKE_CLAUDE_STALE")
    try:
        with pytest.raises(RollbackFailed, match="between two versions"):
            _act(estate)
    finally:
        os.environ.pop("SDS_FAKE_CLAUDE_STALE", None)


def test_a_publish_that_fails_rolls_the_whole_act_back(estate: Estate) -> None:
    """`installed == pinned == clone`, PUBLISHED, is the hub's own invariant, so a machine running
    a plugin whose pin no repository holds satisfies three quarters of it. The act is put back and
    the next pass tries again, rather than inventing an outcome for a state the invariant forbids.
    """
    _git(estate.hub, "remote", "set-url", "origin", str(estate.hub.parent / "not-a-repo.git"))
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


def test_a_dirty_hub_is_refused(estate: Estate) -> None:
    """This lane commits what it writes and will not commit somebody else's."""
    (estate.hub / "NOTES.md").write_text("a person's note\n")
    outcome = _act(estate)
    assert outcome.action == ACTION_INSTALL_FAILED
    assert "uncommitted changes" in outcome.detail


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


def test_the_unpublished_check_reads_no_network(estate: Estate) -> None:
    """The control for the refusal above: a hub whose origin is unreachable still passes it,
    because `origin/main` is a ref on disk that a successful push advances and a refused push does
    not. Going to the network would make this lane's refusal depend on somebody else's landings.
    """
    _git(estate.hub, "remote", "set-url", "origin", str(estate.hub.parent / "gone.git"))
    outcome = _act(estate)
    # It gets all the way to the publish, which is the only step that needs the remote.
    assert outcome.action == ACTION_ROLLED_BACK
    assert "could not be published" in outcome.detail


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
    cache = tmp_path / "cache"
    for version in ("9.45.0", "10.1.0", "11.0.1", "8.0.0"):
        (cache / version).mkdir(parents=True)
        (cache / version / "plugin.json").write_text("{}")

    removed = prune(cache, "11.0.1")

    assert sorted(path.name for path in cache.iterdir()) == ["10.1.0", "11.0.1"]
    assert sorted(removed) == ["removed stale cache 8.0.0", "removed stale cache 9.45.0"]


def test_pruning_two_directories_removes_nothing(tmp_path: Path) -> None:
    """The state the machine is actually in, and the control for the test above: with exactly the
    installed version and one other there is nothing stale, and a prune that removed anything here
    would be taking the rollback buffer."""
    cache = tmp_path / "cache"
    for version in ("9.45.0", "11.0.1"):
        (cache / version).mkdir(parents=True)

    assert prune(cache, "11.0.1") == []
    assert sorted(path.name for path in cache.iterdir()) == ["11.0.1", "9.45.0"]


def test_a_prune_that_cannot_read_the_cache_reports_rather_than_raises(tmp_path: Path) -> None:
    """A directory that could not be pruned is untidy, not broken -- and failing the pass over it
    would roll back an install that has already been verified."""
    assert prune(tmp_path / "absent", "11.0.1") == [
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
