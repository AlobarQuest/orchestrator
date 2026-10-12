# Credential rotation increment 4: build report

ADR-0055 increment 4, built from `docs/superpowers/plans/2026-10-10-rotation-increment-4-executor-handoff.md`
between 2026-10-10 and 2026-10-12. The executor runs as `rotation-worker.service` (a systemd timer,
every five minutes) in the isolated OrbStack VM `rotation-executor`, at orchestrator commit
`5fdb186f87b6`. Production serves `caee6eb` (the orchestrator code from #400). #401 and #402
changed only the registry, documentation and `src/rotation_worker/`, which the orchestrator
image doesn't run, so neither was deployed. No migration. The canary rotation `rotation-canary`
ran end to end on production with the fake adapter on 2026-10-12: three units
completed, one attempt refused and fixed (#402), and one criterion waived for its wording.

## What shipped

| Item | Pull request | Mutations | Survivors |
|---|---|---|---|
| Destination format, package side | intent-packages #119 | 14 | 0 |
| Destination format, comparison command | orchestrator #399 | 44 | 0 (6 fixed) |
| Executor: contract, checks, fake adapter, evidence, VM | orchestrator #400 | 107 | 0 (14 fixed) |
| Credential class `rotation-canary`, never aged | infraops-mcp-server #115 | 2 | 0 |
| Canary registry entry and runbook section | orchestrator #401 | — | — |
| Canary standing package, revision 1 | intent-packages #120 | — | — |
| Read the package at its id's path, not `source_path` | orchestrator #402 | 9 | 0 |

Every mutation was made by hand and run against its tests, with the tests passing unmutated
first. #402's nine are two rounds: four on the first fix, five after review moved the package-id
shape into `registry.py`.

**Dependencies added** (T7), in the dependency group `rotation-worker`, which only the VM
installs (`--no-dev --group rotation-worker`); the orchestrator image never has them:

- `bitwarden-sdk==2.1.0`: the executor's BWS client. A session per process with no state file, so
  every pass logs in afresh (amendment 1's cached-session finding).
- `python-dateutil` and `six`: `bitwarden-sdk`'s own dependencies, pinned by hash in `uv.lock`.

**Credentials created** (the hand-rotated set, `docs/operations/rotation-executor.md`):

- BWS projects `Rotation / Work`, `Rotation / Executor` (`1034c2aa-…`) and
  `Rotation / Healthchecks` (`e6f566a9-…`).
- The machine account `rotation-executor`: read and write on Work, read on the other two, nothing
  else. Its token is in the VM at `/etc/rotation-worker/root-token`, owner `rotation`, mode 0400.
- The executor's WORKER bearer, key id `rotation-executor-1`, agent `rotation-executor`, in
  `Rotation / Executor`; its hash is in both rows of `ORCHESTRATOR_M2M_CREDENTIALS`, after
  `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID=rotation-executor` in both rows of that variable.
- The read-only Healthchecks API key, in `Rotation / Healthchecks`. Nothing reads it yet.
- The canary's keeper, `rotation-canary keeper` (`29bb7360-…`) in `Rotation / Work`.

## The end-to-end run

The standing package `rotation-rotation-canary` revision 1 (occurrence `2026-10-11-requested`,
content hash `6df8c079…`) was approved by Devon, registered as intake
`9ce6d953-db53-42d1-878d-55fc43a30b92`, and decomposed by proposal `809959f0-…` into the fake
adapter's plan:

| Unit | Id | Steps | Criteria |
|---|---|---|---|
| `canary-mint` | `4649efae-bc70-5cde-9344-c27d7665d7b2` | mint, stage, verify_new | AC-001 to AC-003 |
| `canary-store` | `41dfe383-bec8-55cc-9469-cd036d2c8b35` | store, verify_consumers | AC-004, AC-005 |
| attestation | dependency `94de001b-58e9-4b85-92c8-e8a957d3661d` | a person attests `runtime-fetch` | — |
| `canary-retire` | `618c87f4-ad25-59ac-a5ba-b43b8de64894` | verify_consumers, retire_old, confirm_dead | AC-006 to AC-008 |

What happened, in order:

1. **The first pass refused mint** (attempt 1 of 3): the executor read the intake's `source_path`
   as a repository path, and production's emitter records the package directory on the machine
   that emitted it. #402 fixed the executor; the unit was requeued as SYSTEM, since requeue is a
   SYSTEM edge with no `/review` form.
2. **Mint ran** (attempt 2): the old value `c4ac94e4…` and the new `55012aaa…`, both length 43,
   the known-bad control refused with 401 and the new value live with 200. AC-001 and AC-003
   passed. AC-002 was recorded failed and then waived at low risk: its text says the old value is
   quarantined at the stage step, and the contract quarantines it at the store step, just before
   the keeper changes. The criterion is wrong, not the executor.
3. **Store ran.** Read in the VM by fingerprint only: the keeper and the staging secret hold
   `55012aaab06b32f9`/43 and the quarantine holds `c4ac94e43b635704`/43. AC-004 and AC-005 passed.
4. **The attestation** was recorded at `/review` with fingerprint `55012aaab06b32f9`/43. The
   canary's `runtime-fetch` consumer is a placeholder no program reads.
5. **Retire ran.** verify_consumers found 2 of 2 consumers holding or attesting the new value.
   retire_old probed the old value live (200) beside a refused control before revoking it, and
   confirm_dead probed the old value dead (401) and the new one live (200), each beside a refused
   control, then deleted the staging and quarantine secrets and filed a second row naming both.
   Afterwards `Rotation / Work` holds only the keeper, at `55012aaab06b32f9`/43. AC-006 to AC-008
   passed.

Every `/review` decision (approval, adjudication, completion and attestation) was clicked by
this session in Devon's signed-in browser, with his consent for this run, so each is attributed
to `devon`.

## Proposed amendment 4

Proposed, not applied: each point is input for Devon or for increment 5.

1. **An intake's `source_path` is not a git path.** `package_sources.py` records
   `str(path.resolve())`, the package directory on whichever machine emitted it, and the runner
   brief, the evidence packs and traceability all carry it. The executor now reads
   `packages/<package_id>/package.yaml` and never reads `source_path`. Recording the path relative
   to the repository at the emitter would fix every reader; building the flow test's intake from
   `load_executable_intake_payload` would stop the fixture drifting from the emitter again.
2. **The canary's AC-002 is worded wrongly.** The next revision of `rotation-rotation-canary`
   should say the stage step stages the new value; the quarantine belongs to AC-004. Each
   adapter's criteria should be written from its plan's step list, not from decision 3's prose.
3. **A human attestation is matched by fingerprint, not by destination.** `contract.py` counts
   the satisfied human acts whose fingerprint matches the new value, so one attestation covers any
   one attested destination. That is right for the canary (one attested consumer) and is not
   enough for a credential with two: increment 5 or later needs the human act to name the
   destination it attests.
4. **The VM reaches other OrbStack machines.** `--isolate-network` blocks the Mac, not the other
   machines on the OrbStack network. Decision 2's residual
   risk should say so; moving the executor to a VPS removes it.
5. **The destination string is at most 200 characters** (`evidence.MAX_DESTINATION`). A
   `runtime-fetch` destination carries its `ref`, so a registry entry with a long ref can't be
   rotated until the ref is shortened.
6. **A replayed claim returns an empty `lease_token`.** The executor never replays a claim (each
   pass claims afresh), so this doesn't affect it, but a client that retries a claim by
   idempotency key gets a lease it can't use.
7. **The intake emitter needs intent-packages as a sibling of the orchestrator checkout.**
   `_intent_packages_src()` is `parents[2].parent / "intent-packages" / "src"`, so `factory submit`
   fails with "approval verification failed" from a worktree under `.worktrees/`. Run it from the
   main trees.
8. **Requeue has no `/review` surface.** A failed rotation unit with attempts left is requeued
   only through `POST /work-units/{id}/requeue` as SYSTEM.

## Operational notes

- **The root token lost its first character when pasted** at the `set-root-token` prompt, the
  third such paste on this machine. It was restored in the VM after a shape check, with no value
  shown.
- **The machine account's project grants didn't save** on the first pass in the Bitwarden console;
  they were set again and confirmed by listing the three projects from the VM.
- **`ORCHESTRATOR_M2M_CREDENTIALS`' preview row returned 500 on PATCH** three times. It had been
  stale since 2026-07-07; it was deleted and recreated. `ORCHESTRATOR_M2M_ROLES`' preview row has
  been stale since 2026-07-09 and was left alone: it predates this work.
- **The Mac's infraops checkout needs a pull and build** before its drift lane knows class
  `rotation-canary` (#115).
- **The VM's clock is in EDT.** `journalctl --since` with a bare time reads it as EDT; pass an
  epoch (`--since @<seconds>`).

## Increment 5 prerequisites

- Amendment 4 item 3, if `openrouter-generic` has more than one attested consumer.
- The OpenRouter management key in `Rotation / OpenRouter`, created by Devon, with the root given
  read on that project.
- Devon's further exposure-response factors (decision 6), named before increment 5.
- A new occurrence of `rotation-openrouter-generic` approved by Devon, with its destinations equal
  to the registry's (`rotation-worker compare-destinations openrouter-generic`).
