"""One fork's pass: build `main` with every rule's fix applied, check it, and move `hardened`.

THE HARDENED TREE IS A FUNCTION OF `main` AND THE RULES, NEVER OF THE BRANCH'S OWN HISTORY. Each
pass applies every rule to the landed `main` from scratch, so a release upstream rewrote never
conflicts, and a call site upstream added is fixed like the old ones. The commit's parents are
the previous `hardened` and then `main`, the pattern of fork-sync's sync commit (ADR-0056
amendment 2): `hardened` stays an ancestor, so the branch only moves forward and the installer's
`--ff-only` pull holds; `main` is an ancestor, so the branch reads as `main` plus the fixes. The
same inputs give the same tree, and a pass with nothing new pushes nothing.

THREE OUTCOMES, FROM THE CHECKS (ADR-0057):

- `advanced` (or `would_advance` without `--push`): every check passes on the hardened tree and
  the guard holds, so `hardened` moves to the new commit.
- `current`: `hardened` already holds this tree on top of this `main`.
- `held`: a fix failed, a check still flags the hardened tree, the guard refused, or the fork
  could not be read as text where a rule covers it. `hardened` does not move, so the installer
  stays on the last good hardened version.

Separately, a rule whose check flags nothing on `main` is FIXED UPSTREAM: its fix changes nothing
and it can retire. That is reported, not a hold.

THE GUARD IS WRITTEN AGAINST THE RESULT, NOT THE CONSTRUCTION. It diffs the two trees as git
reports them and refuses anything other than a content change to a file some rule covers, with
its mode unchanged and outside `.github/`. So a defect in how the tree was built is caught by a
reading that does not share its code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from fork_harden import git
from fork_harden.rules import BRANCH, SOURCE_BRANCH, Fork, Rule

Status = Literal["advanced", "would_advance", "current", "held"]

FORK_OWNED: Final = ".github/"
"""fork-sync's half of the fork (ADR-0056 amendment 1). Hardening never changes it."""

BLOB_MODES: Final = frozenset({"100644", "100755"})  # not-a-vocabulary: git's own blob modes

IDENTITY: Final = {
    "GIT_AUTHOR_NAME": "fork-harden",
    "GIT_AUTHOR_EMAIL": "fork-harden@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "fork-harden",
    "GIT_COMMITTER_EMAIL": "fork-harden@users.noreply.github.com",
}


class Held(Exception):
    """A reason this pass may not move `hardened`. Its message is the report's line."""


@dataclass
class RuleResult:
    rule: Rule
    files: int = 0
    lines_on_main: int = 0

    @property
    def fixed_upstream(self) -> bool:
        return self.files > 0 and self.lines_on_main == 0


@dataclass
class Outcome:
    fork: Fork
    status: Status
    main: str = ""
    hardened_before: str | None = None
    hardened_after: str | None = None
    reasons: list[str] = field(default_factory=list)
    results: list[RuleResult] = field(default_factory=list)
    changed_files: int = 0

    def lines(self) -> list[str]:
        head = f"{self.fork.repository}: {self.status}"
        if self.main:
            head += f" (main {self.main[:12]}"
            head += f", hardened {(self.hardened_after or self.hardened_before or 'none')[:12]})"
        out = [head]
        out += [f"  held: {reason}" for reason in self.reasons]
        for result in self.results:
            note = " -- fixed upstream; this rule can retire" if result.fixed_upstream else ""
            out.append(
                f"  {result.rule.name}: {result.lines_on_main} line(s) on main in "
                f"{result.files} file(s) it covers{note}"
            )
        if self.changed_files:
            out.append(f"  hardened differs from main in {self.changed_files} file(s)")
        return out


def _text(repo: git.Repo, entry: git.Entry) -> str:
    if entry.kind != "blob" or entry.mode not in BLOB_MODES:
        raise Held(f"{entry.path} is a {entry.kind} (mode {entry.mode}), which no rule can read")
    data = git.blob(repo, entry.sha)
    if b"\0" in data:
        raise Held(f"{entry.path} is binary, which no rule can read")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Held(f"{entry.path} is not UTF-8 text, which no rule can read") from error


def _hardened_text(path: str, original: str, results: list[RuleResult]) -> str:
    """One file with every covering rule's fix applied in order, and every check passed after."""
    covering = [result for result in results if result.rule.covers(path)]
    text = original
    for result in covering:
        result.files += 1
        result.lines_on_main += len(result.rule.detect(path, original))
        try:
            text = result.rule.fix(path, text)
        except ValueError as error:
            raise Held(f"{result.rule.name} could not fix {path}: {error}") from error
    for result in covering:
        if flagged := result.rule.detect(path, text):
            shown = ", ".join(str(number) for number in flagged[:5])
            raise Held(f"{result.rule.name} still flags {path} at line(s) {shown}")
    return text


def _apply(
    repo: git.Repo, fork: Fork, main_tree: str, results: list[RuleResult]
) -> dict[str, tuple[str, str]]:
    """Every covered file's hardened content, written as a blob: `path -> (mode, sha)`."""
    replaced: dict[str, tuple[str, str]] = {}
    for entry in git.entries(repo, main_tree):
        if not fork.covers(entry.path):
            continue
        original = _text(repo, entry)
        text = _hardened_text(entry.path, original, results)
        if text != original:
            replaced[entry.path] = (entry.mode, git.write_blob(repo, text.encode("utf-8")))
    for result in results:
        if result.files == 0:
            raise Held(f"{result.rule.name} covers no file on main; re-read its finding")
    return replaced


def guard(repo: git.Repo, fork: Fork, main_tree: str, hardened_tree: str) -> int:
    """Refuse a hardened tree that differs from `main` beyond what the rules may edit."""
    found = git.changes(repo, main_tree, hardened_tree)
    for change in found:
        # The mode comparison is the whole test of "a content change": with renames off, an add, a
        # delete and a type change each have a different mode on one side (`000000` or another).
        if change.old_mode != change.new_mode:
            raise Held(
                f"guard: {change.path} is {change.status} {change.old_mode}->{change.new_mode}"
            )
        if change.path.startswith(FORK_OWNED):
            raise Held(f"guard: {change.path} is fork-sync's")
        if not fork.covers(change.path):
            raise Held(f"guard: {change.path} is covered by no rule")
    return len(found)


def _message(fork: Fork, main: str, results: list[RuleResult]) -> str:
    lines = [
        f"fork-harden: {', '.join(rule.name for rule in fork.rules)} on main {main[:12]}",
        "",
        f"{fork.repository} main {main}, hardened by the orchestrator's fork-harden (ADR-0057).",
        "",
    ]
    for result in results:
        lines.append(f"- {result.rule.name}: {result.rule.finding}")
    return "\n".join(lines) + "\n"


def harden(repo: git.Repo, fork: Fork, *, push: bool) -> Outcome:
    """One pass over one fork. Raises `git.GitError` when the fork can't be read."""
    heads = git.remote_heads(repo, SOURCE_BRANCH, BRANCH)
    if SOURCE_BRANCH not in heads:
        raise git.GitError(f"{fork.repository} has no {SOURCE_BRANCH}")
    main = heads[SOURCE_BRANCH]
    before = heads.get(BRANCH)
    git.fetch(repo, *[sha for sha in (main, before) if sha])
    outcome = Outcome(fork, "held", main=main, hardened_before=before)
    outcome.results = [RuleResult(rule) for rule in fork.rules]
    main_tree = git.tree_of(repo, main)
    try:
        replaced = _apply(repo, fork, main_tree, outcome.results)
        tree = git.tree_with(repo, main_tree, replaced)
        outcome.changed_files = guard(repo, fork, main_tree, tree)
    except Held as reason:
        outcome.reasons.append(str(reason))
        return outcome
    if before and git.tree_of(repo, before) == tree and git.is_ancestor(repo, main, before):
        outcome.status = "current"
        return outcome
    parents = [before, main] if before else [main]
    new = git.commit(repo, tree, parents, _message(fork, main, outcome.results), IDENTITY)
    outcome.hardened_after = new
    if not push:
        outcome.status = "would_advance"
        return outcome
    git.push(repo, new, BRANCH)
    outcome.status = "advanced"
    return outcome
