# WS-3.1 evidence index

This index records evidence for approved intent package
`ws-3.1-orchestrator-core` revision 1, hash
`4414eae543d9dac8b1983f796593569d9abf97dfee1b8a06ef29b308e7b8337b`.

The verified application head is `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
Evidence was recorded by `codex` at `2026-07-05T21:45:43Z`. This is not a
completion claim. Exact-revision GitHub CI, rendered UI review, project-standards
onboarding, Devon's final review, and Devon's merge remain outstanding.

## Verification summary

- `uv sync --frozen`: passed; 47 packages audited.
- Initial migration attempt at `127.0.0.1:5432` failed because that address reached
  the Mac PostgreSQL instance, which has no `postgres` role. This result is preserved.
- Migration retry against the actual disposable OrbStack Compose endpoint
  `192.168.97.2:5432/orchestrator_test`: downgrade to base and upgrade to
  `0002_default_max_attempts (head)` passed.
- `make check`: Ruff, formatting, and Pyright passed; 485 tests passed with one
  existing Starlette/httpx deprecation warning.
- `docker build` with pinned registry revision and digest: passed; image
  `sha256:47708c1eb51bbe1866befcff471aa821f181bf1ef3f5f630be29c6c7884f5c01`.
- Security scan: 0 BLOCK, 0 WARN, 1 judgment-only INFO
  (`bws.least-privilege-scope`); WS-3.1 consumes no BWS secret yet.
- Portfolio foundation matrix: `9 repos · violations=0 accepted=0 unknown=0`.
  Explicit orchestrator evaluation returned `no foundational repos found under
  roots`; onboarding is an external blocker and project-standards was not modified.
- In-app browser discovery returned no available browser surface. Rendered UI
  inspection is absent and remains Devon-owned evidence.

## Adversarial architecture review

- The transition kernel declares the exact 13-state, 29-edge graph and tests all
  169 ordered state pairs.
- Lease expiry recovery records only `Claimed|Executing → Failed → Ready → Claimed`;
  no direct reclaim edge exists.
- PostgreSQL locks, constraints, and transaction time arbitrate claims and leases.
- Worker transitions require current owner, attempt, token, release, and expiry
  proof. Workers cannot complete units or grant waivers.
- State transitions and mutation facts are written with attributable local events
  in the same transaction. Rollback injection proves state/event atomicity.
- The orchestrator stores a normalized immutable enforcement snapshot plus source
  reference/hash, not an editable duplicate intent document.
- Repository guards enumerate the approved POST surface and exclude event
  publication, dispatch, deployment, automatic merge, and tracker integrations.
- No blocking finding remained after the Task 9 through Task 12 independent reviews.

## AC-001

- Outcome: passed
- Evidence: `uv run pytest tests/kernel/test_state_graph.py -v` returned 178 passed.
  The independent expectation set covers every legal edge, every undeclared pair,
  and the five named forbidden transitions. Artifact:
  `tests/kernel/test_state_graph.py`; commit
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-002

- Outcome: passed
- Evidence: readiness, authority, role, dependency, lifecycle-event, rollback, and
  package-registration suite returned 100 passed. Full PostgreSQL gate returned
  485 passed. Artifacts: `tests/kernel/test_readiness.py`,
  `tests/services/test_dependencies.py`, and
  `tests/services/test_package_registration.py`; commit
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-003

- Outcome: passed
- Evidence:
  `uv run pytest tests/services/test_claim_concurrency.py tests/services/test_reclaim.py -v`
  returned 5 passed, covering exclusive concurrent acquisition, honest expiry
  failure, safe reclaim, token invalidation, attempt exhaustion, and human retry.
  Commit: `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-004

- Outcome: passed
- Evidence: the combined role/authority suite returned 100 passed; API/CLI suite
  returned 63 passed. Worker completion is rejected, worker lifecycle mutations
  require active claim proof, and worker waiver creation is rejected. Artifacts:
  `tests/kernel/test_transition_authority.py`,
  `tests/api/test_lifecycle_api.py`, and `tests/services/test_waivers.py`; commit
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-005

- Outcome: passed
- Evidence: the combined lifecycle-event/rollback suite is included in the
  100-passing focused run. `tests/services/test_lifecycle_events.py` proves
  attributable events; `tests/services/test_lifecycle_rollback.py` proves failed
  event insertion rolls back state. Commit:
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-006

- Outcome: passed
- Evidence:
  `uv run pytest tests/services/test_evidence.py tests/services/test_waivers.py -v`
  returned 19 passed. Evidence association, immutable database rows, exact replay,
  attempt credentials, and append-only supersession are covered. Persistence
  constraints also passed in the 485-test full gate. Commit:
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-007

- Outcome: passed
- Evidence: the 19-passing evidence/waiver suite distinguishes all adjudication
  outcomes and proves human-only waivers require failed evidence, rationale, risk,
  and follow-up with optional scope/expiry. The 485-test full gate includes
  append-only adjudication and readable Evidence Pack tests. Commit:
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-008

- Outcome: passed
- Evidence: authority and readiness tests in the 100-passing focused suite prove
  expansion invalidates approval/readiness until a new exact human approval.
  The approved design explicitly leaves same-scope subscription behavior to
  WS-3.3. Artifacts: `tests/kernel/test_authority.py` and
  `tests/services/test_package_registration.py`; commit
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-009

- Outcome: failed
- Evidence: local API/CLI contract command returned 63 passed, including real
  HTTP/FastAPI/PostgreSQL parity, list response parity, and stable errors. The
  required exact-revision GitHub `Quality` result does not exist until Task 14
  publishes the draft PR. Local artifact:
  `tests/cli/test_cli_http_parity.py`; commit
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy; revise only after exact PR-head `Quality` evidence exists

## AC-010

- Outcome: passed
- Evidence: the actual OrbStack disposable PostgreSQL database downgraded to base
  and upgraded to `0002_default_max_attempts (head)`. Health and migration tests
  passed in the 485-test full gate. The initial incorrect-localhost failure is
  preserved in the verification summary. Image build passed at
  `sha256:47708c1eb51bbe1866befcff471aa821f181bf1ef3f5f630be29c6c7884f5c01`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-011

- Outcome: failed
- Evidence: automated web, CSRF, authorization, Evidence Pack, and canonical-effect
  tests pass within the 485-test full gate. Independent security review found no
  remaining Critical, Important, or blocking Minor issue. No in-app browser surface
  was available, so Devon has not reviewed the rendered local UI or its live
  authentication behavior.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: Devon; rendered review evidence absent

## AC-012

- Outcome: passed
- Evidence: Devon approved the design specification after its adversarial review.
  Artifact:
  `docs/superpowers/specs/2026-07-04-ws31-orchestrator-core-design.md`; design
  correction commit `f233498`; implementation head
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: Devon

## AC-013

- Outcome: passed
- Evidence: architecture suite returned 24 passed. Security scan returned 0 BLOCK
  and 0 WARN. Full-diff search and route inventory found no automatic merge,
  production mutation, publisher/dispatch, tracker-canonical, tracked-secret, or
  worker-completion path. Artifact: `tests/architecture`; commit
  `aa76b29df0753a0eeba4cde67a3e4028d3da4c90`.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: policy

## AC-014

- Outcome: failed
- Evidence: no draft PR, exact PR-head `Quality` result, Devon final review, or
  Devon merge exists yet. Architecture tests prove there is no automatic merge
  path. The worker has not run and will not run a merge command.
- Recorded by: codex
- Recorded at: 2026-07-05T21:45:43Z
- Review: Devon; final review and merge decision remain outstanding
