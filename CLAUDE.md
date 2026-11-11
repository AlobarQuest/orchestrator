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
Subsystem rules continue in nested files that load only when a session reads files in that directory: `migrations/CLAUDE.md`, `scripts/CLAUDE.md`, `src/alert_hub_client/CLAUDE.md`, `src/bump_driver/CLAUDE.md`, `src/deploy_watcher/CLAUDE.md`, `src/execution_watch/CLAUDE.md`, `src/fork_harden/CLAUDE.md`, `src/fork_sync/CLAUDE.md`, `src/landing_ledger/CLAUDE.md`, `src/orchestrator/CLAUDE.md`, `src/orchestrator/api/CLAUDE.md`, `src/orchestrator/kernel/CLAUDE.md`, `src/orchestrator/persistence/CLAUDE.md`, `src/orchestrator/services/execution/CLAUDE.md`, `src/orchestrator/services/intake/CLAUDE.md`, `src/orchestrator/services/landing/CLAUDE.md`, `src/orchestrator/services/lifecycle/CLAUDE.md`, `src/orchestrator/services/membership/CLAUDE.md`, `src/orchestrator/services/release/CLAUDE.md`, `src/orchestrator/services/reporting/CLAUDE.md`, `src/orchestrator/services/verifier/CLAUDE.md`, `src/rotation_decomposer/CLAUDE.md`, `src/rotation_proposer/CLAUDE.md`, `src/rotation_worker/CLAUDE.md`, `src/tool_installer/CLAUDE.md`, `src/unit_verifier/CLAUDE.md`, `tests/protocol/CLAUDE.md`.

- A dependency update's `mutation_commands` must be non-empty and within `allowed_commands`, at
  ingress, approval and dispatch; order is not checked (`test_runner_envelope_contract.py`). Archive #1.
- Everything in this section must stay below `<!-- code-standards:end -->`: `inject_stanza` replaces
  the whole managed block. Re-rendering the block over this file must be a no-op. History:
  `docs/history/claude-md-invariants-archive.md` #2.
- A path that mutates a unit's envelope must ship a fail-closed expansion check; there is no detector
  (`test_authority_write_once.py`; never "fix" it). Archive #4.
- Production `/api` is M2M-only at the Traefik proxy (identity headers stripped); a human API route
  needs a dedicated forward-auth router. The app deliberately does not duplicate this: an in-app
  list of human routes could only be confirmed against the live Traefik file, and the test harness
  calls `/api` with human headers about 60 times. A browser 401 on `/api` means M2M-only, never a
  retry quirk. Detail: `docs/operations/driving-a-unit.md`.
- Coolify `/envs` responses carry `value` and `real_value` (DB passwords) for any token with
  `read:sensitive`: parse in-process, print whitelisted fields only. Each variable is two rows
  (`is_preview` false and true) and a PATCH changes one; PATCH may 500, fall back to delete +
  recreate. Detail: `docs/operations/credentials.md`.
- Change the authority envelope in both repositories together (`test_runner_envelope_contract.py`). Archive #13.
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
- Request entry points own their transaction and must `session.commit()`; a function that commits
  reaches another committer only as its last commit point, a sole handoff
  (`test_transaction_ownership.py`; it misses calls through `from package import module`, calls
  through an object, a commit on a receiver not named `session`, and functions that commit nothing
  themselves, and it does not check the first half: that entry points must commit). A
  persistence test must re-read through a different session, not `expire_all()`.
  History: `docs/history/claude-md-invariants-archive.md` #27.
- `test_unreachable_guards.py` requires every public kernel/services function to be import-reachable
  from a production entry point; an allowlist entry that would read "in fact it is called" means the
  predicate is wrong. Detail: `docs/operations/architecture-guards.md`.
- A DB trigger rewrites `work_units.updated_at` on every update
  (`test_updated_at_trigger_overwrites_a_back_dated_value`), so tests exercise staleness by
  shrinking the env-overridable threshold, never by back-dating the row.
  History: `docs/history/claude-md-invariants-archive.md` #31.
- Never run two pytest runs against one base test database. Each run drops and re-migrates the
  schema once, then `TRUNCATE`s per test; `-n auto` workers get their own `<base>_gwN` databases,
  so the hazard is two separate invocations. Use a per-worktree database. Detail:
  `docs/operations/local-development.md`.
- `automated_test` floors to deterministic-permitted: it resolves from arriving evidence that has an
  evaluator and otherwise asks a human, who may decide it only while the unit is `awaiting_review`.
  Never add it to `DETERMINISTIC_TYPES` (that halts the factory). `automated_check` resolves only
  on verifier-owned named-check evidence, and only `automated_check` criteria accept that evidence.
  Every human-adjudication decision routes through `human_may_adjudicate`. Decomposition `ac_id`
  is the criterion UUID; evidence wants `AC-001`. History: `docs/history/claude-md-invariants-archive.md` #35.
- Merged is not deployed: read `https://sds.alobar.net/openapi.json` before reasoning about what
  production serves. Detail: `docs/operations/deploy.md`.
- The SYSTEM and VERIFIER bearers live in BWS project `SDS Operator`, and the operator (WORKER) and
  observer bearers in `Rotation / Keepers`, which the narrow `sds-operator` account reads by a
  direct grant on each. Bootstrap with `scripts/sds-token.sh` (never vps-backup's helper), UUIDs in
  `.bws-secrets.toml`; never echo values. Detail: `docs/operations/credentials.md`.
- The exception handlers beyond FastAPI's defaults are exactly `APIAuthenticationError`,
  `DomainError` and `Exception` (`test_the_handlers_beyond_fastapis_defaults_are_exactly_three`,
  and `test_the_handlers_beyond_fastapis_defaults_are_the_listed_ones` in
  `test_platform_edges.py`). An unhandled exception is a 500 with the nested `internal_error` body
  and a request id, and one keyed alert per route template and exception type; the body is the same
  whatever alert-hub does, and neither the exception's text nor a traceback leaves the handler
  (`tests/api/test_server_errors.py`). Route parsing must raise `DomainError`, and a service must
  reject every value a DB CHECK would before the write. History:
  `docs/history/claude-md-invariants-archive.md` #44.
- A FastAPI `response_model` silently drops undeclared keys: extend the model in the same change as
  the service, and pin a contract route's model fields in its contract test. History:
  `docs/history/claude-md-invariants-archive.md` #48.
- Coolify only pulls prebuilt GHCR tags; build with the `Release image` workflow (full 40-char sha
  as input). A hand build needs the labels and `ORCHESTRATOR_REVISION`. Detail:
  `docs/operations/deploy.md`.
- Files importing an HTTP client must be in `OUTBOUND_ALLOWLIST` (`test_wsp21_invariant_scan.py`)
  with a reason; these whole-repo scans run only in a full `make check`. Detail:
  `docs/operations/architecture-guards.md`.
- Module-level string collections used in membership tests must be registered in
  `VOCABULARY_REGISTRY` or marked `# not-a-vocabulary: <reason>`
  (`test_cross_boundary_vocabulary.py`); register genuine cross-boundary ones. Detail:
  `docs/operations/architecture-guards.md`.
- Migrate-before-swap is safe only because neither Coolify's (disabled) health check nor the
  Dockerfile `HEALTHCHECK` consults `/health/ready`; never point either at it without re-deciding
  migration order. Detail: `docs/operations/deploy.md`.
- Dispatch has one caller, its route (`test_execution_writers.py`). `ORCHESTRATOR_DISPATCH_ENABLED` is only
  an off switch. Never restart the orchestrator while a dispatched run is live.
- `budgets.max_llm_calls` gates the next claim, not a running attempt (`test_claims_budget.py`), and
  `budget_exceeded` is unrecoverable: authorise at least `max_attempts x max_turns x 2`.
- Build sessions work in `.worktrees/<ws>` with their own venv and their own
  `orchestrator_test_<ws>` database. At session end, check the MAIN tree's `git status` for stray
  files you created, then remove the worktree, database and merged branch. Detail: `docs/operations/local-development.md`.
- Whether landing on a repository redeploys is read from App Brain's `default-branch-landing` route,
  never derived; deploys can come from CI, a repo webhook or the host's git integration. A repo with
  no determination is un-landable. Detail: `docs/operations/landing-lanes.md`.
- The dispatch App has no `checks` permission, so named-check evidence is read from Actions jobs and
  `check_name` must be the JOB name. Merging needs `contents: write`; read an App's permissions from
  the installation or mint response, never `/app`. Detail: `docs/operations/credentials.md`.
- `evidence` rows are append-only by DB trigger (`test_append_only_tables_reject_update_and_delete`);
  a test that corrupts a stored payload sets it on the ORM instance and calls `evaluate_criterion`
  without committing. Archive #103.
- App Brain's landing route never 404s and returns a value plus a `reason`; consumers must tell
  `no_app_record` from `not_assessed`, and one repository may fold several apps. Detail:
  `docs/operations/landing-lanes.md`.
- The enforced attempt cap is the `work_units.max_attempts` column, taken from the proposal and
  raised by `authorize_retry`. `authority.budgets.max_attempts` is a ceiling on it only on
  `decomposition`'s two policy branches: above it policy doesn't approve and a person decides. A
  person's approval or retry may exceed it (`tests/services/test_max_attempts_ceiling.py`). History:
  `docs/history/claude-md-invariants-archive.md` #110.
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
- Interpreter is `.python-version`; `test_interpreter_agreement.py` holds Dockerfile, pyright and
  `requires-python` equal. Raising the floor rewrites source (ruff targets it). Tests need
  `postgresql+psycopg://`. Detail: `docs/operations/local-development.md`.
- A capability name outside the runner's vocabulary is refused only at admission
  (`test_dispatch_blocks_a_legacy_capability_outside_the_runner_vocabulary`); both ingress paths
  refuse names outside the orchestrator's wider set (`test_capability_ingress.py`).
- `FACTORY_PR_TOKEN` (BWS `a3240c2e-…`) lives as Actions secrets in the repositories listed per
  copy in factory-runner's `.cred-consumers.toml`; the rotation executor re-sets every copy and
  verifies each by its repository's `secret-fingerprint.yml` report. Detail:
  `docs/operations/credentials.md`, `docs/operations/rotation-executor.md`.
- Code promising "nothing raises" on an httpx call must catch `(httpx.HTTPError, httpx.InvalidURL,
  ValueError)` at construction and request: a malformed host raises `UnicodeError`, and an escape is
  a bare HTTP 500. The CLI and the App token mint hold it
  (`test_a_malformed_api_url_is_a_stable_cli_error` in `tests/cli/test_cli_errors.py`,
  `test_a_malformed_mint_request_fails_closed` in `tests/services/test_github_app.py`). History:
  `docs/history/claude-md-invariants-archive.md` #161.
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
- change-manager's `resolved` is terminal by design and will not be made recoverable; `reactivate`
  applies only to `wontfix`. Do not re-propose recoverability. History:
  `docs/history/claude-md-invariants-archive.md` #209.
- `UnitPrMerge` records only the orchestrator's own merges; the landing commit for a unit comes from
  the landing ledger's `landing` observation, confirmed against `UnitPrBinding.head_sha`, pinned by
  `tests/contract/test_landing_fact_contract.py`. History:
  `docs/history/claude-md-invariants-archive.md` #228.
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
- Dependabot's update block is read from the commit the gate's `fetch-metadata` reads:
  `commits[0]`, only when the update bot authored it and it is verified
  (`landing_ledger/github.py::_gate_message`, and the hold term's
  `terms._update_bot_message`, held equal by `test_update_bot_commit_agreement.py`), never the
  head, which a branch update makes a merge commit. A landing asks it last, after the landing
  commit and the head. Detail: `docs/operations/landing-lanes.md`, "Dependabot's update block".
- No reported finding kind may be a substring of another;
  `test_no_reported_kind_is_a_substring_of_another` guards it. Detail:
  `docs/operations/landing-lanes.md`.
- `landing_ledger` derives a landing's basis from `merged_by` (`is_machine` = `[bot]` suffix), so
  the identity that merges or arms a merge is the recorded basis; check what reads it before
  changing a credential. Detail: `docs/operations/landing-lanes.md`.
- A producer that publishes with `git push origin main` must refuse unless on `main`, hold the
  command as one string (the merge guard scans text), derive the branch from it, and re-refuse
  unpublished commits each pass. History: `docs/history/claude-md-invariants-archive.md` #257.
- The scheduled lanes run the main tree's working copy and `.venv` by absolute path: never check out
  a branch, edit, or run uv there while a pass may fire; edit in a worktree. Detail:
  `docs/operations/local-development.md`.
- The dispatch App's installation reach is deliberately wider than its work, so every call site
  that mints or resolves its token is bounded by a grant or allowlist check before any read. The
  order is pinned by the `_no_github_read` assertions in `test_estate_landing_admission.py` and
  `test_inert_landing_admission.py`; `test_platform_edges.py` lists each site with its bound and
  requires the bound to be reachable by calls, not ordered first
  (`test_every_call_that_mints_or_resolves_the_token_is_listed_with_its_bound`). The one
  exception is the verifier's named-check read of a unit's own repository for work already
  dispatched, which asks no grant and has a named reason
  (`test_the_verifier_read_is_the_one_named_reason_and_reaches_no_grant`); every landing asks the
  grant again. Detail: `docs/operations/credentials.md`.
- An envelope's verifier must exercise what the change could break (build, tests), not merely
  confirm the edit; use the profile's generated command list, never a hand-authored one. Detail:
  `docs/operations/driving-a-unit.md`.
- A reporting threshold is a plain int with a real default and no off value; a dead config knob is
  deleted with the function it configured. Detail: `docs/method-lessons.md` #32.
- `dead_letter` reports `stalled_verification` over `VERIFICATION_STATES`, kept separate from
  `APPROVAL_STATES` (different owner, different remedy); `revision_required` is deliberately
  uncovered. History: `docs/history/claude-md-invariants-archive.md` #276.
- Before deleting anything, grep the exit manifests (`docs/operations/wave-exit-manifest.toml`,
  `docs/operations/exit-criteria-claims.toml`) and evidence, not only `src/`. Something a met bar
  attests leaves only by an amendment that keeps the bar met: drop the live check, pin the retained
  record of its last pass, and leave the bar's text and `body_sha256` unchanged (ADR-0049, ADR-0040
  amendment 1). History: `docs/history/claude-md-invariants-archive.md` #287.
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
  repository no landing path reaches. Release binding, deployment observations and the
  machine-activation candidates read ask the `rollouts` grant on the unit's own target (never the
  body's `source_repository`) and refuse `release_repository_not_granted` or
  `release_repository_mismatch`; an exact idempotent retry replays first. Detail: `docs/operations/driving-a-unit.md`; full procedure:
  `docs/operations/onboarding-a-target.md`.
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
- The rotation executor rewrites `ORCHESTRATOR_M2M_CREDENTIALS` from what the running orchestrator
  loaded (`GET /api/v1/m2m-identities`, readable only by the configured executor) and changes only
  the entry its approved `coolify-env-hash` destination names. Restart straight after any hand edit
  to that variable, or the executor's next rotation overwrites it. Detail:
  `docs/operations/rotation-executor.md`.
- Every repository file belongs to exactly one layer in `layers.toml`, and every pattern there
  names a file (`tests/architecture/test_layer_manifest.py`). Place a new file in the manifest;
  never widen a pattern to quiet the guard. `make layer L=<id>` lists a layer's files. Detail:
  `docs/operations/architecture-guards.md`, "The layer manifest".
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
- A named person approves every rotation package revision except a lane credential's revision 1
  that the standing approval's policy (`policy:non-software-operational@`) approved and the work
  carrier checked (ADR-0055 amendment 22). The carrier runs `rotation_proposer.standing_approval`'s
  carry form, then records `work_carrier` / `standing_approval_check` under its own identity
  (`WORK_CARRIER_OBSERVATION_KEY_ID`, never `orchestrator-system`) before the intake; the
  decomposition gate admits the policy approver only beside that record, `recorded_by`
  `ORCHESTRATOR_WORK_CARRIER_AGENT_ID`. Either identity unset refuses every standing approval.
  Boot requires the credentials holding `work-carrier-observer-v1` to be exactly that setting's
  identity, or none when unset, so unlike the executors' setting it can't go first
  (`test_work_carrier_boot.py`). Detail: `docs/operations/rotation-proposer.md`.
