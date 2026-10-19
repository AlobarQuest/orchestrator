# Onboarding a fork onto the upstream sync lane

This page explains how to put a fork on the fork-sync lane: a daily workflow opens one rolling pull
request that brings the fork up to upstream's newest release, two required checks build and review
it, the inert landing lane lands it, and, for a tool installed on the operator machine,
`tool-installer` installs it (ADR-0042). `AlobarQuest/rtk` is the reference fork.

The `fork-sync` console script automates every step it can and reports the rest:

| Command | Writes | What it does |
|-|-|-|
| `fork-sync check <fork>` | nothing | Reports each step below as `ok`, `missing`, `human` or `info`. Exits 1 while anything is `missing`. |
| `fork-sync human-steps <fork>` | nothing | Prints the steps only a person can do, then the agent steps. |
| `fork-sync baseline <fork> --tag <tag> --confirm` | the fork's `main` | Resets `main` to an upstream tag with upstream's `.github/workflows` removed. |
| `fork-sync scaffold <fork> --config <file>` | a branch and pull request | Commits `.github/fork-sync.toml` and the rendered workflows and review script. |
| `fork-sync protect <fork>` | repository settings | Applies the landing settings and branch protection on `main`. |

Each writing command refuses before it writes when its precondition does not hold.

## The declaration

Each fork commits `.github/fork-sync.toml`. The workflows and `fork-sync check` read the same file
through one parser (`src/fork_sync/fork_sync_review.py`), and unknown keys are refused.

```toml
upstream = "morluto/rea"                                  # owner/name
ecosystem = "npm"                                         # cargo | npm
release_tag_pattern = '^rea-agents-(\d+\.\d+\.\d+)$'      # group 1 is the version
build_commands = ["npm run build"]                        # optional; ecosystem default
test_commands = ["npm run test:run"]                      # optional; ecosystem default

[review]
paths_watched = ["install.sh", ".claude/*"]   # any change is a finding
trust_critical = ["src/mcp/*"]                # reviewed first, most closely
audit_commands = ["npm audit --audit-level=high"]   # optional; ecosystem default
model = "claude-sonnet-5-5"                   # optional
max_diff_chars = 400000                       # optional
```

Globs are `fnmatch` patterns, so `*` crosses directories. The ecosystem defaults are:

| Ecosystem | Version read from | Build | Test | Audit |
|-|-|-|-|-|
| `cargo` | `Cargo.toml` | `cargo build --locked --all-targets` | `cargo test --locked` | `cargo audit` |
| `npm` | `package.json` | `npm run build` | `npm test` | `npm audit --audit-level=high` |

Read the build and test commands from the fork's own manifest. Upstream's scripts are data: choose
commands that build and test without network side effects. For `npm`, CI always installs with
`npm ci --ignore-scripts`, so no install-time script runs.

## What the lane does

- **`upstream-sync.yml`** runs daily at 06:00 UTC and on demand. It mints a token from the App
  `fork-secure-upstream-sync`, picks upstream's newest published release whose tag matches
  `release_tag_pattern`, and compares its version with the fork's manifest. When they differ, it
  rebuilds the `upstream-sync` branch from the tag, restores the fork's whole `.github/`, and opens
  or edits one rolling pull request. Submodule gitlinks stay as upstream has them; nothing fetches
  their content. It does not review.
- **`ci.yml`** runs on every pull request and on pushes to `main`. Its two job names are the
  required checks:
  - `build and test` runs the configured build and test commands.
  - `security review` runs `.github/scripts/fork_sync_review.py review` on pull requests.

### Why the review is a required check

Before this lane the AI review ran inside the sync workflow with `continue-on-error`, so a review
that failed or flagged something still left a mergeable pull request. As a required check the
review gates the landing: nothing lands unless it passes, and a review that cannot run fails.

The review reports these findings:

- Any change under `review.paths_watched`.
- Any submodule pointer or `.gitmodules` change.
- Any added or changed binary file, with its sha256.
- For `npm`, any change to a `package.json` `scripts` block, or to a file an install-time
  lifecycle script runs.
- Any failing audit command.
- What the model reports from the diff, trust-critical files first. A diff over `max_diff_chars` is
  itself a finding and is not sent.

It writes a report to the job summary and to one rolling pull-request comment, then exits:

| Exit | Meaning | Check |
|-|-|-|
| 0 | No unacknowledged finding | Passes |
| 1 | At least one unacknowledged finding | Fails |
| 2 | The review could not run: no key, API error, unparseable verdict | Fails |

## Acknowledge a finding

A finding a person has read and accepts is acknowledged by its fingerprint, which the report
prints. The fingerprint covers the finding's kind, path, the head's version and the content, so it
expires with the release.

1. Add an entry to `.github/fork-sync-acks.toml` on the fork's `main`:

   ```toml
   [[ack]]
   fingerprint = "3f9c0a1b2c3d4e5f"
   by = "devon"
   date = 2026-10-19
   reason = "Prebuilt Windows binary, sha256 matches upstream's release asset."
   ```

2. Run the sync again: `gh workflow run upstream-sync.yml --repo <fork>`. The rebuilt branch carries
   the new `.github/`, and the re-run review reports the finding as acknowledged.

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
   refuses, and prints the commits, if `main` has commits no upstream branch or tag contains.
4. **Scaffold.** Write the declaration, then run `fork-sync scaffold <fork> --config <file>`. It
   prints the pull request number. If the old lane left files `scaffold` does not render (for rtk,
   `.github/scripts/ai_security_review.py`), push a commit removing them to the
   `fork-sync-scaffold` branch.
5. **Set the secrets** `APP_ID`, `APP_PRIVATE_KEY` and `ANTHROPIC_API_KEY` from BWS, in-process,
   with the value on stdin and no `--body` flag (`gh secret set NAME --repo <fork>`). Never pass
   `--body -`, which stores a literal dash. Register each copy in factory-runner's
   `.cred-consumers.toml`.
6. **Merge the scaffold pull request** once both checks have reported on it.
7. **Protect `main`.** Run `fork-sync protect <fork>`. It refuses until an open pull request has
   reported both `build and test` and `security review` (read from the Actions jobs API), then
   allows merge commits, sets `squash_merge_commit_message` to `COMMIT_MESSAGES`, deletes branches
   on merge, and requires both checks with `strict: false`, no required reviews and no admin
   enforcement.
8. **Run the first sync:** `gh workflow run upstream-sync.yml --repo <fork>`.
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
- The review script on a pull request comes from that pull request's head. The sync restores the
  fork's own `.github/`, so a sync pull request runs the fork's script, never upstream's.
