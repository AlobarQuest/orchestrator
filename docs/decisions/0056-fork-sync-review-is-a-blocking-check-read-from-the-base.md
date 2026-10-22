# ADR 0056 — The fork-sync review is a blocking check read from the base

**Date:** 2026-10-20
**Status:** Accepted (Devon, 2026-10-19, in the onboarding session: "Shared app", "Rebuild main
from latest release tag", blocking review "Yes for both", registry in factory-runner "Agreed", and
the change-manager release outside the production window)

## Context

Devon keeps forks of upstream tools he runs on the operator machine. rtk was the first: a
scheduled workflow rebuilt an `upstream-sync` branch from upstream and opened a pull request, and
an AI review ran inside that workflow with `continue-on-error`. A review that failed, or that
flagged something, still left a mergeable pull request, so the review gated nothing.

Onboarding rea (AlobarQuest/rea, a reverse-engineering toolkit installed as a CLI and an MCP
server) needed the same lane, and Devon asked for it to be secure enough to use. Doing it by hand a
second time showed that the steps lived only in rtk's history. The upstream code is untrusted: a
fork lands it on a machine that holds infrastructure credentials.

## Decision

1. **Upstream reaches a fork only as a release tag.** `upstream-sync.yml` rebuilds the
   `upstream-sync` branch from upstream's newest published release whose tag matches the fork's
   `release_tag_pattern`, and only when its version is greater than the fork's. It restores the
   fork's whole `.github/` from `main`, so a sync can't change the fork's workflows or its gate.
   A fork joining the lane first rebuilds `main` from a chosen release tag (`fork-sync baseline`):
   rea started at 6.6.0.
2. **One GitHub App serves every fork.** `fork-secure-upstream-sync` (id 4094707) mints the sync
   workflow's token and authors the rolling pull request. Its private key lives in BWS
   (`998b817e-…`), and each fork holds a copy as an Actions secret.
3. **The security review is a required check, and blocks landing.** `ci.yml`'s `build and test`
   and `security review` jobs are both required, from the GitHub Actions app only (`app_id`
   15368). The review exits 0 (pass), 1 (an unacknowledged finding) or 2 (could not run), and both
   1 and 2 fail the check. This applies to rtk as well as rea.
4. **The gate is read from the base, never the head.** The review script, the fork's
   `.github/fork-sync.toml` and its `.github/fork-sync-acks.toml` come from the pull request's
   base. The job checks out only the base and reads the head's objects with git, with replace
   objects and attributes disabled, so nothing from the pull request runs in the job that holds
   `ANTHROPIC_API_KEY`.
5. **Every changed path is accounted for.** A path reaches the model, or it carries a
   deterministic finding (watched paths, binaries detected from bytes, lockfile and manifest
   risks, submodules, git configuration files, and one finding per audit advisory). A path that
   is neither fails the review. Only paths that differ from the base tip are reviewed.
6. **A person accepts a finding by acknowledging its fingerprint.** Fingerprints are bound to
   content: a file's blob, a binary's sha256, or, for an audit advisory, the advisory, package
   and installed version. A change to that content brings the finding back.
7. **A pull request that changes the gate lands by an admin merge.** It's judged by the gate on
   `main`, which it is changing, and the model flags the change. `main` is protected without
   admin enforcement so a person can merge it after reading the report. Protection is never
   loosened to land one.
8. **Landing is the inert landing lane's.** A fork that passes both checks is merged by
   `com.devon.inert-landing` under change-manager's landing policy (the fork in
   `inert_landing.repositories`, the App's bot in the permitted authors), and App Brain records
   its default-branch landing as `inert`.
9. **The fork's copies are registered in factory-runner.** `.cred-consumers.toml` holds the
   `fork-sync-app-private-key` and `anthropic-api-key` entries, one Actions-secret consumer per
   fork. infraops-mcp-server's classes for them are `executor: false`: they're rotated by hand,
   and neither is `rotated_by_sds`.
10. **The procedure is a tool and a runbook.** `src/fork_sync/` (`baseline`, `scaffold`,
    `protect`, `check`, `human-steps`) renders and checks the workflows, and
    `docs/operations/onboarding-a-fork.md` is the procedure. A fork installed on the operator
    machine also gets a row in `src/tool_installer/tools.py`; npm tools install with lifecycle
    scripts disabled (ADR-0042 amendment 1).

## Alternatives considered

- **Keep the review advisory inside the sync workflow.** Rejected: it is the arrangement that
  gated nothing.
- **Read the gate from the head.** Rejected: a pull request could then rewrite the script, the
  declaration or the acknowledgements it is judged by. The first build did this; it was fixed
  and pinned by a test that runs a planted head against the base's gate.
- **One App per fork.** Rejected by Devon in favour of a shared App: one key to rotate and one
  installation to audit. The cost is that a leaked key reaches every fork the App is installed
  on.
- **Sync upstream's default branch instead of release tags.** Rejected: a release is the unit
  upstream vouches for, and a version comparison stops the lane from syncing backwards.
- **Path-filter the required check.** Rejected: a filtered required check never reports on a pull
  request it skips, and that pull request can't land.

## Consequences

- Both onboarded forks have run the full path. rea #2 (6.6.0 to 6.7.0) passed with no findings and
  landed on 2026-10-19. rtk #10 (v1.0.1) passed with every finding acknowledged and landed the
  same day. The tool installer installed both on 2026-10-20 and every probe passed.
- rtk's first review carried 46 acknowledgements, including 7 RustSec advisories at their exact
  versions. Each new advisory, or a change to an acknowledged package version, fails the check
  again and needs a person.
- Two adversarial review rounds found 13 defects in the gate before rollout, and the first runs
  found two more (audit acknowledgements bound to the whole lockfile, and the fork's own `.github/`
  counted as part of the change). All were fixed before the landings.
- An instruction planted in upstream's diff can try to talk the model out of a finding. The
  deterministic findings don't pass through the model, so they hold regardless.
- A pull request without the fork's secrets, such as Dependabot's, fails `security review` and
  can't land on a fork.
- claude-octopus joined the gate on 2026-10-20 by the runbook, rebuilt from upstream's v11.14.1
  with its old sync, hardening patches and CI discarded. Its first sync (#31, v11.14.2) passed
  `security review` with no findings, and the inert landing lane landed it as `14edb140`.

## Amendment 1 (2026-10-20): no hardening in the lane; a held sync raises an alert

**Status:** Accepted (Devon, 2026-10-20: "We should drop the auto-hardening for now, from all
fork-sync repos. If there is a security concern, then that version should not land in my
environment," and "Yes, but also the optional Item #3").

- **The lane never changes upstream's code.** A fork's `main` is upstream's release plus the
  fork's `.github/`. A security concern holds that version; it isn't patched out. A hardening
  process a person runs against a fork may come later, outside fork-sync. claude-octopus, which
  patched upstream's shell scripts during its old sync, joined the lane without its patches.
- **A failed review raises an alert.** Before this, a held sync was visible only in the landing
  lane's log, and claude-octopus #29 sat held for nine days unnoticed. Each fork's `ci.yml` gains
  a `notify` job that opens, comments on or closes one issue on the fork, mentioning the owner.
  It needs no credential: Healthchecks has no free check, and a GitHub issue reaches the owner
  through GitHub's own notifications.
- **`main` is audited weekly.** A `weekly audit` job, in a workflow of its own so it can never
  report a required check on a pull request, audits `main`'s dependencies against `main`'s
  acknowledgements. An advisory published against an installed version is then found between
  releases, and alerts the same way.
- **`npm-no-deps`** is a third ecosystem for a fork whose root `package.json` declares no
  dependencies: nothing is installed or audited, and a declared dependency is a finding.
- **A one-time review of each fork's current code.** The gate reviews only what each sync
  changes, so the code each fork started from was never reviewed. It is reviewed once, and the
  result is reported to Devon; it is information, not gate state.

## Amendment 2 (2026-10-21): a monorepo upstream, and a lockfile the fork owns

**Status:** Accepted (Devon, 2026-10-21, choosing "the fork owns the lockfile" from three options
for n8n-as-code).

n8n-as-code was the first upstream that didn't fit the lane. It is an npm workspace monorepo
whose root `package.json` is private and pinned at `0.2.0`, while the version a release tag names
lives in `packages/cli/package.json`. Its `.gitignore` also excludes `package-lock.json`, so no
release records the dependency versions it was built with. Without a lockfile, `npm ci` and the
audit can't run, every build resolves version ranges against the registry at that moment, and the
review never sees a dependency change, which is the supply-chain path the lane exists to close.

- **`version_manifest`** names the manifest a sync's version is compared against. It defaults to
  the ecosystem's manifest at the root. Comparing against a root that never moves would sync every
  release, an older one included.
- **`generate_lockfile = true`** (npm only) makes the fork own its lockfile. After the sync
  restores the fork's `.github/`, `fork_sync_review.py lock <tag>` resolves `package-lock.json`
  with `npm install --package-lock-only --ignore-scripts` and commits it on the sync branch. The
  lockfile sits at the root, so CI, the audit, the review and the weekly audit read it unchanged.
- **npm never runs in upstream's tree.** A project `.npmrc` is upstream's to write, and its `git=`
  key names a program npm runs to fetch a git dependency, even with `--ignore-scripts`; the sync
  job holds the App token in `.git/config`. An adversarial review reproduced that. So `lock`
  resolves in an empty scratch directory that holds only the tag's tracked `package.json` files,
  read from git objects, with empty user and global configuration, a fixed registry, and `git`
  set to `false`, so a git dependency fails the sync rather than being fetched. The lockfile is
  written back with an exclusive create, which never writes through a link. Resolving there
  instead of in the tree gave a byte-identical lockfile at v2.7.0.
- **Resolution is pinned with `--before` to the tag's commit date,** on an exact Node version.
  The lockfile records what upstream's own users received at that release, and a rerun writes
  the same file: two resolutions at v2.7.0 were byte-identical. A lockfile that moved daily would
  make every rebuild a new tree, which would re-review and re-alert a held sync every day. A
  commit dated in the future would unpin it, so it fails the sync.
- **A tag that ships its own lockfile (`package-lock.json` or `npm-shrinkwrap.json`) fails the
  sync** rather than being silently overwritten, so the declaration is revisited when upstream
  starts committing one.
- **A workspace's folder entry isn't a finding; its link is, once.** npm keys each workspace
  folder by its path and links it into `node_modules`. The folder entry installs under no name,
  and its version moves every release, so the alias check applies only to `node_modules/` entries.
  Every new or changed link stays a finding: a link is how an in-tree package can take a
  registry package's name, which a second review round reproduced against an earlier rule that
  trusted the declared `workspaces` patterns. A workspace link names only a path, so it is
  acknowledged once and stays unchanged across releases. At v2.7.0 that is nine links.
- **`lock` refuses a tree path with a `..`, `.` or empty part.** Git accepts a tree entry named
  `..` with only an fsck warning, and copying it would write above the scratch directory.
- **Every fork's sync workflow gains a Node setup and the `lock` step,** which does nothing
  unless the declaration sets `generate_lockfile`. `fork-sync check` reports the older workflow
  as drift, so each onboarded fork is re-scaffolded to the new template.
- **The sync commit has `main` as its second parent.** The first sync after this amendment,
  n8n-as-code #3 (v2.4.1), conflicted on `package-lock.json`. The branch was built on upstream's
  tag, whose history never held the lockfile that the bootstrap committed to `main`, so both sides
  added it with different content. GitHub runs no `pull_request` workflow on a conflicted pull
  request, so neither required check reported. With `main` as a parent, `main` is an ancestor of
  the branch and the merge is clean for every release. The tree is unchanged: the release, the
  fork's `.github/`, and the lockfile. A rebuild also pushes when `main` has moved and the tree
  hasn't, so the branch keeps `main` as an ancestor. Two consequences follow. Landing a sync makes
  `main` exactly the sync's tree, so a file only `main` holds outside `.github/` is deleted, and
  the review shows the deletion. That is amendment 1's rule ("upstream's release plus the fork's
  `.github/`"), which the three-way merge used to blur. The review's old side for the lockfile is
  `main`'s own, so only what a release changes in it is a finding. The bootstrap lockfile was
  never reviewed by the gate, so it belongs to the fork's one-time baseline review.
- **A release tag may start with `@`.** npm monorepos tag each package by its scoped name
  (`@n8n-as-code/n8n-manager-v0.7.1`), which the tag rule refused. `@` is valid in a ref except
  as `@{`, and `{` stays refused.

## Amendment 3 (2026-10-22): a model finding is acknowledged by content and a severity ceiling

**Status:** Accepted (Devon, 2026-10-22, choosing option 1 of three: content plus a severity
ceiling, over content alone and over re-acknowledging each run).

A model finding's fingerprint included its wording and severity, so an acknowledgement covered
one phrasing of one run. n8n-as-code #3 (v2.4.1) showed that this never converges: each of three
review runs reworded the same concerns (a lockfile downgrade, a docs downloader's path handling),
every rewording failed the check under a new fingerprint, and each needed a person again.

- **A model finding's fingerprint is its path and the content it is about,** the path's blob, or
  the whole reviewed diff for a path that did not change. Wording and severity are out of it.
- **Its acknowledgement names `severity`, the highest it accepts.** A finding on that content at
  or below the severity is covered; a higher one fails, and the report marks it "acknowledged up
  to" the accepted severity. An acknowledgement without `severity` covers no model finding, and a
  severity outside `low`, `medium` and `high` is never covered. Other kinds are unchanged.
- **The cost:** a different concern about the same unchanged content, at or below the accepted
  severity, is covered without a person reading it. The content is exactly what the person read,
  and any change to it brings the finding back.
- Existing model acknowledgements no longer match (their fingerprints included the wording), so
  each fork's next review raises its model findings once more, to be acknowledged with a
  severity. Each onboarded fork is re-scaffolded.

