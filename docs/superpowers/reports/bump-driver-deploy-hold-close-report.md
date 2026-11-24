Outcome: done

# Report: close the bump-driver deploy-hold backlog item

## What I built

PR [AlobarQuest/orchestrator#809](https://github.com/AlobarQuest/orchestrator/pull/809), branch
`hq/bump-driver-deploy-hold-close`, based on `origin/main` at `de08d21a`. The `PROJECT.md` change
is commit `aa312d40b29efb845337027d2ca3d3517662d2e1`. This report is the next commit on the same
branch, so the PR's head is that report commit. Not merged.

The new text of `PROJECT.md` line 235:

> - [x] (P2) deploy_orchestrator.py holds the bump-driver launch agent during a deploy (disable before recheck, re-enable after finish, also on failure) so a deploy never waits on the clock; replaces the 'deploy away from :50' rule in docs/operations/deploy.md — added 2026-11-05 DONE 2026-11-24: superseded, not built. The bump driver runs as a VPS lane (`lanes/bump-driver/crontab`, `lane-run bump-driver` at minute 51) and its Mac job `com.devon.bump-driver` is booted out, so no Mac launch agent remains for the deploy to hold. A deploy's effect on a lane pass is governed by the `lanes-recheck` residual (ADR-0059 lines 265-271) and by development mode (`deploy-mode.toml`), which drops a lane's quiet slot, the lane served-tag rule and prepare's lane lag check; ADR-0059 Amendment 13's "paired pass and the deploy hold" item asked for this re-scope.

## Facts checked at `origin/main` (`de08d21a`)

1. **Holds.** `PROJECT.md` line 235 is the open P2 item, word for word as the handoff quotes it.
2. **Holds in content; one cited range is off.** ADR-0059 lines 267-271 hold the `lanes-recheck`
   residual and end "The P2 backlog item in which `deploy_orchestrator.py` holds the bump-driver
   launch agent during a deploy is re-scoped by this rule once the bump driver moves." Amendment
   13's item "The paired pass and the deploy hold (the bump driver's cut-over)" is at lines
   **3973-3977**, not 3977-3981. Line 3977 is its last line, "deploy (lines 269-271)." Lines
   3979-3981 are the next heading, "Passages this amendment changes". The text matches the
   handoff's quote, so I didn't stop.
3. **Holds.** `lanes/bump-driver/crontab`'s only schedule line is `51 * * * * lane-run bump-driver`.
4. **Holds.** `deploy-mode.toml` has `mode = "development"`. Its header lists the four gates
   development drops: a lane's quiet slot, the lane served-tag rule, prepare's lane lag check, and
   the nothing-live check when a deploy carries no migration.
5. **Holds.** `git grep -n -i "launchctl\|bootout\|disable" origin/main --
   scripts/deploy_orchestrator.py` returns nothing (exit 1). The script holds no launch agent.
6. **Reported, not changed.** `docs/operations/deploy.md` doesn't state the old ":50" rule for
   orchestrator deploys. It states the moved form at lines 100-101: "The `bump-driver` lane's
   container starts runs hourly at :51, so deploy away from :51." Line 1191, in the bump driver's
   cut-over steps, still says "Run the pair away from minute 50, when the Mac's acting job runs."
   See the concerns section.

The bump driver's move (HQ gate G-86, 2026-11-21) and the Mac job's bootout come from the handoff.
I made no live call to confirm the Mac job's state.

## Tests and checks run

- `portfolio lint .` (project-standards): exit 0. Its one WARN is in a file I didn't touch:
  `retired_host` in `docs/operations/landing-lanes.md`, which names the retired Hetzner address.
- Token-shape grep over the added lines (BWS-token prefix shape, `BWS_ACCESS_TOKEN=`, `ghp_`,
  `github_pat_`, `sk-`): 0 matches.
- `make check` wasn't run. The handoff says it isn't needed for a one-line `PROJECT.md` change.

## Files changed

- `PROJECT.md`: line 235, closed in place.
- `docs/superpowers/reports/bump-driver-deploy-hold-close-report.md`: this report.

## Self-review

- The line follows the done form of lines 60 to 70: `- [x]`, the original text unchanged, then
  ` DONE 2026-11-24: ` and the closing note.
- I replaced the handoff's word "lockstep" with the gate names `deploy-mode.toml` uses. "Lockstep"
  appears in neither `deploy-mode.toml` nor deploy.md, and the toml's own names tell a reader what
  to look for.
- The ADR line range in the new line (265-271) is the range the handoff asked for. The residual
  starts at line 267, but 265-271 includes it.

## Concerns

- `docs/operations/deploy.md:1191` still says "Run the pair away from minute 50, when the Mac's
  acting job runs." The Mac job is booted out, so that cut-over step is historical. Lines 100-101
  carry the current rule: deploy away from :51. Neither line changed, as the handoff directs.
- The handoff cites Amendment 13's verify item at ADR-0059 lines 3977-3981. At `de08d21a` it's at
  lines 3973-3977.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** not applicable. No code changed.
- **Code Brain 4 (never silently swallow an exception):** not applicable. No code changed.
- **Code Brain 5 (never log secrets, credentials, tokens, or auth headers):** no code and no
  logging changed. The diff and this report name no secret.
- **Infra Brain 591 `bws.no-token-in-tracked-files`:** the token-shape grep over the added lines
  found 0 matches.
- **Infra Brain 592 `bws.no-token-in-git-history`:** the commit adds no token-shaped string, so
  history gains none.
- **Infra Brain 593 `bws.bootstrap-token-not-inline`:** no bootstrap token or assignment of one
  appears in the diff or report.
- **Infra Brain 1032 `cred.exposure-rotate`:** I read no secret and surfaced no credential.
- **Infra Brain 212 (HTTPS):** the only URL I added, the PR link in this report, uses `https://`.
