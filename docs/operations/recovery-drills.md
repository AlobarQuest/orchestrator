# Recovery drills

Five scripted drills that put the orchestrator through the failures it is built to survive, and
check that it actually survives them. They exist because recovery controls that have never been
exercised are a claim, not a capability.

Run them quarterly, and after any change to claims, leases, evidence, or reconciliation. Run the
exit-criteria attestation on the same cadence, and after any production image swap — it fails if
any route-citing scorecard claim names a route production does not serve (remediation 0.5):

```bash
scripts/run-drills.sh                          # all five, ~90 seconds
scripts/drill-2-evidence-recovery.sh --keep    # one drill, leaving the scratch behind to inspect
python3 scripts/attest_exit_criteria.py        # scorecard route claims vs live production OpenAPI
```

(Not a `make` target: the Makefile is vendored from code-standards and a target added to it would
be clobbered on the next sync.)

Exit 0 is PASS. Each drill prints every assertion it makes.

## What they cover

| Drill | Failure | The question it answers |
|---|---|---|
| `drill-1-dispatch-crash.sh` | The orchestrator is SIGKILLed right after a dispatch | Did the crash leave half-written state, and can the unit be recovered? |
| `drill-2-evidence-recovery.sh` | A worker's lease lapses after it did the work but before it could submit | Can an operator attach that work without redoing it — and without wedging the unit? |
| `drill-3-external-pr-conflict.sh` | Someone merges the PR, or moves its head, behind our back | Do the alarms fire — and do they stay silent during normal iteration? |
| `drill-4-deploy-split-brain.sh` | A deploy lands; its post-deploy verification never finishes | Does the detect-pass find a divergence that nobody will report? |
| `drill-5-stalled-approval.sh` | A unit reaches a human approval gate and the human never answers | Is the stalled gate surfaced in the dead-letter view instead of sitting silent forever? |

## Safety

Each drill owns everything it touches and destroys it on the way out:

- a throwaway Postgres container (`drill-pg-$$`)
- a throwaway database, `orchestrator_drill` — **never** `orchestrator_test`, which the test
  fixtures drop and recreate; pointing a drill at it would erase a concurrent test run
- a throwaway uvicorn bound to 127.0.0.1, with credentials generated per run

They make no outbound call, touch no shared system, and merge and push nothing. `dispatch_enabled`
is off, so no `workflow_dispatch` is fired — drill 1 asserts that rather than assuming it.
`tests/architecture/test_drill_scripts.py` pins all of this, so a drill cannot quietly grow a
reach into something it does not own.

If a drill dies mid-run, its `EXIT` trap still removes the container. `--keep` suppresses teardown
so you can inspect the scratch database after a failure.

## Why the drills drive the public API

Every state change in a drill goes through the same HTTP surface an operator or a runner uses. A
drill that reaches into the service layer, or writes its own preconditions with SQL, can pass over
a production path that never runs.

That is not a hypothetical. The PR-binding writers — the rows the whole conflict-detection feature
reads — had **no production caller at all**. Ten unit tests passed. The runner had no PR to poll,
and both PR alarms were dead in production. Drill 3 found it by refusing to seed the binding
itself, and then found a second bug underneath it: the binding service flushed without committing,
so the HTTP response looked right while the row was discarded when the request ended.

There is exactly one exception, and it is contained in `expire_lease`: the 15-minute lease duration
is hardcoded with no override, so a drill cannot make a lease lapse through any public surface
without waiting fifteen real minutes. Aging the lease with one `UPDATE` on the throwaway database
is environment setup standing in for elapsed wall clock — the same latitude the repo already grants
its protocol smoke tests. Every orchestrator *state transition* still goes through the API.

## When a drill fails

The failing assertion names what it expected and what it got. Re-run with `--keep`, then:

```bash
docker exec -it drill-pg-<pid> psql -U postgres -d orchestrator_drill
```

The drill's log (server output included) is in the temp directory it printed on startup.
