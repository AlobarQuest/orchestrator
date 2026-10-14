# Credential rotation increment 6: self-issued bearers (plan)

ADR-0055 increment 6 rotates the bearers the estate issues to itself: change-manager's four and
three of the orchestrator's. It's the first increment in which the executor writes to production
Coolify and restarts a service. Increment 5 rotated an OpenRouter key with no hosted consumer
(report: `docs/superpowers/plans/2026-10-12-rotation-increment-5-report.md`).

This plan comes from three read-only recon passes on 2026-10-14: the bearers' consumers, what
is already built for the overlap, and what the executor's contract can't yet do. Where this plan
and the ADR disagree, the ADR wins until an amendment says otherwise.

## Scope

Seven bearers. None is due by age before 2027-06-14 (`cred-rotation.ts`: 365 days from
`created`), so the increment has no deadline.

| Credential | BWS secret, project | Hosted side | Readers |
|---|---|---|---|
| `orchestrator-operator-bearer` | `bd71bed1-…`, SDS Operator | `orchestrator-operator` entry of `ORCHESTRATOR_M2M_CREDENTIALS` (hash) | interactive sessions working a unit by hand, through `sds-operator` |
| `orchestrator-observer-bearer` | `f793576f-…`, SDS Operator | `orchestrator-observer` entry (hash) | eight orchestrator launchers, through `sds-operator` |
| `orchestrator-drift-reporter-bearer` | `8998c4ea-…`, Ops / Platform | `orchestrator-drift-reporter` entry (hash) | infraops `drift-audit.sh`, both `observe-run.sh` copies and their callers, through the shared account behind `BWS_ACCESS_TOKEN_VPS_BACKUP` and `…_INFRA_DRIFT` |
| `change-manager-full-bearer` | `af0e4192-…`, Ops / Platform | `M2M_TOKEN` (plaintext) | infraops `drift-audit.sh`, `change-window.sh` |
| `change-manager-read-bearer` | `314f276d-…`, Ops / Platform | `M2M_TOKEN_READ` | four orchestrator launchers |
| `change-manager-propose-bearer` | `acccb346-…`, Ops / Platform | `M2M_TOKEN_PROPOSE` | three orchestrator launchers and `run-rotation-proposer` |
| `change-manager-observe-bearer` | `3b9503da-…`, Ops / Platform | `M2M_TOKEN_OBSERVE` | `run-deploy-watcher` |

The observer and drift-reporter bearers hold one value (decision 6).

Out of scope: SYSTEM and VERIFIER (hand-rotated, decision 4); factory-runner's bearer (increment
7); the executor's own WORKER bearer (hand-rotated set). All seven registry entries are in the
orchestrator's and change-manager's `.cred-consumers.toml`, none flagged `rotated_by_sds`.

## What is already built

- **The orchestrator's overlap.** `previous: {token_hash, until}` per identity is parsed
  (`main.py::_previous_hash`), accepted only before `until` (`identity/auth.py::_hash_accepted`),
  capped at eight days at boot, and refused when it equals any identity's current hash. Shipped in
  #387 and #392, deployed, and tested against the production image (increment 3 report).
- **A live-run read the executor can make.** `scripts/deploy_orchestrator.py`'s precheck reads
  `GET /api/v1/status-ledger` and each ready unit's history. The executor's client already
  permits both routes (`orchestrator_client.py`).
- **The destination kinds** `coolify-env` and `coolify-env-hash` exist in `destinations.py`.

## What isn't built

1. **change-manager's overlap.** `app/auth.py` matches four plaintext bearers exactly, with no
   previous value. Decision 7 says it gets the equivalent change, and that change ships, deploys
   and is verified on production before any change-manager rotation.
2. **A hosted-write step.** `validate_plan` requires verify_new before store and puts distribute
   after store, but a bearer can't authenticate until the service holds it. The contract needs a
   step kind between stage and verify_new that writes the hosted side, keeping "nothing unproved
   reaches the keeper".
3. **A source for the complete credentials setting.** The variable is one JSON value holding
   seven identities, including SYSTEM, VERIFIER and the executor's own. The Coolify token can't
   read it (no `read:sensitive`), and nothing else holds a canonical copy. The BWS secret named
   `ORCHESTRATOR_M2M_CREDENTIALS` holds the SYSTEM bearer, not the setting.
4. **A rotation that spans two keepers.** The parser's "previous equals no current hash" check
   means the observer can't be rotated with an overlap while the drift-reporter still holds the
   old value: boot would refuse. `Rotation` holds one credential and one keeper.
5. **A Coolify client in the executor**: both `is_preview` rows of each variable, a restart, and
   an `OUTBOUND_ALLOWLIST` entry. No existing writer does both rows, infraops' included.
6. **Healthchecks reads.** The verification table verifies a scheduled `runtime-fetch` consumer
   by its next green run, read with the `Rotation / Healthchecks` key, with no exception. Nothing
   reads Healthchecks, and `runtime-fetch` can't tell scheduled from invoked consumers.
7. **`WRITING_STEPS` in `checks.py`** leaves out retire_old, which writes to Coolify for a
   bearer, and counts store, which writes only BWS.

## Design the session settles

These aren't forks; the session builds them and mutates each guard.

- **The live-run check excludes rotation units.** The deploy script refuses on any unit in a
  live state, which would include the executor's own claim. The executor's check refuses on a
  live unit that isn't a rotation unit, and on a ready unit dispatched and not yet claimed.
- **No self-lockout.** The executor rewrites a setting that holds its own entry. It changes only
  the entries it rotates, validates the whole value with the orchestrator's own parser before
  the write, and after the restart proves its own bearer still authenticates before anything
  else. The rollback is a human Coolify edit and restart, written into the runbook before the
  first run.
- **verify_new re-probes through the restart.** A Coolify restart swaps containers, so the old
  container answers for a while. verify_new keeps probing until the new value answers 200 or a
  bound passes, the pattern amendment 8 used for OpenRouter's lag.
- **Evidence carries a fingerprint of the generated setting**, not the setting: its sha256
  hashes are 64-character runs, which the evidence check refuses. That departs from decision 7's
  wording, so it goes in the amendment.
- **Registry fixes before any write.** The drift-reporter's consumer list doesn't name the
  `~/.claude/bin/observe-run.sh` copy or its factory-events and checkout callers, and
  verify-consumers counts attested destinations. As in increment 5, an inventory pull request
  lands first.

## Decisions for Devon

Least machinery first in each.

1. **The first credential, and a grant to a session identity.** The operator bearer is the
   simplest first run: no scheduled reader, no shared value. Its reader is `sds-operator`, the
   account this kind of session uses, so moving its keeper to `Rotation / Keepers` gives one of
   the session's own identities a direct read grant there. Increment 5's constraints said no
   session identity gets a grant in a `Rotation /` project; amendment 6's direct grants are what
   make it narrow enough to consider. Confirm the grant, or name another first credential.
2. **Where the complete credentials setting comes from.**
   - **A hashes-only copy in BWS**, seeded once, which the executor rewrites and then writes to
     Coolify. Least code. Anyone who edits the Coolify variable by hand afterward (rotating SYSTEM
     or VERIFIER, for example) must update the copy too, and the executor can't detect a stale
     copy: its next write would revert that hand edit.
   - **An orchestrator route returning agent ids and hashes** for the executor to read. One more
     route and its guard inventories, but no second copy to go stale.
3. **The observer and drift-reporter.** Keep one shared value, written to both keepers in one
   rotation, or split them into two values in one coordinated write. Either way the contract
   gains a rotation that spans two keepers in two projects with different readers.
4. **Healthchecks for scheduled consumers.** Build the reads before the observer rotation, as the
   verification table requires, or accept a human attestation as a new exception. The operator
   bearer has no scheduled consumer, so this doesn't block the first run.
5. **change-manager's overlap shape**: a second variable per bearer holding the previous value
   and its expiry, or the orchestrator's `previous` form inside one JSON variable. Either way the
   extra key joins the approved destination list. A change-manager pull request, deployed and
   verified before its first rotation.
6. **Who clicks `/review` and the change record.** The ADR's default is you; increment 5 had your
   consent for that run only.

## Your acts

Each is prepared so it takes one sitting. No value passes through a transcript, a tool argument or
the Mac's clipboard.

1. Create the production Coolify token (`read`, `write`, `deploy`, not `read:sensitive`) after the
   probes, store it in a `Rotation / Coolify` project, and give the root read on it.
2. Move each keeper from SDS Operator or Ops / Platform to `Rotation / Keepers` (the root has no
   write on either source), and give each consumer account a direct read grant, proven first with
   a canary and an empty `HOME`.
3. Approve each package revision by name, and the change record.
4. The `/review` clicks, unless decision 6 says otherwise.

## Order of work

1. **Inventory pull request**: the drift-reporter's consumer list, and every consumer's kind.
2. **Production Coolify probes** with a throwaway application, repeating amendment 1's question 1
   on beta.473. Also measure what a restart does when the new container refuses to boot: the
   orchestrator's Coolify health check is disabled, so the rolling swap measured elsewhere may
   not hold. Then your act 1.
3. **The executor changes**: the hosted-write step, the Coolify client, the setting's source
   (decision 2), the live-run check, `WRITING_STEPS`, and the runbook's rollback. Reinstall the
   VM.
4. **The operator bearer's rotation**: keeper move, revision 1 of its package, `rotated_by_sds`,
   the request, and the run. Hosted units claim only in the `live_estate` window, 02:00 to 06:00
   America/New_York, so the session runs those passes in the window.
5. **The observer and drift-reporter**, after decisions 3 and 4.
6. **change-manager's overlap**, deployed and verified, then its four bearers.

Steps 4 to 6 each end with the rotation's state checked by fingerprint, and a report. Decisions
1 to 3 block step 3, because each changes what `Rotation` holds.

## Constraints

Increment 5's handoff constraints apply unchanged: worktrees only, values only in the VM, the
root as the only keeper writer, `OUTBOUND_ALLOWLIST` entries with reasons, pinned dependencies,
`make check` from a clean tree, `/code-review`, and a mutation count with every survivor.
Never restart the orchestrator while a dispatched run is live.

## Closing steps

1. Merge each pull request when its checks pass.
2. Deploy the orchestrator only if its code changed, and reinstall the VM at each executor
   change.
3. Record the decisions as an ADR-0055 amendment, and resolve the backlog item with the pull
   requests, revisions and unit ids.
4. Write a report per run, in increment 5's shape.
5. Remove each worktree, test database and merged branch.
