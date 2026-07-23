# Proposal: Cost/Token Actuals Capture

**Date:** 2026-07-23 · **Status:** proposed, not scheduled · **Origin:** WS-P2.2 (SLO/observability) follow-up

## Problem

No actual token or dollar cost is persisted anywhere in the event store for a work-unit
attempt. The only cost-shaped value that exists today is a **declared ceiling** —
`WorkUnit.authority.constraints.budgets.max_llm_calls` (`kernel/authority.py`) — and nothing
in the system ever compares an attempt's real consumption against it. There is no
`llm_calls` actual, no token count, no dollar figure, on any event or table.

Consequently WS-P2.2's SLO report renders its cost metrics honestly rather than
fabricating them: `slo_report._cost` and `slo_report._tokens` both return
`not_instrumented`, guarded by a test that pins this as the correct behavior for the
current system (not a bug to silently "fix" by inventing numbers from the declared
ceiling).

## Proposed increment

The runner (factory-runner) and/or the orchestrator persists the **actual** per-attempt
`llm_calls` count and token consumption as a new event payload (e.g. an
`attempt.cost_actuals` event emitted at attempt completion) or a new table keyed on
`(work_unit_id, attempt)`. Shape should mirror what the budget ceiling already declares
(`max_llm_calls`, plus token counts if the runner has them from its LLM client) so the two
sides of the same measurement — declared vs. actual — line up field-for-field.

This is deliberately a **new collector**, not a refactor of an existing one: nothing in
either repo currently observes or reports this number, so the work is instrumentation at
the source (the runner's LLM call sites / orchestrator's evidence ingestion), not a
projection over existing data.

## Why it matters — shared prerequisite for two workstreams

1. **Cost SLO metric** (this workstream, WS-P2.2): `slo_report._cost` / `_tokens` can only
   stop returning `not_instrumented` once real actuals exist to aggregate.
2. **WS-P2.4 budget enforcement**: enforcing `max_llm_calls` (or any budget) against
   reality requires an actual to compare the ceiling to. Without this increment, budget
   "enforcement" can only ever check that a ceiling was *declared*, never that it was
   *respected*.

Both consumers read the same underlying capture; building it once here avoids two
independent, likely-divergent instrumentation efforts later (the portfolio's default
outcome when two workstreams need the same input and neither is exercised by the other's
tests).

## Explicitly out of scope for WS-P2.2

WS-P2.2 shipped the Tier-1 SLO report under a deliberate YAGNI boundary: compute what is
already observable, and render what isn't as `not_instrumented` rather than guess. Adding
a new collector — new event payload or table, new write path in the runner and/or
orchestrator, a migration — is a scoped increment of its own and is **not** part of
WS-P2.2. This document exists so that increment has a named landing spot (see the linked
backlog item) rather than being rediscovered from scratch when WS-P2.4 needs it.
