Outcome: done

# Report: a recorded read-proof run for broker lanes (W-329)

PR: https://github.com/AlobarQuest/orchestrator/pull/825, branch `hq/broker-lane-read-proof-run`.
The code commit is `07fde7fa`; this report's commit is the PR's head. The PR isn't merged.

## B or C, and why

HQ chose B (question file, answer option 1). B reuses everything the `--token` change already
has: `Change`, the parser and validators, the run chain, `lanes-recheck`, `lanes-finish` and the
`--restored` failed close. It needs one predicate, `read_proof(run)` (`run.change == "token" and
run.brokered`), and branches on it at four sites: the plan, the printed next steps, the inverse
text and the read check. The deploy command's diff is +107/-41 lines, most of it the re-indented
next-steps block.

C would need a new `Change` kind and its `key` and `argv`, a new parser flag, `_change`'s and
both validators' cases, the `lanes-finish` and `--restored` dispatch for the new kind, and every
`run.change == "token"` branch duplicated for it. That's more code with the same behaviour.

## What I built

1. **The plan** (`_plan`, `_plan_read_proof`). A broker lane's `--token` no longer refuses. It
   sets `read_uuids` to the lane's allowlist table at the run's commit, sorted, and
   `control_uuid` to the first of `BROKER_READ_CONTROLS` the table doesn't list:
   - estate-landing's ping URL, `11e940d7-e43a-4ebd-9026-b508017d9cef` (Amendment 32 step 7's
     control);
   - inert landing's ping URL, `35557473-f80c-44e3-892a-b507013158b1`, for a lane whose table
     lists the first (estate-landing, as in its cut-over proof).

   A table that lists both is refused at plan time. The run records `control_uuid` (a new
   `LaneRun` field, default `""`, so earlier run files still load). A token lane's `--token`
   still reads `.bws-secrets.toml` and has no control.
2. **The printed steps** (`lane_next_steps`, `lane_inverse`). A read proof prints no
   `coolify_update_service`, no `coolify_update_service_env` or `coolify_create_service_env`, no
   `coolify_deploy` and no `--redeploy-failed` line. It prints `lanes-recheck`, the reads of the
   running container, and `lanes-finish`. Its inverse text says it wrote nothing and names the
   `--restored` close.
3. **The read check** (`read_mismatches`). Each listed UUID must be `ok` and the control
   `refused`. A wrong result, a missing line, or (through `finish_mismatches`, unchanged) a
   `true` token-present read is a recorded mismatch. A UUID the run didn't plan is a usage error,
   as before. The quiet slot is unchanged, as HQ ruled: development mode already has none.
4. **ADR-0055 Amendment 32** records the settlement.
5. **`secrets-broker.md`** "Prove a lane's reads" gives the run's three steps and what its record
   shows. Line 205 no longer says the run is refused. "What the broker doesn't change" says the
   revoke waits for each lane's proof.
6. **`deploy.md`**: the `--token` bullet, the plan's recorded-UUIDs note, the lane phases'
   write, read and finish steps, and "Before a lane token's revoke" describe the broker case.

## The command a cycle runs per lane, and what its record shows

The run's steps, with `<tag>` the tag the lane runs:

1. `uv run python -m scripts.deploy_orchestrator lanes-prepare --lane <lane> --token --running-tag <tag> [--running-volume <volume>]`
2. `uv run python -m scripts.deploy_orchestrator lanes-recheck --lane <lane> --token`
3. The `vps_exec(instance="prod")` reads: digest, schedule, state mount (stateful lanes), the
   token-present read, and this read, exactly as printed:

   ```
   C=$(docker ps -q --filter name=<resource uuid>); [ "$(printf '%s\n' "$C" | grep -c .)" -eq 1 ] || { echo "expected exactly one running container, found: $C" >&2; exit 1; }; docker exec "$C" lane-run --read <the lane's allowlist UUIDs, sorted> <control uuid>
   ```

   It exits 1, because the control reads `refused`.
4. `uv run python -m scripts.deploy_orchestrator lanes-finish --lane <lane> --token --observed-digest <read> --observed-schedule <read> [--observed-volume <read>] --token-present false --read-result <file>`

**A cycle can't run step 3's `lane-run --read`.** HQ's deploy gate
(`~/.claude/hooks/hq-gate-pass.py`, `lane_commands()`) has no template for it. A session runs it
in the revoke sitting's gate, approved there, as Amendment 32 step 7's proofs were. The other
reads are the four the gate already passes.

**The record.** A passed run's file in `LANES_STATE_DIR/<lane>/` shows `change: "token"`,
`brokered: true`, `read_uuids` (the table), `control_uuid`, and `status: "finished"`, with the
tag, digest and schedule of the lane's last finished run. A failed run's `reason` names each
mismatch, for example `lane-run --read: <uuid> ok, not refused`. After `--restored`, its status
is `closed_failed`, and `last_finished` reads past it.

## ADR lines changed (Amendment 32 only)

- 4595-4596, the Status line: HQ settles 10, 11 and 13.
- 4826-4831, item 5.1: the precondition adds the lane's recorded read-proof run.
- 4878-4880, item 10: the run is a recorded read proof; "Settled by HQ: see Answers (2026-11-27)".
- 4942-4948, Answers: Open item 10 is B, with the run's behaviour, the control, and the session
  running it at the sitting.
- 5022, the disposition row for amendment 30 item 10: settled by Open item 10's answer.

## Tests and checks

- `make check` on `orchestrator_test_hq_w_329`, clean tree at `da63919b` (every code, test and
  doc change, before this report's final edit): **14976 passed, 1 skipped**, ruff and pyright
  clean, exit 0. An earlier run before the review follow-ups had the same counts.
- `tests/scripts/` (before the review follow-ups): 1556 passed.
- `code-standards risk`: `normal`.
- **New tests**, replacing `test_a_broker_lanes_token_run_is_refused_at_plan_time`, which no exit
  manifest or evidence names:
  - `test_a_broker_lanes_token_run_proves_its_reads_and_writes_nothing`: plans the table and
    control; prints none of the four writes, no `--redeploy-failed`, and the read-proof inverse;
    finishes on the right reads.
  - `test_a_read_proof_fails_and_closes_on_a_failed_read_a_read_control_or_a_token`: four cases,
    each recorded and then closed `closed_failed`.
  - `test_a_read_proof_refuses_a_result_naming_an_unplanned_uuid`.
  - `test_a_lane_listing_the_first_control_takes_the_second_and_one_listing_both_is_refused`.
  - `test_every_broker_lane_has_a_control_outside_its_table`: run over the tracked allowlist.
    Estate-landing gets the second control and every other lane the first. Both controls are
    listed by some lane and registered in `.bws-secrets.toml`, so a `refused` is the broker's
    `not_allowed`.
  - `test_a_token_lanes_token_run_reads_no_control`.
- **Mutations** (each restored, bytecode cleared):

  | Mutation | Result |
  |---|---|
  | M1, the next steps' brokered branch removed | 1 failed |
  | M2, the plan's brokered branch removed | 6 failed |
  | M3, the control's `refused` expectation dropped | 5 failed |
  | M4, the control not appended to the read | 1 failed |
  | M5, the fallback control ignored | 2 failed |
  | M6, the inverse's brokered branch removed | survived, then 1 failed after I added an assertion |

  M1 is HQ's check. I reran it on the committed file: with the next steps' brokered branch
  removed, `test_a_broker_lanes_token_run_proves_its_reads_and_writes_nothing` fails at
  `assert write not in err, write` with `AssertionError: coolify_update_service(`, because the
  printed steps hold the compose write.
- **Token-shape grep** of the diff's added lines (`0.` + UUID + `.`/`:`): 0 matches. The same
  pattern matches a known-bad synthetic line, so the grep can find one.

## Files changed

`scripts/deploy_orchestrator.py`, `tests/scripts/test_deploy_lanes.py`,
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`,
`docs/operations/secrets-broker.md`, `docs/operations/deploy.md`, this report, and
`docs/superpowers/reports/broker-lane-read-proof-run-question-answered.md` (HQ's answered
question). None of W-328's files is touched.

## Self-review and concerns

- `/code-review` (medium) found three issues:
  - **Fixed:** item 10 still said the run "stays refused".
  - **Fixed:** the control could read `refused` for a secret that no longer exists. A test now
    pins both controls to live, listed, registered secrets.
  - **Not fixed:** Amendment 30 item 10 (lines 4417-4418) still says the run is refused. The
    handoff limits ADR edits to Amendment 32. Amendment 32's disposition row now records that
    item 10 is settled. HQ may want a pointer added in Amendment 30.
- `lane-run --read` prints the same `refused` for the broker's `not_allowed` and for a
  `bws_failed`. The pinned controls make `not_allowed` the expected cause, but the line doesn't
  prove it. The broker's log line (`outcome: not_allowed`) does, if a session wants to confirm.
- The token-present read prints `false` when `docker exec` itself fails, and `false` is the
  passing value for a proof. The step before it, the exactly-one container check, exits 1 when
  the container isn't there. But a `docker exec` that fails on a running container still reads
  `false`. The `lane-run --read` read on the same container would also fail in that case, so
  the proof as a whole still fails. That read is the token check's backstop.
- The run keeps the quiet-slot wait in production mode, so a proof can wait for the slot. HQ
  ruled no new branch.
- Construction-mode ruling: I read the handoff's pointer and didn't need to open the file. Item
  10 was settled by HQ's answer, which is mechanism, not authority.

## Standards followed

- **Code Brain 3 (logging):** I added no logging. The run uses the deploy command's existing
  output: one JSON object on stdout and the next steps on stderr.
- **Code Brain 4 (error handling):** I added no `except`. A failed, missing or wrong read is a
  named mismatch that fails `lanes-finish`. An unplanned UUID or an unparseable line is a usage
  error. A table with no available control is a named plan-time refusal.
- **Code Brain 5 (security):** the run prints UUIDs and `ok`, `refused`, `true` or `false` only.
  The token-present read is `test -n`, which prints no value. `read_results` still refuses a
  non-result line without repeating it.
- **Infra Brain 591, 592, 593 (no token in tracked files, history or inline):** the token-shape
  grep of the diff found nothing. The only UUIDs added are BWS secret ids, which aren't secrets.
- **Infra Brain 1032 (`cred.exposure-rotate`):** no `.cred-consumers.toml` change in this PR, and
  no credential was surfaced.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added.
