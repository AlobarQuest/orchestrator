# Execution layer design

**Status:** Draft for Devon's approval. Layer id: `execution` (`make layer L=execution`).

## Intent

Execution dispatches work to factory-runner, grants claims and leases, enforces budgets, and moves units through their lifecycle. Its core holds up: recon found no active fault, and 15 of its 23 reviewed rules name a test. Its weakness is at the edges. Most failures reach only `/review`, one scope decision skips the member record, and no test states what the layer reaches.

This pass judges the layer against the same four outcomes as Sensors, Proposers & carry, Landing, and Credential rotation:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert, never only a log.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, unit, or state says something that stopped being so.
4. Edges are clear: one test states each lane's outbound surface.

Phase 4 of the year plan puts client requests through this layer, so these gaps must close before `app_changes` carries real work.

## Measurements, 2026-11-01

| Measure | Value |
|---|---|
| Units in production | 102: 76 completed, 20 cancelled, 4 failed, 2 `revision_required` |
| Active units | 0 |
| Failed units | 4, last active on 2026-08-03 and 2026-09-03 |
| `revision_required` units | 2, both held bumps from 2026-10-31 |
| Alert senders inside `src/orchestrator/` | 0 |
| Fixtures pinned with factory-runner | 7 |
| Reviewed rules | 23: 15 name a test, 5 could, 3 are history |

## Devon's decisions, 2026-11-01

1. **Execution alerts come from a new lane,** `execution-watch`, not from `work_watcher`.
2. **A claim reads the grant** for factory units. Rotation units keep their own check.
3. **The contract copies stay.** The edge test lists every pin. One owner per fixture, with the other repository fetching it in CI, is separate work.

## Goals

- A stalled, held, or failed unit of any class raises one keyed alert that resolves when the unit clears.
- A withdrawn grant stops new work at the claim.
- One test states Execution's outbound surface and every contract pin.
- The layer's rules per thousand lines fall, because prose rules become tests.

## Non-goals

- Moving lanes off the Mac (Phase 1).
- Removing the fixture and hash pins (decision 3).
- Releasing lapsed claims on completed units. Predicates gate on unit state, so the row is untidy, not false. *Claude's call.*
- A reaper that fails or cancels units. The lane reports, and a person decides.
- Changing what `dead-letter` computes, apart from Part 2's requeue fact. *Claude's call.*
- A missing cost actual as an alert. It stays in the SLO report, because a dead run never reports and a completed unit can't clear the alert.

## Anti-goals

- **Alert noise.** One alert per subject, resolved when it clears. A unit that's already covered by a bump or dependency alert raises nothing here.
- **History as news.** The first pass alerts on nothing that predates the lane's install.
- **A claim refusal that burns a unit.** A refusal at `claim_unit` writes nothing and spends no attempt.
- **A wider observer.** The observer gains one read and no write.
- **A rotation affected by a factory rule.** The grant check skips every rotation unit.

## Constraints

- `reclaim_expired_claim` grants attempts without `claim_unit`, so a "may this unit run again" rule belongs in `claims._readiness_eligibility_error`.
- A claim runs in the lifecycle transaction, and a refusal must not commit a write.
- `renew_claim` stays ungated by the grant: withdrawal stops new work, not a run in flight, and landing still refuses it.
- New routes and role changes update `test_scope_guards.py`. New lanes join `layers.toml`, `OUTBOUND_ALLOWLIST` if they add a client, and the edge table.
- Launchers follow `sds_outcome_begin`/`sds_outcome_end` or the dead-man pattern, and read tokens through `sds_bws_value` with `--color no`.
- No reported kind may be a substring of another.
- Never restart the orchestrator while a dispatched run is live, and migrate before the swap.
- Merging a factory-runner change needs its own pin movement. This design changes nothing there unless Part 3 proves it must.

## Design

### Part 1: Every signal has a home

`GET /api/v1/execution-status` is a new read, the one fact source for the lane. `dead-letter` serves no dispatched-but-unclaimed record, no change class, and no rotation flag, and `pending-decisions` is a web page, not a route. Widening `require_operator_actor` would also open `in-flight-units`, `consistency-check`, and `factory-policy`, so this route uses its own gate: OPERATOR or OBSERVER. It serves one row per unit that isn't `completed` or `cancelled`: unit id, state, change class, a rotation flag, target repository, the latest dispatch record's status and time, whether any claim exists, whether the lease has lapsed past grace, and any open circuit breaker. The route joins `test_scope_guards.py` and, since it names dispatch, `WS42_DISPATCH_PATHS`.

`src/execution_watch/` is a scheduled lane that runs every 15 minutes on the observer bearer and writes only to alert-hub. It uses the outcome-alarm launcher pattern with a heartbeat. It raises:

| Key | Condition |
|---|---|
| `execution-unit-failed:<unit>` | State `failed` |
| `execution-unit-held:<unit>` | State `blocked` or `revision_required` |
| `execution-stalled:<unit>` | A lapsed lease past grace |
| `execution-dispatch-blocked:<unit>` | An open circuit breaker, or a latest dispatch record refused or failed for 1 hour by first sighting |
| `execution-run-unclaimed:<unit>` | A latest `dispatched` record with no claim after 2 hours |

`KeyedAlerts` carries the keys. An unreadable pass holds every key, and it raises one `execution-watch-unread` after 2 hours. `held_alerts.py`'s first-sighting pattern carries the clocks, because a dispatch row's own time doesn't say how long it has been refused.

**Ownership.** The lane skips rotation units, whose lanes alert per unit, and skips each condition another lane truly holds for that unit: `bump-unit-failed`, `bump-run-unclaimed`, `bump-act-refused`, and `dependency-unit-failed`. It reads that from a fixed table of condition to owner, tested against those lanes' selection. A class-wide skip would silence a unit that left the bump lane, for example after a grant withdrawal, and no bump lane alerts on a stalled lease.

**History.** On its first pass the lane writes a known-subjects file and alerts on none of them. A known subject stays silent until it clears, then counts as new. A missing file refuses and reports `execution-watch-unread`, so a lost file can't silently re-baseline. Reseeding is a stated command.

**Runs that die early.** A run that fails before `prepare` never reports, so `execution-run-unclaimed` covers it. A run that dies after its claim lapses its lease, so `execution-stalled` covers it.

### Part 2: Scope comes from the member record

`claim_unit` refuses `target_repository_not_granted` between `confinement_refusal` and the budget halt, so a refusal writes nothing and spends no attempt. `TARGET_REPOSITORY_NOT_GRANTED` and the change-class reader move to `target_grant.py`, because `claims.py` sits under the word guards. The code joins `_ELIGIBILITY_CODES` and `_eligibility_error`, or a replay answers `idempotency_conflict`. Rotation units and units with no `target_repository` skip the check.

`_readiness_eligibility_error` gains the same term, so reclaim, release, and requeue agree. Reclaim and release reach it after the claim is released and the unit has failed, so a withdrawn grant parks those units `failed`. They don't stay `ready`. `dead_letter._requeue_target_action` gains the fact, or it names a requeue that refuses.

Dispatch already re-checks the grant, so the claim check matters in the gap between dispatch and claim and on recovery paths. In that gap a refused claim leaves the unit `ready` with `dispatch.dispatched` uncleared, which refuses every orchestrator deploy (`deploy_orchestrator.precheck`) until a person fails and cancels the unit. `execution-run-unclaimed` reports it, and its message names those two steps.

`dispatch_enabled`, the workflow id and ref, and `ORCHESTRATOR_URL` are one service's own settings, not scope, and they stay.

### Part 3: Records stay true

- `execution-watch` covers the `dispatched` record with no claim. Reporting is the repair, because a run might still be starting.
- The column `work_units.max_attempts` comes from the proposal, and the envelope's value is compared only as a ceiling on the two policy branches (`decomposition.py:349,462`). A test pins `column <= authority.budgets.max_attempts` on those branches over stored units, and `CLAUDE.md`'s "no enforcement reader" rule is corrected to say so.
- The rule that `runner_attempt` and `attempt_count` are independent moves to the archive with the other history items.

### Part 4: Edges are clear

`tests/architecture/test_execution_edges.py` states:

- The GitHub dispatch route and its bearer. `dispatch.py`'s call moves to `ConfinedClient`, as `services/landing` already does, with a named User-Agent and `TransportFailure` mapped to `request_error`, so the table's capture sees it. `edge_table` gains a row shape for the orchestrator, which isn't a lane.
- The member-record read.
- Routes the runner calls: brief, claim, renew, evidence, cost actuals, and fail.
- `execution_watch`'s routes and its alert-hub base.
- Every contract pin: each fixture, its pinning test, its hash constant, and its counterpart path in factory-runner, checked against that repository when it's on disk.

### Part 5: Rules into tests

- Write tests for the lease's three writers, `work_units.version`'s three writers, `release_claim`'s callers, and dispatch's one caller.
- Move the three history rules to the archive.
- Run a mutation pass over the 15 enforced rules. A rule whose test survives its mutation gets a stronger test.
- Close the matching `CLAUDE.md` rules to one line, and re-measure the layer card.

## Order

1. Part 1: alerts first, so the rest is watched.
2. Part 2: the grant, which Part 1's report makes visible.
3. Part 3: records.
4. Part 4: edges, after the code parts, so the table states the final surface.
5. Part 5: rules.

Each part gets its own plan, approved by Devon, then subagent-driven execution, a whole-branch review, a deploy, and a live pass.

## Live proof

No unit is active, so each part states what it can prove without staging a failure in a real repository.

| Part | Can prove live | No live proof until a real event | Needs Devon first |
|---|---|---|---|
| 1 | Heartbeat, an empty first pass, the read as the observer | Each alert on a real unit | A staged failure on `sds-canary` |
| 2 | Scope rows; a claim still works on a granted repository | Refusal on a real withdrawal | Withdrawing a grant on a real repository |
| 3 | The agreement test over stored units | A real `dispatched` record with no claim | None |
| 4 | The test is the proof | Not applicable | None |
| 5 | Mutation counts | Not applicable | None |

## Left to the builder

- The exact thresholds' config names, as plain integers with real defaults.
- The wording of each alert, with the next step in it.
