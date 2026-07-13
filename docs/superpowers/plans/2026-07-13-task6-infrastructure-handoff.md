# Task 6 Infrastructure Handoff

**Date:** 2026-07-13  
**Session boundary:** Start a fresh, explicitly authorized **infrastructure mutation** session. Do not mix this work with repository investigation or CI triage.

## Goal And Shape

Complete Phase 0's production-drill proof after the merged Task 6 implementation. The system is not a customer-facing production service yet, but `sds.alobar.net` is the factory's live control plane and must be treated as production for this drill.

Devon's permanent role remains unchanged: he approves sensitive transitions, creates and closes the HUMAN run, approves the controlled restart immediately before it happens, and merges PRs. Never auto-merge or impersonate a HUMAN approval.

## Verified Current State

Verified 2026-07-13 in this session:

- Orchestrator PR [#52](https://github.com/AlobarQuest/orchestrator/pull/52) merged at `1f0a2369a33d706673bec4ebe2dda87754b9dbe7`; both `Quality` checks concluded `SUCCESS`.
- Main contains the Task 6 runtime-observation, drill-control, recovery, and CI-guard changes.
- Live `https://sds.alobar.net/openapi.json` contained **no** `production-drills` or `runtime-observations` route when checked after the merge. The merged code is therefore **not deployed**.
- The merged app will fail closed at startup until production has both the dedicated drill credential and a distinct runtime-observer credential configured. This is intentional; do not bypass it.
- The constrained external runtime observer is a prerequisite, not an implementation detail. Its required capability is documented in `docs/operations/runtime-observations.md`; no root SSH, Docker socket, generic executor, or mutable Coolify path is an acceptable substitute.

## Dependency-Ordered Worklist

1. **Provision the observer boundary first.** Create a dedicated runtime-observer M2M bearer in BWS, a distinct registry actor/key/role mapping, and a Coolify setting for `ORCHESTRATOR_RUNTIME_OBSERVER_CREDENTIAL_KEY_ID`. Configure a fixed read-only inspection capability for the orchestrator container that returns its matching GHCR `RepoDigest` and no mutation capability. This must precede deployment because `main.py` fails closed without the credential key.
2. **Build and deploy merged `main`.** Use the approved amd64 artifact/Coolify flow. Do not deploy the old image or a branch SHA. Apply migrations, then verify readiness, migration head, running image identity, and live OpenAPI routes before any run exists.
3. **Record a fresh runtime observation.** The observer reads the actual serving container's matching `RepoDigest` and SHA-256 of raw, identity-encoded `/openapi.json` bytes, then records only the fixed target through `POST /api/v1/runtime-observations`. This must precede HUMAN start: run provenance is derived from the observation ID, not browser-supplied strings.
4. **Create the HUMAN run.** Devon creates an open run against the exact approved `ws-p2.1-recovery-controls-drills` package revision and records the run ID. A SYSTEM worker may operate only that run and only via the configured dedicated drill credential.
5. **Execute the two-phase crash drill.** The preparation command persists synthetic attempt one and exits with restart-pending evidence. Obtain fresh explicit approval immediately before the Coolify restart. Restart through the approved Coolify control, verify readiness, then resume the runner to prove reclaimed attempt two.
6. **Execute remaining fixed scenarios and close out.** Run evidence recovery, external PR conflict, deploy split-brain, and stalled approval. Capture redacted evidence, verify the run-scoped state, normal recovery views excluding synthetic records, then have Devon perform HUMAN closeout. Record evidence in the designated production-drills evidence document.

## Evidence Base

- Remediation order: `docs/superpowers/plans/2026-07-12-remediation-order.md`.
- Production-drill contract and task plan: `docs/superpowers/specs/2026-07-12-production-drills-design.md` and `docs/superpowers/plans/2026-07-12-production-drills.md`.
- Runtime observer prerequisite: `docs/operations/runtime-observations.md`.
- Drill operation details: `docs/operations/recovery-drills.md`.
- Foundational diagnostic and ledger: `docs/superpowers/evidence/2026-07-12-factory-improvisation-ledger.md`.
- CI evidence: PR #52's two successful Quality checks. Earlier CI failures were fixed by commits `8355bcb`, `33611e4`, and `9f4aff7`; do not reintroduce broad scope-guard exemptions.

## Traps

- **Do not trust the merge as deployment evidence.** Live OpenAPI is the predicate. The route set was absent from production after merge in the verified state above.
- **Runtime provenance is deliberately not caller-supplied.** A HUMAN start must provide a fresh runtime observation ID; free-form image/OpenAPI hashes would recreate the original attestation failure.
- **Observer and drill credentials must be distinct.** Scenario/fail routes require the configured drill key; runtime-observation writes require the configured observer key. Startup fails closed if either mapping is missing or equal.
- **The package is fixed.** Starts against another approved package must fail with `production_drill_package_required`.
- **Crash recovery is two-phase.** Never add a shell restart command. The runner prepares, Devon approves/restarts through Coolify, and the runner resumes.
- **Never run DB-backed pytest suites concurrently.** The fixtures drop/recreate the shared schema.
- **A PR is not verified merely because jobs started.** Monitor every required CI check to a terminal conclusion and repair failures before asking Devon to merge.

## What Did Not Work

- Treating a successful local focused suite as proof of GitHub CI missed strict route inventories, scope guards, idempotency rows, and reachability checks. CI later failed; the fixes were committed and both Quality checks then succeeded before merge.
- Treating PR creation as an external wait was wrong. CI must be monitored to terminal success or failure; this is now recorded in `CLAUDE.md`.
- Using root SSH or the Docker socket as a shortcut for the runtime observer was rejected. It would give the observer more authority than its read-only evidence role permits.
- Free-form deployment metadata was rejected. Immutable storage of a human-supplied digest is still an untrustworthy attestation; only the external observation may bind run provenance.
