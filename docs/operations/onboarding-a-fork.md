# Onboarding a fork onto the upstream sync lane

This page explains how to put a fork on the fork-sync lane: a daily workflow opens one rolling pull
request that brings the fork up to upstream's newest release, two required checks build and review
it, the inert landing lane lands it, and, for a tool installed on the operator machine,
`tool-installer` installs it (ADR-0042). `AlobarQuest/rtk` is the reference fork. The lane's
design decisions are recorded in ADR-0056.

The `fork-sync` console script automates every step it can and reports the rest:

| Command | Writes | What it does |
|-|-|-|
| `fork-sync check <fork>` | nothing | Reports each step below as `ok`, `missing`, `human` or `info`. Exits 1 while anything is `missing`. |
| `fork-sync human-steps <fork>` | nothing | Prints the steps only a person can do, then the agent steps. |
| `fork-sync baseline <fork> --tag <tag> --confirm` | the fork's `main` | Resets `main` to an upstream tag with upstream's whole `.github/` removed. `--discard-fork-commits` drops commits upstream doesn't have. |
| `fork-sync scaffold <fork> --config <file>` | a branch and pull request | Commits `.github/fork-sync.toml`, an empty `.github/fork-sync-acks.toml`, and the rendered workflows and review script. |
| `fork-sync protect <fork>` | repository settings | Applies the landing settings and branch protection on `main`. |

Each writing command refuses before it writes when its precondition does not hold.

## The declaration

Each fork commits `.github/fork-sync.toml`. The workflows and `fork-sync check` read the same file
through one parser (`src/fork_sync/fork_sync_review.py`), and unknown keys are refused.

```toml
upstream = "morluto/rea"                                  # owner/name
ecosystem = "npm"                                         # cargo | npm | npm-no-deps
release_tag_pattern = '^rea-agents-(\d+\.\d+\.\d+)$'      # group 1 is the version
build_commands = ["npm run build"]                        # optional; ecosystem default
test_commands = ["npm run test:run"]                      # optional; ecosystem default

[review]
paths_watched = ["install.sh", ".claude/*"]   # any change is a finding
trust_critical = ["src/mcp/*"]                # reviewed first, most closely
model = "claude-sonnet-5-5"                   # optional
max_diff_chars = 400000                       # optional
```

Globs are `fnmatch` patterns, so `*` crosses directories. The ecosystem defaults are:

| Ecosystem | Version read from | Build | Test | Audit (built in) |
|-|-|-|-|-|
| `cargo` | `Cargo.toml` | `cargo build --locked --all-targets` | `cargo test --locked` | `cargo-audit audit --json --file <copy of Cargo.lock>` (cargo-audit 0.22.2) |
| `npm` | `package.json` | `npm run build` | `npm test` | `npm audit --json --audit-level=high --userconfig /dev/null --registry https://registry.npmjs.org` |
| `npm-no-deps` | `package.json` | none | `npm test` | none: any dependency the root `package.json` declares is a `dependencies-declared` finding |

`npm-no-deps` is for a fork, such as a Claude Code plugin, whose root `package.json` only carries
the version and which installs nothing. CI installs nothing for it, and a nested `package.json`
that declares dependencies the fork never installs is left to the model and the watched paths.

The audit is not configurable: it runs on copies of the head's manifest and lockfile in a scratch
directory, so no configuration the head ships (`.npmrc`, `.cargo/config.toml`) applies to it.

Read the build and test commands from the fork's own manifest. Upstream's scripts are data: choose
commands that build and test without network side effects. For `npm`, CI always installs with
`npm ci --ignore-scripts`, so no install-time script runs.

## What the lane does

- **`upstream-sync.yml`** runs daily at 06:00 UTC and on demand. It mints a token from the App
  `fork-secure-upstream-sync`, picks upstream's newest published release whose tag matches
  `release_tag_pattern`, and compares its version with the fork's manifest. When upstream's is
  greater (compared as numbers; an older or equal tag is never synced to), it
  rebuilds the `upstream-sync` branch from the tag, restores the fork's whole `.github/`, and opens
  or edits one rolling pull request. It pushes only when the rebuilt tree differs from the
  branch's, so a held sync isn't re-reviewed and re-alerted every day. Submodule gitlinks stay as
  upstream has them; nothing fetches their content. It does not review.
- **`ci.yml`** runs on every pull request and on pushes to `main`, and on nothing else: a manual
  run on a pull request's branch would report the required jobs as skipped, which branch
  protection counts as passing. Its first two job names are the required checks:
  - `build and test` runs the configured build and test commands.
  - `security review` runs `.github/scripts/fork_sync_review.py review` on pull requests. It
    checks out the BASE, fetches the head's objects without checking them out, and reads them
    with git (no replace objects, and no attributes from any source, which needs git 2.40 or
    later), so nothing from the pull request executes in the job that holds `ANTHROPIC_API_KEY`.
  - `notify` raises the alert (see the following section). It runs the base's script, holds no
    secret, and its token can write only issues.
- **`fork-sync-audit.yml`** runs weekly (Mondays 13:00 UTC) and on demand. Its `weekly audit`
  job audits `main`'s dependencies against `main`'s acknowledgements, read from git, so an
  advisory published against a version already installed is found between upstream releases.
  Its exits match the review's, and any outcome but success raises the audit alert, including a
  setup step that failed before the audit ran.

### Why the review is a required check

Before this lane the AI review ran inside the sync workflow with `continue-on-error`, so a review
that failed or flagged something still left a mergeable pull request. As a required check the
review gates the landing: nothing lands unless it passes, and a review that cannot run fails.

Every changed path is accounted for: its diff reaches the model (a lockfile as a summary of its
package changes), or it carries a deterministic finding. A path that is neither fails the review
with exit 2. The deterministic findings are:

- Any change under `review.paths_watched`.
- Any submodule pointer change, and any `.gitattributes`, `.lfsconfig` or `.gitmodules` change
  anywhere.
- Any added or changed binary file, with its sha256. Binary is decided from the bytes (a NUL
  byte, or not UTF-8), never from git or `.gitattributes`; a binary file that is still UTF-8,
  such as a script with a NUL in a comment, is also sent to the model.
- Any shell script at the repository root, such as `install.sh`.
- For `npm`: a `package.json` anywhere whose install-time lifecycle scripts or `bin` changed; a
  changed file a lifecycle script runs; a dependency added or changed to a git, URL, file, link
  or `npm:` alias spec; any `.npmrc`; and in `package-lock.json`, a package resolved outside
  `https://registry.npmjs.org/`, a link, an entry whose `name` differs from the package it is
  installed as, an `integrity` change at an unchanged version, or a new or changed package with
  `hasInstallScript`.
- For `cargo`: any `build.rs`, a `proc-macro` change, anything under `.cargo/`, any
  `rust-toolchain*`; and in `Cargo.lock`, a new package from a source other than crates.io or a
  `checksum` change at an unchanged version. Packages are told apart by name, version and source,
  so a git build cannot hide behind a crates.io entry of the same name and version.
- A lockfile with duplicate entries, or one that does not parse.
- Each advisory a failing audit reports: every cargo-audit vulnerability, and every npm advisory
  of `high` or `critical` severity, once per installed package version. cargo-audit's warnings
  (unmaintained, unsound, yanked) are not findings. An audit that fails with output that does not
  parse, or with no advisory listed, means the review could not run (exit 2).
- What the model reports, trust-critical files first. A diff over `max_diff_chars` is itself a
  finding and is not sent.

An ordinary dependency version bump from the registry is not a finding; the model sees it in the
lockfile summary.

It writes a report to the job summary and to one rolling pull-request comment, then exits:

| Exit | Meaning | Check |
|-|-|-|
| 0 | No unacknowledged finding | Passes |
| 1 | At least one unacknowledged finding | Fails |
| 2 | The review could not run: no key, API error, unparseable verdict | Fails |

## The alert

A held sync reaches the maintainer as an issue on the fork. When `security review` fails on the
sync pull request (branch `upstream-sync`), `notify` opens one issue titled "Fork-sync: a
security review is holding an upstream sync", mentioning the repository owner, or comments on
the open one for each new head or new sync pull request. When a later review of a sync pull
request passes, it comments and closes the issue. A pull request a person opens raises no alert:
that person is watching it. The weekly audit alerts the same way with its own issue.

A review that couldn't run (exit 2: an API error or timeout) is not retried: the sync pushes only
a changed tree, so nothing re-triggers it. Re-run the failed `security review` job; the alert
says so. Network calls time out after 300 seconds, so a hung call fails the check rather than
running until the job is cancelled, which raises no alert. The issue holds fixed fields only (the pull request, the head, the run); the findings stay
in the report, because their text comes from upstream's diff.

A failure to open or comment fails `notify`. A failure to close only warns: a red job would hold a
pull request whose review passed, with no alert to say so. The landing lane doesn't alert; its
holds are in `~/Library/Logs/inert-landing.log` only.

The alert needs issues turned on, and a fork starts with them off. `fork-sync protect` turns them
on and `fork-sync check` reports a fork without them. A fork onboarded before the alert existed
needs `fork-sync scaffold` (an admin merge: the pull request is judged by the old gate, whose
script has no `notify`, so that job fails on it) and then `fork-sync protect` again.

## Acknowledge a finding

A finding a person has read and accepts is acknowledged by its fingerprint, which the report
prints. The fingerprint covers the finding's kind, its path and the exact content it is about: the
changed file's blob, a binary's sha256, the lockfile and the package entry, or, for a model
finding, the path's blob (or the whole reviewed diff for a path that did not change) plus the
finding's severity and wording. A model finding can therefore return with a new fingerprint when
the model words it differently on a later run; acknowledge it again after reading it. An acknowledgement therefore covers
that content and nothing later: if the file changes again, the finding returns with a new
fingerprint.

1. Add an entry to `.github/fork-sync-acks.toml` on the fork's `main`:

   ```toml
   [[ack]]
   fingerprint = "3f9c0a1b2c3d4e5f"
   by = "devon"
   date = 2026-10-19
   reason = "Prebuilt Windows binary, sha256 matches upstream's release asset."
   ```

   An audit finding's fingerprint covers only the advisory, the package and its installed
   version, so its acknowledgement names exactly what it accepts. It lasts across releases until
   that package version changes, and a newly published advisory fails the check again. For a
   report row `RUSTSEC-2026-0204 crossbeam-epoch 0.9.18: Invalid pointer dereference … (no CVSS;
   fix: >=0.9.20)`:

   ```toml
   [[ack]]
   fingerprint = "<the fingerprint the report prints for that row>"
   by = "devon"
   date = 2026-10-19
   reason = "RUSTSEC-2026-0204: crossbeam-epoch fmt::Pointer only; upstream v1.0.1 still pins 0.9.18."
   ```

   Put the entries on a branch and open a pull request. Its `security review` runs from the
   current `main`, which doesn't have the new entries, and the model can flag the change to the
   gate itself. Read the report, then merge it with `gh pr merge <number> --repo <fork> --merge
   --admin`: `main` is protected without admin enforcement so that a person can land a gate
   change this way.

2. Run the sync again: `gh workflow run upstream-sync.yml --repo <fork>`. It rebuilds the branch
   with `main`'s new `.github/`, and the review on that push reads the acknowledgements from the
   new base. Re-running the old job does not work: it keeps the base of the event that started it.

`main` must be current: the inert landing policy requires `require_head_current_with_base`, so a
sync pull request is brought up to date with `main` and re-judged after `main` changes, including
after an acknowledgement or a gate change lands. A green check from before that change does not
carry over.

## Procedure

Work from the orchestrator's main tree venv (`~/Projects/orchestrator/.venv/bin/fork-sync`) or a
worktree. Run `fork-sync check <fork>` after any step to see where you are.

1. **Decide the fork belongs on the lane.** The lane is for a fork you take upstream's releases
   into wholesale, where a landing on `main` changes nothing already serving.
2. **Do the human steps.** Run `fork-sync human-steps <fork>`. A person installs the App
   `fork-secure-upstream-sync` on the fork (Only select repositories) and confirms BWS holds the
   App key.
3. **Baseline the fork**, if upstream's workflows are on its `main`. Pick the tag the fork's
   manifest version matches, then run `fork-sync baseline <fork> --tag <tag> --confirm`. It
   removes upstream's whole `.github/`, so nothing upstream planted there (an acknowledgements
   file, a script) becomes the fork's. It refuses, and prints the commits, if `main` has commits
   no upstream branch or tag contains. Read them; if the fork is to drop them (for example, local
   hardening the lane doesn't carry), re-run with `--discard-fork-commits`.
4. **Scaffold.** Write the declaration, then run `fork-sync scaffold <fork> --config <file>`. It
   prints the pull request number. If the old lane left files `scaffold` does not render (for rtk,
   `.github/scripts/ai_security_review.py`), push a commit removing them to the
   `fork-sync-scaffold` branch.
5. **Set the secrets** `APP_ID`, `APP_PRIVATE_KEY` and `ANTHROPIC_API_KEY` from BWS, in-process,
   with the value on stdin and no `--body` flag (`gh secret set NAME --repo <fork>`). Never pass
   `--body -`, which stores a literal dash. Then register the fork's copies in factory-runner's
   `.cred-consumers.toml`: add a `gh-actions-secret` consumer (`repo = "AlobarQuest/<fork>"`)
   named `APP_PRIVATE_KEY` to the `fork-sync-app-private-key` entry and one named
   `ANTHROPIC_API_KEY` to the `anthropic-api-key` entry. Every fork adds to both. `APP_ID` is the
   App's id, not a credential, so it is not registered.
6. **Merge the scaffold pull request** once `build and test` passes on it. Its `security review`
   fails by design: the review runs from the base, and the base has no review script yet. If
   `main` is already protected (rtk), `security review` is not yet a required check, so the
   failure doesn't block the merge.
7. **Run the first sync:** `gh workflow run upstream-sync.yml --repo <fork>`. Both checks now run
   on the sync pull request, from the scaffolded `main`.
8. **Protect `main`.** Run `fork-sync protect <fork>`. It refuses until an open pull request has
   reported both `build and test` and `security review` (read from the Actions jobs API), which the
   sync pull request from step 7 has. It then allows merge commits, sets
   `squash_merge_commit_message` to `COMMIT_MESSAGES`, deletes branches on merge, turns issues on
   for the alert, and requires both
   checks from the GitHub Actions app (`app_id` 15368, so no other status poster can satisfy
   them) with `strict: false`, no required reviews and no admin enforcement.
9. **Review the sync pull request.** Read the review comment. Acknowledge what you accept; do not
   acknowledge what you have not read.
10. **Release the landing policy.** Release a change-manager policy listing the fork in
    `inert_landing.repositories` and `fork-secure-upstream-sync[bot]` in both `permitted_authors`
    and `non_ecosystem_authors` (ADR-0041).
11. **Record App Brain's landing** with `record_default_branch_landing(github_repo=<fork>,
    landing="inert", evidence=...)`, naming the workflows you read.
12. **Add the installer row** in `src/tool_installer/tools.py` if the fork is a tool this machine
    installs.
13. **Confirm.** `fork-sync check <fork>` reports no `missing` step. To have it verify steps 10 and
    11 rather than report them as `human`, set `FORK_SYNC_CHANGE_MANAGER_TOKEN` (a change-manager
    read bearer) and `FORK_SYNC_APP_BRAIN_URL` with `FORK_SYNC_APP_BRAIN_KEY` (App Brain's read
    key).

## Limits

- `check` compares the three rendered files byte for byte, so it reports drift after any hand
  edit. Re-run `scaffold` to bring a fork up to the current templates.
- The `security review` job runs only on pull requests. A pull request without access to the
  repository's secrets, such as one from Dependabot, fails it, which keeps such a pull request
  from landing.
- The review script, the declaration and the acknowledgements all come from the pull request's
  BASE, never its head, so a pull request can't loosen the gate it is judged by. A change to the
  gate takes effect only once it is on `main`. A base with no script fails the check, which is
  why the scaffold pull request fails `security review`.
- The model reads upstream's diff, and an instruction planted in that diff can try to talk it out
  of a finding. The deterministic findings don't pass through the model, so they hold regardless.
- The jobs a `pull_request` run executes are defined by the pull request's own `ci.yml`. A sync
  pull request can't change it, because the sync restores the fork's whole `.github/` from `main`;
  a pull request a person opens on the fork is trusted to the same degree as that person.
- GitHub disables a repository's scheduled workflows after 60 days without activity. A fork whose
  upstream doesn't release for that long, or whose sync stays held, loses both the daily sync and
  the weekly audit until a person re-enables them on the Actions tab.
