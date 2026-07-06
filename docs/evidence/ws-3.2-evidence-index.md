# WS-3.2 evidence index

This index records local evidence for approved intent package
`ws-3.2-package-intake-decomposition` revision 1, hash
`84c929bc0860b6a585a62ec02fa35d9cdf89fce84773660aea1e383d955689df`.

The pre-evidence implementation head was
`c6c6f45e80c059f2543708858796fe81eb82776c`. Evidence was recorded by `codex`
at `2026-07-06T02:04:29Z`, with fresh branch-level checks repeated after final
review fixes. This is not a completion claim.
Final whole-branch review, pushed PR CI, Devon's final review, and Devon's merge
remain outstanding.

## Verification summary

- `pytest tests/cli/test_package_intake_cli.py -v`: passed, 23 tests, one
  existing Starlette/httpx deprecation warning. Covers package reader and CLI
  source verification behavior.
- `pytest tests/services/test_package_intake.py tests/services/test_package_registration.py -v`:
  passed during focused development after tightening package-cli activation. The
  current package-intake file alone passed 10 tests.
- `pytest tests/services/test_decomposition.py tests/services/test_lifecycle_events.py -v`:
  passed during focused development after binding created work units to the
  active approved decomposition.
- `pytest tests/api/test_package_intake_api.py tests/api/test_decomposition_api.py tests/architecture/test_scope_guards.py -v`:
  passed during focused API development.
- `pytest tests/cli/test_cli_contract.py tests/cli/test_cli_http_parity.py tests/cli/test_package_intake_cli.py tests/cli/test_decomposition_cli.py -v`:
  passed, 61 tests, one existing Starlette/httpx deprecation warning.
- `pytest tests/web/test_decomposition_review.py tests/web/test_human_actions.py tests/web/test_csrf.py -v`:
  passed, 16 tests, one existing Starlette/httpx deprecation warning.
- `pytest tests/architecture/test_no_automatic_merge.py tests/architecture/test_scope_guards.py tests/architecture/test_ws32_scope_guards.py -v`:
  passed, 7 tests.
- `ruff check tests/architecture/test_ws32_scope_guards.py tests/architecture/test_scope_guards.py`:
  passed.
- `pyright tests/architecture/test_ws32_scope_guards.py`: passed, 0 errors.
- `make check` with `TEST_DATABASE_URL` pointed at the local OrbStack PostgreSQL
  endpoint: passed. Ruff, formatting, Pyright, and 573 pytest tests passed with
  one existing Starlette/httpx deprecation warning.
- Security scan
  `PYTHONPATH="$HOME/Projects/security-standards/src" python3 -m security_scan.cli . --category security`:
  passed with 0 BLOCK, 0 WARN, and one judgment-only INFO
  (`bws.least-privilege-scope`).

## Preserved failed gates

- First full `make check` stopped at Ruff line-length/import findings in WS-3.2
  persistence and migration tests. The affected files were formatted and rerun.
- Second full `make check` stopped at Ruff format checks for WS-3.2-touched
  files. `ruff format` was applied and rerun.
- A later full `make check` exposed one stale package-intake test that still
  expected `activation_source="approved_decomposition"` to be enough for direct
  unit creation. The test was corrected to assert that an active
  `approved_decomposition_id` is also required. Focused package-intake tests then
  passed.
- Final whole-branch review found two blocking service-boundary gaps:
  `status_at_intake="executable"` was still accepted, and an active
  approved-decomposition id could be reused to mint a unit outside the approved
  proposal. Regression tests were added and the service guards were tightened.
  Focused API/CLI/service suite then passed 68 tests; the final full gate passed
  573 tests.
- One full run also produced PostgreSQL schema-reset cascade errors after the
  stale-test failure. Focused reproduction for `tests/services/test_dependencies.py`
  and for `tests/persistence/test_migrations.py tests/services/test_evidence.py`
  passed. The final full `make check` passed.

## Scope guard evidence

- The architecture route inventory explicitly includes only the three new
  human-review POST routes for decomposition approval, rejection, and
  revision-required decisions.
- `tests/architecture/test_ws32_scope_guards.py` scans runtime imports,
  identifiers, attributes, definitions, and string literals for forbidden Phase
  3 runtime paths including factory-runner, factory-event/v1, workflow dispatch,
  deploy, Coolify, automatic merge, and production mutation language.
- Existing workflow and application guards continue to block automatic merge,
  deployment, external mutation integrations, event publication, and dispatch
  objects.

## Acceptance direction mapping

- Unapproved package revisions cannot become executable intake: package source
  verification rejects non-approved package and lineage states; intake service
  rejects non-approved status.
- Repeated registration is idempotent: package intake service and API/CLI tests
  cover exact replay and concurrent identical intake convergence.
- Hash/source conflicts are rejected: package intake service tests cover stable
  conflict behavior.
- Mutable package content does not become a second source of truth: persistence
  stores immutable authority facts, source reference, verification facts, and a
  normalized acceptance-criteria projection.
- Agent proposal is non-canonical: decomposition proposal tests prove proposal
  submission creates no work units.
- Named human approval activates decomposition: service, API, CLI, and UI tests
  cover human-only approve/reject/revision-required decisions.
- Approved decomposition creates Draft work units through the lifecycle path:
  decomposition service tests cover created Draft units, local events, and
  dependency creation.
- Dependencies and acceptance-criterion mappings are structural: proposal tables
  and tests cover internal dependencies, AC mappings, retained ACs, and cycle
  rejection.
- Unmapped criteria require human-approved handling: proposal submission requires
  each package AC to be mapped or explicitly retained with rationale.
- API and CLI expose equivalent behavior: focused API and CLI suites passed.
- Human UI supports review decisions: web tests cover intake review, proposal
  approval, rejection, revision-required, and already-decided display behavior.
- Every intake, proposal, decision, and created work-unit path appends local
  attributable events in the service transaction.
- Existing WS-3.1 gates continue to pass in the 573-test full local gate.
- No automatic merge path exists; architecture guards and workflow scan remain
  in force.

## Explicitly absent evidence

- No pushed PR CI result exists yet for the final WS-3.2 branch head.
- No production deployment, factory-runner dispatch, external factory-event/v1
  publication, tracker canonicalization, or automatic merge evidence exists;
  those remain out of scope.
