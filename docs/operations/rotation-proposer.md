# The rotation proposer

ADR-0054 amendment 1. `src/rotation_proposer/` turns a credential that infraops reports due for
rotation into a revision of that credential's standing package and, after Devon approves the
revision by name, into a `work` change record. Launcher: `scripts/run-rotation-proposer.sh`.
`scripts/install-rotation-proposer-launchd.sh` schedules it daily at 06:30 with `--submit`, and its
dead-man check is `sds-rotation-proposer`, with exit 3 as its finding code. Whether either is
installed is read from `launchctl list` and the Healthchecks listing, not from this page.

## What it reads

- **What is due: infraops, not this repository.** It runs
  `node <infraops checkout>/dist/cli/security-drift-cli.js cred-findings` and reads JSON. That
  subcommand applies infraops' own due rule (`credFindings`) to the registries listed in
  `~/.config/infra-drift/cred-consumers.list` and to `cred-rotation-state.json`. This program
  opens neither file. The JSON shape is pinned by `tests/fixtures/rotation_findings.json`. A change
  to either side starts there.
- **The packages: the intent-packages checkout** that `bump-proposer` uses, from the same variable
  (`BUMP_PROPOSER_PACKAGES_CHECKOUT`).

An infraops checkout without the `cred-findings` subcommand (infraops-mcp-server #109) answers
`unknown command`. The pass then exits 2. Land #109 and pull that checkout before any pass is
expected to work.

## Which rotation, and its name

Each credential yields one rotation per pass. When infraops reports several triggers for one
credential, the precedence is exposure, then request, then age. The trigger decides the
`occurrence` the package revision carries:

| Trigger | infraops check | occurrence |
| --- | --- | --- |
| exposure | `cred.exposure-rotate` | `<exposure id>-exposure` |
| request | `cred.rotation-requested` | `<rotate_requested date>-requested` |
| age | `cred.rotation-age` | `<date it last rotated>-age` |

The occurrence never contains today's date. If it did, every daily pass would see a new rotation
and revise the package again. `cred.unknown-class` is skipped. Any other check this program does
not know is reported `unrecognised`, which is a finding.

## The order of acts, across passes

For a due credential whose package is `rotation-<credential_id>`:

1. **First pass.** File the observation (`rotation_proposer` / `rotation_due` / subject
   `credential`). Then `revise` the package, write the quoted `occurrence:` line and the
   registry's destination list, `transition --to ready_for_review`, re-pin the hash fixture,
   commit, and publish. The publish goes through `bump_proposer.standing.publish`, which is the
   one publishing act. Nothing is proposed. The line reads `revised`. A credential with no
   package gets one generated instead (see "A package generated from the registry"), and the
   line reads `generated`.
2. **Passes while it waits.** The pass writes nothing and reports `awaiting-approval`.
3. **Devon approves the revision by name**, in the packages checkout, and publishes that commit.
   No approval policy grants this profile, and this program never runs `approve`. The approval
   does not move the hash fixture (`package_hash` drops `status`), so the commit holds the
   package and its lineage only. The checkout must be clean and level with `origin/main` again
   before the next pass, or the pass exits 2. Before approving, Devon does two reads of
   `profile_fields.destinations` (ADR-0055 amendment 2):
   - **Read its change against the last approved revision**, not the proposer's latest commit:
     the baseline is the `commit` of the latest entry under `approvals:` in the package's
     `lineage.yaml` (`git diff <that commit> -- packages/rotation-<credential_id>/package.yaml`).
     The proposer writes the registry's list, so a change in it is a change to the registry since
     that approval: read it as a change to what the rotation will write.
   - **Compare it with the registry.** Run
     `.venv/bin/rotation-worker compare-destinations <credential_id>`. It fetches intent-packages
     and the four registry repositories into its own mirrors (`~/.cache/rotation-worker/mirrors`),
     and prints the registry's destinations as a package lists them, then compares them with the
     package's, both at `main`: `both`, `registry` (missing from the package) or `package` (not in
     the registry) on each line, then the verdict. Exit 0 means equal; 1 means the registry
     changed after the revision was written, and the next pass revises it again; 2 means it
     couldn't read one of them. The command is the executor's own comparison
     (`src/rotation_worker/compare.py`).
4. **The next pass.** If the registry's destinations changed since the approved revision was
   written, the pass revises it again instead of proposing (step 1), because the executor would
   refuse the first unit of a package that differs from the registry. Otherwise it files the
   observation again. It replays and returns the same id. Then it proposes
   the `work` record with the propose-scoped bearer. The record names the approved revision and
   that observation, with `bump_proposer`'s asserted fields (`WORK_RECORD_FIELDS`). Later passes
   replay it. Once Devon approves the record, the work carrier registers its intake, and the
   rotation decomposer proposes its decomposition for a person to approve on `/review` (see
   "The rotation decomposer" in `rotation-executor.md`).
5. **The pass after the rotation completes.** The work watcher retires the record once every unit
   it caused has completed: `resolved`, decided by `work-watcher`. Seeing that, the pass runs
   `security-drift-cli record-rotation --cred <credential_id> --date now` instead of replaying the
   proposal, and reports `recorded`. infraops' `lastRotated` then moves to that day, normally the
   day after the work completed, and the next `cred-findings` stops reporting a requested or aged
   credential due. Two cases record nothing and are findings:
   - **`resolved-by-person`:** the record was resolved by anyone but `work-watcher`, which says
     nothing about whether the work completed. A person decides what happened.
   - **`exposure-open`:** the rotation completed, but `record-rotation` doesn't close an exposure.
     Once the old value is confirmed dead, a person runs `security-drift-cli resolve-exposure` and
     `record-rotation` for the credential.

Compared with `bump_proposer`, the order is observe, revise, publish, propose, with the approval
moved out of the pass and given to a person. `bump_proposer` approves by policy and proposes in the
same pass.

## Alerts

After a `--submit` pass the proposer syncs alert-hub alerts as sender `sds-bridge`
(`rotation_proposer/alerts.py`). The state directory is `ROTATION_PROPOSER_STATE_DIR`, default
`~/Library/Application Support/sds/rotation-proposer`; it holds `alerts.json`, what the pass fired,
and `approval-since.json`, when each approval wait was first seen.

- `rotation-finding:<credential>:<status>` fires on the pass that reports a finding status, and
  resolves on the first pass that doesn't.
- `rotation-approval:<credential>` fires once a credential has stayed `awaiting-approval` or
  `declined` for 48 hours. Waiting on a named person is the lane working, but not for days.

A pass that couldn't use its inputs (exit 2) syncs nothing, so every remembered key holds. A
credential reporting `error` holds its remembered keys and its approval clock: that status may only
say a fetch failed, which says nothing about its other findings, and the code can't tell it from a
genuine error on one credential. The sightings are written before alert-hub is asked and kept when
it refuses, so an outage never restarts a clock. A failed sync is printed to stderr and never
changes the exit code.

## States it reports and does not touch

- **`stacked`** (a finding): the tip awaits review for one occurrence while infraops reports
  another. The pass does not replace a revision Devon is reviewing.
- **`declined`**: Devon rejected the revision that carries this occurrence.
- **`unexpected-state`** (a finding): any lifecycle state this lane never writes.
- **`legacy`**: the registry has not marked the credential `rotated_by_sds`, and no package
  exists. infraops' 04:00 window rotates it; this lane does nothing.
- **`not-handed-over`** (a finding): a standing package exists, but the registry has not marked the
  credential `rotated_by_sds`. The pass never acts on it, because infraops' window still could.
  Set the flag in the credential's `.cred-consumers.toml`, and only when no legacy rotation of it is
  in flight.
- **`unlaned`** (a finding): the registry hands the credential to the SDS, so infraops' window
  refuses it, but no standing package exists and none can be generated: its registry `class`
  names no type the SDS rotates (`rotation_worker.types.CLASS_TYPES`).

Every finding carries `rotated_by_sds` from infraops' `cred-findings`. A finding without it (an
infraops older than the handover) stops the whole pass with exit 2.

## A package generated from the registry

A due credential whose registry row sets `rotated_by_sds`, with no standing package, gets revision
1 generated (`src/rotation_proposer/generate.py`, ADR-0055 amendment 15, increment 4). Its type is
its registry `class`, through `CLASS_TYPES`; everything the executor reads follows from that
type's capabilities and the row:

- **Criteria:** one per machine step of the derived plan (`plans.derive`), numbered as the plan
  numbers them, with the evidence text the executor's `select_plan` and the verifier read.
- **Reach:** `external_system`, plus `operator_machine` for a credential the Mac keeps and
  `live_estate` for a hosted service.
- **Authority:** reading and writing secrets, minting and revoking where the provider does them
  by machine, and changing infrastructure for a hosted service; the rest is prohibited.
- **Destinations and occurrence:** the row's destinations and the due occurrence.

intent-packages' `create-rotation` validates it and writes it with its first lineage entry; the
proposer then takes it to `ready_for_review` and publishes it like any revision, and Devon
approves it by name. The proposer sees only due credentials, so a newly flagged credential gets
its package when it first comes due. Onboarding a credential is therefore its registry row: a
`class` the SDS rotates and `rotated_by_sds = true`, in a registry repository the member record
grants `credential_rotation`.

## Authoring a standing rotation package by hand

Generation covers every type in `CLASS_TYPES`. To author one by hand instead, use the
`non-software-operational` profile, name the package `rotation-<credential_id>`, and set
these `profile_fields`: `standing: true`, `credential_id: <registry id>`, `occurrence`
(quoted), and `destinations`, the credential's full destination list. Copy the list from
`rotation-worker compare-destinations <credential_id>`, which prints each registry destination in
the one format the package, the unit envelope and the executor share
(`src/rotation_worker/destinations.py`): the credential's `provider name="…"`, then one string per
consumer, its kind and its identity fields as `name=<JSON value>`, sorted. To have Devon approve the first
rotation at authoring time, set `occurrence` to that rotation's occurrence from the preceding
table (for example `'2026-10-07-requested'`); the first pass then proposes rev 1 without revising
it. `'unassigned'` instead makes the first pass
revise. A package whose name and `credential_id` disagree stops the pass with exit 2. Then mark
the credential `rotated_by_sds = true` in its registry entry, or the pass reports
`not-handed-over` and does nothing.

## Before the first `--submit`

The intent-packages checkout must be on `main`, level with `origin/main`, and its own `.venv`
synced. The lifecycle commands run that checkout's interpreter, and an `intent_packages` older
than intent-packages #113 does not know `standing`, `credential_id` or `occurrence` on this
profile, so it refuses the package. The checkout is the bump lane's too, so bring it current
between passes, never during one.

The observation vocabulary (migration `0041_rotation_proposer_obs`) must be applied in
production; without it every pass reports `unobserved` and exits 3. An approved rotation record is
carried by the work carrier's operational branch, which registers an intake with no target
repository, worked by HQ rather than dispatched to a runner.
