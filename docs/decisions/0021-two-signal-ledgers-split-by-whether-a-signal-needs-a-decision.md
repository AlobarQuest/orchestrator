# ADR-0021 — Two signal ledgers, split by whether the signal needs a decision

- **Status:** Accepted
- **Date:** 2026-08-13
- **Decided by:** Devon
- **Relates to:** ADR-0002 (report-only runners), ADR-0019, the Phase-3 plan §6.4, and the
  Tier-1 signal census (`~/docs/software-delivery-system/2026-08-12-tier1-signal-census.md`)

## Decision

**The estate keeps two signal ledgers, and the line between them is whether the signal needs a
decision.**

- **change-manager** holds signals that need a **decision**. They have a lifecycle — proposed,
  approved, executed, retired — and something must eventually rule on them.
- **The orchestrator's observations** hold signals that are **facts that happened**. Append-only,
  no lifecycle, nothing to decide.

## Why this needed deciding at all

The census of 2026-08-12 measured what was actually connected and found the estate had **already
drifted to two ledgers without anyone ruling on it**: drift, security findings, deploy outcomes
and deploying-merge proposals all land in change-manager; landings and drift digests land in the
orchestrator. The split existed, but by build order rather than by a principle — so nobody could
say where a new signal belonged.

The Phase-3 plan's §6.4 asked *"should every automated signal post a Tier-1 observation, including
ones handled entirely outside SDS?"*. Read as a connection question it is cheap and appealing.
Read properly it is this architectural question, and answering it "yes, everything" would have
been a shape mistake: **it gives facts a decision lifecycle they should not have.** An observation
that something happened cannot be approved, deferred or retired, and a store that models all four
states invites code that treats a fact as pending.

## What follows immediately

The census listed nine unconnected automated signals. Under this rule the work is **four**, not
nine:

| connect to the orchestrator | why |
|---|---|
| `com.devon.vps-backup` | whether the backup ran is a fact |
| `com.devon.vps-backup-verify` | whether it restores is a fact |
| `com.devon.hetzner-snapshot` | whether the snapshot took is a fact |
| `com.devon.factory-events` | the chain's own health is a fact |

Those four are the estate's **recovery and tamper-evidence floor and they currently report to
nothing at all** — a backup that silently stops looks exactly like one that runs.

The other five — the deploy watcher, the change proposer, the change-window executor, the security
scan, and the drift audit's escalations — stay with change-manager, because each produces
something a person or a policy may have to rule on.

## What this does not do

**It does not close Phase-3 criterion 1's second half.** That needs the traceability chain to carry
an observation, and the chain's hop is *unit-scoped*: measured 2026-08-12, 509 observations are
repo-scoped, 39 service, 1 deployment, and 4 work-unit — all four written by
`orchestrator-system`, none by an external producer. Connecting all nine would have added hundreds
of rows and left that hop empty. See ADR-0022.

## The test to apply to a new signal

Ask what happens when it fires and nobody looks. If the answer is *"a decision goes unmade"*, it
belongs in change-manager. If the answer is *"a fact goes unrecorded"*, it is an observation.

## Amendment 1 (2026-10-26): every signal has a machine home, and observations are one of three

Devon decided on 2026-10-26, closing roadmap open decisions 2 and 3.

### Decision

Not every automated signal posts an observation. Every automated signal needs a home in a machine
system, and an observation is one of three homes, not the default. The test above gains a third
answer:

| When the signal fires and nobody looks | Its home |
|---|---|
| A fact goes unrecorded that something reads: the traceability chain, a lane, or a report | An orchestrator observation |
| A decision goes unmade | A change-manager record |
| A person goes untold | alert-hub, with self-hosted Healthchecks for liveness |

A signal left for a person to carry by hand has no home, and that's a defect at any volume. A
signal whose home is change-manager or alert-hub is not.

### What changed since the census

The census of 2026-08-12 argued for connecting every signal because nothing could answer "did the
machinery run last night?". alert-hub (built 2026-10-24 to 2026-10-26) and self-hosted Healthchecks
now answer that for the jobs connected to them, through alert-hub's `silent:<sender>` checks and
Healthchecks' dead-man pings. Two costs make the rule bite:

- An observation has no delete route and no supersession, so a row nothing reads is permanent.
- Every producer needs a credential and a per-run `source_reference`, or it stops permanently on
  `observation_conflict`.

### The census, re-measured

Measured 2026-10-26 against production (`GET /api/v1/observations`, 3,363 rows):

| `source_system` | Rows | Last posted |
|---|---|---|
| `github` | 2,064 | 2026-10-26 |
| `revision_watcher` | 420 | 2026-10-26 |
| `recovery_floor` | 337 | 2026-10-26 |
| `machine_activation` | 256 | 2026-10-25 |
| `drift_digest` | 185 | 2026-10-26 |
| `tool_installer` | 54 | 2026-10-25 |
| `pin_watcher` | 27 | 2026-10-06 (posts only on a change) |
| `rotation_proposer` | 14 | 2026-10-26 |
| `deployment_observation` | 6 | 2026-08-26 |
| `watchtower`, `ops_dashboard`, `healthchecks`, `uptime_monitor`, `bump_proposer` | 0 | never |

The four jobs this ADR named all post. `recovery_floor` carries seven subjects: five
`backup:` subjects, `chain:factory-events` and `snapshot:hetzner-vps`.

Each surface the roadmap named as unconnected has a home under this amendment:

- **`high-power-actions.jsonl`:** no producer. The factory-events adapter ingests it into the
  tamper-evident store, and the store's health posts as `chain:factory-events`.
- **The daily portfolio scan:** no producer. It pings Healthchecks, which is its liveness home.
- **The five registered sources with no rows:** `ops_dashboard` and `healthchecks` send to
  alert-hub, and `bump_proposer` writes change-manager records. The members are unused; removing
  them needs a migration.

### Stated direction, not decided design

Devon stated two directions on 2026-10-26. Neither is designed, and this amendment builds neither:

- `high-power-actions.jsonl` will need a decider that reads it and acts. The action may be a change
  to a safety measure, made only after consulting Devon, or an alert or notification.
- The SDS will read backlog items. That makes backlog state a signal that becomes proposed work,
  not an observation.

### Open: the OVH host has no snapshot signal

`snapshot:hetzner-vps` reports on the Hetzner host, which production left on 2026-10-23 and which
is due to be turned off. The migration plan says OVH's daily backups and snapshot option replace
`hetzner-snapshot.sh`. Nothing in the estate can read OVH's snapshot or backup state: infraops
reaches the OVH host only over SSH, and no repository holds an OVH API credential. When the Hetzner
host is turned off, the recovery floor loses its only whole-machine image signal. The `backup:`
subjects cover the data, not the machine image.
