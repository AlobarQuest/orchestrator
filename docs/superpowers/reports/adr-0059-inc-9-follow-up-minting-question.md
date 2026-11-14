# Question: where does follow-up minting run in SDS? (ADR-0059 increment 9, worker W-144)

This doesn't block the PR. ADR-0059 Amendment 9 leaves the placement to increment 9's plan, by
Devon's decision, and carries it as a verify item. HQ can put the options in the following
sections to Devon when it plans increment 9.

## What minting is today

- `POST /api/v1/follow-ups/mint` (`src/orchestrator/api/routes/intake.py`, line 290) mints the
  due `follow_up_review` units. The service refuses any actor that isn't SYSTEM
  (`services/intake/follow_ups.py`, line 217). The pass is a database read plus an append-only
  write. It makes no outbound call.
- Minting is idempotent by structure: the unit id is `uuid5(NAMESPACE_URL,
  "sds:follow-up:<revision_id>")`, backed by a unique constraint. A second pass on the same day,
  or a paired pass at cut-over, reports `already_minted` and mints nothing twice
  (`docs/operations/follow-up-scheduling.md`).
- The only scheduled caller is infra-drift: `scripts/drift-audit.sh` lines 152-179 at
  infraops-mcp-server `c3359bf`. It reads the SYSTEM bearer (`221a48d5-…`) with the `sds-operator`
  Keychain item and runs `node dist/cli/orchestrator-cli.js mint-follow-ups` as a non-fatal step.
- The orchestrator's own CLI has the same command: `orchestrator mint-follow-ups
  --idempotency-key <key>` (`src/orchestrator/cli.py`, line 765). The lane image is built from
  the orchestrator image (`lanes/Dockerfile`, line 44), so every lane container already carries it.
- ADR-0007 "Scheduled trigger" and ADR-0002/0003 keep the orchestrator push-only and loop-free.
  The schedule lives outside `src/`.

## Options

### A. A new VPS lane, `follow-up-mint`, with its own lane identity

A launcher `scripts/run-follow-up-mint.sh` runs `orchestrator mint-follow-ups` once a day. It gets
a compose file under `lanes/`, its own Coolify resource (Amendment 1), a Healthchecks check, and a
lane BWS identity that reads only the SYSTEM bearer.

- **Identity:** a new lane account, one more reader of `221a48d5-…`. Devon mints its token and
  pastes its ping URL (ADR-0055 amendment 19 and Amendment 2), and the registry rows land in
  two changes, as for every lane.
- **What changes:** the orchestrator gets a launcher, a compose file, `.bws-secrets.toml` and
  `.cred-consumers.toml` rows, and a layer manifest entry. infraops-mcp-server deletes lines
  152-179. The docs that change are ADR-0007 "Scheduled trigger", `follow-up-scheduling.md` "Running
  a pass", `credentials.md` (line 90), and `.cred-consumers.toml` (line 221).
- **Cut-over:** one paired pass is safe because minting is idempotent. Then infraops deletes the
  step. There's no Mac job to boot out.
- **Cost:** the most new machinery. A full lane exists to make one HTTP call a day.

### B. A step inside a lane that already holds SYSTEM

A lane that already reads `221a48d5-…` adds one non-fatal minting step to its pass. The lanes that
hold SYSTEM are `rotation-decomposer` (it has a compose file under `lanes/`), `bump-driver`,
`work-carrier` and `inert-landing` (their launchers read the bearer and they move in later
increments), and `activation-sweep`, which stays on the Mac and so isn't a candidate.

- **Identity:** none new. The SYSTEM bearer gains no reader, because the host lane already reads
  it. Minted units are attributed to `orchestrator-system`, as they are today.
- **What changes:** one lane's launcher or program gets a step, and infraops deletes lines
  152-179. The doc changes are as in option A. No resource, check, token or ping URL is added.
- **Cost:** it couples minting to an unrelated lane's schedule, alarms and kill switch, which is
  how minting ended up inside infra-drift in the first place. A failed mint must stay non-fatal,
  or it would hold the host lane's outcome check down. The host lane's purpose also widens, against
  the single-purpose rule.

### C. The deployed orchestrator mints on its own schedule

The orchestrator runs minting from a loop, a startup timer, or Coolify's scheduled-task feature,
running `orchestrator mint-follow-ups` inside the orchestrator container.

- **Identity:** in-process, it needs no bearer, but an actor must still be attributed. As a
  Coolify scheduled task, it needs the SYSTEM bearer in the orchestrator's own environment, a
  new place for it.
- **What changes:** ADR-0007 and ADR-0002/0003's loop-free posture would need an amendment, and
  `follow-up-scheduling.md` says "the orchestrator gains no scheduler". A Coolify scheduled task
  also runs during orchestrator restarts and deploys, which ADR-0059's lanes avoid.
- **Cost:** it reverses a recorded architectural decision to save one lane.

## Recommendation

I recommend **A, a new single-purpose lane**. This follows the single-purpose-processes guidance
over the guidance to add less machinery. B adds less, but it repeats the coupling D-41 is
undoing: minting would again ride on a pass whose purpose is something else. A costs one lane
account that reads one secret already in `SDS Operator`, plus the usual token and ping-URL
issue. Every step of that issue already follows an established pattern. If Devon prefers less
machinery, B with `rotation-decomposer` as the host is the fallback, because it is the only
SYSTEM-holding lane with a compose file today. I recommend against C because it reverses
ADR-0007.

## A point for HQ, not a contradiction

The year plan's Phase 1 done-when (sds-hq `plan/year-plan.md`, line 56 at `700944d`) still reads
"no lane that acts on a member runs on the Mac", and ADR-0059's table says infra-drift acts on a
member (line 823). D-41 places infra-drift outside the phase, and Amendment 9 says so, as D-9 did
for SDS HQ. The year plan's text doesn't name either exclusion. The year plan is outside this
handoff, so I didn't change it.
