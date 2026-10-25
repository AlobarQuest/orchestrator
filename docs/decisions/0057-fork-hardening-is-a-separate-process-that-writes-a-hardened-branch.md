# ADR 0057 — Fork hardening is a separate process that writes a `hardened` branch

**Date:** 2026-10-25
**Status:** Accepted (Devon, 2026-10-25: "all as recommended, 10: remove", on the twelve decisions
below)

## Context

ADR-0056 amendment 1 took hardening out of the fork-sync lane: a fork's `main` is upstream's
release plus the fork's `.github/`, and a security concern holds a version rather than patching it.
Devon asked for a separate process for the case where he wants a version he runs fixed: "I am
concerned that cramming too much into a process is a fragility risk" (2026-10-21).

Two forks need it:

- **claude-octopus (`octo`).** Its hooks and scripts run inline Python (`python3 -c`, `-`, `-m`).
  With those forms Python puts the current directory first on the import path, and Claude Code
  runs hooks from the session's project directory, so a repository's own `json.py` runs as Devon
  with no prompt. HIGH, from the one-time review of v11.14.1 (2026-10-20). At v11.15.1 there are
  89 such calls in `hooks/` and `scripts/`, and only one uses `-I`. octo has been disabled
  (`"octo@devon-plugins": false`) since.
- **n8n-as-code.** The Claude plugin registers an MCP server that runs `npx -y @n8n-as-code/mcp`,
  and its skill tells an agent to run `npx --yes n8nac` and `npx --yes @n8n-as-code/n8n-manager`.
  Each runs upstream's newest npm release outside the fork's review (AlobarQuest/n8n-as-code #15,
  medium, 2026-10-22). The plugin is held off the tool installer until this is pinned.

The handoff is `docs/superpowers/plans/2026-10-21-handoff-fork-hardening.md`.

## Decision

`fork-harden` (`src/fork_harden/`) is a program beside fork-sync. It meets fork-sync only at a
fork's landed `main`, which it reads, and the tool installer only at a row's `branch`.

1. **A rule per finding, written by a session Devon starts, approved by him.** A rule is a check
   (is the finding in this file), a fix (rewrite the file) and the paths it may edit, as code in
   `src/fork_harden/`. The session shows Devon the diff the rule makes on the fork, and merges
   the rule after his approval. Nothing drafts rules on its own.
2. **A daily pass at 03:00, plus a manual run.** `com.devon.fork-harden` runs
   `fork-harden run --push` fifteen minutes before the tool installer. The order is a
   convenience: if the pass hasn't run or refused, `hardened` hasn't moved and the installer
   installs nothing new. With no fork in the table, a pass reads nothing.
3. **Rules live in the orchestrator; the fork's `hardened` branch holds only their output.** Each
   pass applies every rule to `main` from scratch. That is what gives three outcomes rather than
   two: a check that passes on unmodified `main` means upstream fixed the finding and the rule
   can retire, which a patch merged forward can't tell apart from upstream moving the code. It
   also fixes a call site upstream adds. The commit is built with `git commit-tree`, the tree
   being the rules applied to `main` and the parents the previous `hardened` and then `main`
   (fork-sync's own pattern, ADR-0056 amendment 2). `hardened` only moves forward, so the
   installer's `--ff-only` pull holds, and there are no merge conflicts to resolve.
4. **No pull request reviews the hardened branch; a structural guard does.** Before a push: the
   diff from `main` changes only the contents of files a rule covers, with modes unchanged and
   nothing under `.github/`, and every rule's check passes on the result. A pull request into
   `hardened` was rejected: its `security review` reads acknowledgements from the base, which is
   `hardened` before the release, so every finding `main` had already accepted would hold again,
   one person per release, the cost ADR-0056 amendment 4 removed. The rule code is reviewed in
   the orchestrator's own pull request.
5. **A hold reaches Devon through Healthchecks.** The check `sds-fork-hardening` on
   `healthcheck.alobar.net` gets `/fail` with the report on every non-zero exit, as
   `sds-fork-sync-watch` does. An issue on the fork was rejected: the pass acts as Devon's own
   account, and GitHub doesn't notify an account of its own activity.
6. **No new credential.** The pass pushes with the operator's own git credentials
   (`gh auth git-credential`), the login `fork-sync ack`, `scaffold` and `baseline` already push
   to forks with. It needs no Anthropic key and no App key.
7. **`hardened` is protected on each fork:** no force-push, no deletion. The forward-only rule is
   then enforced by GitHub as well as by the program, which never forces a push. `fork-sync check`
   reads only `main`'s protection. A pull request is never opened from `hardened`:
   `delete_branch_on_merge` is on for every fork, and merging one would delete the branch.
8. **octo's rule covers `hooks/` and `scripts/`,** because hooks source `scripts/` and
   `scripts/orchestrate.sh` runs every session. It inserts `-I` after the interpreter in every
   explicit inline form (`-c`, `-m`, `-`). A script run is left alone: it puts the plugin's own
   directory first, and octo's helpers import their siblings from it. The medium finding (octo
   runs other agent CLIs with approvals off) is out of scope: it is behaviour, not a fix a rule
   can make.
9. **n8n-as-code's MCP server becomes `n8nac mcp`,** served by the tool installer's build of the
   fork, and the skill's `npx --yes n8nac` becomes `n8nac`. Both resolve through
   `/opt/homebrew/bin/n8nac`, the link the installer's `n8nac` row owns, so that link stays:
   hardening relies on it rather than removing the need for it. The installer's row builds
   `packages/mcp` beside the CLI.
10. **n8n-manager is removed from the skill.** Its commands become `n8n-manager-not-installed`,
    which resolves to nothing, and the skill says so under its heading. It was last run in June
    (an `npx` download of 0.7.1 on 2026-06-22); its state directory, which `n8nac` still reads
    for its API key, is untouched.
11. **The order for a plugin row:** the installer gains a declared manifest path and a cache
    check that accepts no cache directory; the n8n-as-code row is added with `branch =
    "hardened"`; its serving clone is replaced with a fresh clone on `hardened` (the old one holds
    pre-rebuild history). octo's row moves to `hardened` with its serving clone checked out on
    `hardened` in the same step, since `_require_usable_clone` refuses a mismatch.
12. **An upstream fix is noticed by the check, whoever reports it.** No upstream issue has been
    filed for octo's finding from Devon's account.

## How a check reads, and what it over-counts

A check flags more than its fix can repair, because a finding it misses ships silently and a
site it wrongly flags only holds the branch until a person reads it (Code Brain lesson 2). It
narrows by structure it can read exactly: for octo, the first token after an interpreter word and
its options; for n8n-as-code, the parsed manifest and any registry-runner word in the plugin. A
line a person read and found is not the finding is listed by path and the sha256 of its exact
text (`READ_LINES` in the rule's module), so any change to it brings it back. Six lines are
listed at 2026-10-25: five in octo (regular expressions, a registry row and the interpreter
candidates array) and one docs example in n8n-as-code's README.

Stated limits: an interpreter reached through a variable (`"$PY" -c`) is invisible to octo's
check, and octo's one such path runs scripts only. n8n-as-code's CLI writes `npx --yes n8nac`
into a project's generated `AGENTS.md`, which resolves through the same gated prefix link.

## Alternatives considered

- **Patch commits merged forward.** Rejected under decision 3.
- **Rebase the patches onto each `main` and force-push.** Rejected before design: a rewritten
  branch fails the installer's `--ff-only` pull, which is how octo's install broke after
  fork-sync's `baseline` on 2026-10-20.
- **`PYTHONSAFEPATH=1` in place of `-I`.** It has no effect on the macOS system Python 3.9.6,
  which a hook can reach, and octo has no shared preamble to set it in.
- **Pin the MCP server to a version on npm.** A pinned version is still upstream's published code,
  not the fork's reviewed build.

## Consequences

- A tool row tracks `hardened` while its fork has a rule. When every rule of a fork reports
  "fixed upstream", `hardened`'s tree equals `main`'s and the row can return to `main`; that is a
  person's change to `tools.py`, made at leisure, since nothing breaks meanwhile.
- A hold leaves the installer on the last good hardened version, and the alarm says so.
- The tool runs fork code built from `hardened`, so the installer's own probes are the proof that
  the hardened build works.
