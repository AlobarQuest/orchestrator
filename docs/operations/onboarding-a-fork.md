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
| `fork-sync ack <fork> <pr> --reason <text> --by <who>` | nothing; with `--yes`, the acknowledgements on `main` | Lists a held sync's failing rows and the entries it would add or raise. With `--yes --expect <fingerprints>` it records them, admin-merges the change and re-runs the sync. |
| `fork-sync onboard <fork> --config <file>` | everything the steps below write | Runs the whole procedure, resuming where the fork stands, and stops at the first step that needs a person (see "Procedure"). |
| `fork-sync watch` | nothing | Classifies every fork on the lane as `ok`, `waiting_on_person` or `stuck`, scheduled hourly (see "The stuck-lane alarm"). |

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
version_manifest = "package.json"                         # optional; see the following list
generate_lockfile = false                                 # optional; npm only

[review]
paths_watched = ["install.sh", ".claude/*"]   # any change is a finding
trust_critical = ["src/mcp/*"]                # reviewed first, most closely
model = "claude-sonnet-5-5"                   # optional
max_diff_chars = 400000                       # optional
```

Two keys exist for an upstream that is an npm workspace monorepo, such as n8n-as-code
(ADR-0056 amendment 2):

- `version_manifest` is the manifest whose version a sync is compared against. It defaults to
  the ecosystem's manifest at the root. Name the package the release tags version
  (`packages/cli/package.json`) when the root's version never moves.
- `generate_lockfile = true` is for an upstream that commits no `package-lock.json`. The sync
  resolves one as of the release tag's commit date and commits it on the sync branch, so the
  fork's `main` carries a lockfile that CI, the audit and the review read as usual. A tag that
  ships its own lockfile fails the sync.

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
  rebuilds the `upstream-sync` branch from the tag, restores the fork's whole `.github/`, commits a
  resolved lockfile if the declaration sets `generate_lockfile`, and opens or edits one rolling
  pull request. The sync commit has the tag as its first parent and `main` as its second, so the
  pull request always merges cleanly: a file only the fork has, such as its lockfile, would
  otherwise conflict, and GitHub runs no `pull_request` checks on a conflicted pull request. It
  pushes only when the rebuilt tree differs from the branch's or `main` has moved, so a held sync
  isn't re-reviewed and re-alerted every day. Submodule gitlinks stay as
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

Not every finding holds the sync (ADR-0056 amendment 4). These are reported: they stay in the
report, marked `reported`, but fail nothing and need no acknowledgement.

- An audit advisory that isn't critical. Critical means npm's severity `critical`, or a CVSS base
  score of 9.0 or more. npm gives the score. cargo-audit gives a vector, which the review scores
  with the CVSS v3.0 or v3.1 base equations; temporal and environmental metrics after the base
  ones are allowed and don't change it. A vector that isn't a strict v3.0 or v3.1 vector, or no
  vector at all, has no score, so its advisory is reported. An npm `low` or `moderate` advisory
  is not a finding unless its score is 9.0 or more, and then it holds. The weekly audit of `main`
  follows the same rule.
- `npm-install-script`: CI and the tool installer install with lifecycle scripts disabled.
- A model finding rated `low`. `medium`, `high`, and any other rating hold.
- A watched JSON file whose two sides, parsed, differ only in `version` keys whose two values are
  both plain release versions (`1.2.3`, `1.2.3-rc.1+b5`), and none of them below `scripts`, a
  dependency table, `overrides`, `resolutions`, `bin`, `exports`, `engines` or `config`: a
  manifest's release bump. Any other change to it holds, including a `scripts.version` change, a
  version turned into a range or a URL, one that also adds a key, or a side that isn't JSON or has
  duplicate keys.

Every other finding holds until it's acknowledged.

It writes a report to the job summary and to one rolling pull-request comment, then exits:

| Exit | Meaning | Check |
|-|-|-|
| 0 | No unacknowledged finding that holds | Passes |
| 1 | At least one unacknowledged finding that holds | Fails |
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
holds are in `~/Library/Logs/inert-landing.log`, and the stuck-lane alarm (the following section)
reports a sync it has held for six hours.

The alert needs issues turned on, and a fork starts with them off. `fork-sync protect` turns them
on and `fork-sync check` reports a fork without them. A fork onboarded before the alert existed
needs `fork-sync scaffold` (an admin merge: the pull request is judged by the old gate, whose
script has no `notify`, so that job fails on it) and then `fork-sync protect` again.

## The stuck-lane alarm

The lanes' dead-man pings prove only that a pass ran, and a pass that runs and holds everything
looks healthy to them (ADR-0056 amendment 6). `fork-sync watch` reads each fork's lane once an
hour and says whether it is moving. It only reads, and it runs as its own LaunchAgent
(`com.devon.fork-sync-watch`, hourly at :55 local, after the inert landing lane's :35), installed
with `scripts/install-fork-sync-watch-launchd.sh` from the main tree. The log is
`~/Library/Logs/fork-sync-watch.log`.

**Which forks.** Every repository registered as a `gh-actions-secret` consumer named
`APP_PRIVATE_KEY` under `fork-sync-app-private-key` in factory-runner's `.cred-consumers.toml` on
`main` (onboarding step 5 writes it), confirmed by `.github/fork-sync.toml` on its own `main`. A
registered repository without the declaration is printed `not_on_lane` and isn't classified: it
is mid-onboarding. A registered repository that isn't visible at all (renamed, deleted, or out of
the login's reach) is printed `unreadable`. The source needs only the operator's `gh` login.

**What it prints.** One line per fork, `ok`, `waiting_on_person` or `stuck` with its reasons, then
a summary with the number of GitHub API calls the pass made. Each fork costs at most eight reads
and nothing pages, so a pass is bounded by `1 + 8 x forks` calls.

| Exit | Meaning | Healthchecks |
|-|-|-|
| 0 | Nothing is stuck | success |
| 1 | The tool failed | `/fail` |
| 2 | Unreadable: a GitHub read failed (a 403 rate limit among them), or a fork serves something the watcher can't use (a declaration or manifest that doesn't parse) | `/fail` |
| 3 | A fork is stuck, whatever else the pass found | `/fail` |

The codes are the lander group's, as `run-inert-landing.sh` uses them. The check
`sds-fork-sync-watch` is an outcome alarm: unlike a lane's dead-man check, a finding pings
`/fail`, with the report as the ping's body (readable on the check's page). The launcher pings
`/start` first and success only on exit 0, so a watcher that stops is reported too, after the
grace. The installer creates the check (`unique` by name, schedule `55 * * * *` in
`America/New_York`, grace 1,800 seconds, every channel). The first GitHub read that fails ends the pass,
so a rate-limited API is not read again; content one fork serves badly makes only that fork
`unreadable`, and the pass goes on.

**`waiting_on_person`** is a sync whose `security review` failed while the fork's alert issue
("Fork-sync: a security review is holding an upstream sync") is open and was raised or commented
on since the sync's head arrived (`notify` comments once per new head, so an issue left open by an
older sync doesn't count). It isn't stuck: the issue already alerts. Do what the issue says.

**`stuck`**, with each reason that applies:

| Reason | When | First thing to check |
|-|-|-|
| `no_checks` | An open sync pull request has no completed `ci.yml` run at its head 60 minutes after the head was pushed or the pull request opened, whichever is later. The head's committer date stands for the push: the sync builds that commit just before pushing it. A run starts within seconds and builds in minutes, so an hour means GitHub ran nothing, as for a conflicted pull request. | The pull request's mergeability (a conflict runs no `pull_request` workflow), then the Actions tab for a run stuck queued. |
| `not_landing` | Both required checks passed on the latest `ci.yml` run at the head, and the pull request is still open six hours after they completed: six inert landing passes have had it. | `~/Library/Logs/inert-landing.log` for the pull request's hold reason; a policy read failure (`inert_landing_policy_source_unreadable`) means the production orchestrator's change-manager bearer. |
| `review_unavailable` | `security review` didn't reach a verdict (its comment says the review couldn't run, there is no comment of this head, or the job was cancelled or timed out) and no alert issue is open. | The job log; re-run the failed `security review` job. |
| `held_without_alert` | `security review` holds findings but no alert issue is open, so `notify` failed to raise it. | The `notify` job's log; then read the review comment as if the alert had opened. |
| `build_failing` | `build and test` didn't pass on the latest run at the head. Nothing alerts on this, since `notify` follows the review only. | The job log; upstream's release may need a different build or test command in the declaration. |
| `sync_failing` | The latest `upstream-sync.yml` run concluded `failure`, `timed_out` or `startup_failure`, or no run started in 36 hours (one missed day; also GitHub disabling an idle repository's schedules). | The run's log; for silence, whether the workflow is disabled on the Actions tab. |
| `not_syncing` | No sync pull request is open, yet upstream published a release matching `release_tag_pattern`, newer than `main`'s manifest version, more than 36 hours ago. The review script's own release selection decides (`next_release`), so a prerelease or older tag never counts. | The latest `upstream-sync.yml` run, then whether the pull request was closed by hand. |

Reasons are reported together, and an open alert issue never hides one. The thresholds are module
constants in `src/fork_sync/watch.py`, each with its reason.

## Acknowledge a finding

A holding finding a person has read and accepts is acknowledged by its fingerprint, which the
report prints. A `reported` row needs none, and an acknowledgement of one changes nothing. The
fingerprint covers the finding's kind, its path and the exact content it is about: the changed
file's blob, a binary's sha256, the lockfile and the package entry, or, for a model
finding, the path's blob (or the whole reviewed diff for a path that did not change). An
acknowledgement covers that content and nothing later: if the file changes again, the finding
returns with a new fingerprint.

A model finding's fingerprint leaves out its wording and its severity, because the model words
the same concern differently on every run. Its acknowledgement must instead name `severity`, the
highest it accepts (`low`, `medium` or `high`). A rewording at or below that severity stays
acknowledged, and a concern rated higher fails again; the report marks it "acknowledged up to"
the accepted severity. An acknowledgement of a model finding without `severity` covers nothing,
and `severity` is ignored on every other kind. The model's severity is read in lower case, and one
outside the three counts as `high`. Name the severity you read, not `high` by default: an
acknowledgement at `high` covers every model concern about that content until it changes. Each
fingerprint is acknowledged once; a second entry for it fails the review. For a report row
`ai (medium) | scripts/fetch-docs.cjs | Headings from the remote index feed path.join …`:

```toml
[[ack]]
fingerprint = "4922f33d224f0111"
by = "devon"
date = 2026-10-22
severity = "medium"
reason = "Build-time docs script the fork's build never runs; reported upstream."
```

`fork-sync ack` does the steps by hand, below, after one approval:

1. `fork-sync ack <fork> <pr> --reason "<why>" --by "<who>"` changes nothing. `--by` is
   required: it is what the acknowledgements record as having accepted the findings. It reads the
   sync pull request's latest review comment, posted by `github-actions[bot]` (a comment from
   anyone else is ignored), and lists every row still marked failing, then the `[[ack]]` entries
   it would add, the entries it would raise, and the command to record them. Every control
   character in upstream's or the model's text is printed as its escape (`\x1b`), so none can
   move the cursor or hide a row. It refuses a pull request that isn't open, isn't the
   `upstream-sync` branch, or whose review is of an older head, and one whose review couldn't
   run.
2. Read the rows. If you accept them, run the printed command: it adds `--yes --expect
   <fingerprints>`. It re-reads everything and refuses unless the failing fingerprints are exactly
   the ones shown. It writes one entry per fingerprint to `.github/fork-sync-acks.toml` on a new
   branch from `main` (two model concerns about one file share a fingerprint, so they become one
   entry at the higher severity), and opens a pull request. It refuses if `main`'s
   acknowledgements changed since it read them, or if a pull request from an earlier run of the
   same acknowledgement is still open: merge or close that one first.
3. It waits for the latest run of the fork's `ci.yml` at that pull request's head, then
   admin-merges it only if its diff is only the acknowledgements file at the commit it pushed,
   that run's `build and test` passed and its `security review` reached a verdict (`success`, or
   `failure` on the acknowledgements file by design; `cancelled`, `skipped` or a missing job
   refuses), and every failing row of its own review is on the acknowledgements file. Otherwise
   it stops and leaves the pull request open. After merging it dispatches `upstream-sync.yml` and
   prints the merge commit.

A model finding acknowledged below the severity a new review gives it fails under the same
fingerprint. `ack` raises that entry's `severity` in place, upward only, and appends `; raised to
<severity> on <date> (<pull request title>)` to its reason; `--expect` covers raises too. Any
other fingerprint already in the file is refused, for a person to edit.

The steps by hand:

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
   that package version changes, and a newly published critical advisory fails the check again.
   Only a critical advisory needs one. For a report row `GHSA-xvch-5gv4-984h minimist 1.2.5:
   Prototype Pollution … (critical; fix: minimist 1.2.8)`:

   ```toml
   [[ack]]
   fingerprint = "<the fingerprint the report prints for that row>"
   by = "devon"
   date = 2026-10-24
   reason = "GHSA-xvch-5gv4-984h: minimist is a build-time dependency only; upstream still pins 1.2.5."
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

Onboarding is one command, rerun until it finishes (ADR-0056 amendment 5):

```
fork-sync onboard <fork> --config <declaration.toml> [--baseline-tag <tag>] [--discard-fork-commits] [--reset-secrets]
```

Work from the orchestrator's main tree venv (`~/Projects/orchestrator/.venv/bin/fork-sync`) or a
worktree. Each run reads where the fork stands, does the next steps it can, and prints one line
per step: `done` (nothing to do), `did now`, `waiting` (something did not finish within 45
minutes; rerun to resume) or `needs you`, followed by what to do. It stops at the first step that
is waiting or needs a person, and exits 0 only when every step is done. Rerunning continues from
where it stopped; a step whose check says done changes nothing. Each step below gives its reason
and the manual fallback, which is what to do when the step refuses. `fork-sync check <fork>`
reports where the fork stands at any time.

Secret values come from BWS through the broad machine account's token, read from the login
Keychain item `Claude` / `BWS_ACCESS_TOKEN_VPS_BACKUP` (`BWS_ACCESS_TOKEN_BROAD` overrides it),
and go to `bws` in its environment and to `gh secret set` on stdin: no value is printed or passed
as an argument.

1. **Decide the fork belongs on the lane.** The lane is for a fork you take upstream's releases
   into wholesale, where a landing on `main` changes nothing already serving. This is yours;
   `onboard` assumes it.
2. **The App is installed** (a person's). Only a person can install the App
   `fork-secure-upstream-sync` on the fork (Only select repositories). `onboard` asks GitHub
   whether its installation covers the fork, as the App: it signs a short-lived JWT with the
   App's key from BWS (`998b817e-…`) and reads `GET /repos/<fork>/installation`. Until it does,
   it prints the install link and the BWS confirmation `fork-sync human-steps` prints, and
   stops.
3. **Baseline the fork**, if upstream's workflows are on its `main` and the declaration is not.
   Pass `--baseline-tag <tag>`, the tag the fork's manifest version matches; without it, the step
   stops and asks for one. It runs `fork-sync baseline`, which removes upstream's whole
   `.github/`, so nothing upstream planted there (an acknowledgements file, a script) becomes the
   fork's. It refuses, and prints the commits, if `main` has commits no upstream branch or tag
   contains. Read them; if the fork is to drop them (for example, local hardening the lane doesn't
   carry), rerun with `--discard-fork-commits`. It force-pushes `main`, so `onboard` stops if
   `main` is protected: save the protection with `gh api repos/<fork>/branches/main/protection`,
   remove it, and rerun; step 8 applies the lane's own. A fork whose `main` already carries the
   declaration is never baselined. If `tool-installer` already serves the fork as a plugin, its
   local copy (`~/Developer/devon-plugins/<tool>`) still holds the old history, and every nightly
   pass fails `git pull --ff-only` until it's reset: tag its `HEAD`, then `git reset --hard
   origin/main` there. The copy must be clean and on `main`. Manual fallback: `fork-sync baseline
   <fork> --tag <tag> --confirm`.
4. **Scaffold.** Write the declaration and pass it as `--config`. `onboard` runs `fork-sync
   scaffold` unless a `fork-sync-scaffold` pull request is open. If the declaration sets
   `generate_lockfile`, `main` has no lockfile yet, so `build and test` can't install: when the
   scaffold branch has none, `onboard` clones it, checks that its review script is the one
   `scaffold` renders, fetches the baseline tag from upstream, runs that script's `lock <tag>`,
   and commits and pushes the staged lockfile. It needs `--baseline-tag` for this and stops
   without it. Manual fallback: `fork-sync scaffold <fork> --config <file>`; for the lockfile, in a
   clone of the `fork-sync-scaffold` branch, `git fetch --no-tags <upstream-url>
   "refs/tags/<tag>:refs/tags/<tag>"`, `python3 .github/scripts/fork_sync_review.py lock <tag>`,
   then commit and push. If the old lane left files `scaffold` does not render (for rtk,
   `.github/scripts/ai_security_review.py`), push a commit removing them to the scaffold branch;
   step 6 then refuses the merge, which is yours to make.
5. **Set the secrets and register them.** `onboard` sets `APP_ID` (the App's id, 4094707),
   `APP_PRIVATE_KEY` (BWS `998b817e-…`, which holds base64 of the PEM; each Actions copy holds the
   decoded PEM, so it is decoded in-process) and `ANTHROPIC_API_KEY` (BWS `b74bf8b3-…`) with `gh
   secret set NAME --repo <fork>` and the value on stdin, never `--body` (`--body -` stores a
   literal dash). It reads every value before setting any, and skips a secret the fork already
   has unless `--reset-secrets`. Then it registers the fork's copies in factory-runner's
   `.cred-consumers.toml`: a `gh-actions-secret` consumer (`repo = "<fork>"`) named
   `APP_PRIVATE_KEY` in the `fork-sync-app-private-key` entry and one named `ANTHROPIC_API_KEY` in
   the `anthropic-api-key` entry. Every fork adds to both. `APP_ID` is not a credential, so it is
   not registered. Unless both consumers are already there, it opens a pull request adding exactly
   them (the result must parse to the old registry plus those two), waits for every check
   factory-runner's `main` requires to pass at the commit it pushed, and squash-merges it; a
   failed check leaves it open and stops. It refuses unless the fork has committed this
   declaration (on `main` or its scaffold branch) and the App is installed on it. The census
   comments above each entry are not edited. Manual fallback: the same edit by hand.
6. **Merge the scaffold pull request** once `build and test` passes on it. Its `security review`
   fails by design: the review runs from the base, and the base has no review script yet. If
   `main` is already protected (rtk), `security review` is not yet a required check, so the
   failure doesn't block the merge. `onboard` admin-merges it only when its diff is exactly the
   rendered lane files, the declaration and (when `main` has none) the empty acknowledgements,
   plus the lockfile when the declaration generates one, each added or changed, and the rendered
   files and the declaration carry exactly the bytes `scaffold` writes. A lockfile names what gets
   installed, so its bytes must be what the baseline tag resolves to: `onboard` resolves it again
   in a scratch clone with the review script's `lock <tag>` (pinned to the tag's commit date, so
   the same tag gives the same bytes) and compares. That needs `--baseline-tag`. Anything else stops with
   the reason: read the pull request and merge it yourself with `gh pr merge <number> --repo
   <fork> --merge --admin`.
7. **Run the first sync.** Only a sync created after `main` received the declaration counts,
   since an older one (a fork with an earlier lane) ran from some other `main`. That moment is the
   first commit on `main` that touched `.github/fork-sync.toml`: when the pull request carrying it
   merged, or the commit's own date if it was pushed straight to `main`, so a fork scaffolded
   another way still passes. The step is done once any such sync succeeded; a later failure is
   the lane's to report. With none, `onboard` waits for one already running, or dispatches
   `upstream-sync.yml`. Both checks then run on the sync pull request, from the scaffolded
   `main`.
   A sync run that failed stops with its link: fix the cause, run `gh workflow run
   upstream-sync.yml --repo <fork>`, and rerun.
8. **Protect `main`.** `onboard` runs `fork-sync protect` once the sync pull request's CI run has
   reported both checks. `protect` refuses until an open pull request has reported both `build
   and test` and `security review` (read from the Actions jobs API). It then allows merge
   commits, sets `squash_merge_commit_message` to `COMMIT_MESSAGES`, deletes branches on merge,
   turns issues on for the alert, and requires both checks from the GitHub Actions app (`app_id`
   15368, so no other status poster can satisfy them) with `strict: false`, no required reviews
   and no admin enforcement. If the first sync opened no pull request (the fork is already at
   upstream's newest release), nothing can show the checks reporting: the step reports `waiting`
   for the next upstream release, and `onboard` goes on with the steps that don't depend on it
   (the landing policy, App Brain, the confirmation). The run ends unfinished, exit 1, naming the
   step, until a rerun after that release protects `main`.
9. **Review the sync pull request** (a person's). If its review holds, `onboard` stops and prints
   the `fork-sync ack <fork> <pr> --by <who> --reason <why>` dry run. Run it, read the rows, and
   acknowledge what you accept; do not acknowledge what you have not read. `onboard` never
   approves a finding. A review that couldn't run stops with "re-run its `security review` job".
10. **Release the landing policy.** The fork must be in change-manager's
    `inert_landing.repositories`, with `fork-secure-upstream-sync[bot]` in both `permitted_authors`
    and `non_ecosystem_authors` (ADR-0041). Each fork stays an explicit, versioned grant. When the
    served policy lacks the fork, `onboard` clones `AlobarQuest/change-manager`, runs `uv run
    python scripts/release_inert_repository.py --repository <fork, lower-cased> --reason <text>`
    there, which writes the next version. N is read from the clone's `app/deploy_policy.py`
    (`CURRENT_VERSION`), never from what is served: change-manager's `main` can be ahead of its
    deploy. The script exits 0 with one JSON line, `{"status": "released", "version": <N+1>,
    "repository": ...}` or `{"status": "already_present", "version": <N>, ...}`; `onboard`
    refuses any other line, another repository, any other version, and every nonzero exit (2
    included: it is also uv's and Python's own usage exit). `already_present` counts as done once
    change-manager serves the fork. A pull request an earlier run opened is reused only when its
    head's version is the one after `main`'s. The reason is passed
    without quotes, backslashes or control characters and with no long unbroken run, which the
    script refuses. The change must touch only the files the script writes
    (`app/deploy_policy.py` and `app/deploy_policy_history.py`, and possibly
    `contracts/ratified_rollout_policy.json`), checked in the clone before committing and again
    on the pull request before merging. It commits that, opens a pull request, waits for the
    checks change-manager's `main` requires, squash-merges it (staging exactly the checked
    paths), and waits until the served policy is version N+1 or later and lists the fork. The
    running revision (`https://change-mgr.alobar.net/api/health`) is reported, not waited on. It refuses unless the declaration is committed and
    the App installed. A listed fork missing an
    author stops: that is a person's edit. The policy is read with `FORK_SYNC_CHANGE_MANAGER_TOKEN`
    when set, otherwise with the read bearer from BWS (`314f276d-…`), as the inert landing lane
    reads it. Merging redeploys change-manager.
11. **Record App Brain's landing** (an agent's). The write needs App Brain's write path, which has
    no credential outside an agent session. `onboard` prints the exact
    `record_default_branch_landing(github_repo=<fork>, landing="inert", evidence=...)` call, with
    evidence naming `main`'s workflows, for an agent session to make. It counts the step done
    only when App Brain answers `inert`, which it can read only with `FORK_SYNC_APP_BRAIN_URL` and
    `FORK_SYNC_APP_BRAIN_KEY` (App Brain's read key) set.
12. **Add the installer row** in `src/tool_installer/tools.py` if the fork is a tool this machine
    installs. That is a separate change per tool; `onboard` says whether a row exists.
13. **Confirm.** `onboard` ends by running `fork-sync check <fork>` and printing its summary; the
    run is done when nothing is `missing`.

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
