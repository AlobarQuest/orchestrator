# ADR 0003 — Tracker projection is outbound-only, out-of-process, and Todoist-first

**Date:** 2026-07-26
**Status:** Accepted (decision confirmed with Devon 2026-07-26)
**Workstream:** WS-P2.7 (tracker projection adapter) — Program Phase 2, Wave 2 (LEGIBLE)
**Companion:** `~/docs/software-delivery-system/2026-07-02-software-factory-master-plan.md` (D7/D8),
program exit criterion #9 ("no tracker is treated as canonical")
**Precedent:** ADR-0002 (reconciliation via a separate report-only runner)
**Design:** `docs/superpowers/specs/2026-07-26-wsp27-tracker-projection-adapter-design.md`

## Context

D7/D8 defer the human projection surface to Program Phase 2 and specify that the
tracker choice — Linear vs Todoist — be decided "on the Open Engine pilot's evidence":
the WS-0.6 pilot's protocol-learnings note (§8 of the pilot kit), which the plan calls
"the actual output that feeds Phase 3/6."

**That evidence does not exist.** Verified on disk 2026-07-26: `~/docs/software-delivery-system/`
holds only the pilot *setup kit* (an input), there is no WS-0.6 closeout/learnings note, and
`~/.config/open-engine/` contains only the two static setup files — no ledger export, no run
log. The pilot never ran enough throughput to produce the note. The Phase-2 plan anticipated
exactly this ("if the pilot never ran enough throughput, extend it during Wave 1 rather than
deciding blind").

Two program invariants bound any solution:

1. **No tracker may ever be canonical** (exit #9 + the YAGNI ledger: no issue-move-as-lock,
   no comments-as-audit, no issue-content-as-authority — ever). The tracker is a projection.
2. **The orchestrator process must not call external mutation integrations.** A CI guard
   (`tests/architecture/test_scope_guards.py::test_application_has_no_external_mutation_integrations`)
   already bans importing `linear`, `todoist`, `infraops`, `github.actions`, or
   `factory_events.store` anywhere under `src/orchestrator/`.

## Decision

WS-P2.7 Increment 1 ships **outbound-only projection**: canonical work-unit state is mirrored
onto an external tracker as a read-only human view. It does so through **a separate,
out-of-process adapter** — the same species as the ADR-0002 reconciliation runner — that talks
to the orchestrator only through its public API.

Concretely:

- **Todoist first, on first principles.** Because the intended pilot evidence was absent, the
  choice was made on what is usable now: Todoist is deeply integrated and reachable today;
  Linear was never wired (its MCP is browser-auth and was never connected) and its pilot
  produced nothing. The projection interface is tracker-agnostic — a `TrackerProjector`
  protocol (`src/tracker_projection_adapter/tracker.py`) with a concrete `TodoistProjector`.
  **A Linear implementation would be a second class behind the same seam, with zero
  orchestrator change**, if and when its MCP is wired.
- **The unit↔item mapping is canonical-side.** A new `unit_tracker_bindings` table + a
  SYSTEM-only `POST /api/v1/work-units/{unit_id}/tracker-binding` API record *that* a unit is
  mirrored to some external item. The mapping lives in the orchestrator, never in the tracker,
  so issue content never becomes authority.
- **The adapter ships from this repository as a distinct package/entry point**
  (`src/tracker_projection_adapter/`, console script `tracker-projection-adapter`), sharing no
  import path with `src/orchestrator/` — exactly the ADR-0002 shape. It reads canonical state
  (`status-ledger`, `tracker-bindings`) and writes only the binding back; its HTTP client
  permits exactly one write endpoint.
- **Operator-invoked first; scheduler deferred.** No Dockerfile/Coolify service and no loop —
  a `scripts/run-tracker-projection.sh` launcher runs one pass. A scheduled trigger is a
  separate, later decision, mirroring ADR-0002.

## Exit-criterion-#9 guarantees (the tracker can never set canonical state)

Belt-and-suspenders, all mechanical:

1. The existing `todoist` import ban in `src/orchestrator/`.
2. The adapter's HTTP client (`orchestrator_client.py`) permits a **single** write endpoint
   (`ALLOWED_WRITE_PATTERN`, matching only the tracker-binding path) and only GET/POST; every
   lifecycle/command/evidence/adjudication/observation path is structurally unreachable.
3. A service test proving `upsert_tracker_binding` leaves `work_units.state` unchanged — a
   binding carries no lifecycle authority.
4. An isolation test (`tests/architecture/test_tracker_projection_adapter_isolation.py`): the
   adapter imports nothing from `orchestrator.*`, and its third-party deps are confined to
   `{httpx, typer}`.

## Documentary close of the D8 interim

The Linear "Agent Queue" pilot is **retired as the interim non-canonical surface**. The
standing model is now: the orchestrator is canonical, the tracker is pure projection. This close
is the recorded decision — the Linear workspace is not touched programmatically (its MCP is not
connected); Devon may archive the project at will. **No real pilot items (ALO-50/ALO-51) are
re-homed**; creating real non-software work units is WS-P2.13's job (it needs an approved
non-software intent package).

## Alternatives considered

- **Linear first.** Rejected: not wired, and its pilot produced no evidence to justify the
  extra integration friction now. Kept viable as a second adapter behind the same seam.
- **Extend the pilot to generate the evidence first** (the plan's decision-point-2 path).
  Rejected for Increment 1: higher calendar cost and it needs the Linear MCP connected; the
  projection *interface* is the real work and is tracker-agnostic regardless.
- **In-process projection** (the orchestrator calls Todoist directly). Rejected: it breaks the
  `todoist` import ban and the ADR-0002 push-only / loop-free invariant.
- **Tracker-as-canonical / two-way authority.** Rejected permanently by exit #9 and the YAGNI
  ledger.

## Deferred (not built in Increment 1)

The entire **inbound** flow — a human's tracker edit becoming a *requested transition* the
orchestrator validates, with unappliable divergences surfaced as append-only reconciliation
conditions — is Increment 2. It carries the judgment-dense "what actor role does a
tracker-originated transition carry?" question and new reconciliation vocabulary, and is
deliberately out of scope here.

## Cost / trade-off accepted

One additional small operator-invoked process to run, and a projection that is only as fresh as
the last manual pass until a scheduler is added. Both were judged well worth a legible human
surface that provably cannot corrupt canonical state.
