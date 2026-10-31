# Rotation: a stuck rotation tells someone, and scope comes from the grant

- **Date:** 2026-10-31
- **Layer:** `rotation` in `layers.toml` (`make layer L=rotation`)
- **Programs:** `rotation_worker` (VM and Mac instances), `rotation_proposer`, `rotation_decomposer`,
  `rotation_verifier`, and the rotation routes
- **Decisions it builds on:** ADR-0021 (amendments 1 and 2), ADR-0054, ADR-0055 and its amendments,
  ADR-0058
- **Status:** Draft for Devon's approval

## Intent

Rotation works. Since October 12 the SDS has rotated 15 of its 17 standing credentials with one click
per rotation, and its executor refuses anything git doesn't confirm. What it doesn't do is report on
itself. A worker that stops running, a unit that fails, or a package nobody approves tells nobody.
Its scope rests on a flag and a hardcoded list, and the member record's grant is read at one step of
six.

This design brings Rotation to the four outcomes Devon set for every layer:

1. **Every signal has a home:** a finding or failure reaches alert-hub as one keyed alert, never only
   a log.
2. **Scope comes from the member record:** act by grant, observe by membership.
3. **Records stay true:** no record, unit, or state says something that stopped being so.
4. **Edges are clear:** one test states each lane's outbound surface.

## What was measured

Recon on 2026-10-31 at `a94cd856` read the code, the lane logs on both instances, alert-hub history,
the registries, and the orchestrator through the observer bearer. Nothing was written. Change-manager
records and Healthchecks channel settings weren't read, because each needs a credential other than
the observer's.

**The estate.** 28 registry rows sit in four repositories, and 17 are handed to the SDS. 15 of the 17
standing packages have rotated; `bws-tok-ops-mini-20260730` and `bws-sds-operator-token` haven't. No
rotation unit is ready or claimed.

**The 04:00 window.** The infraops window is still loaded and ran 0 items on 2026-10-31. Of the 11
rows not handed over, it can execute only three: two classic GitHub PATs and `openai-project`. The
other eight are `executor: false`, so the window only reports them.

**Findings against the four outcomes:**

- **Signals.**
  - Worker liveness has no check on either instance. The Mac logged 39 crashes, all outages reaching
    `GET /api/v1/status-ledger`, and the VM logged 43 failed services. Nobody was told.
  - `unit_failed`, `unit_refused`, and `unit_not_worked` reach only logs. `work_watcher` deliberately
    skips rotation records, and `dead-letter` is a pull surface.
  - Proposer exit 3 and verifier exit 2 ping success, the shape Landing Part 1 fixed.
  - Non-act waits (readers, restarts, overlaps, adapter), an overlap past `until`, an unapproved or
    declined package, and a decomposition on `/review` tell nobody.
  - Act alerts (`rotation-act`, `rotation-mint`, `rotation-revoke`) exist and have no live proof
    beyond the October 27 self-test. No human act has waited since.
  - Healthchecks alerts carry one key per check and arrive with no message.
  - Both executor senders show `alertOnSilence: true` with a null interval, so the silence watch is
    inert, and they post nothing on a healthy pass. The VM's sender is named `rotation-executor.`,
    with a trailing dot.
- **Scope.**
  - Live `credential_rotation` grants sit on exactly the four `REGISTRY_REPOSITORIES`, and their
    reason text cites that list.
  - Only `work_carrier` and `membership_check` read the grant. The executor, proposer, and decomposer
    act without it.
  - Unsetting the flag stops a unit mid-rotation. Withdrawing the grant doesn't, because a carried
    record is split off before judgment.
  - `membership_check` is one-directional and compares the tuple, not flagged rows, so a flagged row
    in a fifth repository is invisible.
  - Nothing looks for a registry outside the tuple.
- **Records.**
  - A failed or cancelled rotation leaves its record `approved`, reports `replayed`, and exits 0. A
    failed unit after `store` also blocks every BWS token switch (`overlaps.py`).
  - The cancel runbook never closes the record or calls `record-rotation`.
  - `lastRotated` is false for `github-finegrained-mirror` (July 2; units completed October 20) and
    `bitbucket-mirror-token` (July 3; units completed October 21). This fails safe. The proposer records a
    rotation only for a credential still due, so clearing `rotate_requested` first skips the record.
  - A failed stage or a failed evidence write after an OpenRouter mint orphans a key, and each retry
    mints another.
  - A staged value from an abandoned occurrence stays until someone deletes it, and neither store
    can list keys.
  - Destinations compare ordered at the executor and sorted at the proposer and decomposer, so a
    reordered package passes both and fails at its first unit.
  - `touches_no_hosted_service` has four copies with no equality test, and `CLASS_TYPES` has no test
    against infraops' `CLASS_POLICY` (`coolify-api-token` is missing from it).
- **Edges.**
  - No table states the layer's surface. Each lane has a surface test of a different shape, and the
    verifier's covers only `named-check`.
  - The worker's orchestrator client is an exact route list that includes GET and POST. It is not
    POST-only, and `/m2m-identities` is in neither test list.
  - `rotation_proposer` has no client of its own, so a widening in `bump_proposer` widens it.
  - `node`, `git push`, BWS, `ssh`, and `/usr/bin/security` are stated in no table.
  - Launcher bearers aren't pinned, and `rotation_verifier/required_checks.py` is bump work in this
    layer.
- **The Mac's revision.** Both instances run a pinned commit by design. The postgres deploy (#556)
  reinstalled the VM and not the Mac, and no watcher compares an instance's `source_revision` to
  `main`. The lag has caused no harm: #556's shared changes don't affect Keychain types, and #557 is a
  refactor. It did produce one false error. On October 30 the Mac logged `no adapter named
  'postgres-role'` for a VM unit: `_ours` runs first, but it counts an adapter this build doesn't
  know as its own (`worker.py:270-273`), and `check_unit` then refuses it. The claim got a 409, so
  nothing was written.
- **Stale text.** `PROJECT.md` increments 6 to 8 are stale. ADR-0055's header lists 3 of 17
  amendments. ADR-0055 line 350 says observer and drift-reporter rotate together; amendment 9 split
  them. `rotation-proposer.md:79-81`, `rotation-executor.md:127`, and `cli.py:16-17` predate amendment
  16. `rotation-proposer.md` says the grant is checked at onboarding, and it's checked at carry.

## Decisions Devon made

Recorded 2026-10-31, in this conversation:

1. **The grant stays per registry repository, and `rotated_by_sds` stays the per-credential switch.**
   `REGISTRY_REPOSITORIES` stays in the executor as a ceiling the orchestrator can't widen, and the
   grant only narrows it. The proposer, decomposer, and executor read the grant, the executor before
   each step beside the flag. `membership_check` compares flagged rows and grants in both directions
   and reports a registry file in any member without a grant.
2. **A stored copy in a member with no rotation grant is allowed.** The rotation App's installation on
   that repository is the deliberate act, and refusing strands the holder on a dead value.

## Goals

- A worker instance that stops passing raises one alert, and clears it when passing resumes.
- A rotation unit that fails, or waits past its threshold, raises one keyed alert naming the unit and
  the next step.
- Every proposer and verifier finding reaches alert-hub, keyed per subject.
- Withdrawing a grant stops new work and stops a running rotation before its next step.
- A failed rotation is visibly failed everywhere that reports it, and `lastRotated` is true.
- One test states every rotation lane's outbound surface.
- `PROJECT.md`, the ADRs, and the runbooks say what shipped.

## Non-goals

- Rotating the 11 rows not handed over, except as Part 5 says.
- The 11 standalone Coolify databases (their own backlog item).
- Credential-level grants, or deleting `REGISTRY_REPOSITORIES` (decision 1).
- A dedicated SYSTEM identity for the decomposer. That residual is shared with the landers.
- Updating an instance automatically. An instance runs a pinned commit by design (ADR-0055), and
  reporting that it is behind is enough. *Claude's call.*
- A registry scan of `.bws-secrets.toml`. A manifest lists what a repository reads, not what it owns,
  so 22 files would produce noise, not findings. *Claude's call.*

## Anti-goals

These would make the work a failure even if every test passed:

- **A rotation failed by its own reporting.** No step can answer "not yet" once a unit is claimed, and
  any exception spends an attempt. An unreadable grant, or an alert-hub outage, must never fail a unit.
- **A false alarm from the other instance.** An instance alerts only about units it owns.
- **A wider executor.** The grant may only narrow what the executor acts on. Nothing here lets an
  orchestrator answer add a repository, credential, or destination.
- **Alert noise.** One alert per subject, keyed and resolved when the subject clears. A deploy-length
  orchestrator outage raises nothing.
- **A change to what an approved package does.** Every approved package must pass the same checks
  after this work, and a dry run over all 17 proves it before any instance updates.
- **New machinery where a pattern exists.** Alerts reuse `KeyedAlerts` and the first-sighting clock in
  `lander/held_alerts.py`. Edges reuse `test_bridge_edges.py`'s row and `PLATFORM_EDGES`.

## Constraints

These invariants in the orchestrator's `CLAUDE.md` govern the work:

- Any exception after a claim fails the unit and spends an attempt. A step that must wait waits before
  the claim, in `worker.py::_take`.
- A failed rotation unit can't be superseded, so dry-run anything that changes what a real unit does.
- Check that no rotation unit is ready or claimed before updating an instance, merging change-manager,
  or restarting a rotation target.
- The scheduled lanes run the main tree's working copy, and each executor instance runs its pinned
  commit. Reinstalling needs a full commit id and the VM or Mac installer.
- `rotation_worker` may read only its listed orchestrator routes, and a new route joins
  `test_client_surface.py`, the edge table, and `OUTBOUND_ALLOWLIST` if it adds an HTTP client.
- Launchers that send alerts use the `sds-bridge` token through `sds_bws_value`, with `--color no`.
- No reported finding kind may be a substring of another.

## The design

### Part 1: Every signal has a home

**Worker liveness.** alert-hub's silence watch is the home. It already raises `silent:<sender>` and
resolves it, both senders already set `alertOnSilence`, and any ingest request counts as seen
(`ingest/pipeline.ts`). Each instance ends a pass by resolving its own key,
`rotation-heartbeat:<store>`, which is never opened. Both senders get `expectedIntervalS: 1800`, so a
deploy-length orchestrator outage raises nothing, and 39 crashes become an alert only when they
last. *Claude's call,* over a Healthchecks ping: the sender token is already on both instances, so
there is no new secret, route, or provisioning. The plan's first step proves on a test sender that
resolving a key with nothing open writes no alert and no history; if it does, the plan comes back to
Devon. The VM's sender loses its trailing dot.

**Ownership.** `_ours` stops counting an unknown adapter as its own. It answers the server's rule: a
unit belongs to the Keychain instance when its adapter is in `KEYCHAIN_ADAPTERS`, and to the VM
otherwise. `rotation_worker` can't import the orchestrator's set, so it holds a copy, held equal by a
test the way `test_target_grant.py` does. A truly unknown adapter is therefore the VM's to refuse,
and a Mac behind `main` stays quiet. This has to ship with the alerts, or the October 30 case becomes
a false alarm.

**Failed units.** Each pass, an instance reads the ledger for rotation units it owns in `failed` or
`revision_required`, and raises `rotation-unit-failed:<unit>`. The message names the credential, the
reason from `last_failure`, and the runbook step. It resolves when the unit leaves that state. This
reuses `Notifier` and the ledger read `waiting_acts` already makes, so `unit_refused` and `unit_failed`
are both covered, because each ends in `client.fail`. A unit in `awaiting_review` past 48 hours raises
`rotation-unit-review:<unit>` from the same read, aged by `last_event_at`, so it needs no state.

**A unit that can't be claimed.** `unit_not_worked`, and a refusal whose claim was also refused, leave
no state in the ledger. Each raises `rotation-unit-stuck:<unit>` on the same first-sighting clock as
waits.

**Waits.** Each `*_waits` result and `unit_skipped` starts a first-sighting clock, and a unit past its
threshold raises `rotation-unit-waiting:<unit>` naming what it waits for. There are two plain ints:
`ROTATION_WAIT_ALERT_HOURS`, default 48, because the longest measured wait on a shut window was about
31 hours (374 skipped passes); and `ROTATION_READER_WAIT_ALERT_HOURS`, default 192, because retire
waits for weekly readers and a healthy rotation can wait 7 days. An overlap past `until` raises
nothing: the overlap ends by design and retire finds the old value refused. The alert names the date
on the wait it is already reporting.

**Behind `main`.** Each instance compares its `source_revision` with the newest commit on `main` that
touched `src/rotation_worker`, `src/estate_clients`, or `src/alert_hub_client`, read from the git
mirror it already keeps. Seven days behind raises `rotation-instance-behind:<store>`. `deploy.md` gains
the rule that a change to those paths reinstalls both instances.

**Proposer findings.** The launcher adds the `sds-bridge` token. Each `FINDING_STATUSES` row raises
`rotation-finding:<credential>:<status>`, which resolves when the credential stops reporting it.
`awaiting-approval` and `declined` stay non-findings but raise `rotation-approval:<credential>` after
48 hours, because a package nobody approves is how a due credential stays due. The launcher keeps
`--finding 3`, since the alert now carries the finding.

**Verifier and decomposer.** The verifier lane is unchanged: `rotation-unit-review` comes from the
owning instance, and its lane can't see an `awaiting_review` unit after the one pass that moved it
there. Its exit 2 for bump units is Landing's. The decomposer's outcome alarm stays, and it gains
`rotation-decomposition:<revision>` for a decomposition waiting on `/review` past 48 hours.

**Keys.** Every new key goes through `alert_key`. The existing raw `rotation-act` keys move to it, and
the plan checks that the move resolves nothing open (none are open today).

### Part 2: Act by grant, observe by membership

**Proposer.** A due credential whose row is in a repository without a live `credential_rotation` grant
gets a new finding status, `ungranted`, and no package write and no record. It reads members through
`sds_members.read_members` with the observer bearer. If that read fails, the pass exits 2 and acts on
nothing. A dry run has no bearer, so it says the grant wasn't read.

**Decomposer.** A record whose credential's repository has no grant is refused and reported. Its
`orchestrator_permits` gains `GET /api/v1/members`.

**Executor.** `_take` reads the grant before the claim. No grant means the unit waits and is reported
through Part 1's wait clock. A claim never happens, so no attempt is spent.

`_before_step` then reads the grant beside the flag, and a withdrawn grant raises `ContractError`,
exactly as a handed-back flag does. *Claude's call:* an unreadable grant at `_before_step` keeps the
answer read before the claim and logs it, because failing a unit mid-overlap on a read outage spends
an attempt and can leave two values live. An explicit withdrawal still stops it.

The client gains one route, `GET /api/v1/members`. `entry.repository` selects the row.

**Membership.** `membership_check` gains three rows:

- A flagged row in a repository with no grant.
- A grant on a repository with no flagged row.
- A `.cred-consumers.toml` in any non-retired member outside `REGISTRY_REPOSITORIES`.

This is the "observe by membership" half. The existing tuple-to-grant row stays. A stored copy is not
compared, by decision 2.

### Part 3: Records stay true

- **A failed rotation shows as failed.** The proposer gains its own small GET-only client on the
  observer bearer, because the borrowed observation client is POST-only and widening it would widen
  `bump_proposer`. Each pass it walks open rotation records, due or not, and joins their units. A record
  with any unit `failed` or `cancelled` reports `stalled`, a finding, not `replayed`. It stays
  `stalled` until a person acts, so the alert stays open.
- **The cancel path closes the loop.** `rotation-executor.md`'s recovery section gains the record step
  and when `record-rotation` applies. *Whether a value changed decides it:* after a cancel where the
  old value stayed, nothing is recorded, and the next occurrence comes from a new `rotate_requested`.
- **`lastRotated` follows the rotation.** infraops' `cred-findings` gains a read-only `lastRotated`
  field for handed-over rows. The proposer walks those rows, and when a record retired by
  `work-watcher` is newer than `lastRotated`, it runs `record-rotation --date <retired date>`. The
  comparison makes the pass idempotent, so a repeat can't reset an age clock. The two false rows repair
  themselves on the first pass, and an unreadable state file records nothing.
- **An orphan key is removed where it can be.** OpenRouter's key id is the full key hash, which
  evidence must not hold, so no durable handle survives the process. When a stage or evidence write
  fails after a mint, the same process deletes that key by the id the mint returned, then re-raises.
  A crash between the two leaves the orphan, bounded by the $5 limit and 400-day expiry. *Claude's
  call:* that residual is stated and accepted, because listing keys to find it would break "deletes
  only the pinned hash".
- **An abandoned staged value is named, not hunted.** Neither store can list keys, and listing the
  Keychain would break "values only on stdin and stdout". So `rotation-unit-failed` names the
  occurrence's staging and quarantine keys, and the cancel runbook has a step to delete them.
  *Claude's call:* a value someone staged by hand for a rotation that never ran stays unreported.
- **One rule compares destinations.** `destinations.py` gains the one comparison, and the executor,
  proposer, and decomposer call it. A test runs it over all 17 approved packages.
- **Agreement tests.** One test holds the four `touches_no_hosted_service` copies equal, and one holds
  `CLASS_TYPES` against infraops' `CLASS_POLICY`, pinned the way the destination format is. Adding
  the field to `cred-findings` is an infraops change, so that repository's worktree rule applies.

### Part 4: One test states the edges

`tests/architecture/test_rotation_edges.py` reuses `Edge` and adds columns for what these lanes
reach: `platform` (Coolify, Healthchecks, Azure log host), `providers` (one row per adapter), and
`processes` (`node`, `git`, `ssh`, `security`, and the BWS SDK). It has four lanes and:

- Runtime checks through each lane's `permits`, with the per-instance predicates captured the way
  `_captured_permits` does.
- Import and process pins, including the child-process imports AST can't see in
  `bws_machine_token.py`, which become a named constant the test reads.
- Launcher UUIDs equal to `bearers`, and the worker's config ids stated in the table.
- `rotation_proposer` states the part of `bump_proposer`'s surface it uses, and fails if it calls
  anything else. Its change-manager client stays borrowed; only Part 3's read is its own. *Claude's call:* the test is the control, and a copied
  client would be more code to keep equal.

The stale comments the recon listed get fixed (`client.py:119`, the verifier's allowlist and
`CLASSIFICATION` row, the proposer test's docstring). `rotation_verifier/required_checks.py` and its
test move to the `landing` layer in `layers.toml`, with no code move.

### Part 5: Close the backlog

- `PROJECT.md`: increments 6, 7, and 8 close with what shipped, and new items name what remains.
- **`orchestrator-factory-runner-bearer`:** the registry flag and a generated package. It rotates when
  due, and proving it early needs Devon's say-so.
- **`bws-tok-ops-mini-20260730` and `bws-sds-operator-token`:** left to rotate when due.
- **Increment 9.** The window retires only when no row depends on it. The two classic PATs and
  `openai-project` are the rows it can execute, so each needs a ruling first: hand over, or retire the
  credential. The plan for this part brings Devon that table, and the window and change-manager's
  `rotation` source go only after his answers. `sources.py` is a change-manager change, so the no-unit
  check applies.
- ADR-0055 gets an amendment for this design, and its header lists all amendments. ADR-0054's
  consequences and the stale lines get corrected.

## Order

1. Part 1: alerts first, so the rest is watched.
2. Part 2: the grant.
3. Part 3: records. It depends on Part 1's alerts.
4. Part 4: edges, last among the code parts, so the table states the final surface.
5. Part 5: backlog and increment 9.

Each part gets its own plan, approved by Devon, then subagent-driven execution, a whole-branch review,
a deploy, and a live pass.

## Live proof

Nothing is due, so each part states what it can prove without forcing a rotation. A staged proof
proves only the staging.

| Part | Can prove live, no rotation | Has no live proof until a real event | Needs Devon first |
|---|---|---|---|
| 1 | Liveness: stop the Mac agent past 30 minutes and watch `silent:` open, then resolve. This needs no rotation unit ready or claimed. Behind-main: the instances before reinstall. Proposer alerts on the next real finding. | `rotation-unit-failed`, `rotation-unit-waiting`, and act alerts on a real unit | A canary rotation that fails on purpose |
| 2 | `membership_check` rows. The proposer's `ungranted` against a staged withdrawal on `sds-canary`, which holds no registry. | Executor stop before a step | Withdrawing a real grant during a canary rotation |
| 3 | `lastRotated` self-repair on the two rows and the destination rule over 17 packages | Orphan-key delete and `stalled` on a real failure | Any forced OpenRouter mint failure |
| 4 | The test is the proof | Not applicable | None |
| 5 | Increment bookkeeping | Factory-runner bearer rotation | Early rotation and each increment 9 ruling |

The canary (`rotation-canary`) is the safe subject for a staged failure, and it proves only the
reporting path. It doesn't prove the behavior on a live credential.

## Left to the builder

- Where the shared wait clock lives, given `held_alerts.py`'s first-sighting pattern.
- The exact wording of each alert, with the next step in it.
- Whether the behind-`main` read walks the mirror or asks git for the newest commit on those paths.
- How the edge table's extra columns extend `Edge` without changing bridge and landing rows.
