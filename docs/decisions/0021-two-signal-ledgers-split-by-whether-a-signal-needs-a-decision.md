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
