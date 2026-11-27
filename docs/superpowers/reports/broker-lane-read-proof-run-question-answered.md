# Question: broker-lane-read-proof-run (W-329)

I stopped at the handoff's first stop point. The run's main read is `lane-run --read`, and the
deploy gate doesn't admit it for an HQ cycle. No code is written yet. The worktree is clean at
`5c3631b0`.

## What I found

- `docs/operations/deploy-gate.md` (sds-hq), "What passes", admits a `vps_exec` read only when it
  is one the deploy script prints: the orchestrator's `RepoDigest`, or a lane's digest, schedule,
  state mount, or token-present read. It also admits a lane's bare `lane-run <lane>` pass and the
  `chain_flags` crontab lines.
- `~/.claude/hooks/hq-gate-pass.py`, `lane_commands()` (lines 74-86), holds exactly those four lane
  reads as templates. It has no `lane-run --read` template.
- The deploy command already prints a `lane-run --read` command for a token lane's `--token` run
  (`lane_reads()`, `scripts/deploy_orchestrator.py` lines 1844-1847):
  `C=$(docker ps -q --filter name=<uuid>); <exactly-one check>; docker exec "$C" lane-run --read <uuid>...`.
  The gate doesn't pass that command for any lane. Every recorded step 7 proof, for example
  sds-hq `state/gates/broker-batch-3-targets-reads-and-canary.md` item 5, was approved in an
  interactive session.

So the handoff's premise, "no new gate-pass form is needed", holds only if a person approves the
run's `lane-run --read` call each time. For an unattended cycle to run it, the gate helper needs a
new template: the lane's allowlist UUIDs plus the control, in the order the deploy command prints
them.

## The shape, if HQ wants a template

B prints this command, the same shape the token-lane read already uses, with one more UUID:

```
C=$(docker ps -q --filter name=<resource uuid>); [ "$(printf '%s\n' "$C" | grep -c .)" -eq 1 ] || { echo "expected exactly one running container, found: $C" >&2; exit 1; }; docker exec "$C" lane-run --read <the lane's allowlist UUIDs, sorted> <control uuid>
```

The exactly-one check is `_exactly_one("C", "running container")`, the same text the gate
helper's `lane_commands()` builds. A template keyed on the allowlist at `main` could hold the UUID list exactly, as `lane_commands()`
holds the others.

## The question

Which of these does HQ choose?

1. **Build B, run interactively.** I build B as planned. The revoke sitting is Devon's anyway, so
   each lane's `lane-run --read` is approved in that sitting or in an interactive session, as step
   7's proofs were. The gate is unchanged. (My recommendation: it needs no guardrail change, and
   the run is the precondition for a sitting Devon attends.)
2. **Build B, and HQ adds a gate template.** I build B, and HQ adds the `lane-run --read` template
   to `hq-gate-pass.py` in the `~/.claude` repo in its own change, so a cycle can run the proof.
   I can't edit that repo from this worktree.
3. **Stop.** Settle item 10 another way.

## Two settled points, so the answer can cover them too

- **B, not C.** B reuses the `--token` change, its parser, validators, run chain, finish and
  `--restored` close. It branches on `run.brokered` at about five sites: the plan, the reads, the
  next steps, the inverse text and the read check. C would touch every `"token"` branch, plus
  `_change`, `Change.argv`, the parser and the validators. No exit manifest or evidence names
  `test_a_broker_lanes_token_run_is_refused_at_plan_time`, so B may replace it.
- **The control.** I plan to use the UUID the step 7 proofs used: estate-landing's ping URL,
  `11e940d7-e43a-4ebd-9026-b508017d9cef`. For estate-landing, whose table lists that UUID, I plan
  to use inert-landing's ping URL, `35557473-f80c-44e3-892a-b507013158b1`, as its cut-over proof
  did (sds-hq `state/gates/estate-landing-cut-over.md` item 2). The plan refuses a lane whose
  allowlist table at the run's commit lists the chosen control, and it records the control on the
  run. No value is read from Coolify or BWS. If HQ wants a control derived from the table alone
  (for example, the first UUID another table lists that this lane's doesn't), say so; it wouldn't
  reproduce the recorded proofs' control.
- **Quiet slot.** I plan to keep the quiet-slot wait for B's run. The run writes nothing, but
  keeping it leaves `lanes-prepare` and `lanes-recheck` unchanged. Say if HQ wants the wait
  dropped for a broker lane's read-proof run. That costs one branch in `quiet_slot_unmet`'s caller.
