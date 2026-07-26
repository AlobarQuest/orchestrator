# Task 3: Chain assembler + response — report

Status: DONE

Branch: `ws-p2.6-traceability-query`

## What was implemented

Extended `src/orchestrator/services/traceability.py` (created in Task 2) with the two
producer functions Task 4's route will consume:

- `build_chain(session: Session, unit_id: uuid.UUID) -> TraceabilityChainResponse` —
  composes `evidence_pack_projection` (intent/unit/authority-approval spine),
  `list_release_artifacts` (commit + artifact hops), `list_deployment_observations`
  (deployment hops, one fan-out per release-artifact binding), `get_pr_binding` (PR hop),
  plus a hand-written observation tail: `ReconciliationCondition` rows ordered by
  `(detected_at, id)` with `open` computed as a set-difference against
  `ReconciliationResolution.condition_id` (mirrors the evidence-supersession pattern
  used elsewhere in this codebase), and generic `Observation` rows filtered on
  `subject_type == "work_unit"` / `subject_reference == str(unit_id)`, ordered by
  `(observed_at, received_at, id)`.
- `_unwrap(result)` — raises when a `list_*` fetcher returns a `DomainError`; inside
  `build_chain` the unit is already known to exist (via `evidence_pack_projection`), so a
  `DomainError` here would indicate a real bug, not a normal-flow condition.
- `traceability_response(session: Session, anchor: TraceabilityAnchor) -> TraceabilityResponse`
  — calls `resolve_anchors` then `build_chain` per resolved unit id.

Implementation matches the brief's Step 3 code verbatim (imports + function bodies), with
one addition: I ran `ruff format` afterward, which reflowed one multi-line dict-comprehension
call (`resolution_decision=(...)`) — semantics unchanged.

## Files changed

- `src/orchestrator/services/traceability.py` — added imports (`api.schemas` Traceability*
  response types; `persistence.models` `Observation`, `ReconciliationCondition`,
  `ReconciliationResolution`; `services.deployment_observations.list_deployment_observations`;
  `services.evidence_pack.evidence_pack_projection`; `services.pr_bindings.get_pr_binding`;
  `services.release_artifacts.list_release_artifacts`) plus `build_chain`, `_unwrap`,
  `traceability_response`.
- `tests/services/test_traceability.py` — added `_record_condition_for` /
  `_record_observation_for` helpers and 6 new tests (the brief's 4 starter tests plus the
  2 the brief's NOTE mandated).

Not touched: `.superpowers/sdd/task-2-report.md` had a pre-existing uncommitted diff in the
working tree at task start (from a prior session/task, not this one) — left as-is and excluded
from this task's commit. This report file itself replaces a stale WS-P2.2 report that was
previously left at this path (unrelated workstream, `slo_report` service skeleton).

## Deviations from the brief (and why)

1. **Fixture name.** Brief's Step-1 sample tests use `session: Session`; the real fixture in
   this file (and every other file in `tests/services/`) is `migrated_session`. Used
   `migrated_session` throughout, per the task instructions' explicit correction.
2. **`completed_unit` does not itself record a `ReleaseArtifactBinding`.** The brief's Step-1
   comment (`unit = completed_unit(session)  # this helper records a ReleaseArtifactBinding
   with DIGEST`) is inaccurate — `tests/services/test_release_artifacts.py::completed_unit`
   only registers a revision + approved, completed `WorkUnit`; the binding is a separate
   `record_release_artifact(session, command(unit))` call made by each test that needs one
   (mirroring every other test file that uses these helpers). `test_build_chain_includes_intent_unit_and_artifact`
   makes that call explicitly before asserting on `chain.artifact`.
3. **`test_deployment_digest_matches_flag`'s mismatch case cannot be produced through the
   public writer.** `record_deployment_observation` enforces
   `command.observed_artifact_digest == binding.artifact_digest` at write time
   (`deployment_observation_digest_mismatch`, `services/deployment_observations.py`), so no
   `DeploymentObservationCommand` can ever persist a mismatched digest — I confirmed this
   empirically first (see RED/GREEN evidence below: my first attempt at the mismatch case via
   a second differently-digested binding+observation was correctly rejected by that guard).
   The test therefore: (a) records one observation through the real writer and asserts
   `digest_matches is True`; (b) mutates that persisted row's `observed_artifact_digest`
   directly on the ORM object post-write (bypassing the write-time guard on purpose, with a
   comment explaining why) and asserts `digest_matches is False` on a re-fetched chain. This
   exercises `build_chain`'s read-time computation (`obs.observed_artifact_digest ==
   binding.artifact_digest`) independently of the writer's invariant, which is the actual unit
   under test here — the two are different code paths and both need coverage.

## TDD evidence

RED — confirmed the implementation is genuinely load-bearing by stashing
`src/orchestrator/services/traceability.py` and re-running the new tests:

```
$ git stash push -- src/orchestrator/services/traceability.py
$ .venv/bin/pytest tests/services/test_traceability.py -k "build_chain or traceability_response" -v
...
ImportError while importing test module '.../tests/services/test_traceability.py'.
E   ImportError: cannot import name 'build_chain' from 'orchestrator.services.traceability'
$ git stash pop
```

Matches the brief's Step-2 expectation exactly.

GREEN — full file, after restoring the implementation:

```
$ .venv/bin/pytest tests/services/test_traceability.py -v
tests/services/test_traceability.py::test_resolve_by_work_unit_id PASSED
tests/services/test_traceability.py::test_resolve_named_unit_missing_raises PASSED
tests/services/test_traceability.py::test_resolve_by_artifact_digest_filter_empty_is_ok PASSED
tests/services/test_traceability.py::test_resolve_named_revision_missing_raises PASSED
tests/services/test_traceability.py::test_resolve_by_revision_id PASSED
tests/services/test_traceability.py::test_resolve_by_artifact_digest PASSED
tests/services/test_traceability.py::test_resolve_by_commit PASSED
tests/services/test_traceability.py::test_resolve_by_pr PASSED
tests/services/test_traceability.py::test_resolve_by_environment_picks_latest_observation_per_unit PASSED
tests/services/test_traceability.py::test_build_chain_includes_intent_unit_and_artifact PASSED
tests/services/test_traceability.py::test_build_chain_observation_tail_includes_conditions_and_observations PASSED
tests/services/test_traceability.py::test_build_chain_empty_tail_when_none PASSED
tests/services/test_traceability.py::test_traceability_response_orders_chains_by_resolution PASSED
tests/services/test_traceability.py::test_build_chain_pr_and_deployment_hops PASSED
tests/services/test_traceability.py::test_deployment_digest_matches_flag PASSED
============================== 15 passed in 3.92s ==============================
```

(9 pre-existing Task-2 resolver tests + 6 new Task-3 tests, all green.)

An intermediate RED, en route to the final `test_deployment_digest_matches_flag`: my first
draft tried to produce the mismatch by recording a second release-artifact binding (different
digest) with an observation against it using that same digest — i.e. attempting to record an
observation whose digest didn't match the FIRST binding but did match a second. This correctly
failed at the writer:

```
E       AssertionError: assert not True
E        +  where True = isinstance(DomainError('observed artifact digest does not match the
                                     immutable release binding'), DomainError)
```

That failure is what led to the write-guard-vs-read-time-computation split documented above
(deviation #3), not a bug in `build_chain`.

## Lint / type-check / scope guards

```
$ .venv/bin/ruff check src/orchestrator/services/traceability.py tests/services/test_traceability.py
All checks passed!

$ .venv/bin/ruff format src/orchestrator/services/traceability.py   # 1 file reformatted
$ .venv/bin/ruff format --check src/orchestrator/services/traceability.py tests/services/test_traceability.py
2 files already formatted

$ .venv/bin/pyright src/orchestrator/services/traceability.py tests/services/test_traceability.py
0 errors, 0 warnings, 0 informations

$ .venv/bin/pytest tests/architecture/test_ws32_scope_guards.py tests/architecture/test_ws33_scope_guards.py tests/architecture/test_scope_guards.py -q
16 passed in 1.83s
```

Re-ran the focused traceability suite after the `ruff format` reflow to confirm it was still
green (it was — same 15 passed).

## Full-suite result

Ran once, in the foreground-equivalent single invocation (backgrounded by the harness due to
runtime, not run concurrently with anything else):

```
$ SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest -q
...
FAILED tests/architecture/test_unreachable_guards.py::test_every_public_kernel_and_service_function_is_reachable
1 failed, 1523 passed, 1 skipped in 198.45s (0:03:18)
```

The single failure is exactly the expected, pre-existing one:

```
AssertionError: these public functions cannot be reached from any production entry point:
    orchestrator.services.traceability.resolve_anchors (orchestrator/services/traceability.py:69)
    orchestrator.services.traceability.build_chain (orchestrator/services/traceability.py:149)
    orchestrator.services.traceability.traceability_response (orchestrator/services/traceability.py:288)
```

This is the documented Task-4 dependency: `resolve_anchors`/`build_chain`/`traceability_response`
have no production caller until Task 4 adds the `GET /api/v1/traceability` route. Per the task
instructions, I did **not** add these to `ALLOWLIST` and did not attempt to work around it — it
will close when Task 4 wires the route. No other new failures anywhere in the suite; `build_chain`
and `traceability_response` are new entries in this failure (added by this task); `resolve_anchors`
was already flagged unreachable since Task 2, for the same reason.

## Self-review

- **Composition, not reimplementation**: `build_chain` calls `evidence_pack_projection`,
  `list_release_artifacts`, `list_deployment_observations`, `get_pr_binding` for everything
  except the reconciliation-condition/observation tail, which has no existing per-unit
  fetcher to reuse (the brief's own Step-3 code queries `ReconciliationCondition`/
  `ReconciliationResolution` directly, which is what I implemented verbatim).
- **Import direction**: `services/traceability.py` now imports from `api/schemas.py`, mirroring
  `services/release_evidence_pack.py`'s existing pattern (verified: no cycle, `api/schemas.py`
  imports nothing from `services/`).
- **Scope-guard hygiene**: grepped the new code and docstrings for the bare tokens
  `deploy`/`dispatch`/`merges` — none present (`deployment_ref`, `deployment_url`,
  `merge_commit`, `implementation_pr_number` etc. are all suffixed/compound forms, which the
  guard test file (verified by running it) explicitly allows).
- **No route/CLI changes**: `routes.py` untouched, per the brief (Task 4's job).
- **Idempotency of test data**: every `completed_unit(...)`/binding/observation call in the new
  tests uses a distinct `key=` to avoid unique-constraint collisions with the pre-existing
  Task-2 tests in the same file and across each other.

## Concerns

None blocking. One observation for whoever reviews Task 4: `build_chain` raises
`DomainError("work_unit_not_found", ...)` via `evidence_pack_projection` when the unit doesn't
exist, and `_unwrap` re-raises any `DomainError` from the release-artifact/deployment-observation
fetchers as a real exception (not a return value) — Task 4's route handler needs to catch
`DomainError` and translate it to the appropriate HTTP response, consistent with how other
routes in this codebase handle service-layer `DomainError`s.
