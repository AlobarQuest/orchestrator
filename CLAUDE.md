<!-- code-standards:start -->
# Code Quality (code-standards layer)

Standards reference: `~/Developer/code-standards/STANDARDS.md`

## Before writing a cross-cutting pattern — query Code Brain

Before implementing a recurring cross-cutting concern (logging, error handling,
auth, notifications, API conventions, secrets, …), query **Code Brain** — the
machine source of record for our paved roads — and follow its rules:

- `get_road("<slug>")` → the decided approach + rules + exemplars, or
- `get_rules(severity="BLOCK")` → the must-follow rules.

Do **not** infer the standard from existing code; it may predate the standard.
When you decide a new cross-cutting pattern, write it back (`add_road` / `add_rule`).

## Before declaring a non-trivial change done

1. Run `make check` — full-repo lint, type-check, and tests must be green.
2. Run `/code-review` — review the diff for correctness bugs and simplification opportunities.

Both gates apply to any change that touches logic, interfaces, or configuration.
Trivial fixes (typos, comment edits) may skip `/code-review` at your discretion.

## Enforcement

A diff-scoped Stop hook enforces this automatically: it runs the linters over your
changed files when the session ends and blocks completion if new violations are
introduced. Existing baseline violations are tracked and do not block.

## Canonical example module

The authoritative pattern for this repo's style is:

the cleanest, most idiomatic existing module in this repo

When writing new code, mirror the structure, naming conventions, and documentation
style of that module.

<!-- code-standards:end -->

## Known Non-obvious Invariants

Each rule below is stated once, as it stands now. Where a procedure, method lesson or history lives
elsewhere, the rule names it. Pages: `docs/operations/` (deploy, credentials, driving-a-unit,
architecture-guards, local-development, landing-lanes, rotation-executor, onboarding-a-target,
onboarding-a-fork),
`docs/method-lessons.md`, and `docs/history/claude-md-invariants-archive.md` (the full original
text of every bullet, by number).
Placement rule: global CLAUDE.md §7.

- Dependency-update `repo.edit` authority is executable only if the envelope declares a non-empty
  `mutation_commands` that is an ordered subset of `allowed_commands`; proposal admission and
  dispatch both enforce it, and approved envelopes are never rewritten. History:
  `docs/history/claude-md-invariants-archive.md` #1.
- Everything in this section must stay below `<!-- code-standards:end -->`: `inject_stanza` replaces
  the whole managed block. Re-rendering the block over this file must be a no-op. History:
  `docs/history/claude-md-invariants-archive.md` #2.
- Generic authority approvals satisfy work-unit readiness only. An authority-expanding
  standing-context update needs a named human approval bound to the exact standing-context
  fingerprint (`kernel/context.py::classify_context_update`, via `services/lifecycle/context.py`).
- Work-unit envelope expansion (capabilities, levels, budgets) has no detector; it is safe only
  because the envelope is write-once, enforced by `tests/architecture/test_authority_write_once.py`.
  A path that mutates the envelope must ship a fail-closed expansion check; never "fix" that test.
  History: `docs/history/claude-md-invariants-archive.md` #4.
- Protocol smoke tests may manipulate time or lease expiry as fixture setup; runtime recovery
  behaviour must go through public API/CLI surfaces, never private service shortcuts. History:
  `docs/history/claude-md-invariants-archive.md` #5.
- Generated post-deploy acceptance criteria are verifier-owned: public adjudication rejects their AC
  IDs, so post-deploy completion always flows through the verifier evaluators. History:
  `docs/history/claude-md-invariants-archive.md` #8.
- Production `/api` is M2M-only at the Traefik proxy (identity headers stripped); a human API route
  needs a dedicated forward-auth router. A browser 401 on `/api` means M2M-only, never a retry
  quirk. Detail: `docs/operations/driving-a-unit.md`.
- Coolify `/envs` responses carry `value` and `real_value` (DB passwords) for any token with
  `read:sensitive`: parse in-process, print whitelisted fields only. Each variable is two rows
  (`is_preview` false and true) and a PATCH changes one; PATCH may 500, fall back to delete +
  recreate. Detail: `docs/operations/credentials.md`.
- The authority envelope is a cross-repo contract with factory-runner, pinned by byte-identical
  `tests/fixtures/runner_authority_envelope.json` and the same `CONTRACT_SHA256` in both repos;
  change both repos together. History: `docs/history/claude-md-invariants-archive.md` #13.
- `DispatchRecord.runner_attempt` (dispatch decisions, skipped included) and
  `WorkUnit.attempt_count` (worker claims) are independent; never require them equal. See
  `docs/operations/verifier.md`. History: `docs/history/claude-md-invariants-archive.md` #14.
- `AuthorityEnvelope.normalized()` is what an authority approval attests: fields outside
  `KNOWN_FIELDS` contribute only their names. Authority-bearing fields must be known; adding one
  rewrites every authority fingerprint. History: `docs/history/claude-md-invariants-archive.md` #15.
- factory-runner acts only when `target_repo == current_repo`, so dispatch resolves the target per
  work unit from `authority.constraints.target_repository`, never from a process-global setting.
  History: `docs/history/claude-md-invariants-archive.md` #16.
- The envelope contract does not test the reusable workflow: a reusable workflow may invoke only
  what is reachable from the caller's checkout (the installed console script), and factory-runner
  must stay public with Actions access enabled. History:
  `docs/history/claude-md-invariants-archive.md` #18.
- Write `ORCHESTRATOR_M2M_CREDENTIALS` before `ORCHESTRATOR_M2M_ROLES` and verify each in the
  container before the next restart: roles without credentials fail boot closed. Detail:
  `docs/operations/credentials.md`.
- An M2M credential's `agent_id` must resolve in the registry bundle baked into the image from
  security-standards' git tree; attribution is permanent, so adding an actor needs a merged commit
  plus rebuild. Never borrow another identity. Detail: `docs/operations/credentials.md`.
- Envelopes are write-once, and an approved decomposition can be superseded only while none of its
  units has been claimed or dispatched (ADR-0052): dry-run every unit's commands against the real
  target repo (twice, clean clone) before work starts. Detail: `docs/operations/driving-a-unit.md`.
- `make check` here needs Postgres, `SECURITY_STANDARDS_DIR` and a migrated DB, so it must never
  appear in this repo's authority envelope; the dependency-update profile's verifier is
  `uv lock --check`, and CI's `Quality` job runs the tests.
  Detail: `docs/operations/driving-a-unit.md`.
- Request entry points own their transaction and must `session.commit()`; functions called inside
  another transaction must never commit. A persistence test must re-read through a different
  session, not `expire_all()`. History: `docs/history/claude-md-invariants-archive.md` #27.
- `test_unreachable_guards.py` requires every public kernel/services function to be import-reachable
  from a production entry point; an allowlist entry that would read "in fact it is called" means the
  predicate is wrong. Detail: `docs/operations/architecture-guards.md`.
- `work_units.version` has exactly three writers, all state transitions (`_perform_transition`,
  `claims._transition`, `_system_fail_without_new_attempt`); recording an adjudication does not bump
  it. History: `docs/history/claude-md-invariants-archive.md` #30.
- A DB trigger rewrites `work_units.updated_at` on every update, so tests exercise staleness by
  shrinking the env-overridable threshold, never by back-dating the row. History:
  `docs/history/claude-md-invariants-archive.md` #31.
- Never run two pytest suites against one test database: fixtures drop and recreate it. Use a
  per-worktree database. Detail: `docs/operations/local-development.md`.
- Worker FAILED and human CANCELLED transitions release the latest claim in the same transaction via
  the sole `claim_release.release_claim`; keep replay-before-release and unit-then-claim lock order.
  History: `docs/history/claude-md-invariants-archive.md` #34.
- `automated_test` floors to deterministic-permitted: it resolves from arriving evidence that has an
  evaluator and otherwise asks a human, who may decide it only while the unit is `awaiting_review`.
  Never add it to `DETERMINISTIC_TYPES` (that halts the factory). `automated_check` resolves only
  on verifier-owned named-check evidence, and only `automated_check` criteria accept that evidence.
  Every human-adjudication decision routes through `human_may_adjudicate`. Decomposition `ac_id`
  is the criterion UUID; evidence wants `AC-001`. History: `docs/history/claude-md-invariants-archive.md` #35.
- Merged is not deployed: read `https://sds.alobar.net/openapi.json` before reasoning about what
  production serves. Detail: `docs/operations/deploy.md`.
- SDS M2M bearers live in BWS project `SDS Operator`: bootstrap with `scripts/sds-token.sh` (never
  vps-backup's helper), UUIDs in `.bws-secrets.toml`; never echo values. Detail:
  `docs/operations/credentials.md`.
- Only `DomainError` and `APIAuthenticationError` have handlers; anything else is a bare 500. Route
  parsing must raise `DomainError`, and a service must reject every value a DB CHECK would before
  the write. History: `docs/history/claude-md-invariants-archive.md` #44.
- `reclaim_expired_claim` grants attempts without `claim_unit`; any "may this unit run again" rule
  belongs in `claims._readiness_eligibility_error`, shared by reclaim, release and requeue. History:
  `docs/history/claude-md-invariants-archive.md` #45.
- The evidence-pack markdown is redacted for public PR comments (no approver ids, rationale, actor
  ids); new sections must redact by hand. A `text/markdown` route needs a `NON_JSON_SUCCESS_PATHS`
  entry. History: `docs/history/claude-md-invariants-archive.md` #46.
- A FastAPI `response_model` silently drops undeclared keys: extend the model in the same change as
  the service, and pin a contract route's model fields in its contract test. History:
  `docs/history/claude-md-invariants-archive.md` #48.
- The runner brief is a cross-repo contract pinned by byte-identical
  `tests/fixtures/runner_brief.json` and `CONTRACT_SHA256` plus a derivation test in each repo;
  `RunnerBrief` is `extra="allow"`, keep it so. History:
  `docs/history/claude-md-invariants-archive.md` #49.
- Serve a new brief field only after factory-runner declares it and the pin advances; the `Runner
  consumer compatibility` job enforces fields and capabilities. Renaming that required check must
  move the protected context too. Detail: `docs/operations/architecture-guards.md`.
- Under `src/orchestrator/` the word guards (ADR-0051) refuse forbidden terms in CODE only:
  identifiers, imports and non-docstring strings with no whitespace. ws32 forbids `dispatch`,
  `deploy`, `coolify`, `workflow_dispatch`, `factory-runner`, `auto_merge`, `merge_pull_request` and
  similar; ws34 forbids `workflow_dispatch`, `factory_runner`, `github.actions`, `coolify` and
  `merge_to_main`. Read the lists in the tests; reword rather than allowlist.
- New routes must be added to the exact POST/GET inventories in `test_scope_guards.py` (POST
  includes `/review`); a new ingress POST route also needs a row in `tests/idempotency/test_matrix.py`
  or a reasoned `NON_INGRESS_POST_ROUTES` entry; a `workflow_dispatch` workflow
  needs both workflow-guard allowlists edited. Detail: `docs/operations/architecture-guards.md`.
- Coolify only pulls prebuilt GHCR tags; build with the `Release image` workflow (full 40-char sha
  as input). A hand build needs the labels and `ORCHESTRATOR_REVISION`. Detail:
  `docs/operations/deploy.md`.
- Never build a SQL `IN (...)` CHECK with `f"{VOCAB!r}"`: a one-element tuple renders a trailing
  comma. Join quoted members explicitly, identically in model and migration. History:
  `docs/history/claude-md-invariants-archive.md` #54.
- Files importing an HTTP client must be in `OUTBOUND_ALLOWLIST` (`test_wsp21_invariant_scan.py`)
  with a reason; these whole-repo scans run only in a full `make check`. Detail:
  `docs/operations/architecture-guards.md`.
- Module-level string collections used in membership tests must be registered in
  `VOCABULARY_REGISTRY` or marked `# not-a-vocabulary: <reason>`
  (`test_cross_boundary_vocabulary.py`); register genuine cross-boundary ones. Detail:
  `docs/operations/architecture-guards.md`.
- Alembic revision ids must be at most 32 characters (`alembic_version.version_num` is
  `varchar(32)`). History: `docs/history/claude-md-invariants-archive.md` #57.
- The traceability observation hop reads unit-scoped observations plus the landing record of each
  release binding's merge commit (exact repository + commit join); other repository-, service- or
  environment-scoped observations appear in no chain. With no factory PR binding, the `pr` hop
  comes from that landing only when the binding's `implementation_pr_number` agrees, marked
  `source="landing_ledger"`. History: `docs/history/claude-md-invariants-archive.md` #60.
- Migrate-before-swap is safe only because neither Coolify's (disabled) health check nor the
  Dockerfile `HEALTHCHECK` consults `/health/ready`; never point either at it without re-deciding
  migration order. Detail: `docs/operations/deploy.md`.
- `deployment_observation` summaries are strict `extra="forbid"` models in
  `services/release/deployment_observations.py`, published in OpenAPI and held to each producer's
  copy by `tests/contract`. `container_image` needs four summaries plus an optional dispatch one,
  `machine_local` only `activation_summary`; the secret detector rejects any key name containing
  a part in `kernel/secret_metadata.py::SECRET_KEY_PARTS` (`token`, `credential`, `log`, `body`,
  `response` among them). Detail: `docs/operations/post-deploy-verification.md`. History:
  `docs/history/claude-md-invariants-archive.md` #63.
- The orchestrator assigns the dispatch ordinal (`max(attempt_count, highest recorded) + 1`); a
  supplied `runner_attempt` other than that is refused `dispatch_attempt_not_next`, and a retry
  reuses the `idempotency_key`. Detail: `docs/operations/driving-a-unit.md`.
- `ORCHESTRATOR_DISPATCH_ENABLED` defaults true and is only an off switch; dispatch has one caller,
  `POST /work-units/{id}/dispatch`. Never restart the orchestrator while a dispatched run is live.
  Detail: `docs/operations/driving-a-unit.md`.
- `budgets.max_llm_calls` gates the next claim, not a running attempt, and `budget_exceeded` is
  unrecoverable: authorise at least `max_attempts x max_turns x 2` (the profile's `BUDGETS`, its
  approval-policy grant and the known-good pattern are held equal by the budget-agreement rule below). Detail:
  `docs/operations/driving-a-unit.md`.
- `record_approval` enforces no lifecycle state for either subject type; an approval's reach is
  bounded by its consumers (the `AWAITING_APPROVAL -> READY` guard and admission), not by refusal.
  History: `docs/history/claude-md-invariants-archive.md` #74.
- `reach` (`reach_vocabulary.py`) is a package-declared SET over `source_repository`, `live_estate`,
  `external_system`, `operator_machine`; never inferred, absence is `unknown`, members only narrow,
  no `orchestrator_self` member. Read it only via `reach_from_snapshot()`; execution locus is a
  separate, unmodelled dimension. History: `docs/history/claude-md-invariants-archive.md` #78.
- `factory-policy.toml` can only REFUSE: no schema value permits, `factory_policy.py` imports no
  config, an empty refusal list means no objection, and a listed value (`known_good`,
  `[admission]`) only withholds one objection, so an EMPTY `[admission]` list refuses every value.
  Exactly one row per `REACH_VOCABULARY` member or it fails to load; `SUPPORTED_SCHEMA_VERSIONS` is
  exact; a new field ships with its reader. History: `docs/history/claude-md-invariants-archive.md`
  #81.
- Minted follow-up units are created in `AWAITING_REVIEW`, and minting refuses rather than inherit
  an unknown reach. History: `docs/history/claude-md-invariants-archive.md` #88.
- A lease may only LENGTHEN (`kernel/leases.py`: above `DEFAULT_LEASE`, at most `LEASE_CEILING`);
  reach sets compose by maximum. A lapsed lease ends its attempt: `renew_claim` refuses
  (`lease_expired`) and `validate_active_claim` refuses evidence and PR-binding writes
  (`claim_not_active`). History: `docs/history/claude-md-invariants-archive.md` #89.
- The lease has three writers, all via `lease_policy.py::claim_lease`: `claim_unit`, `renew_claim`
  and `reclaim_expired_claim` (which never calls `claim_unit`). Any per-claim rule must cover all
  three, with one deliberate exception: the rotation-unit change window gates starts only, so
  renewal is never windowed and reclaim refuses a rotation unit outright (ADR-0055 amendment 2).
  History: `docs/history/claude-md-invariants-archive.md` #90.
- `test_factory_policy.py` requires exactly one module in `src/orchestrator/` to name the policy
  artifact's filename, docstrings included, and forbids its row rationales in source. Reword; never
  allowlist. Detail: `docs/operations/architecture-guards.md`.
- `release_claim` runs only on failure, cancel, reclaim, release and expiry recovery, so a completed
  unit keeps an unreleased lapsed claim. Claim predicates must gate on unit state
  (`claims.CLAIM_HOLDING_STATES`) and the newest attempt, never on `released_at IS NULL`. History:
  `docs/history/claude-md-invariants-archive.md` #96.
- Build sessions work in `.worktrees/<ws>` with their own venv and their own
  `orchestrator_test_<ws>` database. At session end, check the MAIN tree's `git status` for stray
  files you created, then remove the worktree, database and merged branch. Detail: `docs/operations/local-development.md`.
- Whether landing on a repository redeploys is read from App Brain's `default-branch-landing` route,
  never derived; deploys can come from CI, a repo webhook or the host's git integration. A repo with
  no determination is un-landable. Detail: `docs/operations/landing-lanes.md`.
- The dispatch App has no `checks` permission, so named-check evidence is read from Actions jobs and
  `check_name` must be the JOB name. Merging needs `contents: write`; read an App's permissions from
  the installation or mint response, never `/app`. Detail: `docs/operations/credentials.md`.
- `evidence` rows are append-only by DB trigger; a test that corrupts a stored payload sets it on
  the ORM instance and calls `evaluate_criterion` without committing. History:
  `docs/history/claude-md-invariants-archive.md` #103.
- App Brain's landing route never 404s and returns a value plus a `reason`; consumers must tell
  `no_app_record` from `not_assessed`, and one repository may fold several apps. Detail:
  `docs/operations/landing-lanes.md`.
- `REACH_VOCABULARY` must stay a dict of string literals or `test_cross_boundary_vocabulary` loses
  the whole vocabulary; fix with a pinned duplicate, never an allowlist entry. Detail:
  `docs/operations/architecture-guards.md`.
- `_blocked_reason` (`services/execution/dispatch.py`) normalizes the envelope exactly once,
  test-enforced; an admission term needing envelope data is evaluated inside it. History:
  `docs/history/claude-md-invariants-archive.md` #108.
- A verifier adjudication may arise only from `verify_work_unit`; a direct POST is
  `verifier_evaluation_required`. Reconciliation detects reality changing, never reality
  misreported, so worker-attested evidence has no downstream net. History:
  `docs/history/claude-md-invariants-archive.md` #109.
- `authority.budgets.max_attempts` has no enforcement reader; the enforced cap is the
  `work_units.max_attempts` column, raised by `authorize_retry`. Nothing compares the two. History:
  `docs/history/claude-md-invariants-archive.md` #110.
- The conformance gate's anti-tautology rule is docstring only. Do not delete the `status ==
  "green"` short-circuit: the pinned envelope fixture would then be refused and all dispatch stops.
  History: `docs/history/claude-md-invariants-archive.md` #111.
- In `wave-exit-manifest.toml` a clause rationale goes in `note` (always written), never `proves`
  (written only on pass); neither field holds dated or result-specific text. History:
  `docs/history/claude-md-invariants-archive.md` #121.
- Landers separate deliberate refusals (subset semantics, clear next window), exceptions (never land
  under policy) and `waiting`; none is a finding. Freshness is suppressed only beside an exception,
  keyed on durability. Detail: `docs/operations/landing-lanes.md`.
- `runner_command_authority_violation` mirrors the runner: `allowed_commands` required when
  `command.run` is allowed; `mutation_commands` required iff `change_class == "dependency-update"`,
  validated whenever present; optional `verify_commands` validated whenever present (non-empty,
  subset of `allowed_commands`, disjoint from `mutation_commands`). The orchestrator may be
  stricter, never looser. History:
  `docs/history/claude-md-invariants-archive.md` #126.
- `allowed_commands` is the coding agent's entire enforced Bash vocabulary (exact match), so an
  envelope must list every command the work needs. Without `verify_commands`, finalize re-executes
  it as written, so authors must order it mutators first, verifier last (finalize never reorders).
  With `verify_commands`, finalize runs only the mutators (in `allowed_commands` order) and then
  that script; every other allowed command is agent-only. For edit-shaped work (no mutators) that
  includes a build: a build the verifier depends on must be in `verify_commands` or a mutator, or
  finalize never runs it. Absent keeps every existing fingerprint. Detail:
  `docs/operations/driving-a-unit.md`.
- `DRAFT/READY -> CANCELLED` need the `decomposition_superseded` guard, which only superseding the
  unit's own approval sets (ADR-0052); otherwise a stranded READY unit is inert and is retired by
  letting it fail then cancelling. History: `docs/history/claude-md-invariants-archive.md` #134.
- Never set `required_approving_review_count`; a branch-protection required check is a JOB name
  verified against a real open pull request, and auto-merge with no required checks merges
  instantly. Detail: `docs/operations/landing-lanes.md`.
- The capability vocabulary has four copies (orchestrator, factory-runner, the contract fixture,
  intent-packages' `CAPABILITIES`); grep the whole portfolio before widening it. History:
  `docs/history/claude-md-invariants-archive.md` #140.
- Wire errors are nested (`body["error"]["code"]`); no `UnitPrMerge.status` carries authorship; the
  landing trailer depends on `squash_merge_commit_message`; landing `facts` are frozen at first
  write. Detail: `docs/operations/landing-lanes.md`.
- OBSERVER's only write is `POST /api/v1/observations`, confined in one place,
  `api/dependencies.py::_confine_observer`, by route template with unknown routes refused. All
  observe-and-report producers share `orchestrator-observer`. History:
  `docs/history/claude-md-invariants-archive.md` #144.
- Interpreter is `.python-version`; `test_interpreter_agreement.py` holds Dockerfile, pyright and
  `requires-python` equal. Raising the floor rewrites source (ruff targets it). Tests need
  `postgresql+psycopg://`. Detail: `docs/operations/local-development.md`.
- `change_class` is a free string matched against `factory-policy.toml`'s `[admission]`
  `change_classes`, and `required_capability` against its `capabilities` (bounded to
  `RUNNER_CAPABILITIES` at load); a missing `change_class` falls back to `required_capability`,
  which no listed class matches. Both lists only withhold an objection: empty refuses every value.
  Widening one is a standing authority change and a release. History: ADR-0053;
  `docs/history/claude-md-invariants-archive.md` #147.
- Key runner-shape gates on `RUNNER_ENVELOPE_FIELDS` (pinned by `runner_envelope_contract.json`),
  never `KNOWN_FIELDS`, which adds `unknown_fields`. Store `runner_payload(envelope)`, not
  `normalized()`. History: `docs/history/claude-md-invariants-archive.md` #148.
- `runner_authority_violation` is the one composed predicate, asked at breakdown ingress, unit
  registration, authority approval and admission; capability-name refusal lives only at admission.
  History: `docs/history/claude-md-invariants-archive.md` #149.
- Unknown capability names fail closed but mistyped levels (e.g. `requires_approval`) read as
  prohibited and pass orchestrator gates while the runner refuses the envelope. History:
  `docs/history/claude-md-invariants-archive.md` #150.
- `FACTORY_PR_TOKEN` (BWS `a3240c2e-…`) lives as Actions secrets in the repositories listed per
  copy in factory-runner's `.cred-consumers.toml`; the rotation executor re-sets every copy and
  verifies each by its repository's `secret-fingerprint.yml` report. Detail:
  `docs/operations/credentials.md`, `docs/operations/rotation-executor.md`.
- Named-check evidence is ingested only for criteria declared `automated_check`, with `check_name`
  the JOB name. A human adjudication does not complete a unit: `AWAITING_REVIEW -> COMPLETED` is a
  separate human gate. History: `docs/history/claude-md-invariants-archive.md` #154.
- Columns holding GitHub Actions run ids must be `BigInteger`: ids exceed 2^31, and `Integer` fails
  only on Postgres (`integer out of range`), never on SQLite. History:
  `docs/history/claude-md-invariants-archive.md` #157.
- Code promising "nothing raises" on an httpx call must catch `(httpx.HTTPError, httpx.InvalidURL,
  ValueError)` at construction and request: a malformed host raises `UnicodeError`, and an escape is
  a bare HTTP 500. History: `docs/history/claude-md-invariants-archive.md` #161.
- `TransactionClock.now()` is `transaction_timestamp()` and does not advance: every admission term,
  including change windows, is judged at the instant the transaction opened. History:
  `docs/history/claude-md-invariants-archive.md` #163.
- `change_window` is optional per reach row in `factory-policy.toml`; `live_estate` must keep one,
  enforced by `test_the_live_estate_row_declares_a_change_window`. Never require a window in
  `reach_admission.change_window_refusal`. History: `docs/history/claude-md-invariants-archive.md`
  #164.
- Query change-manager `/api/items` by `source` alone and filter `status` client-side: the server
  hides proposed sources unless named and a status filter makes pending records vanish. Join on
  `(target_repository.lower(), pull_request_number)`. Detail: `docs/operations/landing-lanes.md`.
- Do not propose a required status check to enforce change windows: it puts the whole availability
  chain in front of every actor with no in-band recovery (rejected in ADR-0019 increment 4). The
  orchestrator landing pull requests itself is the design. History:
  `docs/history/claude-md-invariants-archive.md` #170.
- An observation `source_reference` must identify the re-runnable unit (e.g. the attempt) and carry
  a fact digest when its subject can re-run; same reference with different facts is
  `observation_conflict` forever. `record_observation` returns, not raises, `DomainError`. History:
  `docs/history/claude-md-invariants-archive.md` #173.
- A launcher or `factory decompose` run needing two BWS identities must read each Keychain item
  directly into distinct variables; one ambient `BWS_ACCESS_TOKEN` breaks both. Detail:
  `docs/operations/credentials.md`.
- Branch updates use `update-branch` with `expected_head_sha`, treating 202 as success; only a pull
  request whose sole remaining obstacle is freshness qualifies, and Dependabot branches are edited
  one per repository at a time (ADR-0045). Detail: `docs/operations/landing-lanes.md`.
- Deploy verification must read the served OpenAPI schema for changed response models: a healthy,
  correctly tagged image can still lack fields a consumer reads. Detail:
  `docs/operations/deploy.md`.
- `deploy_watcher` derives a unit from the merge commit's `SDS-Unit:` trailer, confirmed against
  `pr_merge` history; the change record only names which pull request to watch, and any status
  suffices. Detail: `docs/operations/landing-lanes.md`.
- change-manager's work policy approves a `work` record (as `work-policy`) only when it is a
  standing dependency-update package (`change_class` `dependency-update`, source repository
  intent-packages, risk `caution`) whose `target_repository` is in its inert or deploying
  population; a person moves every other one. No producer credential moves a status:
  `bump_proposer` is propose-and-read and the `propose` scope refuses status routes.
  `bump_proposer` creates a missing package for a declared repository's failed npm, pip or uv bump
  (reach `live_estate` if it deploys, else `source_repository`), judges each bump by its own
  population's block, and alerts (`dependency_bump_key`) on what it can't route. Detail:
  `docs/operations/landing-lanes.md`.
- `rotation_proposer` never approves a package revision. A pass that revises a standing rotation
  package to `ready_for_review` proposes nothing, and the first pass after a named human approves
  it proposes. "Due" is infraops' rule, read from `security-drift-cli cred-findings` and never
  re-implemented here. The occurrence comes from that answer's structured fields and never
  contains today's date. It acts only on a credential its registry entry marks `rotated_by_sds`,
  which infraops' 04:00 window then refuses; a package without that flag is a finding and is never
  acted on. Its one write to infraops is `security-drift-cli record-rotation`, made only when the
  record for the approved revision is `resolved` by `work-watcher` and the trigger isn't an
  exposure. A revision it writes carries the registry's destinations; an approved revision that
  differs from the registry is revised, never proposed; and a due, handed-over credential with no
  package gets revision 1 generated from its row and its `class`'s type (`generate.py`,
  `CLASS_TYPES`), validated and written by intent-packages' `create-rotation`. The work
  carrier carries a standing rotation package only when its credential's registry row sets
  `rotated_by_sds` and the member record grants `credential_rotation` on that registry's
  repository. Detail:
  `docs/operations/rotation-proposer.md`.
- change-manager's `resolved` is terminal by design and will not be made recoverable; `reactivate`
  applies only to `wontfix`. Do not re-propose recoverability. History:
  `docs/history/claude-md-invariants-archive.md` #209.
- `mergeable_state: blocked` has four causes; the landing lane reads workflow runs at the head to
  separate `landing_checks_not_clean`, `landing_checks_awaiting_verdict` and
  `landing_checks_in_flight`. The Checks API is 403 to the App. Detail:
  `docs/operations/landing-lanes.md`.
- `qualifies_for_branch_update` (acting) and `freshness_derived_refusals` (reporting) are separate
  criteria; never fold a refusal into a shared set to excuse it for one consumer. Detail:
  `docs/operations/landing-lanes.md`.
- Every LaunchAgent installer sources `scripts/sds-install.sh` and calls
  `sds_refuse_linked_worktree` before writing; `tests/scripts/test_launchd_installers.py` enforces
  it. Detail: `docs/operations/local-development.md`.
- In `services/reporting/traceability.py::_resolve_pr`, a `pr_number` anchor with
  `source_repository` reads `ReleaseArtifactBinding`, without it `UnitPrBinding`; try the bare form
  before concluding a chain is absent. History: `docs/history/claude-md-invariants-archive.md` #224.
- `UnitPrMerge` records only the orchestrator's own merges; the landing commit for a unit comes from
  the landing ledger's `landing` observation, confirmed against `UnitPrBinding.head_sha`, pinned by
  `tests/contract/test_landing_fact_contract.py`. History:
  `docs/history/claude-md-invariants-archive.md` #228.
- Making a UNIQUE constraint's column nullable disables it for NULL rows; use
  `postgresql_nulls_not_distinct=True` and test by writing around the service. History:
  `docs/history/claude-md-invariants-archive.md` #229.
- A producer that shells out to `uv` must resolve it with `shutil.which` and fall back to
  `~/.local/bin`; launchd's PATH names only system directories. Detail:
  `docs/operations/local-development.md`.
- A package the factory is to land must carry no `human_review` criterion, retained or mapped
  (`factory create` scaffolds one as AC-002): `verifier_decided_completion` refuses the landing
  after completion. Detail: `docs/operations/driving-a-unit.md`.
- A machine may register an intake only by naming the approved change record that caused it
  (`intake_change_record_required`); an intake with no cause is registered by a human. Detail:
  `docs/operations/driving-a-unit.md`.
- The scheduled launchers do not share exit codes 2 and 3 (found vs unreadable means opposite things
  across lanes); key anything wired to them per launcher from its header. Detail:
  `docs/operations/local-development.md`.
- Landing-ledger observations are content-addressed and immutable: a change to what a producer
  derives must, in the same change, handle every stored row whose facts would move, or each write is
  `observation_conflict`. Detail: `docs/operations/landing-lanes.md`.
- No reported finding kind may be a substring of another;
  `test_no_reported_kind_is_a_substring_of_another` guards it. Detail:
  `docs/operations/landing-lanes.md`.
- `landing_ledger` derives a landing's basis from `merged_by` (`is_machine` = `[bot]` suffix), so
  the identity that merges or arms a merge is the recorded basis; check what reads it before
  changing a credential. Detail: `docs/operations/landing-lanes.md`.
- A producer that publishes with `git push origin main` must refuse unless on `main`, hold the
  command as one string (the merge guard scans text), derive the branch from it, and re-refuse
  unpublished commits each pass. History: `docs/history/claude-md-invariants-archive.md` #257.
- `sds_deadman_finish` pings success for 0 and for the lane's declared `--finding` code; only other
  codes ping `/fail`, so the check answers liveness, never whether something was found. Detail:
  `docs/operations/local-development.md`.
- The outcome-alarm launchers (`run-fork-sync-watch.sh`, `run-fork-harden.sh`,
  `run-membership-check.sh`, `run-bump-proposer.sh`, `run-change-proposer.sh`,
  `run-work-carrier.sh`) run through `sds_outcome_begin`/`sds_outcome_end` in
  `scripts/sds-deadman.sh`: `/fail` with the report on every non-zero exit, findings included,
  pinging only on the bridge lanes' acting run. They never arm the dead-man switch, and a launcher
  that doesn't arm it must not contain the arming function's name, which `test_sds_deadman.py`
  keys on. Detail: ADR-0021 amendment 2.
- The scheduled lanes run the main tree's working copy and `.venv` by absolute path: never check out
  a branch, edit, or run uv there while a pass may fire; edit in a worktree. Detail:
  `docs/operations/local-development.md`.
- The dispatch App's installation reach is deliberately wider than its work; every App call site
  must stay bounded by a grant or allowlist check, since the ruling rests on that. Detail:
  `docs/operations/credentials.md`.
- An envelope's verifier must exercise what the change could break (build, tests), not merely
  confirm the edit; use the profile's generated command list, never a hand-authored one. Detail:
  `docs/operations/driving-a-unit.md`.
- A reporting threshold is a plain int with a real default and no off value; a dead config knob is
  deleted with the function it configured. Detail: `docs/method-lessons.md` #32.
- `dead_letter` reports `stalled_verification` over `VERIFICATION_STATES`, kept separate from
  `APPROVAL_STATES` (different owner, different remedy); `revision_required` is deliberately
  uncovered. History: `docs/history/claude-md-invariants-archive.md` #276.
- Before deleting anything, grep the exit manifests (`docs/operations/wave-exit-manifest.toml`) and
  evidence, not only `src/`; the tracker adapter and routes stay because Wave 2's met bar attests
  them. History: `docs/history/claude-md-invariants-archive.md` #287.
- The dependency-update budget lives in five places; `scripts/check_profile_budget_agreement.py`
  holds every `dependency-update` known-good pattern in `factory-policy.toml` (uv, npm, pip) to
  intent-packages' `BUDGETS` and `CAPABILITIES`. A pattern's `target_repositories` is optional
  (schema 9): absent, the member record's grant decides which repositories. Raising the pattern
  ceiling withholds a human gate: a standing-authority decision. History:
  `docs/history/claude-md-invariants-archive.md` #291.
- Dispatch admission reads the member record's grant for the unit's change class
  (`services/execution/target_grant.py`): `dependency_updates` for `dependency-update`, `app_changes`
  for every other class, only in state `granted` on a non-retired member, case-folded; otherwise
  `target_repository_not_granted`. The lanes' copy of the mapping is held equal by
  `test_target_grant.py`. Detail: `docs/operations/driving-a-unit.md`; full procedure:
  `docs/operations/onboarding-a-target.md`.
- Moving a rollout workflow is a paired change: re-transcribe it in
  `src/deploy_watcher/workflows.py` AND ratify the derived criteria plus the landing blob pin in
  change-manager's `app/deploy_policy.py`, or its records stay silently pending. Detail:
  `docs/operations/landing-lanes.md`.
- `change-mgr.alobar.net` is Cloudflare-proxied: every client must send a named User-Agent, and a
  non-JSON 5xx from it is Cloudflare, not the origin. Detail: `docs/operations/landing-lanes.md`.
- In change-manager a field-list tuple is also the stored payload, and producers replay their whole
  population: a field added to an asserted tuple 409s forever on old rows; pass excluded fields as
  explicit kwargs. Detail: `docs/operations/landing-lanes.md`.
- The orchestrator image does not migrate itself: build, run `alembic upgrade head` from the new
  image, then swap, and verify `alembic current` equals `heads`. Detail:
  `docs/operations/deploy.md`.
- A holding Dependabot branch whose checks never finish stalls its repository by design (ADR-0045);
  do not test that every holding refusal clears on its own. Detail:
  `docs/operations/landing-lanes.md`.
- The rotation executor runs as two identities, each confined both ways
  (`rotation_claims.RotationExecutors`): `ORCHESTRATOR_ROTATION_KEYCHAIN_EXECUTOR_AGENT_ID` alone
  claims a rotation unit whose envelope's `rotation_adapter` is in `KEYCHAIN_ADAPTERS`, and
  `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID` alone claims every other rotation unit
  (`rotation_claims.is_rotation_unit`); neither claims anything else. An unset setting refuses
  every claim on its units, and boot refuses a credential holding `rotation-executor-v1` or
  `rotation-executor-keychain-v1` that its profile's setting doesn't name. A lapsed rotation
  claim is released (`release_expired_claim`), never reclaimed. A rotation claim asks the
  `live_estate` window unless its envelope sets `touches_no_hosted_service: true`, except a
  continuation after a lapse with the holder's evidence. Detail:
  `docs/operations/driving-a-unit.md`.
- A rotation unit needs no per-unit authority approval while `factory-policy.toml`'s
  `rotation_known_good` pattern recognises its envelope under every reach member. Rotation patterns
  apply only when `is_rotation_unit` holds for the unit and its revision, never on envelope shape
  alone, and never recognise `host_without_overlap`. Rotation units are never dispatched, so the
  claim re-asks policy for a unit nobody approved, refusing `authority_approval_missing` or
  recording `authority.human_gate_not_required` (`services/intake/authority_gate.py`). Detail:
  `docs/operations/rotation-executor.md`.
- A rotation unit's `external_attestation` criterion is decided by the verifier from the current
  `rotation.step` evidence only when the unit is a rotation unit and the evidence was filed by its
  owning executor (`RotationExecutors.owner`, passed in `VerifyCommand.rotation_executors`);
  `rotation_verification.rotation_step_verdict` never fails a criterion, and anything else stays
  with a person. The floor stays human: never add `external_attestation` to a deterministic set.
  The `rotation-verifier` lane runs `/verify` as VERIFIER. Detail: ADR-0055 amendment 14,
  `docs/operations/rotation-executor.md`.
- A unit's completion satisfies, in `_perform_transition`'s transaction, the pending `work_unit`
  dependencies that require it `completed` (exact match); no other condition or kind. A human-act
  dependency is resolved only at `/review/dependencies/{id}/resolution`, whose detail is
  `HumanActDetail` plus the secret scan. Detail: `docs/operations/driving-a-unit.md`.
- Worker-filed or SYSTEM-recovered evidence on a rotation unit must be a `RotationStepEvidence`
  payload with no `stable_ref`, pass the secret scan, and hold no 32-character unbroken run
  (`rotation_evidence.check_rotation_evidence`); a recovered row also carries the orchestrator's
  `recovery` key. Verifier-owned evidence isn't checked. The schema is a contract with the
  rotation executor, pinned by `tests/fixtures/rotation_evidence_schema.json`. Never extend the
  check to every unit without first running it over stored evidence. Detail:
  `docs/operations/driving-a-unit.md`.
- The rotation executor (`src/rotation_worker/`) acts only on what git confirms: the revision's
  commit on intent-packages' `main` with its hash recomputed, the registry at its repositories'
  `main`, and registry, package and envelope destinations equal in `destinations.py`'s one format
  (pinned with intent-packages by `tests/fixtures/rotation_destination_format.json`). Every
  adapter's steps run through `contract.py`, which holds ADR-0055 decision 3's invariants; never
  re-implement a step in an adapter. A rotation unit has no `target_repository`, so it reads
  `GET /work-units/{id}`, never the runner brief. It reads the package at its `package_id`'s
  path: an intake's `source_path` is the emitting machine's directory, never a git path. Detail:
  `docs/operations/rotation-executor.md`.
- A credential type's plan is `plans.derive` from `Adapter.capabilities` and the registry row's
  destinations, criteria numbered over its machine steps; the approved package pins which plan
  runs. The executor runs the derived plan, or the type's pre-derivation `Adapter.plan`, whichever
  the package's criteria describe step for step (unit too, where named), and refuses otherwise;
  read `Approved.plan`, never `adapter.plan`, for the plan a unit runs. A derived plan never has a
  person attest a consumer. Detail: `docs/operations/rotation-executor.md`, "The
  plan a credential runs".
- The executor's Keychain instance (ADR-0055 amendment 12) keeps state in the login Keychain
  through `/usr/bin/security` (`secret_store.KeychainStore`): values only on stdin and stdout
  (`security -i`), items updated in place with `-U`, never re-created, and an update never
  restates the access list (macOS then prompts a person). Each instance takes only its own store's
  adapters (`Adapter.store_kind`). A store reads back its own kind (`SecretStore.locate`); any
  other `keychain` destination stays person-attested. Detail:
  `docs/operations/rotation-executor.md`.
- The `rotation-decomposer` lane proposes, as `orchestrator-system`, the decomposition of each
  approved rotation record's registered revision that has no units and no proposal: the derived
  plan the intake's own criteria describe (`select_plan`), every envelope one `rotation_known_good`
  recognises, `touches_no_hosted_service` set by `plans.writes_hosted_service` (held equal to the
  executor's `checks._check_destination_fit` by a test), never `host_without_overlap`, and no
  person-act dependency. It never decides a proposal (approval accepts only a human actor) and
  never proposes over one a person rejected or sent back; a package on a pre-derivation plan is
  refused. Detail: `docs/operations/rotation-executor.md`, "The rotation decomposer".
- A person's mint reaches a rotation only through its instance's `rotation-inbox` BWS secret or a
  staging secret written by hand: the unit after the mint waits, unclaimed, until one is staged
  (`worker._mint_waits`). The inbox binds a value only while no other rotation on either
  instance waits on a mint, and only after the provider accepts it beside a refused control,
  `same_identity` (same account, not scope) holds against the keeper, and no other SDS-rotated
  keeper of its type holds it;
  it writes staging before emptying the inbox. The Keychain instance reads its inbox with its
  own narrow BWS identity, which the VM's root can't reach. Detail:
  `docs/operations/rotation-executor.md`, "A person's mint is taken from the inbox".
- A scheduled `runtime-fetch` reader is verified by the read its registry row names
  (`healthchecks_check`, or `observation_source` with `observation_subject`, beside `note`), never
  by a person; a unit that retires waits unclaimed until every such reader has run since the
  keeper's revision date, and a plan with no human act refuses a reader with no read. Weekly
  readers gate retire even past the overlap's `until`. No step can answer "not yet" once its unit
  is claimed: any exception fails the unit and spends an attempt, so a step that must wait waits
  before the claim, in `worker.py::_take`. Detail: `docs/operations/rotation-executor.md`.
- The rotation executor rewrites `ORCHESTRATOR_M2M_CREDENTIALS` from what the running orchestrator
  loaded (`GET /api/v1/m2m-identities`, readable only by the configured executor) and changes only
  the entry its approved `coolify-env-hash` destination names. Restart straight after any hand edit
  to that variable, or the executor's next rotation overwrites it. Detail:
  `docs/operations/rotation-executor.md`.
- A host with no overlap (`m2m_setting.Change.REPLACE`) is declared only by the mint unit's
  approved envelope (`host_without_overlap: true`), and `build` refuses it unless another served
  entry holds the old hash as current, the one case where boot refuses an overlap. Never widen
  that condition or infer the declaration. Its undo sets the entry's hash to the sharing entry's.
  Detail: `docs/operations/rotation-executor.md`.
- The `change-manager` rotation adapter can't read what change-manager loaded: it writes each
  bearer variable from the old and new values alone, confirms by probing `/api/landing-policy`,
  and refuses a value any other change-manager keeper holds, since a shared `previous` stops
  change-manager booting. All four keepers must be readable by the root before its first
  rotation. Restarts wait while any registered change-manager reader is mid-run. Detail:
  `docs/operations/rotation-executor.md`.
- A value one application holds to call another service is a `coolify-env-held` destination
  (ADR-0055 amendment 13), never unregistered: a rotation that misses one strands its holder on a
  dead value. Distribute writes it and restarts the holder; it counts switched only when the
  holder reports the new fingerprint, and only the orchestrator reports
  (`GET /api/v1/held-credentials`, executor-only, its `HELD_SETTINGS`), so the checks refuse a
  held destination anywhere else, or one the report doesn't list, before the claim. Detail:
  `docs/operations/rotation-executor.md`, "Held copies".
- The executor writes an Actions secret only through the rotation App, with a token minted for
  the destination's one repository and exactly `{secrets: write, metadata: read}` (refused if
  GitHub grants more), and counts a copy switched only when that repository's hourly
  `secret-fingerprint.yml` report shows the keeper's fingerprint; no report in three hours
  refuses the unit. A repository gaining a registered copy needs the App installed and the secret
  added to its report. A plan with Actions-secret destinations needs a distribute step. Detail:
  `docs/operations/rotation-executor.md`, "Actions secrets".
- A fork's `security review` reads its script, declaration and acknowledgements from the pull
  request's BASE, so a change to the gate takes effect only on `main`, and a pull request that
  changes the gate is judged by the old one. Such a pull request lands by an admin merge after a
  person reads its report (`main` is protected without admin enforcement for this); for an
  acknowledgements-only change, by `fork-sync ack`, which merges only when the diff is that file
  alone and its review fails only on it; and for the scaffold, by `fork-sync onboard`, which
  merges only when the diff is exactly the rendered files with their rendered bytes after `build
  and test` passed. Never weaken the protection to land it. Detail:
  `docs/operations/onboarding-a-fork.md`.
- A fork's `ci.yml` must trigger only on `pull_request` and `push`: a manual or scheduled run on a
  pull request's branch reports the required jobs as skipped, which branch protection counts as
  passing. Scheduled work (the weekly audit) lives in its own workflow. The held-sync alert is an
  issue, so the fork needs issues on (`fork-sync protect` sets it). Detail:
  `docs/operations/onboarding-a-fork.md`, "The alert".
- A plugin row's installed revision is `plugin.loaded_entry`: the serving folder's `HEAD` when
  Claude Code's record names the folder's version and the folder is clean and on the branch,
  else the record's `gitCommitSha`, which Claude Code rewrites only on a version change. The
  measurement and `verify` must both use it. Detail: ADR-0042 amendment 3.
- The `n8nac` row owns `<npm prefix>/bin/n8nac` (`/opt/homebrew/bin/n8nac`): `npx` resolves a
  command there and downloads from the registry when it's missing, so never remove that link
  without the row's replacement. Detail: ADR-0042 amendment 2.
- The sync commit's parents are the release tag and `origin/main` (`upstream-sync.yml`, built
  with `git commit-tree`). Without `main` as a parent, a file only the fork has (an owned
  lockfile) conflicts on every release, and GitHub then runs no `pull_request` checks, so no
  required check ever reports. History: ADR-0056 amendment 2.
- A fork's `hardened` branch (ADR-0057) is written only by `fork-harden`, rebuilt each pass as the
  rules applied to `main` with the previous `hardened` and `main` as parents, and never
  force-pushed: the installer's serving clone pulls it `--ff-only`. Never open a pull request from
  it (`delete_branch_on_merge` would delete it). A tool row moves to or from `hardened` only with
  its plugin serving clone checked out on that branch in the same step. Detail:
  `docs/operations/fork-hardening.md`.
- Every repository file belongs to exactly one layer in `layers.toml`, and every pattern there
  names a file (`tests/architecture/test_layer_manifest.py`). Place a new file in the manifest;
  never widen a pattern to quiet the guard. `make layer L=<id>` lists a layer's files. Detail:
  `docs/operations/architecture-guards.md`, "The layer manifest".
- Only `services/membership/proposals.py` writes the member tables, and only a human confirming
  a proposal on `/review` reaches it; reads return frozen records, never ORM objects.
  `test_membership_writers.py` names every route that may write them and the role each requires.
  The deploys service's wire value is `rollouts` (word guards). Detail: `docs/operations/membership.md`.
- Every path that lands or proposes a dependency bump honours the member record's holds through the
  shared matcher (`factory_breaches`, `exceeded` or `terms.version_ceiling_term`), or is covered by
  a gate that does, or is not a bump: `tests/architecture/test_hold_paths.py` classifies each and
  fails on an unclassified `MERGE_EXEMPT_PATHS` entry; a new propose path is classified by hand.
  The matcher's two copies (`services/membership/holds.py`, `sds_members/holds.py`) are held equal
  by `test_hold_agreement.py`. Detail: `docs/operations/landing-lanes.md`, "Holds"; ADR-0028
  amendment 2.
- On the factory paths (merge, bump proposer, carrier) a uv `to_version` is a `>=` floor, so a uv
  bump breaches every hold on its dependency whatever its version (`factory_breaches`); never
  compare it as an exact version until the uv mutator locks one. A hold's ecosystem must be the one
  Dependabot uses for that repository. Detail: `docs/operations/membership.md`, "Hold a dependency".
