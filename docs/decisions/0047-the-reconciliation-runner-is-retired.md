# ADR 0047 — The reconciliation runner is retired; reconciliation is what gets reported

**Date:** 2026-09-28
**Status:** Accepted (Devon, Tier 3 item 22, 2026-09-28)
**Supersedes:** the *mechanism* of ADR-0002 (a separate report-only runner that pulls GitHub
and deploy reality). ADR-0002's invariants — the orchestrator process stays push-only and
loop-free, and nothing that reports may set canonical lifecycle state — are unchanged.

## Context

ADR-0002 put reconciliation in a separate program, `src/reconciliation_runner/`, that would
read pull-request, check and deployment reality for in-flight units and push what it saw back
as observations, then invoke the detect pass. It shipped in WS-P2.1 as an operator-invoked
command that read a fixture file; the scheduled trigger ADR-0002 deferred was never decided.

A read-only safety investigation on 2026-09-28 (recorded in
`~/docs/software-delivery-system/2026-09-28-tier3-decisions.md`) found:

- it was **never scheduled** — no launcher, no plist, no workflow — and **no credential was
  ever configured** for it;
- production holds **zero** observations in its source-reference shapes; the three
  `reconciliation_conditions` rows in production came from the 2026-07-27 recovery drills
  posting observations directly, not from the runner;
- **no orchestrator code is reachable only through it** — every route it called is also
  reached by other callers or by operators.

So the runner was a design that never ran. Keeping it cost an entry-package exemption in the
unreachable-code guard, an egress allowlist entry, two architecture-table rows and a console
script, all protecting a program with no operator.

## Decision

Delete `src/reconciliation_runner/`, its tests, its contract test, its console script, and
the four guard entries that existed for it.

**Kept, because they are the part of ADR-0002 that is load-bearing without the runner:**
`POST /api/v1/reconciliation/detect` and the `reconcile-detect` CLI, `GET /in-flight-units`
(including `release_bindings`), `POST /api/v1/observations`,
`services/reconciliation/reconciliation_detection.py`, and the three production condition
rows (the wave-exit probe needs a condition outside the release it measures).

Reconciliation is therefore what gets *reported*: an observation posted by any trusted
producer is still checked on ingest, and the detect pass still reads elapsed time. What is
lost is the *active* half — nothing goes and looks.

## The two accepted gaps

Named, so their absence reads as a decision rather than an oversight:

1. **A check flips, or a pull request's head moves, after `/verify`.** The ingest-side
   detectors fire only on an observation that says so, and nothing now produces one for a
   unit after it has been verified. A named check that later turns red at the armed head, or
   a head pushed after verification, is not recorded as a reconciliation condition.
2. **A release binding is deployed and nobody reports the deploy.** The split-brain detect
   pass sees elapsed time on post-deploy verification units that exist; the case ADR-0002
   rejected Alternative A for — a deploy of a release binding that no producer reported —
   stays invisible.

Both were already open in practice: the runner that was meant to close them never ran.

## What reopens this

A real, scheduled observer of GitHub checks and heads, or of deploys, that posts observations
for in-flight and release-bound units. That producer would be built as its own lane in the
shape the estate now uses (a launcher, a dead-man switch, the observer credential), not by
restoring this package; the retained detect route and ingest detectors are what it would feed.

## Consequences

- `reconciliation-runner` leaves `[project.scripts]`; a reinstall of the editable package
  drops the binary. Nothing invokes it.
- `tests/architecture/test_unreachable_guards.py`'s `ENTRY_PACKAGES` is empty; a future
  separate program under `src/` that is its own root set is added back there.
- Comments that pointed at `reconciliation_runner/facts.py` as the writer of `github_pr`
  observations now name it as retired; the `github_pr` observation type and its readers stay.
