# ADR-0040 — The tracker projection is retired as an operating lane, and its code stays

- **Status:** Accepted
- **Date:** 2026-09-05
- **Amended:** 2026-11-04 (amendment 1, code removed)
- **Decided by:** Devon
- **Retires the OPERATION of:** ADR-0003 (tracker projection, outbound only), ADR-0004 (inbound
  tracker reconciliation). Neither is superseded on its reasoning; both are correct about what they
  decided, and both remain the record of why the code is shaped as it is.

## Decision

**The two tracker lanes stop existing as programs a person can run.**
`scripts/run-tracker-projection.sh` and `scripts/run-tracker-reconciliation.sh` are deleted.

**Everything they invoke stays** — `src/tracker_projection_adapter/`, its tests, the three
canonical routes, `services/tracker_bindings.py`, the detection half in
`services/reconciliation_detection.py`, and the `unit_tracker_bindings` table with its four rows.

That split is not a compromise. It is what the evidence permits, and the second half is
load-bearing: see *What stopped a fuller deletion*.

## What was measured, 2026-09-05

Devon asked what these lanes were for, and said they might be "discarded litter" from wrong turns.
They are not. Both are decisions he confirmed by name and date — ADR-0003 on 2026-07-26, ADR-0004
on 2026-07-27 — and the mechanisms were built and work.

What is true instead is narrower and was invisible from the repository alone:

| | |
|---|---|
| Tracker bindings in production | **4**, written at 13:55:08, :09, :10 and :11 on 2026-07-28 |
| Inbound reconciliation conditions, ever | **0** |
| Last change to the adapter's code | 2026-07-28 |
| Days idle at this ADR | **39**, across 61 work units, the newest two days before |

So each lane ran **exactly once, on the day its ADR was accepted** — the pilot pass IS the
acceptance evidence — and neither has run since. Four bindings four seconds apart is one pass, not
a practice. An inbound lane that has never produced a row has never had anything to report.

Neither was ever scheduled. Both headers say "operator-invoked", which was a design choice; what
follows from it is that every pass either ever made was typed by hand, and nobody has typed one
since July.

## Why retire the lanes rather than keep them

The projection mirrors canonical work-unit state onto Todoist as **a read-only human view**. The
human it was built for did not know it existed. A view nobody opens is not a view, and the estate
has a stronger claim on the same attention: `/review`, and nine lanes that report on a clock.

The inbound half is weaker still. It exists to turn a tracker edit into a reconciliation condition
a human then adjudicates — a path that requires somebody to be editing the tracker, which requires
the outbound view to be in use. It has produced nothing in 39 days because there was nothing to
produce.

## What stopped a fuller deletion, and it is not caution

The first attempt at this deleted the adapter, its tests, the routes and the services — 24 files.
That is wrong, and the thing that says so is a guard doing its job.

**Wave 2's exit bar attests these artifacts, and the bar was MET.** Clause 3 reads *"the pilot queue
is orchestrator-canonical with the tracker as pure projection"*, and
`docs/operations/wave-exit-manifest.toml` binds it to two checks: a `routes_served` check naming all
three tracker routes, and a `command` check running `exit_probe.py tracker-is-a-projection`, which
asserts canonical bindings exist **and executes `tests/tracker_projection_adapter`** to prove the
adapter imports nothing from the orchestrator.

`.github/workflows/attest-wave-exit.yml` re-measures that on demand and says so in its own header:
*"a route a Wave-2 clause depends on going missing reds this job."*

So deleting the routes or the adapter's tests would either red that guard or force the Wave 2 bar to
be rewritten — **and rewriting a bar that was met is the back-dating mistake ADR-0014 names.** A
decision made once, on evidence that was true then, does not become false because the estate later
stopped using what it attested. The record is not a description of the present.

**The clause stays measurable, which is the point.** Nothing here changes what the probe reads.

## Consequences

- The estate carries **nine** scheduled lanes and no dormant tracker lanes. The two scripts that
  made these look like live lanes are gone.
- `scripts/run-follow-up-mint.sh` is deliberately untouched. It is a separate question with a
  separate answer pending: **six approved revisions carry follow-up prose a human wrote and
  approved**, and nothing will ever mint them. Deleting that lane decides those promises are void,
  which is a ruling rather than a cleanup.
- **The `bws --color no` guard added to both of these scripts hours earlier is deleted with them.**
  Worth stating plainly rather than quietly: that fix was correct, it was applied to three
  launchers, and two of the three are now gone. The third — the follow-up minter — is the one that
  needed it, and it keeps it.
- The adapter is now reachable only by its own tests and by the wave-exit probe. That is a real
  residual, named here rather than implied: it is code with no operator, retained because an
  attestation depends on it. If Wave 2's manifest is ever retired, this is what becomes deletable
  with it.

## Amendment 1: the code is removed (2026-11-04)

Devon decided on 2026-11-04 to retire the adapter, because it adds no value. This amendment ends
the sentence "the code stays because Wave 2's bar attests it".

### What is removed

- `src/tracker_projection_adapter/`, its tests, and its console script.
- The three tracker routes, their services, and their schemas.
- The `unit_tracker_bindings` table, and the `tracker` and `tracker_state_divergence`
  reconciliation vocabulary (migration 0047).

### How the bar stays met

This amendment ends the re-measured attestation the way ADR-0049 did for the drills. Wave 2
clause 3's `text`, `annotation`, `body`, and `body_sha256` are unchanged. Its two live checks are
replaced by a retained-evidence check on `docs/evidence/exit-manifest/2026-10-08-wave2.json`, the
last run in which both live checks passed. Criterion 9 leaves `exit-criteria-claims.toml`, because
an empty route list would pass vacuously.

The 2026-10-08 record names manifest `04bc12b0…`, and commit `e0e5df33` holds those bytes. Nothing
compares that field. A fresh record is taken after the deploy.

### Measurements, 2026-11-04

| Measure | Value |
|---|---|
| Callers of the adapter or its routes | 0 launch agents, workflows, or scripts. Only `scripts/exit_probe.py tracker-is-a-projection` reads a route. |
| Sibling repositories naming the adapter, its routes, or its table | 0 (`~/Projects`, excluding this repository) |
| Consumers of the Todoist token `ff396349-…` outside this repository | 0 (`~/Projects`, `~/.claude`, `~/Developer`, `~/Library/LaunchAgents`) |
| `unit_tracker_bindings` rows | 4, all `todoist`, all `cancelled`, written 2026-07-28 13:55:08 to 13:55:11 |
| Reconciliation conditions of kind `tracker` | 0. Production holds 3 conditions: `deployment`/`deploy_split_brain`, `github_pr`/`external_merge_alarm`, `github_pr`/`pr_state_divergence`. |
| Production alembic head | `0046_observation_source_seen` |
| Served tracker routes | 3: `POST /api/v1/work-units/{unit_id}/tracker-binding`, `GET /api/v1/tracker-bindings`, `POST /api/v1/reconciliation/tracker-detect` |
| Readers of a record's `manifest_sha256` | 0. `attest_wave_exit.py --record` writes it; nothing compares it. The checked pin is `body_sha256`. |
| A clause whose only check is `retained_evidence` | Resolves `pass` (`resolve_clause`), as Wave 1 clause 1 has since ADR-0049. |
| `attest-wave-exit.yml` trigger | `workflow_dispatch` only |
| `attest-exit-criteria.yml` trigger | Mondays 08:00 UTC, and `workflow_dispatch` |

The four bindings, recorded here so the history survives the table:

| `work_unit_id` | Todoist item | State | Written (UTC) |
|---|---|---|---|
| `ddda84ac-42f2-53b8-ba78-ed93339d2961` | `6h8fW6Rx2x6HwH53` | `cancelled` | 2026-07-28 13:55:08 |
| `69e63f39-348c-53ef-bdab-af2d81e1b7e3` | `6h8fW6VQFjJjHvH3` | `cancelled` | 2026-07-28 13:55:09 |
| `4afb7207-10cf-5875-91a0-d4a602f49777` | `6h8fW6f38cjr2WR3` | `cancelled` | 2026-07-28 13:55:10 |
| `3a650c23-1369-5fb1-8dc2-f794d46b3819` | `6h8fW6PwHRGxh5FV` | `cancelled` | 2026-07-28 13:55:11 |

### What stays true

The orchestrator is canonical, and a tracker can only project it. ADR-0003 and ADR-0004 remain the
record of that reasoning.

The Todoist token (`ff396349-aec1-4250-b2f0-b493015188da`) has no remaining consumer. Devon
deletes the BWS secret.
