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
text of every bullet, by number; `Archive #N` means that entry, and each rule shortened by Execution
Part 5 keeps its earlier text under that heading there).
Placement rule: global CLAUDE.md §7.

- A dependency update's `mutation_commands` must be non-empty and within `allowed_commands`, at
  ingress, approval and dispatch; order is not checked (`test_runner_envelope_contract.py`). Archive #1.
- Everything in this section must stay below `<!-- code-standards:end -->`: `inject_stanza` replaces
  the whole managed block. Re-rendering the block over this file must be a no-op. History:
  `docs/history/claude-md-invariants-archive.md` #2.
- Generic authority approvals satisfy work-unit readiness only: an authority-expanding
  standing-context update needs a named human approval bound to the exact standing-context
  fingerprint (`kernel/context.py::classify_context_update`, via `services/lifecycle/context.py`;
  `test_a_generic_authority_approval_does_not_satisfy_an_expanding_context`).
- A path that mutates a unit's envelope must ship a fail-closed expansion check; there is no detector
  (`test_authority_write_once.py`; never "fix" it). Archive #4.
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
- Change the authority envelope in both repositories together (`test_runner_envelope_contract.py`). Archive #13.
- `AuthorityEnvelope.normalized()` is what an authority approval attests: fields outside
  `KNOWN_FIELDS` contribute only their names. Authority-bearing fields must be known; adding one
  rewrites every authority fingerprint. History: `docs/history/claude-md-invariants-archive.md` #15.
- Dispatch takes the target from each unit's envelope (`test_dispatch_routes_to_the_units_own_target_repository`). Archive #16.
- Write `ORCHESTRATOR_M2M_CREDENTIALS` before `ORCHESTRATOR_M2M_ROLES` and verify each in the
  container before the next restart: roles without credentials fail boot closed. Detail:
  `docs/operations/credentials.md`.
- An M2M credential's `agent_id` must resolve in the registry bundle baked into the image from
  security-standards' git tree; attribution is permanent, so adding an actor needs a merged commit
  plus rebuild. Never borrow another identity. Detail: `docs/operations/credentials.md`.
- Envelopes are write-once, and an approved decomposition can be superseded only while none of its
  units has been claimed or dispatched (ADR-0052, `test_decomposition_supersession.py`): dry-run every session-authored unit's commands
  against the real target repo (twice, clean clone) before work starts. A standing bump's
  decomposition is built without a local run, by Devon's decision of 2026-10-30 (installing the
  bumped dependency runs its lifecycle scripts where infrastructure credentials live): a bad
  envelope fails its attempts and the bump stays held. Detail: `docs/operations/driving-a-unit.md`.
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
- `work_units.version` moves only on a state transition, in three places (`test_execution_writers.py`). Archive #30.
- A DB trigger rewrites `work_units.updated_at` on every update, so tests exercise staleness by
  shrinking the env-overridable threshold, never by back-dating the row. History:
  `docs/history/claude-md-invariants-archive.md` #31.
- Never run two pytest suites against one test database: fixtures drop and recreate it. Use a
  per-worktree database. Detail: `docs/operations/local-development.md`.
- `release_claim` alone ends a claim, from its listed callers (`test_execution_writers.py`); keep
  replay-before-release and unit-then-claim lock order. Archive #34.
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
- Any "may this unit run again" rule belongs in `claims._readiness_eligibility_error`, shared by reclaim,
  release and requeue (`test_reclaim.py`, `test_requeue.py`). Archive #45.
- The evidence-pack markdown is redacted for public PR comments (no approver ids, rationale, actor
  ids); new sections must redact by hand. A `text/markdown` route needs a `NON_JSON_SUCCESS_PATHS`
  entry. History: `docs/history/claude-md-invariants-archive.md` #46.
- A FastAPI `response_model` silently drops undeclared keys: extend the model in the same change as
  the service, and pin a contract route's model fields in its contract test. History:
  `docs/history/claude-md-invariants-archive.md` #48.
- Change the runner brief in both repositories together (`test_runner_brief_contract.py`); `RunnerBrief`
  stays `extra="allow"`. Archive #49.
- Serve a new brief field only after factory-runner declares it and the pin advances
  (`test_brief_consumer_compatibility.py`); renaming the required check moves the protected context too.
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
- The orchestrator assigns the dispatch ordinal; a retry reuses the `idempotency_key` (`test_dispatch.py`).
- Dispatch has one caller, its route (`test_execution_writers.py`). `ORCHESTRATOR_DISPATCH_ENABLED` is only
  an off switch. Never restart the orchestrator while a dispatched run is live.
- `budgets.max_llm_calls` gates the next claim, not a running attempt (`test_claims_budget.py`), and
  `budget_exceeded` is unrecoverable: authorise at least `max_attempts x max_turns x 2`.
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
- A lease may only lengthen, and a lapsed lease ends its attempt (`test_lease_policy.py`,
  `test_drill_lapse_before_submit.py`). Archive #89.
- A lease has three writers, each sized by `claim_lease` (`test_execution_writers.py`). A per-claim rule covers
  all three, except that the rotation change window gates starts only (ADR-0055 amendment 2). Archive #90.
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
- `_blocked_reason` normalizes the envelope once (`test_dispatch_uses_one_normalized_authority_snapshot`). Archive #108.
- A verifier adjudication may arise only from `verify_work_unit`; a direct POST is
  `verifier_evaluation_required`. Reconciliation detects reality changing, never reality
  misreported, so worker-attested evidence has no downstream net. History:
  `docs/history/claude-md-invariants-archive.md` #109.
- The enforced attempt cap is the `work_units.max_attempts` column, taken from the proposal and
  raised by `authorize_retry`. `authority.budgets.max_attempts` is a ceiling on it only on
  `decomposition`'s two policy branches: above it policy doesn't approve and a person decides. A
  person's approval or retry may exceed it (`tests/services/test_max_attempts_ceiling.py`). History:
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
- `runner_command_authority_violation` mirrors the runner and may be stricter, never looser. Archive
  #126. Each term below is tested in `test_runner_envelope_contract.py`.
- `allowed_commands` is required when `command.run` is allowed
  (`test_allowed_commands_is_required_for_every_change_class`).
- `mutation_commands` is required iff `change_class == "dependency-update"`
  (`test_mutation_commands_guard_fires_on_dependency_update_without_the_field`).
- `mutation_commands`, whenever present, is non-empty and a subset of `allowed_commands`
  (`test_present_mutation_commands_is_validated_for_every_change_class`).
- `verify_commands` is optional, and whenever present is non-empty, a subset of `allowed_commands`
  and disjoint from `mutation_commands` (`test_a_malformed_verify_script_is_refused`).
- `allowed_commands` is the coding agent's entire enforced Bash vocabulary (exact match), so an
  envelope must list every command the work needs. Detail: `docs/operations/driving-a-unit.md`.
- Without `verify_commands`, finalize re-executes `allowed_commands` as written and never reorders,
  so authors must order it mutators first, verifier last (factory-runner
  `test_finalize_without_a_verify_script_replays_the_whole_vocabulary`).
- With `verify_commands`, finalize runs only the mutators, in `allowed_commands` order, and then
  that script; every other allowed command is agent-only (factory-runner
  `test_finalize_runs_mutators_then_the_verify_script_and_skips_the_rest`,
  `test_finalize_runs_mutators_in_allowed_order_not_mutation_order`).
- For edit-shaped work (no mutators), a build the verifier depends on must be in `verify_commands`
  or a mutator, or finalize never runs it (factory-runner
  `test_an_edit_shaped_verify_script_runs_only_the_script`).
- An absent `verify_commands` keeps every existing fingerprint (factory-runner
  `test_a_policy_without_verify_commands_keeps_its_pre_3c1_bytes`).
- Only superseding retires a draft or ready unit (`test_decomposition_supersession.py`); a stranded one is
  failed, then cancelled. Archive #134.
- Never set `required_approving_review_count`; a branch-protection required check is a JOB name
  verified against a real open pull request, and members keep `allow_auto_merge` off (ADR-0038),
  which `membership_check` reports. Detail: `docs/operations/landing-lanes.md`.
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
- `runner_authority_violation` is the one composed predicate, asked at breakdown ingress, authority
  approval (service and form) and admission; `test_authority_asking_points.py` pins the surfaces.
  History: `docs/history/claude-md-invariants-archive.md` #149.
- Unit registration asks only the field and level terms of that predicate, not the command term
  (`test_registration_asks_the_field_and_level_terms`).
- A capability name outside the runner's vocabulary is refused only at admission
  (`test_dispatch_blocks_a_legacy_capability_outside_the_runner_vocabulary`); both ingress paths
  refuse names outside the orchestrator's wider set (`test_capability_ingress.py`).
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
  population, or a standing rotation package's record (`rotation-<credential>`, intent-packages
  source, risk `caution`, change class `credential-rotation`, no target repository; ADR-0055
  amendment 16, and the carrier refuses that class on a package of any other profile); a person
  moves every other one. No producer credential moves a `work` record's status:
  `bump_proposer` is propose-and-read and the `propose` scope refuses status routes.
  `bump_proposer` creates a missing package for a declared repository's failed npm, pip or uv bump
  (reach `live_estate` if it deploys, else `source_repository`), judges each bump by its own
  population's block, and alerts (`dependency_bump_key`) on what it can't route. Detail:
  `docs/operations/landing-lanes.md`.
- `rotation_proposer` never approves a package revision. A pass that revises a standing rotation
  package to `ready_for_review` proposes nothing, and the first pass after a named human approves
  it proposes. "Due" is infraops' rule, read from `security-drift-cli cred-findings` and never
  re-implemented here. The occurrence comes from that answer's structured fields and never
  contains today's date. It acts only on a credential its registry entry marks `rotated_by_sds`; a
  package without that flag is a finding and is never acted on. Each `--submit` pass also walks rotation records, due or not, through its own GET-only
  client on the observer bearer (`rotation_proposer/client.py`): an approved record of the current
  revision with a `failed` or `cancelled` unit reports `stalled`. Its one write to infraops is
  `security-drift-cli record-rotation --date <day>`, made only for a credential's newest record
  `resolved` by `work-watcher` when the trigger isn't an exposure and the UTC day its last unit
  completed (from unit history, since change-manager serves no record date) is later than
  infraops' `last_rotated`. That field is the age anchor, served for every handed-over row and
  `null` for one with no date, and the comparison alone stops a repeat moving an age clock, because
  infraops accepts an earlier date: a handed-over id missing from it is an `error`, and an absent
  field skips recording. The exposure test reads the occurrence from the record's `reasoning`, so
  that sentence's wording is a contract with `record_occurrence`. A due credential nothing will
  move is a finding (`record-wontfix`, `due-after-rotation`), never a quiet pass.
  A revision it writes carries the registry's destinations; an approved revision that
  differs from the registry is revised, never proposed; and a due, handed-over credential with no
  package gets revision 1 generated from its row and its `class`'s type (`generate.py`,
  `CLASS_TYPES`), validated and written by intent-packages' `create-rotation`. The work
  carrier carries a standing rotation package only when its credential's registry row sets
  `rotated_by_sds` and the member record grants `credential_rotation` on that registry's
  repository. The proposer reads the same grant before it observes, revises, generates or
  proposes, never to record a finished rotation or report, and an unread record is that
  credential's `error`; the decomposer reads it before proposing. Detail:
  `docs/operations/rotation-proposer.md`.
- The rotation executor reads the member record's `credential_rotation` grant (`GET
  /api/v1/members`, `rotation_worker/grants.py`) before it claims a unit, which stays unclaimed
  while the grant is absent, even one the checks refuse whose registry row resolves, and before
  each step, where a withdrawn grant fails the unit as a
  handed-back `rotated_by_sds` flag does and can leave an overlap open. An empty or unreadable
  record is unread, never a withdrawal: before the claim the unit waits, and mid-rotation the step
  proceeds. That protects the grant read only; a failed `renew` still fails the unit. Detail:
  `docs/operations/rotation-executor.md`, "The member grant".
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
- The post-landing watch selects a factory merge from rows alone: status `merged` or
  `already_merged`, a unit that is a dependency update by both class and profile, `landing_inert`
  on the row's event (the estate's answer when our call was made; an `already_merged` found before
  any call, or at a head other than the row's, never carries it, and an event without it is never
  watched), and no record naming it as a revert. `UnitPrMerge.repository` is the envelope's
  spelling and `landing_reverts` is folded, so every join folds it. Detail:
  `docs/operations/landing-lanes.md`, "Automatic reverts (inert repositories)".
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
- Dependabot's update block is read from the commit the gate's `fetch-metadata` reads:
  `commits[0]`, only when the update bot authored it and it is verified
  (`landing_ledger/github.py::_gate_message`, and the hold term's
  `terms._update_bot_message`, held equal by `test_update_bot_commit_agreement.py`), never the
  head, which a branch update makes a merge commit. A landing asks it last, after the landing
  commit and the head. Detail: `docs/operations/landing-lanes.md`, "Dependabot's update block".
- A landing row whose reason is `merge_refused_by_remote:<code>` is an unknown outcome
  (`interfaces.outcome_is_unknown`), answered `landing_outcome_unknown`; the bare reason is
  terminal. Each lander asks about every such row first, from `GET
  /api/v1/landing-unknown-outcomes`, because no enumeration reaches a landed pull request.
  `lane_act._resettle`, in its own committed step, is the only code that may change or remove a
  landing row (`test_only_lane_act_may_write_a_landing_row_and_only_resettle_changes_one`, which
  tracks rows passed as parameters, built by calling the model, or held in locals assigned from
  `select(Model)` or `session.get(Model, ...)`; a row reached any other way escapes it): landed
  makes it `already_merged`, repoints `event_id` to an event with that action and runs `on_landed`
  (at a head other than the row's, on this or the reconciling read, the event's action is
  `<lane>.already_merged_other_head`, which the revert watch never selects);
  confirmed unlanded removes it, and that stands whatever admission then says. Events are unique
  and append-only, so a removed row's key is spent (`_event_key`). Detail:
  `docs/operations/landing-lanes.md`, "An unknown landing outcome".
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
  `run-work-carrier.sh`, `run-bump-driver.sh`) run through `sds_outcome_begin`/`sds_outcome_end` in
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
- `scripts/check_profile_budget_agreement.py` holds every `dependency-update` pattern to intent-packages'
  `BUDGETS` and `CAPABILITIES`. Raising the ceiling is a standing-authority decision. Archive #291.
- Dispatch admission, the claim, the decomposition proposal and its approval, and every landing
  admission read the member record's grant. The first four map the unit's change class
  (`services/execution/target_grant.py`): `dependency_updates` for `dependency-update`,
  `app_changes` for every other class, only in state `granted` on a non-retired member, case-folded;
  otherwise `target_repository_not_granted`. A proposal asks past replay and writes nothing,
  approval asks again, and rotation units and units naming no repository skip both, so a person is
  never asked to approve work that can't run while intake stays open
  (`tests/services/test_decomposition_grant.py`). `claim_unit` refuses before anything writes, skipping rotation units and units with no
  `target_repository`; `_readiness_eligibility_error` holds the same term, so reclaim and release
  park a withdrawn unit `failed` and requeue refuses it. `renew_claim` is ungated. A dispatched unit
  refused at the claim stays `ready`, which refuses every orchestrator deploy and holds rotation
  restarts (`rotation_worker/live_runs.py`). Exits: restore the grant and dispatch again, or fail
  it through the SYSTEM `commands/fail` route (`/review` has no fail control) and cancel it.
  `authorize_retry` refuses a withdrawn unit too. The landers and the pr-merge gate
  refuse with `landing_repository_not_granted` and `merge_repository_not_granted`. The lanes' copy of
  the mapping is held equal by `test_target_grant.py`, and `membership_check` reports a granted
  repository no landing path reaches. Detail: `docs/operations/driving-a-unit.md`; full procedure:
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
  person-act dependency. It never proposes over a proposal a person rejected, sent back or
  superseded; a package on a pre-derivation plan is refused. Detail:
  `docs/operations/rotation-executor.md`, "The rotation decomposer".
- `decomposition._policy_approval` approves a decomposition SYSTEM proposes, in the proposal's own
  transaction, on one of two branches, rotation or bump. A human's or WORKER's proposal, and every
  other decomposition, waits for a person on `/review`
  (`test_a_person_s_proposal_waits_for_a_person`, `test_a_worker_s_proposal_waits_for_a_person`).
- A rotation's one click is its package revision approval (ADR-0055 amendment 16): change-manager
  approves its `work` record by policy.
- The rotation branch approves only when the revision is a standing rotation package with a
  recorded approval (`test_a_revision_an_approval_policy_approved_waits_for_a_person`).
- The rotation branch needs every dependency unit-on-unit `completed`
  (`test_a_dependency_on_anything_but_a_unit_completing_waits_for_a_person`).
- The rotation branch needs every unit a rotation unit `human_authority_gate` finds recognised
  (`test_a_unit_no_rotation_pattern_recognises_waits_for_a_person`).
- A standing bump needs no click (ADR-0006 amendment 2): the bump branch takes any revision with the
  `dependency-update` profile or a `policy:dependency-update@` approver.
- The bump branch needs that approver and that profile both to hold
  (`test_a_revision_the_bump_policy_did_not_approve_waits_for_a_person`,
  `test_a_revision_of_another_profile_waits_for_a_person`).
- The bump branch needs `standing` true (`test_a_revision_that_isn_t_standing_waits_for_a_person`).
- The bump branch needs reach exactly `source_repository`
  (`test_a_reach_past_the_repository_waits_for_a_person`).
- The bump branch needs one unit (`test_two_units_wait_for_a_person`) with no dependencies
  (`test_a_dependency_waits_for_a_person`).
- The bump branch needs every criterion mapped to the unit and none retained
  (`test_a_retained_criterion_waits_for_a_person`).
- The bump branch needs attempts within the unit's budget
  (`test_more_attempts_than_the_envelope_budgets_waits_for_a_person`).
- The bump branch needs only dependency-update patterns to recognise the unit
  (`test_recognition_by_anything_but_a_bump_pattern_waits_for_a_person`).
- The bump branch needs `bump_binding_violations` empty
  (`test_a_mutation_of_something_else_waits_for_a_person`).
- The binding is a whole-command allowlist of the generator's forms, and covers `allowed_commands`
  and `verify_commands` too; a generator change means regenerating
  `dependency_update_proposals.json`.
- The bump branch needs the unit's `title` and `outcome` to be the generator's (`bump_text_differs`,
  `test_text_the_generator_didn_t_write_waits_for_a_person`).
- A bump's `dependency_updates` grant is asked before either branch: the proposal itself is refused
  without it (`test_a_grant_of_another_service_is_refused_at_the_proposal`).
- Each refusal of a SYSTEM proposal on either branch, every bump refusal, and an approval that fails
  after policy said yes, records one `decomposition.policy_not_applied` per proposal, served as
  `policy_not_applied` on the proposal routes (`test_a_replayed_refusal_records_one_event`,
  `test_an_approval_that_cannot_complete_says_so`).
- `register_approved_unit` admits SYSTEM only with `approved_by_policy`
  (`test_system_may_register_a_unit_only_for_a_decomposition_policy_approved`).
- `pr-merge`'s authority term is a bound human approval or the policy path (ADR-0025 amendment 4).
  The bound approval suffices for every change class and is blind to any switch
  (`test_a_bound_approval_never_consults_the_policy`,
  `test_a_human_approved_merge_ignores_the_switch`).
- The policy path needs a `dependency-update` envelope (`test_the_change_class_is_asked_for_itself`).
- The policy path needs a `standing_bump_refusal`-clean revision, the decomposition gate's own
  conditions, reach exactly `source_repository`
  (`test_a_unit_outside_the_policy_path_needs_its_approval`).
- The policy path needs the estate to answer `inert`
  (`test_a_redeploying_repository_stays_with_a_person_even_with_its_record`).
- The policy path needs recognition at merge time, only by dependency-update patterns
  (`test_recognition_is_asked_again_at_merge`).
- The policy path needs the `dependency_updates` grant
  (`test_an_ungranted_repository_is_refused_on_both_terms`).
- The policy path needs `ORCHESTRATOR_BUMP_MERGE_ENABLED` on. Off, a unit the path covers refuses
  `merge_policy_path_off` in place of `authority_approval_not_bound`
  (`test_the_switch_off_refuses_a_unit_the_policy_path_would_admit`); other terms report beside it,
  so it means waiting only as the sole refusal
  (`test_the_switch_off_reports_beside_another_terms_refusal`).
- The admission answer writes nothing (`test_reading_the_answer_records_nothing`).
- The act records `authority.human_gate_not_required` beside a landing its own call made, including
  one reconciled after that call at the head it named
  (`test_landing_after_a_lost_response_is_recorded_as_policy_lifted`), never beside an
  `already_merged` found before it or at another head
  (`test_a_pull_request_somebody_else_landed_records_no_lifted_gate`).
- A row whose reason is `merge_refused_by_remote:<code>` is an unknown outcome: `pr-merge-admission`
  answers `landing_outcome_unknown` (`test_the_answer_names_an_unknown_row_until_it_settles`), and
  the bump driver replays it whatever the pull request's state or the other refusals
  (`test_an_unknown_outcome_row_is_waiting_not_refused`).
- `pr_merge._resettle`, in its own committed step and asking no term, is the only code that may
  change or remove a `UnitPrMerge` row (ADR-0025 amendment 5;
  `test_only_pr_merge_may_write_a_factory_row_and_only_resettle_changes_one`).
- The policy path alone also reads the pull request in `pr_merge._act`, after it is known open and
  before landing: the head is `sds/<unit8>-attempt-<attempt_count>` in the repository itself and is
  the adjudicated head, the body has the whole-line `SDS-Unit:` trailer, and `pulls/{n}/files` reads
  fully (its length equals `changed_files`), neither end of any entry is a workflow or gate file,
  and a tooling manifest changed. Each failure is a `pr_merge_*` refusal with no `UnitPrMerge` row,
  so a person can still land it. Detail: `docs/operations/landing-lanes.md`, ADR-0025 amendment 4.
- The `bump-driver` lane acts as SYSTEM only on a standing `dependency-update` revision in a
  repository granted `dependency_updates`, and on a unit whose own envelope says so; it reads
  `resolved` records as well as `approved`, since `work_watcher` retires a record before its unit
  lands. A dispatch record that started no run holds its idempotency key and moves no version, so
  the lane's key gains a suffix per such record, or it would replay the skip for ever. Its mirrors
  run only git and `fresh` proves them clean at `origin/main`, because `factory`'s conformance claim
  runs `code-standards` in the mirror; `factory`'s environment is an allowlist holding no credential
  but the Brain keys, so the lane passes `--intake-file` and `--assume-checkout-current`. It never
  requeues a unit and never re-dispatches a run nobody claimed. Detail: `docs/operations/landing-lanes.md`, "The bump
  driver".
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
- Only `services/membership/proposals.py` writes the member tables, reached only by a human
  confirming on `/review`, with one exception: `revert_hold.place` inserts a ceiling hold, only
  through `revert_landed.record_revert_landed`, in either landing lane's landing transaction (inert
  or estate) for a landed SDS revert, and never updates or deletes. Reads return frozen records,
  never ORM objects. `test_membership_writers.py` pins both writers, the hook's two callers, every
  write route and its role. The deploys service's wire value is `rollouts` (word guards). Detail:
  `docs/operations/membership.md`.
- Every path that lands or proposes a dependency bump honours the member record's holds through the
  shared matcher (`factory_breaches`, `exceeded` or `terms.version_ceiling_term`), or is covered by
  a gate that does, or is not a bump: `tests/architecture/test_hold_paths.py` classifies each and
  fails on an unclassified `MERGE_EXEMPT_PATHS` entry; a new propose path is classified by hand.
  The factory merge is also the one factory path that reads reverted versions and a revert in
  progress (`_hold_term`: `merge_version_reverted`, `merge_repository_reverting`); the proposer,
  carrier and driver are covered by it.
  The matcher's two copies (`services/membership/holds.py`, `sds_members/holds.py`) are held equal
  by `test_hold_agreement.py`. Detail: `docs/operations/landing-lanes.md`, "Holds"; ADR-0028
  amendment 2.
- On the factory paths (merge, bump proposer, carrier) a uv `to_version` is a `>=` floor, so a uv
  bump breaches every hold on its dependency whatever its version (`factory_breaches`); never
  compare it as an exact version until the uv mutator locks one. A hold's ecosystem must be the one
  Dependabot uses for that repository. Detail: `docs/operations/membership.md`, "Hold a dependency".
- A `postgres-role` host replaces the password with no overlap and restores the old value on its
  own failure, because `/review` sits behind the service it restarts. Its only reach to the
  database is the forced-command helper (`deploy/vps/sds-pg-rotate`), and a password must never be
  sent in plain text to `ALTER ROLE`: the helper sends only a SCRAM-SHA-256 verifier. Detail:
  `docs/operations/rotation-executor.md`, "Postgres role passwords"; ADR-0055 amendment 17.
- A rotation instance alerts only for the units it owns: `rotation_worker.envelope.KEYCHAIN_ADAPTERS`
  is a copy of `rotation_claims.KEYCHAIN_ADAPTERS` (the worker may not import the orchestrator), and
  `test_bws_machine_token.py` holds them equal. Change both together.
- A unit's wait alert runs on two clocks: reader waits (weekly readers, another rotation's overlap)
  on `unit_reader_clock_key` with the 192-hour threshold, every other wait, including a reader
  mid-run and `Revoke.UNKNOWN`, on `unit_waiting_key` with 48. A pass judges overdue by its own
  clock alone; while the unit stays waiting both clocks are kept and its waiting and stuck alerts
  are held. Never fold them into one key. Detail:
  `docs/operations/rotation-executor.md`, "Alerts".
- `execution-watch` alerts on a stalled, held or failed unit, from `GET /api/v1/execution-status`,
  which is that route's one reason to exist. It skips a rotation unit, and each condition
  `owned_elsewhere` says a bump lane or `work_watcher` holds, a table
  `test_the_ownership_table_matches_the_bump_lane` holds to the bump lane's selection. A dispatch
  condition applies only to a `ready` unit, because a circuit breaker never closes. Its
  `known.json` silences history; a missing file refuses, and `--seed` writes it. The same read serves
  rows that aren't units (staged intakes, undecomposed packages, undecided proposals), so `unit_id`
  and the unit flags are nullable and a unit condition must gate on `subject_kind`: a sent-back
  proposal's state is a held unit's. `admission-waiting` is raised while the server says `waiting`
  and fires only on `overdue`, because `known & raised` would prune a seeded key raised only when
  overdue. Detail: `docs/operations/local-development.md`.
- `rotation-heartbeat` is sent as `resolved` after every finished pass and is never raised; its
  meaning is the absence of the ping, heard by an alert-hub schedule. A pass that raises sends none.
  `rotation-instance-behind` is held when the mirror lacks the running revision.
