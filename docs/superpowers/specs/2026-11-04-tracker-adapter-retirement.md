# Tracker projection adapter retirement: design and plan

**Status:** Draft for Devon's approval. Devon decided on 2026-11-04 to retire the adapter, because
it adds no value. This document is the spec and the plan in one.

## Intent

Remove the tracker projection adapter's code, routes, services, table, and tests, and keep its
history. ADR-0040 already retired it as an operating lane; it stayed only because Wave 2's met exit
bar attests it. This change amends that attestation the way ADR-0049 amended the drills clause, so
the bar stays met on the evidence it was met on.

The design principle the adapter proved stays true: the orchestrator is canonical, and a tracker
can only project it. The amendment says so.

## Measurements, 2026-11-04

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

## Rulings for approval

Each ruling names what it costs if it's wrong.

1. **Drop the table and narrow the vocabulary in one migration (recommended).** Migration `0047`
   drops `unit_tracker_bindings` and rewrites the two `reconciliation_conditions` CHECKs without
   `tracker` and `tracker_state_divergence`. The model constants lose the same members, so the
   generic `POST /api/v1/reconciliation/detect` route refuses `observation_kind="tracker"` too.
   Production holds no tracker condition, and the CHECK fails closed (the migration rolls back
   before the swap) if one appears first. Downgrade recreates the table empty and restores the
   members; it can't restore the 4 rows, which this document and the ADR-0040 amendment record.
   *Cost if wrong:* the 4 rows exist only as text. Nothing reads them.
   *Alternative:* keep the table and vocabulary as orphans. No migration, but a table and two
   vocabulary members that nothing writes or reads, which `alembic check` and every future reader
   then has to explain.
2. **Clause 3 rests on the in-repo record `docs/evidence/exit-manifest/2026-10-08-wave2.json`.**
   Its two live checks go; one `retained_evidence` check pins that record by digest, and a `note`
   names ADR-0040 amendment 1. The record is the last run in which both clause-3 checks passed
   live, and it's version-controlled, unlike the `~/docs` closeout. The clause's `text`,
   `annotation`, `annotation_separator_count`, `body`, and `body_sha256` stay byte-identical, so
   the bar's pin holds. *Cost if wrong:* clause 3 stops being re-measured; it's attested by the
   intact record of a pass, which is what "met 2026-07-23" already meant.
3. **The pin is handled by a sentence and a new record, not by a mechanism.** The 2026-10-08
   record stays unaltered and names manifest `04bc12b0…`, which commit `e0e5df33` holds. After the
   deploy, a fresh `attest_wave_exit.py --record` run writes `2026-11-04-wave2.json` (or the day's
   date) against the amended manifest. *Cost if wrong:* none known; nothing reads the field.
4. **Criterion 9's entry leaves `exit-criteria-claims.toml`.** The file lists only scorecard cells
   that rest on live routes. An empty `routes` list would pass vacuously, which is the false-MET
   shape this file exists to stop. A comment where the entry was names the ADR amendment and says
   criterion 9 stays met on its historical evidence. *Cost if wrong:* the weekly run no longer
   names criterion 9, which is what's true.
5. **The Todoist token leaves `.bws-secrets.toml`, and you delete the BWS secret.** No consumer
   remains anywhere I can grep. The manifest comment claiming "the UUID is consumed elsewhere in the
   portfolio" is false and goes. `sds-token.sh`'s header, which says the `SDS Operator` project
   holds the Todoist token as a runtime fetch, changes too. *Cost if wrong:* a consumer outside the
   grepped roots loses its token when you delete it; the drift scanner stops watching a UUID
   nothing here reads.

## Non-goals

- Rewriting the Wave 2 bar, its `body`, or any retained record.
- Changing ADR-0003 or ADR-0004. They stay the record of what was decided and why.
- Editing generic mentions of trackers that state the design principle (`reach_vocabulary.py`,
  `factory-policy.toml`, `observation-ingestion.md`, `verifier.md`, and similar).
- Editing historical plans, specs, evidence, and `docs/history/` records, apart from the archive
  entry that receives the old CLAUDE.md rule.
- The shared fixture `tests/persistence/conftest.py::work_unit_and_event` and the "tracker
  projection drifting" sample prose in two decision-surface tests.

## Plan

Work runs in `.worktrees/tracker-retire` with its own venv and database
`orchestrator_test_tracker_retire`, `PGTZ=UTC`. Each task is one commit, implemented by a Sonnet
subagent and reviewed by a Sonnet reviewer; the final whole-branch review runs on Opus. Each task
ends with the tests it touches green; tasks 3 and 5 end with a full `make check`.

### Task 1: amend the attestations and claims

- `docs/operations/wave-exit-manifest.toml`, Wave 2 clause 3: remove the `routes_served` and
  `command` checks; add a `note` naming ADR-0040 amendment 1 and a `retained_evidence` check on
  `docs/evidence/exit-manifest/2026-10-08-wave2.json` with its SHA-256 and a `proves` sentence
  that claims only what the record shows.
- `docs/operations/exit-criteria-claims.toml`: remove criterion 9's `[[claim]]`; leave a comment.
- `docs/decisions/0040-…md`: add amendment 1, recording the removal, the 4 rows, the measurements,
  the rulings, and that the design principle stays true.
- Verify: `attest_wave_exit.py --pin-only` reports the Wave 2 pin `verified`; a run of the Wave 2
  bar reports clause 3 `pass` on `retained`; `attest_exit_criteria.py` passes against production.

### Task 2: remove the routes, services, and schemas

- `api/routes/reconciliation.py`: the three tracker routes. `api/schemas/reconciliation.py`: the
  four tracker models.
- `services/reconciliation/tracker_bindings.py`: delete. `reconciliation_detection.py`: delete the
  tracker half (`ObservedTrackerItem`, `TRACKER_*`, `_tracker_binding`, `_record_tracker`,
  `_detect_tracker_item`, `detect_tracker_conditions`).
- `scripts/exit_probe.py`: the `tracker-is-a-projection` probe; `tests/scripts/test_exit_probe.py`
  lines about it.
- Guards and inventories: `test_scope_guards.py` (two POST, one GET), `tests/idempotency/matrix.py`
  (two rows), `VOCABULARY_REGISTRY` (`TRACKER_CLOSED_STATES`),
  `test_external_content_observes_only.py`'s route set for the adapter (the row itself goes in
  task 4).
- Tests: `tests/api/test_tracker_bindings_api.py`, `tests/api/test_tracker_reconciliation_api.py`,
  `tests/services/test_tracker_bindings.py`, `tests/services/test_tracker_reconciliation_detection.py`,
  `tests/idempotency/test_tracker_binding_idempotency.py`,
  `tests/idempotency/test_tracker_detect_idempotency.py`,
  `tests/architecture/test_tracker_closed_states_sync.py`.
- `layers.toml`: the deleted files' entries.

### Task 3: drop the table and narrow the vocabulary

- `persistence/models.py`: remove `UnitTrackerBinding` and `TRACKER_SYSTEMS`; remove `tracker`
  and `tracker_state_divergence` from the two reconciliation tuples.
- Migration `0047_retire_tracker_bindings` (id at most 32 characters): drop the table; replace the
  two CHECKs with frozen, join-quoted copies. Downgrade reverses both, table empty.
- `tests/persistence/test_tracker_reconciliation_vocab.py`: replace with a test that the narrowed
  CHECK refuses `tracker`, and a migration round-trip test (upgrade, downgrade, upgrade) on
  Postgres.
- Verify: full `make check`, and `alembic check` reports no drift for the touched tables.

### Task 4: remove the adapter and its console script

- Delete `src/tracker_projection_adapter/` and `tests/tracker_projection_adapter/`.
- `pyproject.toml`: the `tracker-projection-adapter` script; `uv lock --check`.
- `test_out_of_process_isolation.py` and `test_external_content_observes_only.py`: the adapter's
  rows. `test_wsp21_invariant_scan.py`: its two `OUTBOUND_ALLOWLIST` entries. `layers.toml`: its
  two patterns.
- Docstrings in `src/deploy_watcher/__init__.py` and `src/landing_ledger/__init__.py` that name it
  as a sibling.

### Task 5: documents and credentials

- `CLAUDE.md`: rewrite the rule that keeps the adapter as the rule stands ("a thing a met bar
  attests leaves only by an amendment that keeps the bar met, as ADR-0040 amendment 1 and ADR-0049
  did"); move the old text to `docs/history/claude-md-invariants-archive.md` #287. It stays below
  the managed block's end marker.
- `docs/operations/credentials.md`, `.bws-secrets.toml`, `scripts/sds-token.sh` header: the Todoist
  token.
- `docs/operations/architecture-guards.md` (lines 139 and 162), `docs/roadmap.md:76`,
  `.github/workflows/attest-wave-exit.yml` header, `docs/evidence/exit-manifest/README.md` if it
  names a live clause-3 check.
- `PROJECT.md:104`: close the Todoist reopen/delete divergence item as moot, in the worktree.
- Verify: full `make check`; the security scanner from the worktree root.

### Task 6: final review, merge, deploy

1. Opus whole-branch review; fix rounds on Opus.
2. Open the pull request, wait with `gh pr checks --watch --interval 30`, and merge when green.
3. Before the restart: `GET /api/v1/execution-status` shows no ready or claimed rotation unit and no
   live dispatched run; re-count tracker conditions (expect 0).
4. Deploy with `scripts/deploy_orchestrator.py` (`prepare`, migrate, swap, `finish`).
5. Read `https://sds.alobar.net/openapi.json`: the three paths are absent; `/health/ready` is 200
   at head `0047_…`.
6. Run `attest_wave_exit.py --record` locally and commit the record; run
   `attest_exit_criteria.py` locally.

### Task 7: knowledge capture

- The SDS Layer Map artifact: remove the "Tracker projection adapter" note under "Things that look
  dead"; update the Release & observation card's code list and line count.
- The SDS year plan doc: one sentence recording the retirement.
- Memory: a progress entry and a `MEMORY.md` line; add `X-Credential-Key-Id` to the M2M credential
  memory.

## Live proof expected

- The served OpenAPI document lacks the three routes, and production is at the new head.
- A local `attest_wave_exit.py --record` run shows Wave 2 clause 3 `pass` on retained evidence.
- A local `attest_exit_criteria.py` run passes; the next Monday run confirms it in CI.

Nothing in this change has a scheduled caller to prove live; the absence of callers is the reason
for it.
