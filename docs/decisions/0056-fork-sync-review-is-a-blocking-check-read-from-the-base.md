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
- claude-octopus uses the shared App but not this gate: it keeps its own non-blocking
  `ai_security_review.py` and has no `fork-sync.toml`. Bringing it onto the lane follows the
  runbook.
