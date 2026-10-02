# ADR 0049 — The local recovery drills are retired; the re-measured attestation of the drills clause ends

**Date:** 2026-09-28
**Status:** Accepted (Devon, Tier 3 item 24, drills ruling option 1, 2026-09-28)
**Retires:** the local drill harness (`scripts/drill-*.sh`, `scripts/drill_common.sh`,
`scripts/run-drills.sh`, `tests/architecture/test_drill_scripts.py`) and the WS-3.1 seeding routes
it depended on (`POST /api/v1/revisions`, `POST /api/v1/revisions/{revision_id}/work-units`, and the
`register-revision` / `register-unit` CLI commands). It does NOT supersede ADR-0005: that decision
was carried out, and its evidence stands.

## Context

The five recovery drills were the scripted proof behind the programme's exit criterion 5
("crash/retry/reconciliation drills pass and are scripted", Wave 1 clause 1). They ran against a
throwaway Postgres and uvicorn and seeded their units through the WS-3.1 bootstrap routes.

Those routes are unreachable in production and have been since WS-3.2: both call `_require_human`
and sit on the M2M-only proxy router, so no actor can reach them. Their only callers were the two
CLI commands and `drill_common.sh`. Production units are born through intake, breakdown and the
`/review` approval, and the service functions behind the deleted routes — `register_revision` and
`register_approved_unit` — remain load-bearing there.

**Criterion 5 was MET on 2026-07-27.** Under ADR-0005 disposition A the five drills were run
against `sds.alobar.net` itself, 5/5 PASS, none waived. The evidence is retained and is not
touched by this change:

- `~/docs/software-delivery-system/2026-07-27-production-recovery-drill-run.md` — the run record,
  pinned by digest in `docs/operations/wave-exit-manifest.toml` (Wave 1, clause 1,
  `retained_evidence`);
- `docs/operations/production-drill-adaptations.md` — the per-drill production variants;
- `docs/operations/exit-criteria-claims.toml`, criterion 5 — the routes that run called, still
  checked live against production by `scripts/attest_exit_criteria.py`.

What kept the local harness alive was a second, *re-measured* attestation of the same clause: a
`command` check in the manifest running `scripts/exit_probe.py drills-are-scripted`, which passes
only while five `drill-*.sh` files exist and `run-drills.sh`'s own glob reaches every one.

## Decision

Delete the drills, their harness and guard, the seeding routes and their CLI commands. End the
re-measured attestation: remove the `drills-are-scripted` probe and the manifest's `command` check
that ran it.

**The met bar is not rewritten.** The clause's text, annotation, body and `body_sha256` in the
manifest are unchanged, and its `retained_evidence` check — the 2026-07-27 run record, pinned by
digest — stays. A `note` on the clause names this ADR. Criterion 5 remains MET on the evidence it
was met on; what ends is the claim that the drills are *still* scripted in this repository, which
after this change they are not, and which would otherwise force the harness to be kept for no
operator.

This is the ADR-0040 / ADR-0014 line drawn the other way round. ADR-0040 kept the tracker adapter's
code because a met bar's *live* check exercised it and removing the code would have meant
rewriting the bar. Here the ruling is to retire the live check instead, deliberately and on the
record, while leaving the bar's evidence exactly where it was.

## What is kept, and why

- `services/intake/packages.py::register_revision` and `register_approved_unit` — reached by
  intake and breakdown approval. Their bootstrap-lane `acceptance_criteria` parameter
  (WS-P2.32) now has no production caller; it is left in place rather than deleted in the same
  change, and is exercised by the test-only seeding router.
- `services/reconciliation/reconciliation_detection.py`, `POST /api/v1/reconciliation/detect` and
  the other routes criterion 5 cites. Drills 3 and 4 were the only production exercisers of the
  reconciliation detectors; the detectors and their routes stay, and lose their scripted exerciser.
- Both operations documents, marked retired.

Tests still need to seed a hand-registered unit, so the two seeding routes move to a test-only
router in `tests/_support/seeding.py` (`/test-support/revisions…`), mounted by the test clients and
never by `create_app()`. Production does not serve them.

## Consequences

- **No scripted recovery drill exists until a replacement suite is built** (backlog, P2,
  `d5e596e35e0a`). Until then crash, lease-lapse, split-brain and stalled-gate recovery are covered
  by unit and service tests only, and by the 2026-07-27 production run as history.
- Rerunning `attest_wave_exit.py` measures Wave 1 clause 1 from its retained evidence alone.
- `test_drill_scripts.py`, the seventh architecture guard, goes with the scripts it guarded; the
  replacement suite should carry its equivalent (drills change state only through the public API).

## Amendment 1: the replacement suite (2026-10-02)

The five drills are rebuilt as pytest tests in `tests/protocol/drills/` (backlog `d5e596e35e0a`).
Each unit is born through package intake and a `/review` breakdown approval, so the drills reach
only routes production serves. `tests/_support/protocol.py` holds the shared setup, which the
WS-3.3 smoke test now uses too.

- **They run in every `make check` and in CI.** The scripts ran by hand and went unexercised;
  tests cannot.
- **The retired guard has an equivalent.** `test_drills_change_state_publicly.py` refuses a
  session write or a service import in a drill file, and allows exactly one writing function in the
  shared setup, `expire_latest_claim`, because `DEFAULT_LEASE` is fifteen real minutes.
- **What they do not do.** A crash is a fresh application over the same database rather than a
  killed process, and they run locally only.
- **Wave 1 clause 1 stays attested by its retained evidence.** A live check there would need
  Postgres in the manual attestation workflow, and CI already runs the drills on every pull request.
