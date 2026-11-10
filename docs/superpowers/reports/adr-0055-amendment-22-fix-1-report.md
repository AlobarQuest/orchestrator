Outcome: done

# Report: ADR-0055 amendment 22, fix round 1

## What I built

This fix round is for draft PR #647, on branch
`hq/adr-0055-amendment-22-lane-credential-standing-approval`. It's docs only. I started the
worktree branch at the PR head `81371d62` with `git switch -C`, because `git reset --hard` was
denied and the PR's base (`5145adff`) is behind `origin/main`. I didn't merge in `main`, so the
citations stay pinned at `5145adff` and the push is a fast-forward.

The content commit is `5d97b9d7`, and the following commit adds the reports. I didn't open a new
PR, merge, or force-push, and the PR is still a draft.

- **Blocking finding 1 (other intake doors).** The decomposition gate doesn't re-run the checks.
  The server has no registry read, and adding one would add an outbound path and a token mint
  that the grant bounds would have to cover. Instead, the gate requires a recorded result of the
  carrier's carry form, bound to the revision: an observation on `package_revision` whose facts
  bind the package id, revision, package hash, package commit and registry commit.
  - The record only means something if the carrier holds an identity no other caller holds. At
    `5145adff` the carrier posts as the shared `orchestrator-system` key, so increment 3 gives it
    its own agent id, M2M credential and setting.
  - "Rotation revision" is defined by the package's `profile`.
  - The Consequences overclaims and the original report's rule-4 line are corrected.
- **Blocking finding 2 (order).** The increments ship in the order 2, 3, 1, 4, with a reason for
  each step. Increment 3 is one change, so the gate never ships alone. The gate's exception also
  depends on the record in code.
- **Small fixes.** Amendment 15 increment 4 design item 4 is added to the narrowed list. The
  report's Infra Brain 2890 line now reads "no ping URL".
- **Left for Devon.** The reissue-shaped revision 1 stays open. The amendment's stated default is
  now the narrower one: the standing answer covers only the issue plan. I reconciled the text that
  depended on the old default: decision 2's new issue-plan condition, the carry form's plan
  bullet, the "carry form's plan" open item, and the original report's open-items bullet.

The original report's new "Fix round 1" section maps each finding to its line numbers.

## Tests and checks run

- `make check` against `orchestrator_test_hq_w_68`, migrated to head, with `SECURITY_STANDARDS_DIR`
  set to the fixtures and `PGTZ=UTC`: ruff "All checks passed!", pyright "0 errors, 0 warnings",
  and pytest **14207 passed, 1 skipped** in 303 s. Exit 0.
- Security scan from the repo root: exit 0, 0 BLOCK, 2 WARN and 1 INFO. None of the findings come
  from this diff.
- `git diff 81371d62` grepped for `ping/` and the Healthchecks host: the only hit is the removed
  literal.
- I verified every new citation at `5145adff`:
  - `api/routes/intake.py:118-124` and `:142`;
  - `package_intake.py:34`;
  - `web.py:753`;
  - `decomposition.py:332-335` and `357-358`;
  - `prepare.py:240-250`;
  - `work_carrier/cli.py:162-167`;
  - `services/release/observations.py:210` and `:239`;
  - `bump_policy.py:20, 31-32` (`revision.profile`).

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: amendment 22's
  decision 1 to 3, the increments and their order, open items and Consequences.
- `docs/superpowers/reports/adr-0055-amendment-22-lane-credential-standing-approval-report.md`:
  the corrected summaries, the "Fix round 1" section and the refreshed "Standards followed".
- `docs/superpowers/reports/adr-0055-amendment-22-fix-1-report.md`: this report.

## Self-review

- An advisor review before I wrote anything did two things. It had me check whether a recorded
  result can be forged: it can by any SYSTEM caller today, which is why the amendment adds a
  carrier identity. It also listed the text the narrower default touched, and I reconciled each
  place.
- I checked the cited line ranges against the final file and corrected one of them (the carry
  form's plan bullet).
- I didn't run `/code-review`, because this round is docs only.

## Concerns

- **A design choice HQ may want Devon to see.** The fix picks a recorded result over the gate
  re-running the checks, and that choice adds a new actor: a registry agent id, a credential, an
  image rebuild and a migration for the observation type. The handoff allowed either option. The
  amendment gives the reason (the server can't read the registry) and leaves the identity's
  details open for increment 3.
- **The narrower default leaves one case partly open.** For a deleted and regenerated package, the
  issue-plan condition refuses it only when the earlier issue set `last_rotated`. The amendment
  leaves "whether every issue does" to increment 2, and doesn't assert it.
- **Cleanup.** The test database `orchestrator_test_hq_w_68` and the scratch files
  `/tmp/w68-*.txt` and `/tmp/w68-*.log` are still in place for HQ's teardown.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the diff is docs only, so it doesn't
  apply. Increment 2 still says the command logs structured JSON on stdout and names no value.
- **Code Brain rule 4 (never swallow an exception silently):** every condition refuses on error,
  and the command exits 2 for anything it can't read. The carrier refuses unless the carry form
  refuses nothing. The gate refuses unless the carrier identity's record is present and bound to
  the revision, so an absent, mismatched or unreadable record goes to a person.
- **Code Brain rule 5 (never log secrets):** the diff names package ids, commits, settings and
  file paths. It names no secret id and no value.
- **Infra Brain 591, 592 and 593:** the diff has no BWS token, no inline `BWS_ACCESS_TOKEN` value,
  and no token-shaped example. The scan has 0 BLOCK findings.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during the work.
- **Infra Brain 212 (HTTPS for public FQDNs):** the diff is docs only, so it doesn't apply.
- **Infra Brain 2890 (never commit a ping URL):** the fix removes the one literal ping-URL prefix,
  and the diff adds no ping URL.
