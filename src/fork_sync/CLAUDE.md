# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Fork sync (`src/fork_sync/`): the fork security review, CI and upstream sync

- A fork's `security review` reads its script, declaration and acknowledgements from the pull
  request's BASE, so a change to the gate takes effect only on `main`, and a pull request that
  changes the gate is judged by the old one. Such a pull request lands by an admin merge after a
  person reads its report (`main` is protected without admin enforcement for this); for an
  acknowledgements-only change, by `fork-sync ack`, which merges only when the diff is that file
  alone and its review fails only on it; and for the scaffold, by `fork-sync onboard`, which
  merges only when the diff is exactly the rendered files with their rendered bytes after `build
  and test` passed. Never weaken the protection to land it. Detail:
  `docs/operations/onboarding-a-fork.md`.
- A fork's `ci.yml` must trigger only on `pull_request` and `push`: a manual or scheduled run on a
  pull request's branch reports the required jobs as skipped, which branch protection counts as
  passing. Scheduled work (the weekly audit) lives in its own workflow. The held-sync alert is an
  issue, so the fork needs issues on (`fork-sync protect` sets it). Detail:
  `docs/operations/onboarding-a-fork.md`, "The alert".
- The sync commit's parents are the release tag and `origin/main` (`upstream-sync.yml`, built
  with `git commit-tree`). Without `main` as a parent, a file only the fork has (an owned
  lockfile) conflicts on every release, and GitHub then runs no `pull_request` checks, so no
  required check ever reports. History: ADR-0056 amendment 2.
