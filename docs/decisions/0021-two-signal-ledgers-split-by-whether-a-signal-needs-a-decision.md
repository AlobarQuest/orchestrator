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

A sensor's finding can have two homes: its observation records the fact, and a keyed alert tells
the person (amendment 3).

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

## Amendment 2 (2026-10-27): bridge-lane findings reach alert-hub through outcome alarms

### Decision

The bridge lanes' findings go to alert-hub, the home this ADR gives to "a person goes untold". The
three bridge launchers (`run-bump-proposer.sh`, `run-change-proposer.sh`, `run-work-carrier.sh`)
no longer arm the dead-man switch, which pinged success on a finding (exit 3) and left the finding
in the lane log only. Each is now an outcome alarm, like `sds-fork-sync-watch`:

- It pings `/start` before the program, then success on exit 0, or `/fail` with the pass's report
  as the body on any non-zero exit: 1, 2, 3, or a code outside the vocabulary. A credential the
  launcher can't read is exit 1 and pings `/fail` with its reason.
- It pings only on its acting run (`--submit` for the two proposers, `--register` for the carry).
  A bare run is a person looking and pings nothing.
- The checks (`sds-bump-proposer`, `sds-change-proposer`, `sds-work-carrier`) deliver to the
  `alert-hub` channel, and each installer upserts its own.

The trade-off is accepted, not overlooked. A check answers liveness only while it is up. A
standing finding keeps it down after one page, and while it is down a lane that then stops running
causes no state change, so nothing reports the stop until the finding clears. The same holds for a
new, different finding: while the check is down it causes no state change either, so alert-hub
never receives it, and only the check's Healthchecks ping log holds its report. Both costs apply
only while a finding stands, and findings are rare in the lane logs read on 2026-10-27:
change-proposer 2 in about 1,394 passes, bump-proposer 1 in 58, the carrier 1 in 64. One
Healthchecks check has one state, and the bridge layer design
(`docs/superpowers/specs/2026-10-27-bridge-layer-design.md`) names alert noise as an anti-goal, so a
page per pass and a second liveness check per lane were both rejected.

## Amendment 3 (2026-11-04): a delivered finding pings success

Devon decided on 2026-11-04, in the Lane runtime layer pass
(`docs/superpowers/specs/2026-11-04-runtime-layer-design.md`).

### Decision

An outcome-alarm lane may ping success on a finding code only when every exit with that code was
delivered to a person as a keyed alert, and a failed delivery exits with a different code.
`sds_outcome_end <rc> --told <code>` does it: an exit with the told code pings success with the
pass's report as the body, and every other non-zero exit still pings `/fail`.
`tests/scripts/test_sds_deadman.py` holds each launcher's told code to a sentence in its
`EXIT CODES` block that says so.

It applies to `run-bump-driver.sh` alone (`--told 2`). Every `bump_driver` outcome that exits 2
carries the key of its alert (`test_every_outcome_that_exits_two_carries_the_key_of_its_alert`),
and a failed delivery exits 3. The other two outcome lanes that send keyed alerts don't meet the
condition: `bump-proposer`'s keyed alerts already exit 0, and none of `rotation-decomposer`'s
exit-2 causes raises an alert.

### Why

Amendment 2 accepted that a standing finding blinds a check to a lane that stops. That cost was
live. `sds-bump-driver` stood down from 2026-10-31 on a finding (a unit in `revision_required`)
whose keyed alerts were already open in alert-hub, and six missed runs on 2026-11-03 caused no
state change. Once the finding reaches a person as a keyed alert, the `/fail` adds nothing but that
blindness.

### The sensors and the tool installer tell a person

The findings a person must act on from four lanes that arm the dead-man switch reach alert-hub as
keyed alerts on their acting pass, through `alert_hub_client.KeyedAlerts` with the `sds-bridge`
sender: the activation sweep (`checkout-behind`, `checkout-dirty`, `checkout-parked` and
`activation-incomplete`), the revision watcher (`revision-stale`), the pin watcher (`pin-behind`)
and the tool installer (`tool-install`). Their dead-man checks keep answering liveness alone. Their
observations stay the findings' fact home (amendment 1); alert-hub is where a person is told. A
failed send exits with the lane's could-not-report code, which outranks its finding code, so the
check pings `/fail`.

### No second hearer for an unreadable Healthchecks key

When a lane can't read the Healthchecks key, it can't ping, and its check goes down after its
grace. Measured on 2026-11-03: every missed run in Healthchecks' retained window flipped its check
down, except the six `sds-bump-driver` runs this amendment unmasks. The key read fails when BWS or
the network does, and anything else that could report it needs the same two, so no second hearer
is built.
