# The rotation proposer

ADR-0054 amendment 1. `src/rotation_proposer/` turns a credential that infraops reports due for
rotation into a revision of that credential's standing package and, after Devon approves the
revision by name, into a `work` change record. Launcher: `scripts/run-rotation-proposer.sh`. It is
built and not scheduled: no LaunchAgent installer and no Healthchecks check exist for it yet.

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
   `credential`). Then `revise` the package, write the quoted `occurrence:` line,
   `transition --to ready_for_review`, re-pin the hash fixture, commit, and publish. The publish
   goes through `bump_proposer.standing.publish`, which is the one publishing act. Nothing is
   proposed. The line reads `revised`.
2. **Passes while it waits.** The pass writes nothing and reports `awaiting-approval`.
3. **Devon approves the revision by name**, in the packages checkout, and publishes that commit.
   No approval policy grants this profile, and this program never runs `approve`. The approval
   does not move the hash fixture (`package_hash` drops `status`), so the commit holds the
   package and its lineage only. The checkout must be clean and level with `origin/main` again
   before the next pass, or the pass exits 2.
4. **The next pass.** File the observation again. It replays and returns the same id. Then propose
   the `work` record with the propose-scoped bearer. The record names the approved revision and
   that observation, with `bump_proposer`'s asserted fields (`PROPOSAL_FIELDS`). Later passes
   replay it.

Compared with `bump_proposer`, the order is observe, revise, publish, propose, with the approval
moved out of the pass and given to a person. `bump_proposer` approves by policy and proposes in the
same pass.

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
  refuses it, but no standing package exists, so nothing rotates it. This program never authors
  one. Authoring `rotation-<credential_id>` brings the credential into the lane.

Every finding carries `rotated_by_sds` from infraops' `cred-findings`. A finding without it (an
infraops older than the handover) stops the whole pass with exit 2.

## Authoring a standing rotation package

Use the `non-software-operational` profile, name the package `rotation-<credential_id>`, and set
these `profile_fields`: `standing: true`, `credential_id: <infraops registry id>`, and
`occurrence` (quoted). To have Devon approve the first rotation at authoring time, set it to that
rotation's occurrence from the preceding table (for example `'2026-10-07-requested'`); the first
pass then proposes rev 1 without revising it. `'unassigned'` instead makes the first pass
revise. A package whose name and `credential_id` disagree stops the pass with exit 2. Then mark
the credential `rotated_by_sds = true` in its registry entry, or the pass reports
`not-handed-over` and does nothing.

## Before the first `--submit`

The intent-packages checkout must be on `main`, level with `origin/main`, and its own `.venv`
synced. The lifecycle commands run that checkout's interpreter, and an `intent_packages` older
than intent-packages #113 does not know `standing`, `credential_id` or `occurrence` on this
profile, so it refuses the package. The checkout is the bump lane's too, so bring it current
between passes, never during one.

The observation vocabulary (migration `0041_rotation_proposer_obs`) must be live: build the image,
migrate, swap (`deploy.md`). Without it every pass reports `unobserved` and exits 3. The work
carrier also holds packages with no target repository (ADR-0054 increment 2 gives it an
operational branch), so an approved rotation record is not carried until that change lands.
