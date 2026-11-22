# Question: four stale "infra-drift mints" comments sit in files the handoff rules out of scope

Worker W-262, handoff `lanes-inc-9-follow-up-mint-records`. Branch
`hq/lanes-inc-9-follow-up-mint-records`, commit `0867f8b0` (not pushed). Items 1 to 6 are done and
committed. The targeted tests pass (`tests/rotation_worker/`, `test_sds_bws.py` and
`test_run_follow_up_mint.py`: 1776 passed). Both broker-row mutations fail the test. I haven't run
`make check`, the security scan, the risk check, `/code-review`, the push or the PR yet. I'll run
them after your answer so they run once over the final diff.

## The conflict

Item 7 says to fix every line that still says infra-drift mints. The out-of-scope section rules out
the launcher, the crontab, other lanes, and every credential row except the two named ones. Four
lines that say infra-drift mints (or will until #173) are in those files. All four are comments
only; no code or schedule changes.

1. `scripts/run-follow-up-mint.sh:27-28`, the launcher's header: "minting runs on the Mac as a
   non-fatal step of infraops-mcp-server's `drift-audit.sh` until a pull request there removes
   it."
2. `lanes/follow-up-mint/crontab:2-4`, the crontab's comment: "after infra-drift's 03:00 audit,
   which mints on the Mac until infraops-mcp-server drops that step, so on a day both run the
   lane's pass finds what the audit minted `already_minted`." The schedule line doesn't change.
3. `.cred-consumers.toml:1459-1462`, the comment on the `follow-up-mint-ping-url` row, a third
   credential row: "The broker reads the keeper for the lane once the compose names its id and
   the broker's account `lane-secrets-broker` is granted a read on it ... which the lane's
   `lane-run --read` proves". #772 rewrote the bump driver's equivalent comment and note to the
   "granted and proved" form. This row's `bws-secret` consumer note (line 1473) has the same
   wording: "the broker reads it for the lane once the compose names its id and the broker's
   single-secret grant is made".
4. `scripts/install-landing-ledger-launchd.sh:13-14`, the landing-ledger lane's installer: "The
   survivor, run-follow-up-mint.sh, was deleted on 2026-09-28 (Tier 3 item 24b):
   infraops-mcp-server's drift-audit.sh runs the daily mint pass."

Two more lines don't say infra-drift mints, but they went stale with #173.
`scripts/run-deploy-watcher.sh:106` and `scripts/run-estate-landing.sh:27` cite
`drift-audit.sh` as "the in-estate precedent for overriding the identity for a single foreign
call". At `c7de0b70`, `drift-audit.sh` reads only `BWS_ACCESS_TOKEN` from its own Keychain item
and overrides no identity. Both are other lanes' launchers.

## The choice

- **A (I recommend this):** Edit lines 1 to 4 in this PR as comment-only changes. Rewrite line 3 in
  #772's form: the grant was given at G-87, and the first scheduled pass on 2026-11-22 logged `ok`
  for it. Leave the two precedent comments for a later change, and list them in the report.
- **B:** Leave all six. I list them in the report as concerns for a follow-up handoff.
- **C:** Edit all six now.

One cost to weigh for A and C: the launcher and crontab ship in the lane image. A comment change
there differs from the running release until the next lane release. It changes no behaviour.
