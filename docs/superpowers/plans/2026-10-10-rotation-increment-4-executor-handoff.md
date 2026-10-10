# Credential rotation increment 4: the executor, with no live credentials (session handoff)

ADR-0055 designs credential rotation as a pull-based executor in an isolated OrbStack VM.
Increment 3 shipped and deployed everything the orchestrator side needs (report:
`docs/superpowers/plans/2026-10-10-rotation-increment-3-report.md`). Increment 4 builds the
executor itself: the contract, its invariants, the revision and registry checks and a fake
adapter, built and tested in an isolated VM, plus the `Rotation /` BWS projects, the root token,
the executor's WORKER bearer and the read-only Healthchecks key.

This session rotates no credential, holds no provider admin credential, and moves no keeper. The
first real adapter (OpenRouter) is increment 5.

## What the session must produce

Each item below is merged, and the executor runs as a service in its VM and claims a test rotation
unit on production end to end with the fake adapter (item 9). ADR-0055's "Increments" section and
amendments 1 and 2 are the list of record; where this handoff and the ADR disagree, the ADR wins
and the session says so in its report.

1. **The executor package**, `src/rotation_worker/` in this repository (ADR-0054's settled
   location), a deterministic program with no LLM. It pulls: it claims rotation units with its own
   WORKER bearer, renews while a step runs, files evidence, and listens on no port (decision 1).
2. **The contract** (decision 3): every adapter declares its step order and, per step, `machine`
   or `human`, from the step kinds mint, stage, verify-new, store, distribute, verify-consumers,
   retire-old and confirm-dead. The contract, not each adapter, enforces decision 3's invariants:
   - retire-old only after every registered consumer has passed verify-consumers, and it is the
     last step that changes what any consumer accepts;
   - every step after mint refuses if the new value's fingerprint equals the old one's;
   - the old value is quarantined in a named secret in `Rotation / Work` before the keeper
     changes, and the executor refuses if the keeper already holds a new value with no quarantine;
   - every probe is shown to refuse a known-bad value in the same run before its answer counts;
     live is exactly 200, dead is exactly 401 or the provider's documented equivalent, anything
     else stops the rotation (amendment 1 replaces BWS's dead answer and its control);
   - quarantine and staging secrets are deleted after confirm-dead, and the deletion is recorded;
   - values never enter a process argument list: BWS is reached through its API in-process
     (decision 3 names one added dependency for this), never the `bws` CLI.
3. **The checks before acting** (decision 1 and amendment 2): the claimed unit's package revision
   is checked against intent-packages' git (its `source_commit` is an ancestor of `main` and its
   `content_hash` recomputes); the registry is read at a revision that is an ancestor of
   infraops' `main`; the credential is still `rotated_by_sds = true` there (R6); and the unit
   envelope's destination list, the revision's `profile_fields.destinations` and the registry's
   consumers are equal. A unit whose envelope marks it `touches_no_hosted_service` while its step
   writes a hosted consumer kind (`coolify-env`, `coolify-env-hash`) is refused.
4. **The destination string's format.** Increment 3 left it undefined (report, amendment 3 item 2):
   one flat string per destination, "consumer kind and destination". Define it so the registry's
   consumers render to it exactly, then tighten intent-packages' `destinations` validation to it
   and keep `rotation-openrouter-generic` revision 1 hashing as approved. Pick the envelope
   `constraints` key that carries the list in the same change.
5. **Re-probe, never trust evidence** (decision 1): before acting on a step evidence marks done,
   re-probe its result wherever a probe exists. Never take a provider key id from evidence.
6. **Evidence** in increment 3's contract: every step files a `rotation-step/1` payload that
   conforms to `tests/fixtures/rotation_evidence_schema.json`, through the existing evidence route.
   A recovered row carries the orchestrator's extra `recovery` key, so the executor validates a
   stored payload without it.
7. **The read-only comparison command** amendment 2 promises the approval runbook: given a
   credential, it prints the registry's destinations rendered in item 4's format beside the latest
   revision's list, and their difference. Replace the "manual until increment 4" sentence in
   `docs/operations/rotation-proposer.md` with it.
8. **A fake adapter** that exercises every contract path: a machine and a human step, a probe and
   its known-bad control, a quarantine and its deletion, an overlap ended by retire-old, and each
   invariant's refusal. Each invariant ships with tests that fail when it is removed; run the
   mutations by hand and report the count and every survivor
   (`memory: mutate-the-code-run-the-controls`).
9. **The VM and the service** (decision 2): an isolated OrbStack machine
   (`orb create --isolated --isolate-network`), the executor as a service under the VM's init
   system, its root token in a file readable only by the service's user, and its start path proven
   with only `PATH` and `HOME` set (R16, `memory: launcher-credentials-need-env-i-proof`). Built so
   it moves to a VPS unchanged. Then an end-to-end run on production: a throwaway rotation package
   and decomposition whose only adapter is the fake one, claimed, worked, evidenced and completed,
   with its human acts resolved at `/review`.
10. **The credentials** this increment creates (decision 4 and the asset table), none of them a
    rotated credential:
    - BWS projects `Rotation / Work`, `Rotation / Executor` and `Rotation / Healthchecks`. Keeper
      and provider projects come with the increments that use them.
    - The executor's BWS machine account (the root): write on `Rotation / Work` (and later the
      keeper projects), read on `Rotation / Executor` and `Rotation / Healthchecks`, nothing else.
      Its token lives only in the VM.
    - The executor's WORKER bearer in `Rotation / Executor`, its hash added to
      `ORCHESTRATOR_M2M_CREDENTIALS` under agent `rotation-executor`, and
      `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID=rotation-executor` set in the same restart. The
      orchestrator refuses to boot with the executor's credential and no setting naming it; the
      setting alone boots.
    - The read-only Healthchecks API key in `Rotation / Healthchecks`.

    All four are in the hand-rotated set (decision 4). Write `docs/operations/` pages for creating
    and rotating each by hand.

## Read first

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, in full: decisions
  1 to 9, the verification table, the threat model, and amendments 1 and 2.
- `docs/superpowers/plans/2026-10-10-rotation-increment-3-report.md`, especially the proposed
  amendment 3.
- `docs/decisions/0054-the-sds-owns-credential-rotation.md`: where the worker lives, and what
  ADR-0055 left standing.
- `docs/operations/driving-a-unit.md`, "Claim a rotation unit" and "Resolve dependencies".
- `docs/operations/credentials.md`: the M2M credential settings, and writing credentials before
  roles.
- `docs/operations/rotation-proposer.md`, and `src/rotation_proposer/` for how this repository
  already reads infraops' registry and intent-packages' checkout.

## Where the code is

Starting points, not designs. Read each before using it; the shapes are the code's, not this
handoff's (`memory: handoffs-state-pointers-not-shapes`).

| Concern | Where to start |
|---|---|
| Rotation-unit predicate, claim confinement, the window | `src/orchestrator/services/lifecycle/rotation_claims.py` |
| Evidence contract | `src/orchestrator/services/verifier/rotation_evidence.py`, `tests/fixtures/rotation_evidence_schema.json` |
| Human-act dependencies | `src/orchestrator/services/lifecycle/human_acts.py`, `web.py` (`resolve_human_act`) |
| Lapse recovery | `claims.release_expired_claim`, `orchestrator release-expired-claim` |
| Executor identity | security-standards `registry/agents/rotation-executor.yaml`, `registry/profiles/rotation-executor-v1.yaml`; boot check `main._require_rotation_executor` |
| Registry | infraops-mcp-server `.cred-consumers.toml` and `src/security-drift/cred-consumers.ts` |
| Destinations field | intent-packages `src/intent_packages/profiles/non_software_operational.py` |
| How a producer here talks to the orchestrator | `src/rotation_proposer/`, `src/estate_clients/` |

## Decisions already made

Don't reopen these; they are Devon's (ADR-0055 and its amendments).

- Pull, never push; the executor listens on no port. It lives in an isolated OrbStack VM, and the
  residual risk that anything running as Devon's user can reach it is accepted.
- Claims are confined both ways at the orchestrator; a lapsed rotation claim is released, never
  reclaimed; only the `live_estate` window is asked at claim, and only for units not marked
  non-hosted.
- Destinations are written by the package author, read by Devon in the revision's diff, carried in
  each unit's envelope, and compared by the executor with the registry. The proposer doesn't
  change.
- Per-unit completion clicks stay; don't add a type to `DETERMINISTIC_TYPES`.
- No automatic disable or revoke on an exposure (decision 6). The SYSTEM and VERIFIER bearers stay
  hand-rotated and outside the root's reach (decision 4).
- The Coolify token is option 2 (amendment 1), and it is created in increment 6, not here.

A new design fork found during the build goes to Devon, least machinery first
(`memory: devon-prefers-less-new-machinery`). Everything else is the session's to decide
(`memory: construction-mode-not-bear-wrestling`).

## Devon's acts

The session prepares each of these so it takes Devon one sitting, and says exactly what to click.
No value passes through a transcript, a tool argument or the Mac's clipboard where a path around
it exists (`memory: secrets-never-in-tool-arguments`):

1. Creating the three BWS projects and the root machine account with item 10's permissions, in
   the Bitwarden web console. The session's BWS identities can't create projects or accounts.
2. Creating the root's access token and placing it in the VM's token file. Design this so the
   value goes from the console into the VM without landing in a file on the Mac.
3. Writing the WORKER bearer into `Rotation / Executor`. The root reads that project but doesn't
   write it, by design; generate the value where it is stored, and carry only its hash out.
4. Creating the read-only Healthchecks API key and storing it in `Rotation / Healthchecks`.
5. Clicking the `/review` gates of item 9's end-to-end run. Driving `/review` clicks is
   pre-authorized during the build (`memory: sds-build-gates-are-pre-authorized`); confirm with
   Devon whether he wants to click this run himself, since it is the executor's first.

## Constraints

- **Edit only in worktrees**, one per repository, each with its own venv and test database.
- **No live rotated credential and no provider admin credential** enters the VM, the session or
  any test. The fake adapter's "provider" is local to the VM.
- **The orchestrator settings change is a production restart.** Write
  `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID` first and `ORCHESTRATOR_M2M_CREDENTIALS` second (boot
  refuses the credential without the setting, not the reverse), both rows of each variable, through infraops, after confirming
  no dispatched run is live, and verify the executor authenticates afterwards. Never print a
  credentials row's value (`docs/operations/credentials.md`).
- **Dependencies are few and pinned by hash** (T7). Name each new one and why in the report.
- **Run `make check` from a clean tree**, and `/code-review` on each diff. Before any
  `git reset --soft origin/main` squash, rebase on `origin/main` and read the staged file list
  (`memory: soft-reset-squash-reverts-merged-work`).
- **Every new route** goes into the exact route inventories, and every new POST into the
  idempotency matrix or a reasoned non-ingress entry. Under `src/orchestrator/` the word guards
  apply; the executor lives outside it, but check whether the guards scan `src/rotation_worker/`.

## Out of scope

- Any real provider adapter, the OpenRouter management key, and any keeper move (increment 5).
- The Coolify token, hosted writes and restarts (increment 6), and change-manager's `previous`
  parser, which increment 6 needs first.
- The rotation GitHub App and Actions-secret writes (increment 7). The executor's profile doesn't
  grant them yet (report, amendment 3 item 8).

## Closing steps

1. Merge each pull request when its checks pass; the session merges its own work.
2. Deploy the orchestrator if any orchestrator code changed, with `scripts/deploy_orchestrator.py`,
   and verify it as `docs/operations/deploy.md` describes.
3. Resolve this increment's backlog item, naming the pull requests, the deployed revision and the
   VM's name.
4. Report: what shipped, the mutation counts and survivors, the end-to-end run's unit ids, the
   dependencies added, anything the ADR got wrong (as proposed amendment text, not an edit), and
   increment 5's prerequisites.
5. Check each main tree's `git status` for stray files, then remove each worktree, test database
   (including xdist's `_gw` copies) and merged branch.
6. Write the increment 5 handoff, OpenRouter, if Devon asks for it.
