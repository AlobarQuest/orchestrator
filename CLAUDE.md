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

- Dependency-update repo.edit authority is not executable unless the fingerprinted envelope
  declares a non-empty mutation_commands list that is an ordered subset of allowed_commands.
  Proposal admission and dispatch both enforce this; existing approved envelopes are never
  rewritten to comply.

- **Everything in this section must stay BELOW `<!-- code-standards:end -->`.**
  `code_standards.stanza.inject_stanza` replaces the whole `start`…`end` block in
  place and preserves only what surrounds it, and the canonical stanza template
  contains no invariants section. Until 2026-07-09 these bullets lived *inside*
  the block, one `code-standards init`/`sync` away from silent deletion.
  Verify with: re-rendering the block over this file must be a no-op.
- On this machine, repo-local agent instructions live in `CLAUDE.md`. Treat
  `AGENTS.md` references from generic agent tooling as equivalent to checking
  `CLAUDE.md` unless a repo explicitly provides both files.
- Generic authority approvals satisfy work-unit readiness only. Authority-expanding
  standing-context updates require a named human approval bound to the exact
  standing-context fingerprint — enforced by `classify_context_update()`
  (`kernel/context.py`) via `services/context.py::_effective_decision`.
  **That is the STANDING-CONTEXT check, and it is not the whole story.** It compares
  capability *sets* and authority-profile *rank*. It does **not** check capability
  *levels* or *budgets*. **Work-unit envelope expansion — including budget expansion —
  has NO detector at all.** `is_expansion()` was that detector; it had zero callers and
  WS-P2.15 deleted it. This is safe only because the envelope is **write-once** (assigned
  at construction, in exactly two places), which
  `tests/architecture/test_authority_write_once.py` now enforces. **If you add a path that
  raises a unit's budget or capabilities by mutating the envelope, that test will fail —
  and you must ship a fail-closed expansion check with it.** Do not "fix" the test.
- Protocol smoke tests may manipulate time or lease expiry as deterministic fixture
  setup. Runtime recovery behavior itself must go through public API/CLI surfaces,
  not private service shortcuts.
- The default `make check` gate must resolve Python tools from the repo-local
  `.venv/bin` before global PATH. A global `pytest` can collect with the wrong
  interpreter and fail imports even when the uv-scoped suite is green.
- Local dogfooding must use a runtime database separate from `orchestrator_test`.
  The test fixtures intentionally drop and recreate the test database, so storing
  live orchestrator lifecycle state there will erase approved intake, decomposition,
  claims, and evidence during `make check`.
- Generated post-deploy acceptance criteria are verifier-owned. Public
  adjudication must reject generated post-deploy AC IDs, so post-deploy completion
  always flows through the WS-5.1 verifier evaluators and lifecycle guards.
- Production Coolify images must be amd64 or multi-arch. Local Apple Silicon
  Docker builds produce arm64 images by default; use `docker buildx build
  --platform linux/amd64 --push` or a multi-arch build for `sds.alobar.net`, and
  verify the running container image/digest after Coolify reports deployment
  finished.
- Production `/api` is M2M-only at the proxy: the Traefik dynamic config
  (`/data/coolify/proxy/dynamic/orchestrator.yaml` on the VPS) strips
  `X-authentik-*` headers from `/api` routes, so human-actor API routes are
  unreachable from a browser session unless a dedicated router applies the
  `/review` middleware chain (strip → Authentik forward-auth → proxy marker) to
  those paths — see the `orchestrator-promotion-human` router (WS-6.3).
  **There is no "first POST behind forward-auth 401s" quirk. It was speculation,
  it has never once been observed, and it should not be planned around.** This
  bullet used to assert it; sessions then inherited it as fact, wrote retry
  branches for it, and narrated it as expected. The 2026-07-27 drill intake POST
  returned 201 on the first attempt with the retry branch never firing — as has
  every other such POST. Real 401s on `/api` mean the route is M2M-only (see the
  routing bullets below), not that a retry is needed.
- Production observation ingestion requires an `ActorRole.SYSTEM` actor — and
  **two standing SYSTEM credentials already exist, so the temporary-credential
  dance this bullet used to prescribe is unnecessary and must not be revived.**
  Verified 2026-07-28 from the running container, `ORCHESTRATOR_M2M_ROLES` is
  `{"orchestrator-drift-reporter": "system", "orchestrator-system": "system",
  "orchestrator-verifier": "verifier"}`. The superseded claim ("the standing M2M
  credential is worker-role") conflated `orchestrator-system` with
  `factory-runner-github`, the one credential carrying no roles entry; it cost a
  spec draft an outage-shaped deploy step before being caught in review.
  Use `orchestrator-system` for SYSTEM-role writes; `orchestrator-drift-reporter`
  belongs to the WS-P3.0 drift producer and **must not be borrowed for canonical
  mutation** — its registry profile is observe-and-propose, and `agent_id`
  attribution is permanent.
  The env-write ordering rule still stands whenever a credential IS added:
  write `ORCHESTRATOR_M2M_CREDENTIALS` before `ORCHESTRATOR_M2M_ROLES`, verify
  each from inside the container before the next restart — a roles entry without
  its matching credential fails startup validation closed and takes production
  down.
- Coolify's env PATCH endpoint intermittently 500s on this app; the reliable
  fallback is delete-by-env-uuid + recreate. All `/envs` API responses include
  `real_value` for every variable (DB URLs with passwords) — parse them
  in-process and print only whitelisted fields, never through ad-hoc shell
  pipelines.
- The work-unit authority envelope is a **cross-repo contract** with
  `AlobarQuest/factory-runner`, not a local data shape. It is pinned by a
  byte-identical `tests/fixtures/runner_authority_envelope.json` in both repos
  and the same `CONTRACT_SHA256` in `tests/contract/test_runner_envelope_contract.py`
  here and `tests/test_orchestrator_envelope_contract.py` there. Changing the
  envelope means changing both repos together; a one-sided edit fails the repo
  that was not updated. Before WS-6.4.0 no test crossed this boundary, and the
  two sides had silently diverged into mutually unsatisfiable fixtures.
- **Dispatch and execution attempts have independent ordinals.**
  `DispatchRecord.runner_attempt` counts dispatch decisions, including skipped
  decisions; `WorkUnit.attempt_count` counts worker claims. Bind verifier evidence
  to the exact dispatch row and current claim artifacts, and never require these
  counters to be equal; see `docs/operations/verifier.md`.
- `AuthorityEnvelope.normalized()` defines what a human's authority approval
  actually attests. Fields outside `KNOWN_FIELDS` contribute only their *names*
  to the fingerprint, never their values — so a field carrying real authority
  (where code ships, which change class, what conformance was claimed) MUST be a
  known field. **Adding to `KNOWN_FIELDS` rewrites every authority fingerprint**,
  so its cost is proportional to the live ledger: free on 2026-07-09 (2 completed
  units, empty ledger), expensive later. No data migration is needed for existing
  units — every `authority_fingerprint()` call site is at write/activation time,
  and readiness compares stored columns rather than recomputing.
- factory-runner refuses to act unless `target_repo == current_repo`: it may only
  mutate the repository it checked out (`factory_runner/authority.py`). Dispatch
  must therefore resolve the target repository **per work unit**, from
  `authority.constraints.target_repository`, never from a process-global setting.
  A global target does not fail closed — it silently misroutes every fan-out unit
  to whichever repo was configured at process start.
- **The scope guard covers ALL of `src/orchestrator/`, not just `kernel/`** — an earlier
  version of this bullet said `kernel/`, and that is wrong.
  `tests/architecture/test_ws32_scope_guards.py` walks `SOURCE_ROOT = src/orchestrator`
  and scans runtime string literals **including docstrings**, minus an explicit
  per-file allowlist (`WS42_DISPATCH_PATHS`, `WS53_POST_DEPLOY_PATHS`, …). So a
  brand-new module anywhere under `src/orchestrator/` may not contain the bare
  words `dispatch` or `deploy` **in prose**. This bites documentation, not logic:
  a module whose whole reason for existing is the conformance *admission gate*
  may not say the word for the gate it serves, and must reach for a synonym.
  Verified empirically 2026-07-12 (`conformance_claim.py` reddened the guard on
  its module docstring alone).
  **The forbidden list is NOT just `dispatch`/`deploy`/`merges` — read it, don't
  recall it.** `FORBIDDEN_SEQUENCES` (`test_ws32_scope_guards.py`) is
  `factory-event/v1`, `merge_pull_request`, `workflow_dispatch`, `factory-runner`,
  `production mutation`, `auto_merge`, `productionmutation`, **`coolify`**,
  `dispatch`, `deploy`; `test_ws34_scope_guards.py` independently forbids
  **`coolify`**, `gh pr merge`, `git push origin main`, `merge_to_main`; ws33 adds
  `merges`. `coolify` is the one that surprises, because naming the platform is
  the natural way to describe estate-facing work in prose — WS-P2.18 Inc 1 wrote
  "a Coolify application or database" in a vocabulary description and reddened
  **two** guards. Say "a hosted application" instead.
- The **envelope contract and the workflow contract are different contracts.**
  WS-6.4.0's shared-fixture test validates the authority envelope across both
  repos and never executes the workflow. Two independent workflow bugs shipped
  under a green suite and blocked every dispatch until 2026-07-09: factory-runner
  was private (a reusable workflow called from repo X runs with X's
  `GITHUB_TOKEN`, so `uv tool install git+https://…` could not authenticate), and
  the workflow ran `./scripts/run-factory-task.sh`, which exists only in
  factory-runner's tree while `actions/checkout` checks out the **caller's** repo.
  A reusable workflow may only invoke things reachable from the caller's working
  directory — i.e. the installed console script. factory-runner is now public;
  keep it public or the install breaks again. Its Actions access policy was also
  `access_level: none`, so **no repo could call it at all** — that, not
  credentials, is why the pilot sat at "merged, credentialed, not dispatched".
- **`sds.alobar.net` is NOT Cloudflare-proxied** — it answers `server: uvicorn`
  behind Traefik directly. The portfolio-wide invariant that Cloudflare 403s
  default Python User-Agents with `error code: 1010` does **not** apply here;
  factory-runner's `httpx` default UA authenticates fine (verified from a
  GitHub-hosted runner, 2026-07-09). Do not misdiagnose a failure here as that.
- **Write `ORCHESTRATOR_M2M_CREDENTIALS` before `ORCHESTRATOR_M2M_ROLES`, and
  verify each from inside the container before the next restart.** `main.py`
  raises when `set(roles) ⊄ set(credentials)` — it fails **closed**, so the
  container will not boot. A half-applied credentials write leaves roles absent,
  which is a healthy configuration; a half-applied roles write is an outage (this
  is the WS-6.3 ~3-minute 503). There is no ordering of the two that can strand
  production, at the cost of one extra restart. Never "save a restart".
- The registry bundle is built from the **git tree** at `SECURITY_STANDARDS_REVISION`,
  not from a working copy, and is baked into the image. A credential's `agent_id`
  must resolve to an actor in that bundle, and `ActorContext(identity.actor_id, role)`
  means **every event is attributed to that `agent_id` forever**. Adding an actor
  therefore requires a merged security-standards commit plus an image rebuild —
  never borrow an unrelated identity for a durable credential. `token_hash` is
  `sha256(bearer_token)`; Coolify stores only the hash, so the hash is safe to
  handle and the token must never leave BWS.
- **`make check` exit 0 does not prove the tests ran.** The vendored Makefile runs
  `pytest; rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi` — exit
  code **5 means "no tests collected"** and is deliberately swallowed so a TS-only
  repo can share the target. A misconfigured `testpaths`, a collection error in a
  `conftest.py`, or a tool resolved from the wrong venv can therefore produce a
  green `make check` having executed nothing. Read the collected-test count, in CI
  as well as locally (`collected N items`), not just the exit code. This is the
  local twin of the portfolio-wide invariant that `uv sync` installs no extras and
  `quality.yml` guards every tool with `command -v`.
- **`constraints.allowed_commands` is an ordered command list the worker re-executes,
  not a permission set.** `finalize-run` runs **every** entry, in envelope order,
  and only then checks `git status` before committing. So (a) anything authorized
  *will* run again at finalize — there is no coding-phase-only grant; and (b) a
  mutator listed after the verifier means the recorded evidence (`"make check:
  passed"`) attests to a tree that is not the one pushed. Order mutators first,
  the verifier last.
- **An envelope that authorizes no mutating command cannot produce a diff, and the
  authority approval that blessed it cannot be taken back.** `approve_decomposition_proposal`
  raises `decomposition_already_approved` while an `ApprovedDecomposition` has
  `superseded_at IS NULL`, no supersede route is exposed, and unit `authority` has no
  mutation path — so a wrong envelope costs a whole new package revision plus a fresh
  human approval per unit. Before spending any of that, dry-run each unit against its
  real target repo, read-only: prove the mutator yields a diff (`uv lock --upgrade
  --dry-run`, `npm outdated`) and prove the verifier actually executes tools (no
  `"… not installed — skipping"`, a real `collected N items`). Verifying a manifest's
  *type* is not verifying an upgrade is *available*: a `==` pin makes `uv lock
  --upgrade` a silent no-op, and the unit then dies on the same `no changes to submit`
  guard the envelope was rewritten to avoid. The general failure is **authored intent
  never validated against executable reality**; WS-6.4 hit it three times.
- **That dry-run rule is necessary but NOT sufficient — it passed all three WS-6.4 defects.**
  Add three clauses. (1) **Run the ordered list twice** in one checkout: `finalize-run`
  re-executes every `allowed_commands` entry before `git status`, and `uv venv` is not
  idempotent (`uv venv --clear` is). (2) **Name every site of a pin**: `uv` resolves
  `[dependency-groups]` and `[project.optional-dependencies]` jointly, so bumping one of
  two identical `==` pins is *unsatisfiable*, not merely inconsistent. (3) **Control for
  the environment** — run the verifier against an *unmodified* clean clone first, or a
  runner-environment failure reads as an update-induced one.
- **`make check` cannot pass on a bare runner: it needs Postgres, `SECURITY_STANDARDS_DIR`,
  and a migrated database.** `tests/conftest.py` connects to `127.0.0.1:5432`, and
  `factory_events` (which lives in `security-standards`, not in this repo's dependencies)
  is importable only via `SECURITY_STANDARDS_DIR` pointing at
  `tests/fixtures/security-standards`. `quality.yml` supplies a `postgres:16-alpine`
  service, both DB URLs, that env var, and a prior `alembic upgrade head`;
  `factory-runner-pilot.yml` supplies none of them. A clean clone in a bare environment
  fails `18 failed, 836 passed` with `ModuleNotFoundError: No module named 'factory_events'`
  — *unmodified*, so this is never evidence a dependency update broke anything.
  Consequently **`make check` must never appear in this repo's authority envelope**: with
  no `.venv` it exits 0 having verified nothing, and with one it hard-fails at finalize.
  Its envelope verifies `uv sync` + `uv lock --check`; its tests are gated by its own
  named check on the pull-request head, which is where AC-001..006 already place that
  evidence. **Exit 0 from `make check` is never proof tests ran — read the collected count.**

- **A service that FLUSHES but never COMMITS looks correct in tests and is dead in production.**
  `upsert_pr_binding` (WS-P2.1) flushed and returned; the HTTP response carried the right values,
  because the ORM hands back the instance it is holding, while the row was discarded when the
  request-scoped session closed. Ten unit tests passed — they assert in-session, where the flush is
  visible. Request entry points in this repo OWN their transaction and must `session.commit()`
  (see `claim_unit`, `requeue_unit`, `record_observation`); functions invoked INSIDE another
  transaction (`arm_verification_head`, called from the SUBMIT transition) must never commit.
  **CORRECTED 2026-07-31 (WS-P2.17 Inc 4).** This bullet used to end: *"A test that asserts
  persistence must `expire_all()` and re-read, or it is asserting that a call returned an
  object."* That check **does not discriminate**, so following it produced a pin that passes
  under the exact defect it was written to catch: `expire_all()` expires the identity map, and the
  re-read then re-`SELECT`s **inside the same open transaction**, where a flushed-but-uncommitted
  row is visible. WS-P2.17 Increment 3 proved it — it injected `session.commit()` into a core
  that lacked one and watched the pin stay green. **A persistence assertion must re-read through a
  DIFFERENT session** (a second `Session(engine)`, or a `TestClient` request), which is the only
  reader that cannot see an uncommitted write. `expire_all()` remains useful for defeating the
  identity map *within* a session; it is not evidence of persistence.
- **A test fixture calling a service is not evidence the service has a caller.** WS-P2.1's PR-binding
  writers had no production call site at all: every reference in the approved plan was a test. The
  binding table was never written, the reconciliation runner (which discovers PRs to poll FROM those
  rows) had nothing to poll, and AC-001/AC-002 detection was never reached — silent, not merely
  blind, since `skipped_correlations` never incremented either. Green unit tests said nothing. When
  adding a service that another subsystem READS, grep for its production caller before believing it
  works — and prefer a drill that drives the public API, which is what actually caught this.

- **`tests/architecture/test_unreachable_guards.py` now answers "can production actually get
  here?" — and its PREDICATE is the whole design.** WS-P2.15 prototyped two wrong ones first,
  and both looked fine. (1) *Reference-counting* ("referenced nowhere outside its defining
  module") flags 11 of 93, eight being public helpers whose callers live in the same module —
  their allowlist entries would read *"in fact it is called"*, which is the predicate being
  **wrong** masquerading as an exemption being **justified**. (2) *Name-keyed reachability*
  flags 3 of 93, looks excellent, and is **blind to 12 of its own subjects**: `cli.py` is a pure
  HTTP client (it imports **zero** services) whose typer commands are **named identically** to
  the services they proxy, so seeding roots by NAME marks the *service* reachable via the mere
  existence of its CLI command. Delete the `dead_letter` route entirely — the WS-P2.1 defect
  reconstructed — and a name-keyed graph **does not notice**. Hence: nodes are import-resolved
  `(module, symbol)`. **The guard covers ONE of the two failures WS-P2.1 produced** — it cannot
  see an endpoint that is reachable but that no *client* calls (that is WS-P2.16 + drills), nor
  one that is called but wrong (that is the commit/re-read discipline). An allowlist entry that
  would read "in fact it is called" means the predicate is broken; fix the predicate.

- **`work_units.version` has exactly THREE writers, and recording an adjudication is not one of
  them.** They are `services/lifecycle.py::_perform_transition`, `services/claims.py::_transition`
  and `services/evidence.py::_system_fail_without_new_attempt` — every one a state transition.
  Consequently a submission's single `expected_version`, checked once against the locked unit row,
  stays valid for every criterion in it: it guards against another actor **transitioning the unit**
  between render and submit, not against a sibling criterion. Any note claiming that the old
  per-criterion adjudication forms staleness-broke each other is **wrong**; the real WS-P2.13
  AC-002 defects were the missing atomicity (a refusal on the third criterion left the first two
  committed — fixed in WS-P2.17 Increment 3) and a `<select>` whose first option defaulted to
  `passed`. Note the Increment 3 docstring on `record_adjudications` says "the only two writers",
  omitting `_perform_transition`; the count is three. (Verified 2026-07-31 by grep, WS-P2.17
  Inc 4.)

- **`work_units.updated_at` cannot be back-dated — a DB trigger rewrites it on EVERY update.**
  `set_work_unit_updated_at` (migration 0001) sets `NEW.updated_at = now()` on any UPDATE, so a
  test or drill that "ages" a unit by writing `updated_at` is silently overwritten and its
  assertion quietly tests nothing. Exercise staleness by **shrinking the threshold**, never by
  ageing the row (`reconcile_split_brain_stall_seconds` and
  `dead_letter_stalled_approval_seconds` are both env-overridable for exactly this). The upside:
  for a unit parked in an approval state, nothing else touches the row, so `updated_at` genuinely
  IS "when it entered that state".

- **A nullable, default-disabled threshold is how a guard goes silent.** `age_out_human_gates`
  was fully implemented, fully unit-tested, and configured by
  `dispatch_human_gate_age_out_seconds: int | None = None` — it returned `()` immediately when
  `None`, and it was `None` in production. It reported nothing for an entire workstream and had
  no production caller either. WS-P2.15 deleted it and made its replacement's threshold a plain
  `int` with a real default and **no off value**. A reporting obligation that can be switched off
  is one that will be. **A dead config knob is the same defect as a dead function** — delete both
  together.

- **Never run two pytest suites against the test database concurrently.** The fixtures drop and
  recreate `orchestrator_test`, so a background run and a foreground run corrupt each other and
  produce a spray of unrelated failures (27 failed / 13 errors, on a tree that passes 1210/1210
  when run alone). Before believing a suite-wide regression, re-run it *alone*.

- **Ordinary terminal lifecycle commands release active claims in the same transaction.** A
  WORKER transition to `FAILED` uses `work_unit_failed`; a HUMAN transition to `CANCELLED` releases
  the latest unreleased claim, when present, with `work_unit_cancelled`. Both paths go through the
  sole `services.claim_release.release_claim` primitive and reuse the transition timestamp. Keep
  idempotent replay before release and preserve the unit-then-claim row-lock order, or retries can
  mutate terminal metadata and concurrent lifecycle operations can deadlock.

- **This system had THREE vocabulary mismatches, and at the time nothing checked any of them.**
  Wherever two vocabularies must agree, assume they don't until you have grepped both sides. All
  three below surfaced in a single workstream (WS-P2.15) and none was caught by any test. Read the
  per-item corrections: #1's consequence was closed by WS-P2.17 and #3's orchestrator half by
  WS-P2.16; #2 stands unchanged. The lesson, not the inventory, is the durable part.

  1. **`evidence_type: automated_test` resolves to `judgment_required` in the verifier.**
     `DETERMINISTIC_TYPES` is `{test, tests, pytest, runner.verification, gate.summary,
     security.scan, github.checks, health.probe, automated_check, …}` and `JUDGMENT_TYPES` is
     `{human.review, code_review, judgment, manual, automated_test, human_review,
     external_attestation, observation}` (`services/verifier_evaluators.py`). **MECHANISM
     CORRECTED 2026-07-28:** this bullet used to say `automated_test` was in *neither* set and
     fell off the end of `DETERMINISTIC_TYPES`. That was true when written; **WS-P2.16 U4 moved it
     INTO `JUDGMENT_TYPES`**, so it is now a *named* judgment type — the same outcome by a
     deliberate route rather than by omission, which is exactly the difference between "a typo"
     and "a decision". **CONSEQUENCE CLOSED 2026-07-31 (WS-P2.17 Inc 1).** It used to read:
     `evaluate_criterion` returns `judgment_required` for every automated AC however good the
     evidence, so package authors must declare `evidence_type: "test"` and never `automated_test`.
     **That authoring rule was unfollowable and must not be revived** — `test` is not among the
     five types `intent_packages/validate.py`'s `EVIDENCE_TYPES` permits (`automated_test`,
     `automated_check`, `human_review`, `external_attestation`, `observation`), so `factory
     validate` rejects a `test` criterion before it can ever reach intake.
     **`automated_test` is now the correct declaration for an automated criterion.** It carries a
     deterministic-permitted *floor*: `evaluate_criterion` resolves it deterministically when
     readable evidence arrives (the evaluator is selected by the ARRIVING evidence row's type —
     e.g. a `pytest` evidence row with `{"status": "pass"}` → `passed`), and still asks a human
     when the evidence is absent or has no evaluator. `automated_test` remains in `JUDGMENT_TYPES`
     and is deliberately still **not** in `DETERMINISTIC_TYPES` — adding it there halts the factory
     (four adversarial reviews), which is why the floor is a separate concept layered over the
     intake vocabulary rather than a rewrite of it. `HUMAN_FLOOR_TYPES` /
     `DETERMINISTIC_PERMITTED_TYPES` / `floor_for()` live in `services/verifier_evaluators.py`; an
     unknown or absent criterion type floors to human, fail-closed.
     **`JUDGMENT_TYPES` had THREE consumers and Inc 1 moved only one — evaluation — which opened a
     fail-open that reached `main`: a human could record `passed` on an `automated_test` criterion
     the verifier would now resolve.** WS-P2.17 Inc 2 closed it. All three now route through
     `human_may_adjudicate(declared_type, evidence, unit_state)`
     (`services/verifier_evaluators.py`): evaluation, authorization (`evidence._authorize_outcome`),
     and the `/review` form's per-criterion flag (`web.py`, renamed `is_judgment` →
     `human_may_decide`), the last pinned to the first by set-equality test. A human may decide when
     **(a)** the floor is `human`, **or (b)** the floor is deterministic-permitted, the current
     evaluation is `judgment_required`, **and the unit is in `awaiting_review`**. Clause (b) is
     load-bearing, not a convenience: Inc 1 made deterministic-floored-but-asking a common state,
     and without it those criteria are adjudicable by **no actor at all** — the unit can neither
     complete nor be failed. It also replaces the old `# A-static:` comment's protection, guarding
     the `automated_check`-before-CI window **by timing** rather than by declared type, which was
     only ever a proxy for it. A HUMAN may now also record `failed` (it was VERIFIER-only, and the
     verifier records nothing on a criterion it deferred, so nobody could fail a judgment
     criterion); it flows through the same predicate, so it is not a wider door. This was the real
     root of the known "judgment_required ACs must be passed out-of-band via the verifier M2M
     credential / no adjudication form in `/review`" gap. **It was a vocabulary gap, not a UI gap** —
     fixing the UI would not have fixed it. `automated_check` is now a deliberately narrower supported
     vocabulary: it is deterministic only when the current evidence is verifier-owned
     `verifier.github.named_check`; pre-CI worker evidence continues to require review.
     Evidence ingestion and `/verify` are separate transactions, so the verifier must revalidate
     that stored named-check evidence against the current dispatch, attempt, repository, PR, and
     armed head, and prove the evaluated evidence row is still the current evidence-chain head.
     It locks `UnitPrBinding` through the terminal verifier transition; the ingestion lock alone
     cannot protect a later verification request from changed canonical state or superseding
     verifier evidence.
     **This does not fix or alias `automated_test`.**
  2. **`ac_id` means two different things.** `ac_mappings[].ac_id` / `retained_acs[].ac_id` on a
     decomposition proposal want the criterion's **database UUID** (`services/decomposition.py`
     builds its lookup on `str(criterion.id)`), while **evidence and adjudication want the human
     string** `"AC-001"` (`criterion.ac_id`). Same field name, opposite meanings, and the failure
     is a bare `package_acceptance_criterion_not_found` with no hint.
  3. **`github.pr.create` is validated as a NAME and ignored as a PERMISSION — and the orchestrator
     does neither.** Be precise here, because a first draft of this entry was wrong:
     - **orchestrator: STALE as of WS-P2.16 — corrected 2026-07-31.** This used to read
       "`grep -rn "github.pr.create" src/` → **zero hits**; nothing reads it, and nothing validates
       capability names at ingress at all (`_validate_unit_constraints` checks `constraints` and
       `conformance` only); the orchestrator will accept **any string** as a capability." All three
       clauses are now false. `github.pr.create` is a member of the capability vocabulary
       (`capability_vocabulary.py`) and IS read as a permission —
       `services/lifecycle.py` gates PR-opening on `envelope.level_for("github.pr.create") ==
       "allowed"`. Names ARE validated at ingress: `validate_unit_capabilities`
       (`capability_vocabulary.py`) is called from both `services/packages.py` and
       `services/decomposition.py`, so an unknown capability string is a named error at the gate.
       ADR-0001 still defers the package-authority → unit-capability projection (`pr_open` →
       `github.pr.create`) to the decomposition author — that part stands.
     - **factory-runner:** *does* validate names — `SUPPORTED_CAPABILITIES` +
       `_validate_capabilities` raise `AuthorityError` on an unknown key. But it then computes
       `can_create_pr=_allowed(envelope, "github.pr.create")` into `RunnerPermissions`
       (`authority.py:35`) **and nothing ever reads it** — the runner opens a PR without consulting
       the permission it just derived.

     A submission guard keyed on this capability would have been simultaneously **too strict**
     (every dispatched unit carries it and none has a binding → the factory halts) and **too lax**
     (the orchestrator would admit a registry-vocabulary envelope the guard can't see) — **and every
     acceptance test would have passed while it was both.** WS-P2.16 closes it.

  **Before building anything keyed on a field that crosses a boundary, `grep` for that field in
  `src/` of every repo that must honour it.** Zero production hits means the field is decoration,
  and the guard you build on it is decoration too. Three instances in one workstream is not three
  bugs — it is a missing class of test.

- **A FOURTH vocabulary mismatch, and its failure mode is the opposite of the first three: the
  correct answer WAS known, written down, and commented — in a sibling file — and nothing carried
  it across.** `intent-packages`' per-profile `TAG_TO_EVIDENCE_TYPE` maps evidence tags to the
  orchestrator's criterion vocabulary. `dependency_update.py` maps `ci:`/`gate:` →
  `automated_check` above an explicit comment: *"Never automated_test: it resolves to
  judgment_required in the verifier … which is exactly what automated_check evaluates
  deterministically against."* `maintenance_remediation.py` matches it. **`software_delivery.py`
  maps EVERY automated tag — `ci:`, `gate:`, `scan:`, `health:`, and even `review:` — to
  `automated_test`**, and the orchestrator's named-check ingestion refuses anything but
  `automated_check` **server-side** (`services/verifier_evidence.py:271`, not merely in a CLI
  verb — a reviewer placed it in the CLI and was wrong). Consequence, measured by the WS-P2.35
  pilot: **no software-delivery package could reach the observed-check verifier lane at all**; its
  AC-001 completed on human adjudication instead. The two profiles that had actually been
  dispatched were correct; the one that had not was not — so *being exercised* is what fixed the
  other two, and nothing else would have. Read this as the standing hazard: a per-profile lookup
  is N copies of one vocabulary, and only the copies that run get corrected.
  **CLOSED 2026-08-04 (WS-P2.36, intent-packages PR #57 `d96ea73`), and the CLOSING is the more
  useful half of this entry.** `ci:`/`gate:` now map to `automated_check`; `scan:`/`health:` stay
  `automated_test` and `review:` became `human_review`; `infrastructure_change` was assessed and
  deliberately left unchanged, with the reasoning recorded in the module. Proven the same day:
  unit `a1493627…` completed with **AC-001 resolved from observed `verifier.github.named_check`
  evidence**, evaluator reason *"the named check was observed to conclude success"*.
  Three things worth carrying, none of which the handoff anticipated:
  **(1) The two evidence types are NOT ordered — they are deterministic for DIFFERENT producers.**
  `automated_check` is special-cased ahead of the evaluator lookup and resolves *only* on
  verifier-owned `verifier.github.named_check` evidence; `automated_test` dispatches on the
  *arriving* row's type and can resolve off a worker-recorded row. So declaring `automated_check`
  for a tag no CI job produces does not merely fail to help — it **forfeits the producer that tag
  actually has**. That, not "an unreachable lane", is why `scan:`/`health:` stayed put: measured
  across the seven factory-target repos, only `security-standards` publishes a scan job and none
  publishes a health probe reachable on a PR head (`brain`'s is a step inside a `deploy` job gated
  to pushes on `main`). A per-profile map also cannot be per-repo.
  **(2) The permissive map was NOT a lost lesson — it was a deliberate WS-P2.10 decision**, and
  reading it as an oversight makes the fix look like a one-line edit when it is not. That spec says
  the two profiles were *"wrapped, not changed … All 19 existing packages must validate
  byte-identically"*, and `profiles/base.py` states the reason: an approved package's YAML cannot
  be edited because `evidence_type` is inside the canonical hash, so editing it invalidates the
  lineage approval (probed: `verify-approval` rc=0 → rc=1). The naive map change reds **12 of 16**
  software-delivery packages. The fix therefore needed a grandfathering set keyed on
  **`(package_id, revision)`** — never `package_id` alone, since a new revision is fresh authoring
  that must comply, and `ws-3.4-evidence-events` was already at revision 2.
  **(3) Generalise: when a validation rule changes in a repo whose artifacts are immutable and
  hash-bound, the old population is EXEMPTED, never rewritten** — the same trade the factory-policy
  grandfathering table records for reach.

- **MERGED IS NOT DEPLOYED. Ask production what it is running before you reason about
  what it can do.** On 2026-07-12 production was serving
  `ghcr.io/alobarquest/orchestrator:d6d73b3-ws64-verifier-amd64` — a WS-6.4-era image —
  while WS-P2.1 (PR #47) and WS-P2.15 (PR #50) had been merged to `main` for days.
  `recover-evidence`, `dead-letter`, `requeue`, `reconciliation/detect`,
  `consistency-check` and **`pr-binding`** were all **absent from production** and
  returned 404, though every one of them exists in `main`. Program exit criterion #7
  ("operator status and recovery controls exist") was marked **MET**, citing five routes
  **none of which production served**; the five recovery drills that marked criterion #5
  MET run against a **local** orchestrator and had never touched production. WS-P2.16's
  entire subject — the `pr-binding` route — was undeployed, so a perfectly correct
  worker call would have 404'd; **six adversarial reviews of the WS-P2.16 plan missed
  this, because every one of them read the repository instead of asking production.**
  The check is one command and it is not optional:
  `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json; print(sorted(json.load(sys.stdin)['paths']))"`.
  A green suite on `main` says nothing about the machine that serves traffic.

- **There are TWO kinds of approval, and the generic `/review` "approval" button records the
  one readiness does not want.** `POST /review/units/{id}/approval` (`web.py`) hardcodes
  `subject_type="action"` — which satisfies the `AWAITING_APPROVAL → READY` transition
  guard. But readiness and dispatch both require an **`authority`** approval:
  `subject_type="authority"`, bound to `subject_revision_or_fingerprint ==
  unit.authority_fingerprint`, setting `unit.authority_approval_id`
  (`persistence/repositories.py::exact_authority_approval`). **CORRECTION (verified 2026-07-22,
  AC-003 dispatch): there IS now an authority-approval form in `/review`.** The unit page
  renders "Approve this authority envelope" (`templates/unit.html`, gated on no
  `authority_violation`) which POSTs to `POST /review/units/{id}/authority-approval`
  (`web.py::approve_authority`, `_human` + CSRF, `subject_type="authority"`). So a human does
  the authority approval as a **GUI click**, not a devtools `fetch()`. Use that form, NOT the
  generic "approve" button. Package **intake** is done by a browser `fetch()` to `POST
  /api/v1/package-intakes` through the `orchestrator-intake-human` forward-auth router (also
  verified 2026-07-22). The earlier "no authority-approval form; pasted `fetch()` in devtools"
  claim here is obsolete.

- **Authority approval does NOT move the unit's state — `DRAFT → READY` is a separate SYSTEM
  step that is easy to forget.** Recording the `subject_type="authority"` approval sets
  `unit.authority_approval_id` (a *dispatch admission* precondition), but the unit stays in
  `DRAFT`. Dispatch admission requires `unit.state == "ready"` (`services/dispatch.py`), so a
  dispatch attempt on a still-`DRAFT` unit is `blocked` with reason `work_unit_not_ready` — even
  with the authority approval recorded and `readiness` reporting `status: ready` (that endpoint
  reports *conditions met*, not lifecycle state). `(DRAFT, READY)` is a **SYSTEM** edge
  (`kernel/transitions.py` `SYSTEM_EDGES`) with **no approval guard** (only `AWAITING_APPROVAL →
  READY` is guarded), so the orchestrator-system credential drives it via
  `POST /api/v1/work-units/{id}/commands/ready`. Order: human authority approval → system
  `commands/ready` → dispatch. (Verified 2026-07-17 dispatching the brain AC-002 unit.)

- **Decomposition approval is human-only and reachable ONLY through the `/review` GUI, never the
  raw `/api` path.** `approve_decomposition_proposal` calls `_require_decision_actor`, which
  raises unless `actor.role is ActorRole.HUMAN`. But the raw `POST
  /api/v1/decomposition-proposals/{id}/approve` sits on the default `orchestrator-api` Traefik
  router (headers-strip only = **M2M-only**), so a browser session `fetch` to it `401`s **by
  design of the routing table** — not because forward-auth is broken. The human path is the
  `/review/decomposition-proposals/{id}` GUI page, whose form POSTs to
  `POST /review/decomposition-proposals/{id}/approve` (`web.py`, `_human` + CSRF) under the
  `orchestrator-review` router, which *does* carry the forward-auth chain. This is unlike intake
  and authority approval, which each have their own dedicated forward-auth `/api` router
  (`orchestrator-intake-human`, `orchestrator-authority-approval-human`) and so *are* done by
  browser `fetch`. A browser `401` on any orchestrator `/api` route means the endpoint is
  M2M-only, not that auth is down. (Verified 2026-07-17.)

- **Driving a dispatch needs the M2M bearer tokens — fetch them, don't hunt.** The two
  credentials and how to get them are already recorded; do not re-derive them each session.
  `.bws-secrets.toml` (repo root) names the BWS UUIDs; source `BWS_ACCESS_TOKEN` via the
  approved Keychain helper **`scripts/sds-token.sh` in this repo** (service `Claude`, account
  `BWS_ACCESS_TOKEN_SDS`), then `bws secret get <uuid>` — never echo any value.
  **Do NOT use `~/Projects/vps-backup/bws-token.sh` — it no longer works for these secrets.**
  Until 2026-07-30 every SDS fetch bootstrapped with that helper, i.e. the shared broad machine
  account (one account behind BOTH the `BWS_ACCESS_TOKEN_VPS_BACKUP` and
  `BWS_ACCESS_TOKEN_INFRA_DRIFT` Keychain names — verified identical, sha256 `da55db37ea81`).
  The three SDS runtime secrets now live in the `SDS Operator` BWS project, readable only by
  the read-only `sds-operator` machine account. The old token is DENIED on all three; that is
  the migration working, not a fault. No secret VALUE changed — this narrowed who can read.
  Consumers resolve by UUID, and the UUIDs survived the project move unchanged.
  **SYSTEM** (`orchestrator-system`, decomposition-submit / `commands/ready` / dispatch):
  `221a48d5-3f29-4898-b300-b4820140c880`. **VERIFIER** (`orchestrator-verifier`,
  `verifier-evidence/named-check` + `/verify`): `660d5846-abcb-4751-be86-b483012899eb`.
  **WORKER** (`orchestrator-operator`, agent_id `claude-code-interactive` — claim / `start` /
  evidence / `submit`): `bd71bed1-4aac-4af8-9094-b4970180bc59`, added WS-P2.13 2026-07-30.
  A unit **cannot reach COMPLETED without a WORKER actor**: `CLAIMED→EXECUTING` and
  `EXECUTING→SUBMITTED` are worker-only edges and there is no human path around them. It carries
  **no `ORCHESTRATOR_M2M_ROLES` entry** — `authenticate_m2m` returns WORKER for every M2M
  credential and the roles map only *promotes*, so worker is what an unpromoted credential falls
  to. The alternative was `factory-runner-github`, whose `agent_id` attribution is permanent and
  untrue for non-software operational work. Every
  M2M call sends both `Authorization: Bearer <token>` and `X-Credential-Key-Id: <key-id>`.
  Read endpoints (`status-ledger`, `runner-brief`, …) also require the SYSTEM bearer — a
  bare GET is `401`. (Verified 2026-07-22, AC-003; token migration 2026-07-30.)
  The third SDS secret in that project is the Todoist token
  (`ff396349-aec1-4250-b2f0-b493015188da`, BWS key `TODIST-API-DEVON-PERSONAL`), used by the
  tracker launchers. **The SDS consumer set is five, not the three the migration plan named:**
  this repo's three launchers, intent-packages' `credentials.py` (env-driven, no code change),
  **and `infraops-mcp-server/scripts/drift-audit.sh`** — which runs on a 03:00 LaunchAgent and
  fetches the SYSTEM bearer for its `mint-follow-ups` step with a *different* BWS identity from
  every other secret it reads, so it overrides `BWS_ACCESS_TOKEN` for that one call.
  `factory-validation-kit-restart-recovery/credentials.py` hardcodes the same SYSTEM UUID but is
  operator-invoked, on no schedule. A grep of `src/` in one repo would have found neither: when
  moving a secret, grep the whole portfolio for the UUID, not the repos you expect to own it.

- **`factory decompose` (intent-packages) needs three env pieces the tool does not set itself.**
  **CORRECTED 2026-08-04: `.venv/bin/factory` IS installed and works** — `[project.scripts]`
  declares it and `factory --help` prints usage, so invoke the console script directly. What is
  still broken is only `python -m intent_packages.factory_cli`, which has no `__main__` guard and
  so exits 0 with no output (backlogged in intent-packages; the WS-P2.35 pilot's subject). The old
  `python -c "…main(sys.argv[1:])"` workaround is no longer needed. Required env: (1) the orchestrator console script on
  `PATH` (`PATH=~/Projects/orchestrator/.venv/bin:$PATH`) — the tool shells out to
  `orchestrator show-package-intake` / `conformance-claim` / `propose-decomposition`;
  (2) `ORCHESTRATOR_API_URL=https://sds.alobar.net` + `ORCHESTRATOR_API_TOKEN=<SYSTEM>` +
  `ORCHESTRATOR_API_CREDENTIAL_KEY_ID=orchestrator-system`; (3)
  `PYTHONPATH=~/Projects/project-standards/src:~/Projects/security-standards/src` or
  `conformance-claim` fails `scanner_unavailable: portfolio.compliance is not importable`. Run
  once without `--submit` (dry — clones the target, runs the mutator twice, all four fail-closed
  validations) and review the proposal before re-running with `--submit`. (Verified 2026-07-22.)

- **`make check` runs `ruff format --check .` over the WHOLE repo — but per-task `ruff check` and
  the diff-scoped Stop hook only see CHANGED files + lint rules, so whole-repo *format*-debt is
  invisible until a full `make check`.** A file can be committed to `main` ruff-check-clean but not
  ruff-format-clean and nothing catches it until someone runs the full gate — `test_pr_bindings.py`
  landed format-dirty via WS-P2.16 (`2b18c98`) and only surfaced during WS-P2.2's final `make check`
  (it fails *without* your change too — a differential, not your regression). Two consequences: (1)
  before declaring `make check` green, expect it may red on **pre-existing** format-debt in files you
  never touched — run `ruff format --check .` and diff against `main` before blaming your diff; (2)
  have implementers run `ruff format` (or `make fix`), not just `ruff check`, before committing, or
  the debt accretes. (Verified 2026-07-24, WS-P2.2.)

- **Only `DomainError` and `APIAuthenticationError` have registered exception handlers (`main.py`) —
  every other exception raised from a route surfaces as a bare, unhandled HTTP 500.** There is no
  handler for `IntegrityError`, `ValueError`, `TypeError`, or a generic `Exception`, so anything a
  route (or a service it calls) raises that is not one of those two types is a 500, not a clean 4xx.
  Two consequences, both of which bit WS-P2.3: (1) **route-level input parsing must raise
  `DomainError`, never let the stdlib raise** — `uuid.UUID(bad)`/`datetime.fromisoformat(bad)` raise
  `ValueError`, and a timezone-*naive* `datetime.fromisoformat("2027-06-01")` compared against an
  aware `now` raises `TypeError` deep in the service (both → 500); wrap parses and reject naive
  datetimes (`tzinfo is None`) up front. (2) **A partial service guard that leaves a DB CHECK to fire
  is a 500, not a validation error** — `record_adjudication`'s `except IntegrityError` routes to
  race-detection and then re-`raise`s, so a CHECK violation the service did not pre-validate (e.g. an
  out-of-vocab `risk` on a *non-waiver* adjudication) escapes as an unhandled `IntegrityError`.
  Whenever you add a DB CHECK, the service must reject every value the CHECK would, with a
  `DomainError`, for *every* code path that can reach the column — not just the one the feature
  targets. (Verified 2026-07-24, WS-P2.3 — two independent 500 paths, both caught only by the
  whole-branch review, not by five prior per-task reviews.)

- **`claim_unit` is NOT the only place a unit is granted an attempt — `reclaim_expired_claim`
  bypasses it.** `reclaim_expired_claim` → `_perform_reclaim` → `_acquire_reclaimed_claim`
  (`services/claims.py`) transitions an expired unit and grants a fresh CLAIMED attempt **without
  ever calling `claim_unit`**. So any per-attempt gate placed only in `claim_unit` (e.g. a budget
  cap) is silently bypassed when a lease expires. The choke point for "may this unit get another
  attempt?" is the shared `_readiness_eligibility_error` (`claims.py`), used by BOTH reclaim and
  requeue — it is where `attempts_exhausted` lives and where WS-P2.4 Inc 2 added the `is_over_budget`
  gate. Any future "can this unit run again" rule belongs there, not (only) in `claim_unit`.
  (Verified 2026-07-25, WS-P2.4 Inc 2 — the final whole-branch review caught an over-budget unit
  running past its cap via the reclaim path; per-task reviews and the plan's own claim-only decision
  missed it.)

- **The evidence-pack `/api` is authentication-only (any authenticated actor reads any unit's full
  pack); the markdown relayed onto a possibly-public PR comment is deliberately REDACTED, the JSON
  is not.** `GET /api/v1/work-units/{id}/evidence-pack` (JSON) and `/evidence-pack/markdown` take
  `_actor: ActorDep` with no role gate — the runner's worker credential reads them, consistent with
  `runner-brief`/`status-ledger`/`history` (all auth-only). Because factory-runner posts the
  **markdown** as a comment on the target repo, **which may be public**, the markdown renderer
  (`services/evidence_pack.py::render_evidence_pack_markdown`) omits approver identities and waiver
  rationale (`decided_by`, `rationale`, `approved_by`, `reason`, event `actor_id`) while keeping the
  facts. The **JSON stays full-fidelity** (auth-gated, for WS-P2.6/audit). The redaction is
  hand-edited per section — a new markdown section that interpolates those fields must redact them by
  hand until a structural allowlist exists (backlogged). A `text/markdown` route also needs an entry
  in `NON_JSON_SUCCESS_PATHS` (`tests/api/test_lifecycle_api.py`) to satisfy the
  every-success-response-has-a-json-schema invariant. (Verified 2026-07-25, WS-P2.5 Inc 1 — the
  public-exposure decision was the final review's one Important finding.)

- **The brains have a REST read API built for off-machine agents, it is approved-only by
  default, and there is NO read-only credential.** Code Brain (`https://code-brain.devonwatkins.com`)
  serves `GET /api/roads`, `/api/road/{slug}`, `/api/rules`, `/api/search`; Infra Brain
  (`https://infra-brain.devonwatkins.com`) serves `GET /api/rules`. Auth is
  `x-brain-key: <key>` (or `?key=`), and the middleware accepts **either** the approver key or
  the contributor key, gating every non-allowlisted path identically — so the contributor key
  (`CODE_BRAIN_CONTRIBUTOR_KEY` `750f737f-4cb6-4876-9a98-b48200ea1c0b`,
  `INFRA_BRAIN_CONTRIBUTOR_KEY` `da8134b0-565f-45c8-8965-b48200ea1c40`, BWS project `brains`,
  bootstrap identity Keychain `Claude`/`BWS_ACCESS_TOKEN_VPS_BACKUP`, **not** the SDS-narrow
  account) is the least privilege available and can also POST proposals. Narrowing that is open.
  Containment comes free from the repositories: `list_all` defaults `include_proposed=False`, so
  REST serves approved-only records. The REST route exposes `category/severity/road_slug/
  include_retired` but **not** `min_authority` (the repository supports it, the route does not
  pass it), so an authority floor is applied client-side.
  **Severity and authority are orthogonal and disagree** — Infra Brain has **12** BLOCK-severity
  rules of which only **4** are `authority: required`, and Code Brain has **zero** at `required`
  (all 11 of its rules are `informational`). A filter keyed on the wrong one carries three times
  the material. Content is thin: the only substantive road is `error-logging` (9 rules, 2
  exemplars, a real `decided_approach`); `dependency-update` is `paved` with `decided_approach:
  null` and 0 rules/exemplars/lessons. (Verified live 2026-07-30, WS-P2.12.)

- **A FastAPI `response_model` silently DROPS every key the service returns but the model does
  not declare — so "the service returns it" is never evidence "the worker receives it".**
  `runner_brief_route` declares `response_model=RunnerBriefResponse`. WS-P2.12 added an
  `enrichment` key to `services/runner_brief.py`, every service-level assertion passed, and the
  HTTP body carried nothing, because the response model had not been extended. This is the
  WS-P2.1 shape (service correct, wire empty) in a new place, and it is invisible to exactly the
  test you would reach for: a cross-repo contract test that asserts on the **service dict**
  rather than the **served body** has its blind spot precisely where the consumer reads.
  factory-runner parses the body. Two consequences: (1) adding a field to any service backing a
  `response_model` route means editing the model in the same change; (2) a contract test for such
  a route must pin the model — `tests/contract/test_runner_brief_contract.py` asserts
  `set(RunnerBriefResponse.model_fields) == set(golden_brief())`, which needs no HTTP client and
  cannot drift. Note the failure direction is silent-drop, never an error.
  (Verified 2026-07-30, WS-P2.12.)

- **The runner BRIEF is a cross-repo contract too, and until WS-P2.12 nothing tested it.**
  WS-6.4.0 pinned the authority *envelope* across both repos and left the brief unpinned, and the
  brief is the larger surface. It is now pinned the same way: byte-identical
  `tests/fixtures/runner_brief.json` in both repos plus the same `CONTRACT_SHA256`
  (`1cf3c51678ad…`). The hash pin alone proves only that a file is unchanged; both repos therefore
  also carry a *derivation* assertion (orchestrator: the served key set; factory-runner: that the
  fixture's content reaches `_prompt`). Proven by control: deleting the prompt's enrichment section
  leaves both **shape** tests green and reds only the derivation test.
  **TWO CORRECTIONS, 2026-08-01 (WS-P2.23) — this bullet was wrong in the way that cost a day.**
  (1) It said "the runner is installed fresh per run from its **default branch**, so merge-first
  suffices." **The runner has never been installed from a branch.** The reusable workflow installs
  a pinned revision, so merge-first suffices for *nothing on its own* — the pin has to advance too.
  Believing otherwise is precisely why the 2026-07-30 `enrichment` addition was thought safe: the
  orchestrator merged the field, everyone assumed callers would pick up a runner that knew it, and
  **every dispatch in the estate died at brief-parse for a full day with nothing noticing** while
  `runner.caller` reported `[ok]` throughout (it compares SHAs, and a SHA says nothing about
  whether the revision behind it can read what you serve).
  (2) It said `RunnerBrief` is `extra="forbid"`, so an unknown key kills every run at claim. **True
  when written; false since factory-runner `b0305b5`.** It is `extra="allow"` and *reports* what it
  tolerated — see the next bullet. Do not restore strictness: it guarded only the safe case (an old
  runner cannot use a field it does not know about), while a renamed or removed field is caught by
  required-field validation whatever `extra` says.

- **Drift between what the orchestrator serves on the brief and what its pinned consumer can use is
  now refused at the PULL REQUEST, and the two guards are deliberately keyed on different things.**
  WS-P2.23. Three parts, and the interaction between them is the whole design:
  - factory-runner's reusable workflow installs `job.workflow_sha` — **its own commit.** `job.*`
    describes the workflow file defining the job (factory-runner) even when called from another
    repo; `github.*` describes the caller. So from `b0305b5` onward **the caller's `uses:` SHA IS
    the CLI revision**: pin X → workflow at X → CLI at X. `workflow_sha`, never `workflow_ref` —
    the ref is what the caller pinned, *unresolved*, so a branch ref appears verbatim and is
    mutable. (Documented GitHub context properties, confirmed against docs 2026-08-01.)
  - the `Runner consumer compatibility` job in `quality.yml` runs (named `Runner brief
    compatibility` until 2026-08-09, when it grew its second surface)
    `scripts/check_brief_consumer_compatibility.py`: it reads the pin from
    `factory-runner-pilot.yml`, asserts the workflow at that revision still installs itself (the
    premise, checked rather than assumed), reads `RunnerBrief` at that revision **from source via
    the GitHub contents API**, and fails if `RunnerBriefResponse` declares a field it does not.
    Source rather than install: introspecting is more accurate but would drag factory-runner's
    whole dependency tree into this repo's PR gate to read one attribute. The AST parse is pinned
    against `RunnerBriefResponse.model_fields` in `tests/contract/test_brief_consumer_compatibility.py`,
    so a parser that stopped agreeing with pydantic is caught before it can vet anything wrongly.
  - **The check is keyed on DECLARED fields, not on whether the consumer would tolerate an
    undeclared one — and that is load-bearing, not fussiness.** `RunnerBrief` is now
    `extra="allow"`, so a "would it parse?" check would pass on everything and **part C would
    silently switch part B off**, putting the ordering rule back to being prose. A field the
    consumer does not declare is a field it cannot use: the run survives, the feature does not
    exist. Proven to fire both directions on 2026-08-01 — adding `cadence` to
    `RunnerBriefResponse` reds the job, removing it greens it.
  - **UPDATED 2026-08-09 (WS-P3.7 Inc 3): that job now vets TWO surfaces, and the second one is
    not about fields at all.** Alongside the brief fields it checks that every capability name
    `capability_vocabulary.py` declares is recognised at the pinned revision — the same red→green
    proof, measured in CI rather than locally: PR #153's job was red for the whole batch, and the
    identical job re-run 13 minutes after factory-runner's PR #51 landed, **with no code change
    and no rebase here**, printed both PASSes. A check that turns green because a *different*
    repository merged is the ordering rule made mechanical. The capability half additionally vets
    factory-runner's `RECOMMENDED_CALLER_PIN`, which the brief half does not — because dispatch
    fires the caller workflow in the unit's own TARGET repository, so this repo's pin is not the
    one that will run. Its residual, stated in the module rather than papered over: **a target
    repo that drifts off the recommendation is invisible to it**; `runner.caller` in the
    conformance kit is what sees that, per repo. Neither is sufficient alone — the gate holds the
    recommendation to what this repo serves, `runner.caller` holds every target repo AT the
    recommendation. **The job's NAME is now wrong** and renaming it is a PAIRED operation: it is a
    required status check on `main`, so a rename must move the protected context in the same
    operation or every pull request is blocked, silently, by a context nothing reports.
  So: **merge factory-runner first, then advance the pin in `factory-runner-pilot.yml`, then serve
  the field.** That is now mechanical. And if a gate is ever bypassed, factory-runner records the
  undeclared keys in the `runner.pr.opened` evidence payload (`unknown_brief_keys`), so an escape is
  neither fatal nor invisible.

- **Adding a route — `/api` OR `/review` — or a new `src/orchestrator/` module trips a FAMILY of
  architecture guards. There are FIVE, and three are exact set-equality inventories that fail CI on
  a missing entry.** The `test_ws32_scope_guards.py` word guard (bare tokens `deploy`/`dispatch`,
  with the `WS42_DISPATCH_PATHS`/`WS53_POST_DEPLOY_PATHS` allowlists) is the one this file documents
  elsewhere — but it is not alone. (1) **`test_ws33_scope_guards.py` forbids the bare word `merges`
  (and merge-path phrases) anywhere under `src/orchestrator/` with NO allowlist** — a module docstring
  saying "never dispatches, deploys, or merges" reddens it; reword (e.g. "writes to git"). Note the
  tokenizer matches whole tokens: `merges`→forbidden, but `deployment`/`deployments` do NOT match
  `deploy` and `dispatches` does NOT match `dispatch` (only the exact bare token does).
  **CORRECTED 2026-07-31 (WS-P2.17 Inc 5): "whole token" is not "whole word" — a COMPOUND
  tokenizes into its parts and each part is matched.** The tokenizer is
  `re.split(r"[^a-z0-9]+", camel_boundary_split(value).lower())` (`test_ws32_scope_guards.py`), so
  **every** non-alphanumeric character is a separator and a camelCase boundary is one too:
  `post-deploy`, `pre/deploy`, `deploy_hook` and `postDeploy` all yield a bare `deploy` token and
  all red the guard. Increment 4 hit this on a docstring containing `post-deploy`. What survives is
  only a longer single token — `deployment`, `redeploy`, `dispatches` — because no separator splits
  it. (An ALL-CAPS identifier like `POST_DEPLOY_AC_IDS` shreds to single letters and is invisible,
  which is why the token forms in prose and not in code.) Reword; never add an allowlist entry. (2)
  **`test_scope_guards.py::test_production_post_route_inventory_is_explicit` AND
  `::test_production_get_route_inventory_is_explicit` assert those path sets EXACTLY** — every new
  route must be added to the matching set literal or CI fails (the per-task `make check` may miss it
  locally if the working tree isn't the committed state; this is the class that broke PR#69 CI in
  WS-P2.4). ⚠ **The POST inventory is NOT `/api/v1`-only — it includes `/review` POST paths**, and
  an earlier version of this bullet said otherwise. A WS-P2.9-era session handoff inherited that
  error and told the build `/review` routes were exempt from the inventory family; they are not, and
  it reddened the final gate. The GET inventory is `/api/v1`-only (the `/review` GETs render HTML
  and are `include_in_schema=False`). (3) **`test_ws33_scope_guards.py::
  test_no_workflow_dispatch_or_factory_runner_dispatch_code_exists` forbids the bare strings
  `workflow_dispatch` / `factory-runner` / `factory_runner` in ANY workflow file**, with its own
  allowlist that is SEPARATE from `test_no_automatic_merge.py`'s. Both scan `.github/workflows/`
  and both must be edited to add a `workflow_dispatch` workflow — updating one leaves the other
  red, and neither mentions the other. (4) **`tests/idempotency/test_matrix.py` requires every
  ingress POST route — `/api/v1` AND `/review` — to have a `COVERAGE_MATRIX` row or a reasoned
  entry in `NON_INGRESS_POST_ROUTES`**; a `/review` form that delegates to a service already
  covered by an `/api` row belongs in the exclusion set with that delegation named. It is gated
  both ways: a stale exclusion for a route that no longer exists also fails, and every matrix row
  must name a test that actually exists.
  `api/routes.py` + `api/schemas.py` are already in `WS42_DISPATCH_PATHS`, so route/schema *words*
  are exempt — but the route-inventory sets are NOT word guards and apply to every route regardless.
  `web.py` is in no allowlist: keep its route bodies free of the bare words (delegate to a service).
  Jinja `.html` templates are not scanned at all.
  (Verified 2026-07-25, WS-P2.5 Inc 2 — the ws33 "merges" guard and the GET-route inventory caught
  mid-build. Extended 2026-07-28, gap-closure session 1: adding ONE `/review` POST route and ONE
  scheduled workflow reddened three of these five at the final `make check`, all invisible to the
  per-task loop and the diff-scoped Stop hook.)

- **The prod orchestrator image build is PAVED-ROAD-automated (image-build-automation
  workstream); Coolify only ever pulls a prebuilt GHCR tag and does not build.** The paved
  road: `security-standards.pin.toml` (repo root) is the **single source of truth** for the
  pinned `revision`/`artifact_sha256` — prose here is context, not authority; read the file.
  `scripts/shape_registry_context.py` turns a security-standards checkout at that revision
  into the shaped `{agents/, src/, schema/, SOURCE_REVISION}` build-context via `git archive`
  (never a raw checkout — untracked files would poison the digest); it writes `SOURCE_REVISION`
  **with a trailing newline** — that byte is part of the digest contract, and it's a real
  footgun the old manual recipe left implicit (the fixture convention and
  `build_registry_bundle.py` both hash the file verbatim, newline included). The `Release
  image` GitHub Actions workflow (`.github/workflows/release-image.yml`,
  `workflow_dispatch`, native `linux/amd64`) reads the pin, shapes the context, and runs
  `docker buildx build --push` to **two** tags (WS-P2.18 Inc 7): the derivable
  `ghcr.io/alobarquest/orchestrator:sha-<full-40-char-sha>`, which depends on the commit and
  nothing else, and the human-readable `:<short-sha>[-<label>]-amd64`, which is kept as a caption.
  Both are composed by `scripts/compute_image_tags.py` — it is the function, and it refuses a
  revision that is not a full 40-character sha, so a malformed one fails the build rather than
  producing an image that asserts an unresolvable provenance.
  **The workflow only builds and pushes — it never deploys.** Pointing Coolify at the new tag
  stays a separate, manual gate, same as before.
  Two digests, two different jobs, not competing checks: the **bundle digest**
  (`REGISTRY_ARTIFACT_SHA256`, currently `7aea8471…` per the pin file) is the **build-time,
  security-critical** gate — the Dockerfile's `registry` build stage recomputes it from the
  shaped context and **fails closed** on any mismatch (wrong/tampered actor registry), whether
  the build runs in CI or by hand. The **image SHA / running container's `RepoDigest`** is the
  separate **deploy-time identity** check Devon still does by hand after the Coolify swap —
  proving prod is running bit-for-bit what the workflow pushed.
  **CORRECTED 2026-07-31 (WS-P2.17 Inc 4): the recipe this file has been shipping for that check
  does not work.** `docker inspect <container> --format '{{index .RepoDigests 0}}'` fails, because
  `RepoDigests` is a property of an **image**, not of a container — inspecting a container returns
  no such field. Go container → image → digest instead:
  `docker image inspect "$(docker inspect <container> --format '{{.Image}}')" --format '{{index .RepoDigests 0}}'`.
  The invariant ("ask production what it is running") was right; only the command was wrong, and
  a wrong command that errors is at least loud — do not replace it with one that prints an empty
  string. Bumping either digest requires
  bumping `security-standards.pin.toml`'s `revision` and `artifact_sha256` together (see that
  file's own header comment for the recompute recipe).
  **Fallback / differential baseline — keep this runnable, don't delete it:** the manual
  `docker buildx` recipe still works and is the thing to fall back to if the workflow is down,
  or to diff against if a CI-built image looks wrong. Recipe:
  `docs/software-delivery-system/2026-07-09-ws64a-deploy-and-onboarding-state.md`. Unless an
  actor/registry change is intended, PIN `SECURITY_STANDARDS_REVISION` to the pin file's
  `revision` (`65655ddf…`) and assert the computed `artifact_digest()` equals the pin file's
  `artifact_sha256` (`7aea8471…`) BEFORE the long build — that is the same byte-identical bundle
  gate (13 actors), done by hand. Then `docker buildx build --platform linux/amd64
  --build-context registry=$ART --build-arg SECURITY_STANDARDS_REVISION=$SHA --build-arg
  REGISTRY_ARTIFACT_SHA256=$DIGEST -t ghcr.io/alobarquest/orchestrator:<sha>-<ws>-amd64 --push .`
  produces a single amd64 v2 manifest; verify the running container's RepoDigest == the pushed
  digest after Coolify swaps (via `.Image`, per the correction above).
  **A hand-run build must also pass the three `--label` flags the workflow passes**
  (`org.opencontainers.image.revision` with the FULL sha, `.source`, `.created`) and the second
  `-t sha-<full-sha>`, or the fallback silently produces a less-identifiable artifact than the
  paved road — precisely the state Inc 7 closed. The workflow refuses such an image; a hand build
  has nothing to refuse it.
  **The FULL 40-character SHA goes in the workflow's `ref` INPUT, not in `gh workflow run --ref`.**
  These are two different things and this bullet used to conflate them, which cost WS-P2.18 Inc 4 a
  422. `--ref` selects the git ref the workflow FILE is read from and expects a branch or tag;
  passing a raw SHA to it fails `HTTP 422`. The revision to build is a workflow **input**
  (`-f <input>=<40-char-sha>`), and there `actions/checkout` treats a non-40-character value as a
  branch/tag pattern, matches nothing, and fails the run — so a short SHA cannot build, however
  valid it is to `git`. Read `.github/workflows/release-image.yml`'s `inputs:` block for the input's
  actual name rather than guessing it. A plain `docker build .` with no `registry` context fails at
  `COPY --from=registry` — that is expected, not a Dockerfile bug. (Verified 2026-07-25, WS-P2.5
  Inc 2 deploy. Automation added 2026-07-26, image-build-automation workstream.)

- **An orchestrator image with NO labels is an old image, not a tampered one — every image built
  before 2026-08-02 carries `Config.Labels: null`.** WS-P2.18 Inc 7 made the build assert
  `org.opencontainers.image.revision` (full 40-char sha), `.source` and `.created`, and made the
  workflow **pull the pushed image back from the registry and fail closed** unless the revision
  label equals the commit built — a label present in the build command and absent from the
  artifact proves nothing, which is exactly how the no-label state survived unnoticed. Ask an
  image what it is with
  `docker image inspect <ref> --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`.
  **Until an image is next rebuilt the answer is `<no value>`, and for those the ONLY provenance is
  the tag's short sha** — `git rev-parse <short-sha>` back to the commit, and nothing at all if the
  tag was lost. Production's `c755c99-wsp218inc5-amd64`
  (`sha256:615c0b3053671fed9a33f9012f645f301eaf6da7aa9dafa29e14a7c06f820939`) is such an image; the
  discontinuity ends the first time production is rebuilt, and nothing about the deployed image was
  changed to close it. Note the container→image→digest correction above applies to reading labels
  too: `Config.Labels` on a *container* is the container's own label set, not the image's.
  **`sha-<full-sha>` is DERIVABLE, not IMMUTABLE — a rebuild silently re-points it, measured, not
  assumed.** Two builds of `e1204893…` on 2026-08-02 produced the same tag name and the digests
  `sha256:da035e69…` and `sha256:a0936e08…`. The `.created` label alone guarantees this (it is a
  build timestamp), and `python:3.12-slim` is a moving base tag underneath it, so image
  reproducibility was never a property this build had. **The digest is the identity; the tag is how
  you find it.** Two consequences: a rollback target should be recorded as a digest whenever one is
  to hand, and during a rebuild there is a real (seconds-long) window in which the two tags
  disagree, because buildx pushes them sequentially — observed live, and a plausible way to
  misdiagnose a defect that is not there. Making the derivable tag refuse to overwrite is an open
  follow-up, not something GHCR enforces.

- **A single-element closed-vocabulary tuple breaks a SQL `IN (...)` CHECK built with `!r`.**
  The established pattern for a closed vocabulary is `CheckConstraint(f"col IN {VOCAB!r}")` — and
  it is correct ONLY because every existing vocab (`RECONCILIATION_OBSERVATION_KINDS`,
  `RECONCILIATION_CONDITION_TYPES`, `WAIVER_RISK_CLASSES`, …) has ≥2 members. A **one-element**
  tuple's `repr` carries a trailing comma — `('todoist',)` — so `f"col IN {('todoist',)!r}"`
  renders `col IN ('todoist',)`, which is a **syntax error** in Postgres, not merely ugly. Build
  the list explicitly for a single-element (or any) vocabulary:
  `"col IN ({})".format(", ".join(f"'{v}'" for v in VOCAB))`, and apply the SAME construction in
  BOTH the model `__table_args__` and the Alembic migration (migrations inline a frozen copy of
  the tuple; they do not import the model constant). (Verified 2026-07-26, WS-P2.7 `TRACKER_SYSTEMS
  = ("todoist",)` — caught by the per-task review before merge.)

- **`make check` runs whole-repo architecture scans that per-task `ruff check` and the diff-scoped
  Stop hook never execute — beyond the ws32/ws33 word guards and the route-inventory family, two
  more will red a green-looking per-task change.** (1) **`test_unreachable_guards.py`** import-
  resolves every public `kernel`/`services` function and fails if one has no production entry
  point: a new service function must be reached by a real caller (a route, another service) or be
  made private / deleted — and "a test calls it" is explicitly NOT a valid reason (the guard's own
  message says so). A fix that removes a function's *only* caller silently orphans it, so **after
  any refactor that drops a caller, re-run this guard.** (2) **`test_wsp21_invariant_scan.py`** is
  the repo-wide outbound-egress scan: any file that imports an HTTP client (`httpx`, …) must be in
  `OUTBOUND_ALLOWLIST` with a reason, because the orchestrator is push-only. A new out-of-process
  runner/adapter package that legitimately speaks HTTP (e.g. `src/tracker_projection_adapter/`,
  like `src/reconciliation_runner/` before it) must (a) register its egress files in that
  allowlist and (b) ship its own isolation test asserting it imports nothing from `orchestrator.*`
  and confines its third-party deps. Both of these are whole-repo scans: only a full `make check`
  runs them, so a per-task loop can look green and still break CI. (Verified 2026-07-26, WS-P2.7 —
  both reddened the final gate after every per-task review passed.)

- **The architecture-guard family has a FIFTH member the bullets above omit:
  `test_cross_boundary_vocabulary.py` (WS-P2.16).** It AST-scans `src/orchestrator/` for every
  module-level string collection (≥2 str members: a set/list/tuple/dict-keys/`frozenset(...)`)
  that is used in an `x in S` membership test or `S.get(x)` — resolved ACROSS module boundaries by
  import, not by bare name. Each such vocabulary must be EITHER registered in `VOCABULARY_REGISTRY`
  (keyed `"<module-relpath>:<symbol>"`, value naming the cross-boundary source of truth) OR carry a
  `# not-a-vocabulary: <reason>` marker on its definition. A genuine cross-boundary vocabulary
  (one whose members must agree with another repo/subsystem) is REGISTERED, not marked exempt —
  registering it is the correct handling, and an exemption that would read "a legitimate second
  copy pinned elsewhere" means the predicate is wrong, not that the entry is justified. Structural
  exclusions the predicate already handles (do NOT try to register these): DB-`CheckConstraint`-
  pinned enums; derived/union collections (`frozenset(X["k"])`, `A | B` — not literals); and
  vocabularies validated by SET ALGEBRA (`set(x) - KNOWN`, `.issubset`, `<=`, column `.in_()`)
  rather than `in`/`.get`. It is a whole-repo scan (only `make check` runs it), so a per-task loop
  looks green and still breaks the final gate. (Verified 2026-07-27, WS-P2.7 Inc 2 — the tracker
  detector's `TRACKER_CLOSED_STATES = frozenset({...})`, a real cross-boundary mirror of the
  adapter's `TERMINAL_STATES`, reddened it; the fix was to REGISTER it, sync-guarded to the adapter
  set.)

- **Alembic revision ids must be ≤32 characters — `alembic_version.version_num` is `varchar(32)`.**
  A longer `revision = "…"` string does not fail at authoring time; it fails at RUNTIME when the row
  is stamped, with `psycopg2.errors.StringDataRightTruncation` / `value too long for type character
  varying(32)`, aborting `alembic upgrade`. Keep the descriptive-but-short form (e.g.
  `0019_wsp27_tracker_recon`, 24 chars — not `0019_wsp27_tracker_reconciliation`, 33). `down_revision`
  points at the prior head's real (already-valid) id, so only a NEW revision id can trip this.
  (Verified 2026-07-27, WS-P2.7 Inc 2 migration 0019.)

- **Four things the LOCAL recovery drills structurally cannot exercise, all found by running them
  against production on 2026-07-27 (ADR-0005 disposition A, 5/5 PASS).** The local harness seeds
  and asserts in ways production does not permit, so a green local suite is silent on all of these.
  (1) **`seed_unit`'s seeding ROUTES are UNREACHABLE in production — but the functions behind them
  are not dead, so be precise about which is which.** `POST /api/v1/revisions` and
  `/revisions/{id}/work-units` both call `_require_human` (`services/packages.py`) but sit on the
  M2M-only `orchestrator-api` Traefik router — so a browser gets 401 (identity stripped) and a
  SYSTEM bearer is rejected as non-human. **No actor can reach those two routes.** Their only
  callers are the `orchestrator register-revision` / `register-unit` CLI commands and
  `scripts/drill_common.sh`; the defaults `intake_source="manual_ws31"` /
  `activation_source="legacy_manual"` mark them as the WS-3.1 manual bootstrap path, superseded by
  intake → decomposition in WS-3.2. The *service functions* `register_revision` and
  `register_approved_unit` remain load-bearing — reached constantly via
  `services/package_intake.py` (POST `/package-intakes`) and `services/decomposition.py`
  (decomposition approval). So production units must be born through intake → decomposition →
  `/review` approval, and the two shipped CLI commands above cannot work against production at all.
  Corollary: an intake needs a genuinely
  approved intent package — `package.yaml` + `lineage.yaml`, `status == current_state == approved`,
  exactly one lineage approval whose hash equals `canonical_package_hash(package)`, plus a real git
  HEAD commit. It cannot be synthesized. The lighter `intake_purpose="protocol_fixture"` lane does
  NOT help: `packages.py` raises `protocol_fixture_not_executable` — fixtures can be intaken but can
  never create work units.
  (2) **Release-artifact binding validates `package_revision_hash` against the approved revision**
  (`release_artifact_package_hash_mismatch`). The local drill passes a synthetic `sha256:drill4` and
  succeeds only because its seeded revision matches by construction.
  (3) **`docker kill` does NOT auto-restart a container whose restart policy is `unless-stopped`** —
  the daemon records an explicit kill as a manual stop, so the policy deliberately does not fire.
  A crash drill must pair the kill with an explicit `docker start`; assuming the policy recovers it
  leaves production down (it did, ~2 minutes).
  (4) **A FAILED or COMPLETED unit is absent from `GET /api/v1/in-flight-units`**, which is the only
  read surface carrying `version` — as are DRAFT units. For any unit that is not in flight, POST with
  `expected_version: 0` and read `current_version` off the `version_conflict` error, then retry.
  That is the documented client contract, not a workaround. (Note the probe body must be otherwise
  VALID, or FastAPI 422s on schema validation before the service ever raises `version_conflict`.)
  Evidence: `~/docs/software-delivery-system/2026-07-27-production-recovery-drill-run.md`;
  per-drill production variants in `docs/operations/production-drill-adaptations.md`.

- **An M2M credential's `agent_id` is resolved against a registry bundle BAKED INTO THE IMAGE, and
  an unresolvable one is a boot failure, not a 401.** `_m2m_credentials` (`main.py:140`) calls
  `registry.resolve(agent_id)` at startup against `/app/registry-bundle.json`, built at image-build
  time from the security-standards tree at `security-standards.pin.toml`'s `revision`. So checking
  that an actor exists in git — even at exactly the pinned revision — does **not** establish that
  the running image carries it: the image may predate the pin. Ask production before writing the
  env var:
  `docker exec <container> python3 -c "import json;b=json.load(open('/app/registry-bundle.json'));print(b['source_revision'],[a['agent_id'] for a in b['actors']])"`.
  Getting this wrong fails **closed** on the next restart, which is the same outage shape as the
  WS-6.3 roles-before-credentials write. Verified 2026-07-27 (WS-P3.0) on image
  `8da4af3-wsp27inc2-amd64`: bundle revision `65655ddf…`, 13 actors, `drift-reconciler` present.
- **The traceability query's observation hop is unit-scoped, so most observation producers are
  invisible to it.** `services/traceability.py` filters observations on
  `subject_type="work_unit"` AND the unit id. An observation about a service, endpoint, monitor or
  environment — which is what every external monitor naturally produces — lands in
  `GET /api/v1/observations` and in nothing else. Do not treat "wired an observation producer" as
  "exercised the traceability chain's observation node"; WS-P3.0 wired the first producer and that
  node remains unexercised.

- **Migrating before the image swap puts the STILL-RUNNING old image into `/health/ready` 503
  `migration_drift`, and that is only survivable because neither health check consults
  `/health/ready`.** The readiness probe compares the code's expected head against the database's,
  so between `alembic upgrade head` and the Coolify swap the old container reports unavailable
  while `/health/live` stays 200 and traffic keeps flowing. Coolify's own health check is
  **disabled** (`health_check_enabled: false`) and the Dockerfile `HEALTHCHECK` probes
  `/health/live`. **If either is ever pointed at `/health/ready`, migrate-first becomes an
  outage** — the container would be killed as unhealthy mid-window. Keep the window short and do
  not "improve" the health checks without re-deciding the migration order. Verified 2026-07-28
  (WS-P2.8 deploy, ~4-minute window, no traffic impact).

- **A package that describes its own release recording cannot evidence that recording at
  adjudication time.** `record_release_artifact` raises `work_unit_not_completed` unless the
  implementation unit is already `COMPLETED` (`services/release_artifacts.py`), and follow-up
  minting additionally requires every unit of the revision to be settled — so the binding, the
  deployment observation, the traceability answer and the mint all necessarily happen *after* the
  unit whose ACs assert them has completed. There is no ordering that avoids this. Either put the
  recording ACs in a **separate, later package**, or accept the delegation deliberately: adjudicate
  on the ordering, say so in each rationale, and discharge the confirmation in the follow-up review
  unit the revision mints. **Do not reach for `waived` to express the caveat** — `waiver_invalid`
  requires *failed* evidence plus a risk class, follow-up and future expiry, so a waiver is not a
  general "accepted with reservations"; for judgment evidence the only honest outcomes are `passed`
  and `not_applicable`, with the caveat in the rationale. (Verified 2026-07-28, WS-P2.8 deploy.)

- **`deployment_observation` summaries are EXACT-key-set bounded, and the secret detector matches
  key NAMES, not just values.** `_require_keys` uses `set(payload).issubset(allowed)`, so any extra
  key is `deployment_observation_invalid: "… contains unbounded fields"`. The allowed sets are:
  `auth_summary` = `{missing_m2m_status, configured_m2m_status}` (and `missing_m2m_status` **must**
  be `401`); `route_summary` = `{routes}`, each route exactly `{path, present}`;
  `dispatch_summary` = `{dispatch_enabled}`; `status_summary` = `{status, summary}`. Separately, a
  key merely *called* `missing_credential_status` is rejected as
  `deployment_observation_secret_rejected` — the detector reads the JSON path, so avoid `credential`
  / `token` / `key` in key names even when the value is an integer. Every one of these is a clean
  `DomainError`, never a 500. Same for adjudication `expires_at`, which must carry a timezone
  offset. (Verified 2026-07-28, WS-P2.8 deploy.)

- **`/review/intakes/new` takes its idempotency key from the FORM, not from the pasted payload.**
  Re-submitting the rendered page is therefore a *replay* of the same intake, and a genuinely new
  registration requires reloading the page to mint a fresh key. The payload's own
  `idempotency_key` is ignored for this purpose. Do not debug an unexpected "duplicate" intake
  before checking whether the page was reloaded. (Verified 2026-07-28, the form's first real use.)

- **A REUSED `runner_attempt` makes dispatch a silent no-op that is indistinguishable from
  success.** `dispatch_unit` (`services/dispatch.py`) looks up `DispatchRecord` by
  `(work_unit_id, runner_attempt)` and, if one exists, **returns that existing record** — HTTP 200,
  `status: "dispatched"`, `reason_code: null` — **without triggering any `workflow_dispatch`.** The
  response is byte-shaped like a real dispatch; only the `id` differs, and only if you knew the
  prior one. The correct next ordinal is `_next_runner_attempt` =
  `max(unit.attempt_count, latest_runner_attempt) + 1`; read the prior ordinal from the last
  `dispatch.dispatched` event's `payload.runner_attempt` (a `UniqueConstraint("work_unit_id",
  "runner_attempt")` backs this). **Verify a dispatch by confirming a NEW record id and a new
  Actions run — never by the `status` field alone.** Note this compounds the already-documented
  independence of dispatch and claim ordinals: they drift apart the moment a dispatch is skipped or
  a claim is reclaimed, so "attempt_count + 1" is not a safe substitute. (Verified 2026-07-29,
  GAP-4 attempt 3 — the prior two dispatches were ordinals 1 and 2.)

- **THE BOUNDED DISPATCH WINDOW NO LONGER EXISTS. `ORCHESTRATOR_DISPATCH_ENABLED` is `true`
  PERMANENTLY** (Devon's standing decision, 2026-08-04, container-verified). **Do not open or close
  a window; there is nothing to close.** Every "open the window / close it after terminal" recipe
  elsewhere in this file is obsolete, and the restart hazard below is now a historical record of
  why the practice was retired rather than an instruction.
  The reasoning, because it reverses a long-standing ceremony. The flag is the **outermost of eight
  admission terms and the only one that is not per-unit**, and `dispatch_work_unit` has **exactly
  one caller** — `POST /work-units/{id}/dispatch`; no sweeper, no cron, no background loop — so an
  open window dispatches **nothing** on its own. A dispatch still requires `ready` state, an
  authority approval bound to the exact fingerprint (a human click), an allowlisted
  repo/change-class/capability, declared reach the estate agrees with, and the change window.
  Toggling, meanwhile, costs a restart, and a restart during a live run is the one thing that
  genuinely strands a unit. Per-run toggling was buying a gate that stops nothing the seven inner
  gates don't, while creating a hazard they cannot prevent. The other three gates are unchanged and
  still standing: `CLASSES=["dependency-update","maintenance-remediation","software-delivery"]`,
  `CAPS=["repo.edit","github.pr.create"]`, `REPOS=["AlobarQuest/intent-packages"]`.
  **The restart hazard itself is still real for ANY restart** — a release, an env write, a Coolify
  swap — so the rule survives in that form: never restart while a run is live.

- **[HISTORICAL — the window is now permanently open, see above] Closing the bounded dispatch
  window RESTARTS the orchestrator, and a restart while a dispatched
  run is live strands the unit. Close the window only after the run is terminal.** The dispatch
  gates (`ORCHESTRATOR_DISPATCH_ENABLED`, `..._ALLOWED_TARGET_REPOSITORIES`) are read at startup,
  so reverting them requires a restart — and the runner calls the orchestrator at the *end* of its
  run. On 2026-07-29 a window-close restart at `12:50:07Z` met the runner's `finalize-run` at
  `12:50:18Z`: three 503s in two seconds (`finalize-run`, cost-actuals emit, `fail-run`). Because
  **`fail-run` fails the same way**, the runner cannot even report the failure — a recoverable
  failure becomes a strand in `executing`, and the attempt is spent. **There is no safe gap to aim
  for:** the dependency-update coding action took **40 seconds** end to end (prepare `13:16:52` →
  submit `13:17:50`), so guarding only the *start* of the run (waiting for the claim before
  restarting) protects the wrong end. Terminal means all three: the Actions run concluded, the unit
  has left `executing` for `submitted`, and cost-actuals exist. Holding the window open is bounded
  by construction — dispatch admission requires a READY unit with its authority approval, so if the
  target unit is the only one in the system there is nothing else an open window can dispatch.
  (Verified 2026-07-29: attempt 2's tightly-optimised ~2.5 min window failed; attempt 3's ~13.5 min
  window succeeded. Window duration trades directly against run integrity.)

- **`gh search` cannot see PR comments on these repos — never measure the Evidence Pack marker with
  it.** `gh search issues "sds-evidence-pack in:comments"` returns 0 with the marker demonstrably
  present, and the index is stale enough that searching a term from a PR's own *title* also returns
  nothing. The Wave-2 clause-1 baseline was taken this way and was only coincidentally right (no PR
  existed at the time); the method reports 0 either way, so it is not evidence of absence. Count
  markers by REST enumeration instead:
  `gh api repos/<owner>/<repo>/issues/comments --paginate --jq '[.[] | select(.body | contains("sds-evidence-pack"))] | length'`.
  More generally: before treating a zero from any search API as proof of absence, run the same
  query against something you *know* is present. (Verified 2026-07-29, GAP-4 closeout.)

- **`budgets.max_llm_calls` does not constrain a running attempt — it gates the NEXT one.**
  **CORRECTED 2026-08-03 (WS-P2.31): there are TWO call paths, not one.** This used to say
  `is_over_budget` is "consulted only inside `_readiness_eligibility_error`". It is called at
  `claims.py:81` — directly inside `claim_unit`, where it additionally **halts the unit to FAILED
  and commits** before refusing, which the shared helper does not do — and at `claims.py:592`
  inside `_readiness_eligibility_error`. Both are claim-time. The substantive claim stands:
  it decides whether a unit may be *claimed again*, and **nothing checks spend mid-run**.
  GAP-4's envelope declared `max_llm_calls: 4` and attempt 3 recorded
  **15** (`attempt.cost_recorded`, 23 turns, $0.176) and completed normally. The practical cap on a
  single attempt is the workflow's `max_turns` literal, which is a separate number in
  factory-runner's workflow YAML and is not derived from the envelope. Read the field as
  "budget remaining before another attempt is allowed", not as a spend cap. (Verified 2026-07-29;
  call paths corrected 2026-08-03.) **A breach is now RECORDED** where the SLO report and the
  evidence pack can see it (WS-P2.31) — recording, never prevention: the orchestrator is push-only
  and cannot interrupt a running runner, and making the runner stop itself would be the runner
  attesting to its own compliance.
  **AUTHORING RULE, and it is `max_attempts × max_turns` — NOT a multiple of an observed run.**
  `max_turns` (a literal in factory-runner's workflow — `"40"` at revision `0e047df`) is the only
  thing that actually caps one attempt, so `max_attempts × max_turns` is the structural worst case.
  Setting the ceiling there guarantees the **recoverable** gate (`attempts_exhausted`, curable by
  `approve_retry`) binds before the **unrecoverable** one (`budget_exceeded`, curable by nothing —
  see the bullet above). Over-provisioning costs nothing: nothing checks spend mid-run.
  **Measured burn keeps beating the estimate: GAP-4 15, WS-P2.35 29, WS-P2.36 58** — the last a
  small additive test change. WS-P2.36's envelope was authored at 60 on the WS-P2.35 figure and
  raised to 120 by adversarial review before the human approval; the run then recorded 58, which
  would have left **two** calls for a second attempt and killed the unit permanently. An envelope
  is write-once and its approval cannot be taken back, so this number is one of the few that
  genuinely cannot be fixed later.

- **Coolify stores an `is_literal` env value wrapped in single quotes and injects the STRIPPED
  form — so a write must send the RAW value, and `is_build_time` is rejected outright.** Two
  independent traps in the same API, both hit during the GAP-7 password rotation. (1) The
  production `ORCHESTRATOR_DATABASE_URL` is stored as 111 bytes (`'<109-byte DSN>'`) while the
  container receives 109; POSTing the already-quoted form yields a **double-quoted** 113-byte
  value that still parses one layer down, so a naive readback check passes while the app would
  get a broken DSN. Verify by hashing what the CONTAINER receives
  (`docker exec … sh -c 'printf %s "$VAR"' | sha256sum`), never what the API returns. (2)
  `POST /api/v1/applications/{uuid}/envs` **422s on `is_build_time`** — `{"errors":
  {"is_build_time":["This field is not allowed."]}}` — the accepted spelling is `is_buildtime`,
  exactly as the GET response spells it. Because the reliable write path is
  delete-by-uuid + recreate, a 422 leaves the variable **absent**: validate an unfamiliar body
  against a throwaway key first, and retry rather than exit. (Verified 2026-07-29, GAP-7.)

- **Coolify-managed Postgres runs `local all all trust`, which is why a password rotation is
  survivable and why the backup lane is not a credential consumer.** `pg_hba.conf` in
  `postgres:16-alpine` under Coolify trusts the container's local socket, so (a) `docker exec …
  psql -U postgres` always works regardless of the password — a generated-then-lost credential is
  recoverable by a second `ALTER USER`, not an outage — and (b) vps-backup's
  `pg_dump_container` (`docker exec … pg_dump -U postgres`) needs **no password at all**, so it
  never appears in a credential-consumer inventory. Only TCP connections from other containers
  hit `host all all all scram-sha-256`. To prove a password is dead, probe over TCP against the
  container's network name (not `127.0.0.1`, which is also `trust`), and always pair the probe
  with a wrong-password control — otherwise a broken probe reads as a successful revocation.
  (Verified 2026-07-29, GAP-7.)

- **Never print a parsed component of a secret-bearing string; persist a generated secret before
  the mutation that depends on it.** Both rules were learned by breaking them in one session.
  `urlsplit()` on a value that fails to parse puts the ENTIRE raw string into `.path`, so a
  diagnostic printing `parts.path` leaked a live DB password to the transcript — emit sha256
  prefixes, lengths and booleans only. Separately, a rotation script that ran `ALTER USER` and
  only afterwards persisted the value left the database on a credential nobody held when the
  next step failed. Order is: generate → persist (0600 + clipboard) → mutate → verify.
  (Verified 2026-07-29, GAP-7.)

- **An all-terminal work-unit population does NOT imply an empty `/review` queue — the queue stopped
  keying on unit state in WS-P2.17 Increment 4.** It lists *pending human decisions*, and decisions
  are not only unit-shaped: an approved package revision with no breakdown in progress, and an open
  reconciliation divergence, are both queue entries with no work unit in a live state behind them.
  Verified in production 2026-07-31 immediately after the Inc 3–6 deploy: 42 units, **all terminal**
  (29 completed, 13 cancelled), 0 in flight — and **4 queue items** (one approved package awaiting
  breakdown, three divergences detected during the 2026-07-27 drills). HQ predicted an empty queue
  from the unit census alone and wrote that prediction into a deploy handoff; the queue was
  non-empty *because the new queue works*. **The general fault is reasoning about a subsystem from
  the inputs of the model it replaced** — the same shelf-life error that made an earlier plan cite
  `_adjudicatable_criteria` as it existed before Increment 4 moved it. When an increment changes
  what a surface is keyed on, re-derive expectations from the new key, not from the old census.

- **`record_approval` enforces NO lifecycle state, for either subject type — an approval's reach is
  bounded by what CONSUMES it, not by what the service refuses.** Verified 2026-07-31 against
  `services/packages.py`: its entire guard set is `_require_human`, `subject_type ∈ {authority,
  action}`, unit exists, the `dependency_update_authority_violation` check (authority only),
  idempotency replay, and `expected_version`. **A human can record either approval on a `cancelled`
  or `completed` unit and a row is written.** Nothing about the unit's state stops it. What bounds
  the approval is downstream: an `action` approval is fingerprinted to `unit.version` and satisfies
  exactly one guard on exactly one edge (`AWAITING_APPROVAL → READY`), and an `authority` approval is
  consumed only when a unit is admitted for work. So on a settled unit both are inert rather than
  refused. **Reading the route alone gives you the opposite impression** — HQ asserted in a WS-P2.17
  Inc 7 handoff that a cancelled unit's five action forms were "every one of which the service would
  refuse", and that was false for two of them. The `/review` page hides those two anyway, which is
  the one place it is deliberately narrower than the service; the justification is inertness, not
  refusal, and it is the increment's single judgment call.

- **`code-standards sync` is SAFE in this repo as of 2026-08-01 (WS-P2.25) — the prohibition below
  is lifted, and what made it necessary is worth keeping.** Ownership is now block-level
  (code-standards ADR-0008): each vendored file wraps the canonical content in
  `code-standards:managed:start`/`:end` markers, sync replaces the block and preserves everything
  around it, and **a file with no markers is locally owned, is never written, and is REPORTED on
  every sync**. This repo's `quality.yml` and `.github/dependabot.yml` carry no markers and each says
  so in a header comment; the `Makefile` now DOES carry a managed block (see below). Proven here,
  not assumed: a full `sync` on 2026-08-01 left
  `quality.yml` byte-identical (sha `6403063119dce3fd` before and after) with the brief-compatibility
  job present, and printed a `LOCAL:` line for all three. **Before trusting any sync, run
  `code-standards sync --dry-run` — it reports, per file, exactly what sync would do, from the same
  classifier sync uses.**

  Two consequences specific to this repo. (1) **RESOLVED 2026-08-01 (PR #113): the `Makefile` was
  `local` only because it was the pre-WS-P2.24 template — skip-semantics, no refusal on a missing
  tool — not because anyone chose to own it. It has been adopted; the gate now REFUSES.** Adoption
  was a measured no-op (rc=0, 1883 collected, 1882 passed, zero `skipping` lines, before and after);
  what changed is the failure mode. **Proving that required a control with BOTH conditions, and
  neither alone discriminates here:** `ruff`, `pyright` and `pytest` are all on this machine's
  **global** PATH (`~/.local/bin`, homebrew), so removing `.venv` leaves `command -v` succeeding;
  and the Makefile re-prepends `$(CURDIR)/.venv/bin`, so a scrubbed PATH alone is undone. A tree with
  **no `.venv`** under **`env PATH=/usr/bin:/bin`** gives the real differential — old: rc=0 with four
  `skipping` lines; new: rc=2, `make check: ruff not found`. The global-PATH half is specific to this
  machine and is the easier one to miss.
  (2) `.shellcheckrc` was absent and sync created it, so `make check` now runs shellcheck here.
  Verified clean under the Makefile's actual invocation (`find . … -exec shellcheck {} +`, all files
  in one call, relative paths from the repo root) with a positive control proving the sweep fires.
  Note a probe using absolute paths from another cwd reports 5 spurious SC1091s — measure under
  conditions of use.

  **The rest of this bullet is the pre-WS-P2.25 record. It explains why the mechanism exists.**

- **Dependabot's `pip` ecosystem does not update `uv.lock` — a repo that locks with uv and declares
  `pip` gets a feed of PRs that are unmergeable from birth, and nothing reports it.** Under `pip`,
  Dependabot edits `pyproject.toml` alone; `uv sync --frozen` then fails on the stale lockfile and
  CI reds. The signature is a *repeated* bump of the same pin: this repo's #46, #61 and #74 are
  three proposals to move the same `ruff` pin off `0.15.20`, none landed, and the pin was still
  `0.15.20` when PR #113 switched the ecosystem to `uv` on 2026-08-01. **Read a stack of
  same-version dependabot PRs as a broken producer, not as a busy one.** Eight repos in the estate
  already declared `uv`; `code-standards`' generator keys this on the dependency **manifest**
  (`uv.lock` → `uv`, else `pyproject.toml`/`requirements*.txt` → `pip`) since WS-P2.25, but this
  repo's `dependabot.yml` is locally owned — it also tracks github-actions and docker — so `sync`
  could not reach it. A locally-owned file does not receive that fix; check it by hand.
  `pip` remains correct for a repo that genuinely uses `requirements.txt` (`brain`, `Contacts`) —
  the defect is the mismatch, not the word `pip`. This one matters beyond hygiene: Dependabot is the
  input source the SDS was built to consume, so the factory's intended feed was broken at the source.

- **[SUPERSEDED 2026-08-01 — see the bullet above] DO NOT run `code-standards sync` in this repo. It
  re-vendors TEN files, including
  `.github/workflows/quality.yml`, and would destroy this repo's CI.** Verified 2026-08-01 against
  `code_standards/initrepo.py:99-106`: the `pairs` list vendors the TS configs, `.shellcheckrc`,
  `.editorconfig`, `Makefile`, `.pre-commit-config.yaml` **and
  `.github/workflows/quality.yml`**. It is not a Makefile-only operation, and its own template header
  ("Edit upstream and `code-standards sync`") reads as though it were.

  This repo's `quality.yml` carries content that exists nowhere else and that a sync would silently
  replace with the generic template: the **WS-P2.23 "Runner consumer compatibility" job** (the build gate
  that makes runner/orchestrator drift unshippable — the entire deliverable of that workstream), the
  **`postgres:16-alpine` service**, **`SECURITY_STANDARDS_DIR`**, both database URLs, and
  **`uv run alembic upgrade head`**. Without those, `make check` cannot run here at all (see the
  invariant above: a bare clone fails ~18 tests for exactly this reason), so the loss would present as
  a mysteriously broken suite rather than as a missing file.

  WS-P2.24 synced nine repos and **deliberately excluded this one, `security-standards` and
  `infraops-mcp-server`** for this reason. Its handoff instructed "run `code-standards sync`, then
  `make check`" per repo; followed literally here it would have deleted the previous day's work, and
  only the build session noticing the ten-file `pairs` list prevented it.

  **The hazard here is EXACTLY ONE FILE, and the rest is safe** — classified 2026-08-01 by diffing
  all nine verbatim-vendored files (sync writes 9 copied + 3 generated/merged, and only those a
  repo's declared languages call for). Of the two that differ in this repo:
  - `Makefile` — **pure drift, safe to overwrite.** Its entire local content is one character:
    `export PATH :=` versus `PATH :=`, and the template adopted `export` upstream. Nothing to
    preserve.
  - `.github/workflows/quality.yml` — **local ownership, never re-vendor.** 177 lines of divergence;
    structurally a different file, with no stale template content left in it to refresh.

  So **hand-copying the Makefile is safe today**; it is `quality.yml` alone that must never be
  replaced. Do not let the blanket prohibition above be read as "this repo cannot track the
  template" — it can, minus one file.

  **The upstream unblock** — SHIPPED 2026-08-01 as block-level ownership (ADR-0008), not the
  file-level declaration this paragraph asked for. File-level was rejected because it converts
  *clobbered* into *silently stale*: `security-standards` was already effectively file-level-owned
  and its `check` recipe consequently had **no shellcheck step at all**, which nobody decided. The
  consumer set this paragraph put at five files across four repos was undercounted — the portfolio
  dry-run found **12 locally-owned files across 6 repos** (13 once the generated `dependabot.yml`
  joined the same model), including `brain`'s Makefile, which had hand-rolled the block mechanism in
  a comment, and **four repos carrying a byte-identical stale `quality.yml` that nobody had chosen
  to own** — file-level ownership would have frozen all four in place and called it a decision.

- **`reach` is a DECLARED SET of what work touches when it runs — not a severity, not a change
  class, and not where the work executes.** WS-P2.18 Inc 1, ADR-0009,
  `src/orchestrator/reach_vocabulary.py`. Four members: `source_repository` (writes land in a git
  repo and nothing outside it changes until something separately acts on the result),
  `live_estate` (something already serving changes — hosted app or its DB, the VPS, DNS, **the
  orchestrator itself**), `external_system` (a system of record this estate does not run and cannot
  put back on its own), `operator_machine` (runs on, or writes to, Devon's machine). Four properties
  that are each load-bearing: it is **declared by the package author, never inferred** (R8 — an
  inferred value trades a loud failure for a quiet one); it is a **set**, because real work touches
  more than one thing (5 of 24 packages need two members, and `~/.claude` packages are both
  `source_repository` and `operator_machine` — the repository IS the machine); composition is
  **intersection-of-permission**, so adding a member can only ever NARROW; and **absence is
  `unknown`, never "reaches nothing."** There is deliberately **no `orchestrator_self` member** —
  the orchestrator is `live_estate`, and self-update keys on reach **plus a second dimension**.
  **`reach_from_snapshot()` is the single reader; do not read the snapshot yourself.**
  **Execution locus is a DIFFERENT dimension from reach, and is unmodelled ANYWHERE — there is no
  partial precedent to build on.** Reach describes what work *touches*; execution locus would
  describe where it *runs*, and a job can execute on a CI runner while touching Devon's machine.
  **CORRECTED 2026-08-02 (WS-P2.18 Inc 6): this bullet previously offered `local-heavy` in
  `intent-packages/routing-policy.toml` as the existing execution-locus dimension. That is wrong.**
  `local-heavy` is one of eight `[[surface]]` entries and is a MODEL-ROUTING key —
  `{id = "local-heavy", models = ["fable-5"], rationale = "Work routes here because it is the hard
  kind (multi-repo, deep context)"}`. It selects which LLM handles a class of work and says nothing
  about where anything executes; the name misleads, which is how this error survived several
  handoffs. See the fuller correction later in this file. Do not conflate reach with execution
  locus, and do not assume the latter has any precedent.

- **`reach_from_snapshot()` was FAIL-OPEN for its first increment, and the test that should have
  caught it asserted the right intent in prose while checking only the case that passes trivially.**
  It did not return `None` for unrecognised shapes — it **filtered unknown members out**, so
  `["source_repository", "invented"]` read as *"touches a repository and nothing else."* Increment
  1's report asserted the fail-closed behaviour, HQ repeated it into Increment 2's handoff, and
  Increment 2 found the truth by reading the code. Fixed there, with a discriminating test.
  **The general lesson is the transmission vector, not the bug: every build report in this
  programme is written by a session that verified its OWN work and restated its predecessor's from
  prose.** Four inherited-claim errors have now occurred at increment boundaries in one workstream
  (`is_expansion`-era guard claims, "14 of 24", the `None` fallback, "the consumer, singular").
  When a handoff or report states a behaviour, `grep` it before building on it — including when HQ
  wrote it.

- **NO authored package declares `reach` — it is 0 of 24, not "14 of 24" as an earlier report
  said** (verified 2026-08-01 by grep across `intent-packages/packages/*/package.yaml`).
  Two consequences that pull in opposite directions. (1) The WS-P2.18 Inc 3 known-good mechanism is
  **inert and therefore safe**: it recognises nothing, so the authority gate fires exactly as it did
  before, and switching it on costs one `reach:` line in one package. (2) When WS-P2.18 Inc 4 binds
  refusals into *admission*, `reach_undeclared` refuses **the entire population**, not a legacy
  subset — so Inc 4 is a factory-halting change unless it ships an answer for undeclared reach.
  Note the information already exists: Inc 1's census (`tests/fixtures/reach_census.json`) classified
  all 24 packages from their own declared `profile` / `profile_fields` / `outcome.what` /
  `deliverables`. The open question is therefore not *"where would reach come from"* but *"is a
  derivation trustworthy enough to admit work on"* — which R8 answers `no` for new packages and
  leaves open for grandfathering existing ones.

- **`factory-policy.toml` can only ever REFUSE, and that is a structural guarantee, not a
  convention.** WS-P2.18 Inc 2, ADR-0010. `refusals_for(reach)` returns why policy objects; empty
  means *no objection*, which is **weaker than "go ahead"** — permission is the conjunction of every
  admission term, and the hard off-switch (`ORCHESTRATOR_DISPATCH_ENABLED`) is one of them.
  **No value in the schema permits anything and `factory_policy.py` imports nothing from config, so
  policy cannot see the off-switch and nothing written in the artifact can widen what it allows.**
  Do not add a permission field to "simplify" a later increment: WS-P2.18 Inc 3 wanted exactly that
  (a known-good pattern is inherently permissive) and expressed it instead as a **withheld
  refusal** — every envelope draws `authority_envelope_novel`, and a declared pattern withholds that
  objection. **Total coverage** pins the artifact to `REACH_VOCABULARY`: exactly one row per member,
  and a new member with no row **stops the document loading** — a document that does not load
  permits nothing. `SUPPORTED_SCHEMA_VERSIONS` is an **exact set, not a floor**. The editing
  contract is in the file's own header: a new field is an additive version bump made *only in the
  same commit that teaches the loader and ships the code reading it*, because a field with no
  consumer is a second copy of a value that still lives somewhere else.

- **Changing policy costs a release, and a release restarts the orchestrator — the artifact is read
  per call but its bytes arrive with the image.** WS-P2.18 Inc 2 separated two things that read as
  one: *getting new bytes to the process* (a release) and *making the process notice bytes it has*
  (free — `factory-policy.toml` is re-read per call, never cached). Only the second was decidable at
  that increment, and it is the half that cannot be retrofitted. **Operational rule: change policy
  only when no run is live** — the Actions run concluded, the unit out of `executing`, cost-actuals
  recorded. This is the same rule closing a bounded window already imposes, and for the same reason:
  the runner calls back at the *end* of its run, `fail-run` fails the same way `finalize-run` does,
  and a restart in that window strands the unit with its attempt spent.

- **An unhashable value in a membership test is an unhandled HTTP 500, not a validation error.**
  Found reviewing WS-P2.18 Inc 3's own diff: a reach member that is not hashable made the
  membership check raise `TypeError`, and since only `DomainError` and `APIAuthenticationError` have
  registered handlers (`main.py`), it surfaced as a bare 500 from the human gate. Pre-existing, now
  guarded. This is the same class as the WS-P2.3 findings — **any route-reachable parse or
  membership test must raise `DomainError`, never let the stdlib raise** — and it generalises: when
  validating a value of unknown shape, `in`/`set()`/`dict[...]` are as dangerous as
  `uuid.UUID(bad)`.

- **83% of this repo's test runtime is per-test schema rebuild, and no test runs in parallel.**
  Measured 2026-08-01: `migrated_engine` (`tests/services/conftest.py`, re-exported by
  `tests/persistence`, `tests/api` and `tests/web`) is **function-scoped** and does
  `DROP SCHEMA public CASCADE` → `CREATE SCHEMA` → **a full `alembic upgrade head` over 21
  migrations** for every test that requests it. One rebuild is **0.554s**; **305 test functions**
  take it, which is **169s of a 203s local suite** (~620s of ~750s in CI). Separately,
  `pytest-xdist` is a declared dev dependency that is **never invoked** — there is no `addopts`, so
  nothing is parallel locally or in CI. The fix is a session-scoped schema plus a fast per-test
  reset, and the reset must be **`TRUNCATE ... RESTART IDENTITY CASCADE`, not transaction
  rollback**: services commit internally by design, and a persistence assertion must re-read through
  a DIFFERENT session, neither of which survives being wrapped in an outer transaction. Only after
  that is xdist safe, keyed per worker (`orchestrator_test_gw0…`), because the fixtures drop and
  recreate the database and two suites must never share it.

- **`coolify` is a forbidden runtime string literal in `src/orchestrator/`, in addition to the
  `dispatch` / `deploy` / `merges` tokens documented above.** WS-P2.18 Inc 1 reddened both
  `test_ws32_scope_guards` and `test_ws34_scope_guards` on the phrase "a Coolify application or
  database" in a **description string**. The full ws32 forbidden sequence list is `factory-event/v1`,
  `merge_pull_request`, `workflow_dispatch`, `factory-runner`, `production mutation`, `auto_merge`,
  `productionmutation`, `coolify`, `dispatch`, `deploy`; ws34 adds `gh pr merge`,
  `git push origin main`, `merge_to_main`. Compounds tokenize, so `post-deploy` matches `deploy`.
  **Reword; never add an allowlist entry.**

- **"Withholding a refusal" is NOT "softening a requirement" — where the consumer reads an empty
  answer as permission, withholding DELETES the requirement.** WS-P2.18 Inc 4. The handoff specified
  grandfathering as "a withheld `reach_undeclared` refusal", which is the correct idiom for the
  policy artifact (ADR-0010) and a **fail-open** if applied inside `authority_refusals`: no reach →
  no row → no pattern consulted → fall-through, and admission reads that as the human gate **lifted**.
  Grandfathered units would have dispatched with no envelope examined by policy or by a person. The
  fix was to split the questions — reach became its own admission term, with grandfathering applying
  only there, and `human_authority_gate` left untouched. **Before expressing anything as a withheld
  refusal, find every consumer of that refusal set and check what each does with empty.** The idiom
  is safe only where empty means "this policy raises no objection" and some *other* term still has to
  say yes.

- **The grandfathering table deletes itself, which couples it to deployment: if its last live
  revision settles while the table still ships, the artifact stops loading and recovery needs a
  release.** WS-P2.18 Inc 4's rule names an explicit list of revision ids (never a date — a date can
  still absolve a package created before it but decomposed after), and `require_live_subject` raises
  once no listed revision can still produce work, so a spent rule forces its own deletion rather than
  becoming a permanent hole. Its entire live subject as of 2026-08-01 is **one** revision:
  `wsp211-conformance-kit` rev 1, `f921c842…`. Two consequences. (1) **Decomposing and settling that
  revision is a factory-halting act while the table is deployed** — do it only in the same change
  that removes the table. (2) A listed id the database has never seen reads **live**, deliberately,
  so a restored or empty database cannot halt the factory on missing rows; "spent" requires positive
  proof that units exist and are all stopped.

- **Minted follow-up units are created in `AWAITING_REVIEW` and are not normally admitted at all.**
  So requiring reach at mint time is right for a different reason than "they will be admitted": the
  point is that minting is the last moment a human is in the loop before the unit exists, not that
  the unit dispatches straight away. Note also the denominator — **7 approved revisions carry a
  `follow_up` block** (not 17 packages, which is a different set that `mint_due_follow_ups` does not
  iterate). Because minting now refuses rather than inheriting an unknown reach, none of those 7 can
  mint until reach is supplied — which is also why the grandfathering list needs one entry instead of
  twenty-one.

- **A lapsed lease does not merely permit a second claimant — it stops the FIRST worker recording
  what it already did.** `validate_active_claim` (`services/claims.py`) raises `claim_not_active`
  when `claim.lease_expires_at <= now`, and it is shared by evidence recording and PR-binding
  reporting. So shortening a lease is a correctness hazard, not a scheduling preference: a run that
  outlives its hold cannot submit its own evidence, and the natural first design — *give the fastest
  reach the shortest hold* — is the one that breaks runs. This is why the WS-P2.18 Increment 6 policy
  lease may only ever **lengthen** (`kernel/leases.py` bounds it strictly above `DEFAULT_LEASE` and
  at or below `LEASE_CEILING`) and why a reach set composes by **maximum**, the opposite arrangement
  from the change window, which composes by intersection. Both compose toward more restraint;
  restraint points the other way for a hold than it does for an hour. Neither the WS-P2.18 spec nor
  the Increment 6 handoff mentioned this, and without it the direction looks arbitrary. See ADR-0013.

- **`work_units`' lease has THREE writers, not the two the reclaim trap suggests.**
  `claim_unit`, `renew_claim` and `reclaim_expired_claim` → `_perform_reclaim` →
  `_acquire_reclaimed_claim` (`services/claims.py`), all now reading
  `services/lease_policy.py::claim_lease`. The documented trap is the third — reclaim never calls
  `claim_unit`, so a per-claim rule placed only there is ignored on exactly the path a lapsed lease
  leads to. **`renew_claim` is the one that gets forgotten after that**, because it extends rather
  than grants: a renewal that reset the hold to the kernel default would silently undo a considered
  one, on the path a long-running attempt takes by definition. Any future per-claim rule must name
  all three. Separately, `RENEWAL_CADENCE` (a 5-minute constant in `kernel/leases.py` since WS-3.1)
  had **no reader in either repository** and was deleted in Increment 6 — factory-runner renews only
  on an explicit `local-heavy-renew` command, so nothing renews on a cadence at all.

- **Policy 4 (self-update) is HALF SHIPPED and the shipped half is easy to re-litigate.** WS-P2.18
  Increment 5 already answered *when* the orchestrator may update itself: `live_estate`'s
  `change_window` governs it and its own rationale says so ("a restart is invisible at 03:00 and is
  an outage at 15:00"). What is open is *to what* — and there is no subject to hang it on. A
  self-update has no package, no revision and no enforcement snapshot, so `reach_from_snapshot` has
  nothing to read; `authority.constraints.target_repository` is a per-**work-unit** constraint and a
  self-update has no unit; and nothing models "which image tag production may be moved onto"
  (`security-standards.pin.toml` is a build input, `release_artifact_bindings` binds a completed
  unit, `deployment_observations` records a deployment after the fact). Note also that
  `local-heavy` in `intent-packages/routing-policy.toml` is **not** an execution-locus dimension —
  it is one of eight `[[surface]]` entries selecting which LLM handles a class of work — so the
  second dimension has no partial precedent either. Do not add an `orchestrator_self` reach member,
  and do not compute a self-update refusal onto the policy report: it would be a second copy of the
  `live_estate` window row, which that same response already serves.

- **The architecture-guard family has a SIXTH member, and it is keyed on a FILENAME appearing in
  any `.py` — including in a docstring that only points at the file.**
  `tests/services/test_factory_policy.py::test_no_second_copy_of_the_artifact_values_exists_in_the_source_tree`
  asserts the policy artifact has **exactly one reader** in `src/orchestrator/`
  (`readers == [MODULE_PATH]`), and it cannot distinguish a module that *loads* the artifact from
  one that merely *names* it in prose. WS-P2.18 Inc 8 reddened it on a single docstring sentence
  saying where a human goes to declare a pattern. **Reword; never allowlist** — the guard protects
  exactly the one-reader property, and the natural place for that human-facing pointer is the
  Jinja template, which the guard does not scan. The same test also forbids any row's rationale
  (first eight words) appearing in the source tree. Note this is a *different* trap from the
  ws32/ws33 word guards: those forbid a vocabulary, this one forbids a filename, and reading the
  failure rather than guessing which guard fired is the only reliable way to tell them apart.

- **Adding a module under `src/orchestrator/` adds TWO collected tests by itself, so
  "baseline + tests I wrote" always under-predicts the collected count.**
  `tests/architecture/test_wsp21_invariant_scan.py` parametrizes
  `test_no_tracked_source_carries_a_secret` and `test_nothing_in_the_repo_merges_a_pull_request`
  **one case per source file**. WS-P2.18 Inc 8 added 17 tests to a 2148 baseline and collected
  **2167**. Reconcile a collected-count discrepancy by diffing node IDs between `main` and the
  branch (`pytest --collect-only -q | grep :: | sort`, then `comm`) rather than explaining it away
  — the two extra lines carry the new module's path in their parameter id, so the diff names them
  outright.

- **An absence-keyed marker absolves the future; a DATE-keyed marker certifies it. Both are the
  same mistake, and only the first one is documented.** WS-P2.18 Inc 4 established that "rows with
  no marker are construction-era" never expires and silently exempts everything written later. The
  mirror image cost Inc 8 a design decision: a cutoff date laid down **while the defect it marks
  is still live** asserts that rows after it are clean, which is false, and it is more dangerous
  precisely because it looks like diligence. `/review` still reads the actor from the forward-auth
  header, so **every** authority approval — past and future — is equally unattributable, and there
  is no instant at which that changes until a mechanism ships. **A boundary is sound only if the
  population on its CLEAN side is actually clean**; otherwise the boundary belongs to the change
  that closes the hole, whose own record cannot be forged backwards. See ADR-0014.

- **The gate-cleared population is smaller than the unit census, and reasoning from the census
  overstates it.** On 2026-08-02 there were 43 work units and **35** with a human authority
  approval bound to the unit's current fingerprint. The eight without are the ones no human ever
  gated: generated post-deploy verification units, a minted follow-up unit, and the three WS-P2.15
  units. Any question of the form "how has the gate performed" has 35 as its denominator, not 43 —
  the WS-P2.18 Inc 8 handoff used 43 and overstated the evidence base by a fifth.

- **A claim is NOT released when its work succeeds — so "unreleased and long lapsed" describes most
  of the estate's history, not a stalled unit.** `release_claim` is called only from the failure,
  cancel, reclaim and expired-claim-recovery paths; a unit that completes leaves its claim row
  behind with `released_at IS NULL` and a `lease_expires_at` receding into the past forever.
  Measured 2026-08-02: **29 of 43 production units carry such a claim, and every one of those units
  is `completed` or `cancelled`.** Any predicate over claims must therefore gate on the UNIT's state
  (`claims.CLAIM_HOLDING_STATES` = `{claimed, executing}`, the write path's own definition of
  "has an active claim") and on the NEWEST attempt. WS-P2.19's first formulation did neither and
  would have reported the whole history of the estate as stalled on day one. Corollary for the
  reverse direction: a `released_at IS NULL` clause has no reachable case of its own — every
  `release_claim` caller transitions the unit out of those two states in the same transaction — so
  adding one can only ever HIDE a unit some future path has stranded.

- **A lapsed lease is TERMINAL for its attempt: there is no window in which a recovery action races
  a worker that was about to report.** `renew_claim` refuses a lapsed claim (`lease_expired`) and
  `validate_active_claim` refuses its evidence and PR-binding writes (`claim_not_active`), so the
  moment the hold ends the worker is locked out permanently — a renewal cannot rescue it. The
  WS-P2.19 handoff warned that a detector "can destroy work that was about to be reported"; that is
  false at every instant such a detector can observe, because the work became unreportable at the
  lapse. Same conclusion (do not auto-reclaim, do not auto-fail — WS-P2.19 reports only) for a
  materially different reason, and the difference matters: read literally the warning argues for a
  LONGER grace to protect work that is in fact already gone. It also means no in-band signal of
  life survives a lapse, so no stall report can distinguish a hung worker from a live one — only
  the narrower claim, *this attempt can no longer report anything*, is available and it is true
  either way.

- **Build sessions run in their own git WORKTREE, with their own venv and their own test database.
  HQ keeps the main tree — and that split is forced, not preferred.** A session's shell cwd resets to
  its launch directory between tool calls, and the diff-scoped Stop hook runs from that cwd, so a
  session **cannot relocate itself**; only the person launching it can. Until 2026-08-02 every
  handoff said *"Repo: `~/Projects/orchestrator`"*, putting HQ and the build session in one tree.
  That cost three false Stop-hook blocks in a single day — a build session's uncommitted
  work-in-progress attributed to HQ, each needing an audited `CODE_STANDARDS_BYPASS=1` — plus a
  CLAUDE.md batch held for hours and a running "the tree is busy" sequencing tax.
  **Recipe, proven 2026-08-02** (created, ran 38 tests, torn down; shared `orchestrator_test`
  untouched, and the worktree invisible to the main tree's `git status`):
  `git worktree add .worktrees/<ws> -b <branch> main` · `uv sync --frozen` in it ·
  `createdb -h 127.0.0.1 -U postgres orchestrator_test_<ws>` · export `TEST_DATABASE_URL` and
  `ORCHESTRATOR_DATABASE_URL` at that database and `SECURITY_STANDARDS_DIR` at
  `$PWD/tests/fixtures/security-standards` · `uv run alembic upgrade head`. Teardown is
  `git worktree remove … --force` + `dropdb`.
  **`.worktrees/` is ALREADY in `.gitignore` and already in the Makefile's `PRUNE_DIRS`** — the
  convention was anticipated and simply never used. **The per-worktree DATABASE is the half that is
  easy to miss and not optional:** `tests/conftest.py` drops and recreates whatever
  `TEST_DATABASE_URL` names, so two sessions sharing one database corrupt each other *regardless of
  which tree they are in* — a worktree alone fixes the Stop hook and leaves the real hazard intact.
  `conftest` reads that variable from the environment, so no code change is needed. (An
  `orchestrator_test_task6` database already existed, so somebody improvised this once without it
  becoming convention.)
  **TEARDOWN HAS A DEFINED POINT IN TIME, and it is the END OF THE SESSION, after the report is
  written** (Devon, 2026-08-14, after a morning in which three merged worktrees, five test databases
  and one stray file had accumulated). It is three steps and all three matter:
  **(1) Check the MAIN tree, not only your worktree.** `git -C <main tree> status --porcelain` must
  show nothing you created. The cwd-reset trap puts writes there while the session works correctly
  in its worktree, and the diff-scoped Stop hook lints untracked files at the *session's* cwd — so a
  fragment left behind blocks **whoever stops next**, not its author. On 2026-08-14 one such file
  blocked two sessions on work neither had written.
  **(2) Remove the worktree and drop the test database** — `git worktree remove … --force` +
  `dropdb`. **(3) Leave the branch and the pull request**; HQ merges, so the branch must survive.
  **The objection to answer, because a session will raise it: "leave it up in case CI sends me
  back."** Recreating is fully scripted and takes about three minutes, HQ owns the merge and
  therefore owns any CI failure, and a genuine second attempt wants a fresh tree from current `main`
  anyway. Standing by is the exception and needs to be asked for, not assumed. Note the Agent tool's `isolation: "worktree"` covers subagents a session
  spawns and does nothing for a session opened in a terminal, which is the case that was hurting.

- **A claim is NOT released when a unit COMPLETES — only on failure and cancellation — so
  "unreleased claim" carries no information about whether anything is wrong.** `release_claim`
  (`services/lifecycle.py`) is called with `terminal_reason="work_unit_failed"` and
  `"work_unit_cancelled"` and for nothing else; success leaves the row unreleased. Verified in
  production 2026-08-02: **29 of 43 units carry an unreleased claim whose hold lapsed days ago, every
  one of them on a finished unit.** WS-P2.19 found this by asking production before building, and it
  killed the obvious stall predicate — *unreleased claim + lapsed hold* would have reported the
  estate's entire history on its first run. It also means `released_at IS NULL` has no reachable
  failure case to guard, so a test asserting it passes for the wrong reason. Whether the asymmetry is
  intentional is undocumented; the claim is inert on a terminal unit, so it is harmless in itself —
  the damage is entirely in predicates built on it.

- **R13's "merging a PR deploys nothing" is TRUE OF COOLIFY AND FALSE OF THE ESTATE — a repo can
  redeploy itself, and inspecting the deploy target cannot see it.** R13 verified `change-manager`
  from Coolify's own settings (`source_id: null`, `source_type: null`,
  `manual_webhook_secret_github: null`) and concluded a landed PR is inert. That check is correct and
  answers the wrong direction: it establishes that **Coolify will not PULL on push**, not that
  **nothing PUSHES to Coolify**. `change-manager/.github/workflows/deploy.yml` runs on
  `push: branches: [main]` and its `build-and-deploy` job ends with a step named
  *"Trigger Coolify redeploy"* that curls a `COOLIFY_DEPLOY_WEBHOOK` secret. Confirmed empirically
  2026-08-02: merging PR #40 fired a `deploy` run **two seconds later**.
  **Consequence for `reach` (ADR-0009): a package author cannot honestly declare reach without
  reading the TARGET REPO'S OWN workflows.** The first pattern-recognised unit declared
  `reach: [source_repository]` and asserted in `scope.excluded` that *"a landed pull request is inert
  until something separately triggers a deployment"* — false for that repository. The unit itself was
  not misrouted (its work was opening a PR, which genuinely is inert), but **the merge is
  `live_estate` work**, and it happened outside the 02:00–06:00 window `live_estate` declares.
  Harmless in that instance and merged knowingly; the determination METHOD is wrong for every repo
  that self-deploys. Backlogged P1 `c99a4e598506`.
  **CORRECTED 2026-08-02 (WS-P2.29): this bullet used to end "treat 'does merging deploy?' as a
  question about the source repository's CI, never about the deploy platform's configuration."
  That is ALSO wrong — it is the same error pointing the other way.** Determining the answer for
  all 25 registered apps found **three independent trigger mechanisms**, and reading CI sees one
  of them: (1) a workflow step that calls the deploy target; (2) a **repository webhook** pointed
  at the deploy target — `AlobarQuest/booking-system`'s `test.yml` runs tests and nothing else, so
  a CI scan concludes inert while every push redeploys it through Coolify's manual webhook
  endpoint; (3) the **hosting platform's own git integration** — `AdjustRight-Photo-Pro` is built
  from `main` by Cloudflare Pages with no workflow and no webhook *in the repository at all*, so
  neither the CI nor the repo's webhooks reveal it. Checking any single surface fails closed in
  one direction and fail-OPEN in the other. **Do not derive this on demand: read it from App
  Brain** — `GET https://app-brain.devonwatkins.com/api/apps/default-branch-landing?github_repo=Owner/Repo`
  returns `redeploys` | `inert` | `unknown`, folded over every registered app fed by that
  repository (`AlobarQuest/brain` feeds four), with `unknown` for a repository nobody assessed and
  `matched_apps` so `inert` never arrives without its denominator. A read-only credential exists
  for exactly this call (`APP_BRAIN_READ_KEY`, BWS `726a18ba-7a38-4ecc-aa03-b49a015fd302`): it
  authenticates GET on app-brain's two read paths and is 401 everywhere else, including `/mcp`.
  **CORRECTED 2026-08-04 (WS-P2.36) — this used to say `intent-packages` and `project-standards`
  "have no App Brain app record at all" and answer `unknown` / `no_app_record`, which a fail-closed
  consumer refuses. That is false for `intent-packages`**, which answers **`landing: "inert"`** with
  a dated evidence string (determined 2026-08-02) and `matched_apps: 0`. A repository-level
  determination exists without any app record, so `matched_apps: 0` does **not** imply `unknown` —
  and the estate admission term passed on the first attempt for the WS-P2.36 dispatch, which it
  could not have done had the old claim been true. Do not infer the answer from the app census;
  **ask the route**, which is the bullet's own advice one paragraph up. **`project-standards` also
  answers `inert`** — measured 2026-08-09, correcting this bullet's own "was not re-checked and may
  still answer `unknown`".
  **The registry holds 21 repositories as of 2026-08-09 and `factory-runner` is the one factory-
  adjacent repo still absent** — it answers `unknown` / `no_app_record`, which a fail-closed
  consumer refuses. That is harmless only because ADR-0015 makes it not a factory target;
  `security-standards`, which IS one, was in the same state until WS-P3.7 Increment 4's dry run
  found it and it was recorded `inert` the same day. **A repository can hold a caller workflow, a
  `FACTORY_PR_TOKEN` and an allowlist entry and still be un-landable**, because the estate never
  determined what landing on it does — that is a fourth onboarding step, invisible until an
  admission term refuses. The determination itself is cheap and must read all three mechanisms:
  every workflow, the repository's webhooks, and the hosting platform's own git integration.
  Evidence: `~/docs/software-delivery-system/2026-08-02-wsp229-build-report.md`, the WS-P2.36
  report, and the WS-P3.7 Increment 4 dry run.

- **The `Alobar SDS Dispatch` App has NO `checks` permission — the Checks API is 403 for the
  orchestrator, and named-check evidence is read from workflow JOBS instead.** Measured 2026-08-02
  from production's own credential. **UPDATED 2026-08-09: the permission set is now
  `{'actions': 'write', 'contents': 'write', 'metadata': 'read', 'pull_requests': 'write'}`**
  (app `4259746`, installation `145535298`, `repository_selection: all`), granted for ADR-0020.
  `checks` is still absent, so everything below stands unchanged — only the merge capability was
  added, and `administration` is absent too, so **branch protection is 403 to this App: whether a
  merge would be blocked can only be learned by attempting it, never by reading the protection
  settings.**
  **`pull_requests: write` alone CANNOT merge — a merge writes a commit to the base branch, so it
  needs `contents: write` too.** Measured 2026-08-09 (WS-P3.7 Inc 2): with `pull_requests: write`
  only, `PUT /repos/{repo}/pulls/{n}/merge` returned **403 `Resource not accessible by
  integration` on a pull request GitHub itself reported `MERGEABLE/CLEAN`** — which is what
  isolates the cause to permission rather than to protection, and the trap is that the identical
  403 on a *red* pull request reads like the safety property working. With `contents: write`
  added, the same call on the same two pull requests answers **405 `Required status check
  "Quality" is failing.`** on the red one and **200 `merged=True`** on the green one. So branch
  protection does bind a GitHub App, and the App now carries a write that reaches every
  repository in the account (backlogged `880ba73ecc24`).
  At the 2026-08-02 measurement the installation carried exactly
  `{'actions': 'write', 'metadata': 'read'}`, so
  `GET /repos/{repo}/commits/{sha}/check-runs` answers **403 Resource not accessible by
  integration** while `GET /repos/{repo}/actions/runs?head_sha=` and `/actions/runs/{id}/jobs`
  answer 200. WS-P2.20's observer (`services/github_checks.py`) therefore reads Actions jobs, which
  is every check this estate produces — a check run published by any OTHER application is invisible
  to it and refuses rather than guesses. **Two consequences.** (1) `check_name` must be the **job**
  name, not the workflow name: in this repo both are `Quality`, but in `change-manager` the workflow
  is `Quality` and the job is `Lint, type-check, and test`, and naming the workflow yields
  `named_check_not_found`. (2) An App's *token-mint response* reports its own `permissions`, so
  asking what a credential may do costs one call and never needs the private key locally:
  `POST /app/installations/{id}/access_tokens` → `permissions`. Do not infer an App's reach from
  what it is already used for — triggering a run (`actions`) and reading a check (`checks`) are
  different permissions. (3) **ADDED 2026-08-08: the APP and the INSTALLATION carry separate
  permission sets, and only the installation's is the credential.** Granting a permission on the
  App raises a *request*; the installation owner must accept it before any minted token carries
  it. Observed in the gap: `GET /app` reported `pull_requests: write` while
  `GET /app/installations/{id}` still reported `{'actions': 'write', 'metadata': 'read'}`, so a
  check written against `/app` would have said "done" and the token could not have merged
  anything. Read the installation, or the mint response, never the App. And confirm the
  permission *does* something — `GET /repos/{repo}/pulls` answered 403 before and 200 after —
  because a reported permission and a functioning one are the same class of difference.

- **`test_ws34_scope_guards` forbids the literal `github.actions`, and the CLAUDE.md list of ws34's
  forbidden strings omits it.** The full set in
  `test_ws34_adds_no_factory_runner_or_workflow_dispatch_code` is `workflow_dispatch`,
  `factory_runner`, **`github.actions`**, allowlisted only in `services/dispatch.py`,
  `api/routes.py`, `api/schemas.py`, `config.py` — a different and *smaller* allowlist than ws32's.
  WS-P2.20 reddened it on a constant whose value was `"github.actions.jobs"`; reworded to
  `"github.workflow_jobs"`. This is a *substring* match on the lowercased file text, not the
  whole-token tokenizer ws32 uses, so `github.actions.jobs` matches where `deployment` would not.
  Reword; never allowlist. (The separate ws34 list this file already documents — `coolify`,
  `gh pr merge`, `git push origin main`, `merge_to_main` — belongs to a *different test* in the same
  module.)

- **`evidence` rows are append-only at the DATABASE level, so a test that mutates a stored payload
  must do it in memory and never commit.** A `reject_append_only_mutation()` PL/pgSQL trigger raises
  `IntegrityConstraintViolation: evidence is append-only` on any `UPDATE`. The established pattern
  (`test_named_check_evaluator_revalidates_all_payload_bounds`) assigns `evidence.payload = …` on the
  ORM instance and calls `evaluate_criterion` directly — never `session.commit()` and never through
  `verify_work_unit`, which commits. A payload-corruption test written the obvious way fails on the
  trigger rather than on the assertion, which reads as a bug in the change under test.

- **`git stash` does not stash UNTRACKED files, so a stash-based control against `main` is
  contaminated by any new file the branch adds.** WS-P2.20's collected-count reconciliation measured
  `main` at 2187 rather than the true 2185, because the new `src/orchestrator/services/` module
  stayed on disk through the stash and
  `tests/architecture/test_wsp21_invariant_scan.py::PYTHON_SOURCES` is
  `sorted(SRC.rglob("*.py"))` — a **filesystem** walk, not `git ls-files`. So the new module's two
  parametrized scan cases were counted on both sides and silently cancelled out. Use
  `git stash -u` (or `git archive HEAD`) for any control that must not see the branch's new files.

- **The reach check needs `ORCHESTRATOR_APP_BRAIN_URL` and `ORCHESTRATOR_APP_BRAIN_READ_KEY` in the
  environment, and without them it fails closed SILENTLY.** WS-P2.28. Absent, admission refuses with
  `reach_estate_source_unconfigured` — correct behaviour, but nothing is stranded and nothing
  complains until somebody routes work and wonders why it will not run. **Both variables must ship
  with the release that first carries the check**; verify them from inside the container after the
  swap, the way `ORCHESTRATOR_M2M_*` is verified. Use the **read-only** key WS-P2.29 provisioned,
  never the full-access one.

- **App Brain's landing route answers with TWO fields and never 404s — `reason` is load-bearing, not
  decoration.** `GET /api/apps/default-branch-landing?github_repo=Owner/Repo` returns a value plus a
  reason, and an unregistered repository is a normal `unknown` + `no_app_record` response rather than
  a missing resource. So `no_app_record` (nobody registered it) and `not_assessed` (registered,
  nobody determined it) are **different states that a consumer must be able to tell apart** — a check
  keyed on the value alone cannot distinguish "not an app we know" from "an app we never looked at".
  The route also **folds multiple app records**: `AlobarQuest/brain` is one repository serving four
  applications and resolves as one answer. HQ's WS-P2.28 handoff described three landing values and
  that read as the whole contract; it is not.

- **`REACH_VOCABULARY` must stay a dict of STRING LITERALS — naming a single member silently blinds
  the cross-boundary scanner to the entire vocabulary.** `test_cross_boundary_vocabulary` finds
  vocabularies by AST-scanning for module-level string collections. Key one member by a named
  constant instead of a literal and the collection stops matching the pattern, so the registry loses
  its view of **all** of it — not just the renamed member. The failure is silent in the direction
  that matters: the guard stops guarding and nothing says so. Caught by that guard during WS-P2.28.
  **The fix is a pinned duplicate, never an allowlist entry** — an exemption here would read "a
  legitimate second copy", which is the predicate being wrong rather than the entry being justified.

- **`_blocked_reason` normalizes the authority envelope exactly once, and a test enforces it.**
  `services/dispatch.py`. Any new admission term that needs the unit's target repository must be
  evaluated **inside** `_blocked_reason`, not computed by the caller and passed in — a second
  normalization is a second reading of the envelope, and the envelope is what a human's authority
  approval attests. WS-P2.28 added the reach term inside it for this reason.

- **[CLOSED 2026-08-03 by WS-P2.32 — the FIRST half only. Read the closing note at the end of this
  bullet before relying on it.]** **A VERIFIER credential could drive a unit to COMPLETED with ZERO
  evidence rows — the completion
  guard reads adjudications and structurally cannot read evidence.** `_completion_satisfied`
  (`services/lifecycle.py:473`) takes `(required_ac_ids, adjudications, occurred_at)`: there is no
  evidence parameter, so completion is decided on adjudication rows alone. `_authorize_outcome`'s
  VERIFIER branch (`services/evidence.py:966`) is `allowed = outcome in NON_WAIVER_OUTCOMES` with
  no evidence requirement, and `_validate_adjudication_fields` demands evidence only for `waived`
  (a `failed_evidence_id`) — `passed` needs a rationale string, and `evidence_id` is validated only
  when non-null. `(SUBMITTED→COMPLETED)` is a verifier-held edge. So POSTing `passed` with prose on
  each required AC completes the unit, and **everything WS-P2.20 built — the App-token observation
  of the named check, unanimity, `failed_closed` on divergence — is bypassed by one POST.** The
  `orchestrator-verifier` credential is standing in production. This may well be intentional (the
  verifier as an out-of-band trusted actor) but nothing in the code says so, and it makes every
  WS-P2.20 guarantee conditional on a credential that also holds the bypass. The second half of the
  same hole: **the reconciliation lane detects reality CHANGING, never reality having been
  MISREPORTED** — `_detect_check` (`reconciliation_detection.py:321-342`) needs a prior *observed*
  success at the armed head before it will report a failure as a flip, and a claim that was never
  observed leaves no such prior, so the predicate is False and the detector returns silently
  without even incrementing `skipped_correlations`. There is no downstream net under this.
  (Found by WS-P2.31 2026-08-03, independently re-verified by HQ the same day.)
  **CLOSING NOTE, WS-P2.32 (`52d7d7e`).** The bypass is shut: **a verifier adjudication may only
  arise from `verify_work_unit`**, and a direct POST is refused as a named
  `verifier_evaluation_required` — *"the role is not the problem, the route to it is."* Two things
  survive and are the reason this bullet is superseded rather than deleted.
  **(1) The measurement, which shows it was not a latent hole but standing practice:** of 70 current
  adjudications, **36 of 59 verifier adjudications came through the bypass across 12 units**, 17 of
  them on `ac_id`s with **no evidence row at all**, and **three completed production units hold zero
  evidence**. Those 18 historical completions are left as they are — terminal, nothing re-evaluates
  them, and back-dating a judgment about them is the mistake ADR-0014 names. The practice stopped on
  2026-07-27 of its own accord, when WS-P2.17 Inc 2 gave the human the case it was being used for.
  **(2) The SECOND HALF IS NOT CLOSED.** The reconciliation lane still detects reality *changing* and
  never reality *having been misreported*, so there is still no downstream net under a false claim —
  narrower now (the evaluator runs against real evidence rows) but not gone, because worker-recorded
  evidence is itself attested. Backlogged P2; do not read the closure as "completion now rests on
  observed fact."

- **`budgets.max_attempts` is DECORATION; the enforced cap is `unit.max_attempts`, a different
  value on a different column reached from a different API field — and the name collision is what
  makes it invisible.** The envelope budget is parsed (`kernel/authority.py:105`), contributes to
  the authority fingerprint the human approves, and **has no enforcement reader**: the only
  `.budgets.` access in `services/budget.py` is `max_llm_calls`. What actually bounds attempts is
  the `work_units.max_attempts` column (`persistence/models.py:235`, defaulted from
  `DEFAULT_MAX_ATTEMPTS` via `services/packages.py`), checked at `claims.py:79`, `:552` and `:590`,
  and raised by `authorize_retry` with no reference to the envelope at all. Nothing ever compares
  the two. **CORRECTS an earlier claim of HQ's** — a WS-P2.31 handoff asserted "`max_attempts` IS
  enforced, so one of the two budget fields is decoration", offered as the contrast that made
  `max_llm_calls` look like the outlier. Both halves were wrong: both envelope budget fields are
  decoration, and this is the worse of the two because the name collision hides it. (Verified
  2026-08-03.)

- **The conformance anti-tautology rule is PROSE, not code — and the branch it would guard has
  never been reached in production.** `services/dispatch.py`'s conformance gate carries a docstring
  saying `accepted_standards` "must come from a real waiver source … never echoed from
  `standards_touched`, or the subset branch below admits everything." **Nothing enforces it.** The
  gate is `if status == "green": return None` / `if touched and touched <= accepted: return None` /
  `return "conformance_not_green"`. Two consequences. (1) Anyone told "the anti-tautology precedent
  already exists in this repo" will go looking for a check that is not there — there is **one**
  exemplar of the observed-not-attested move (`services/github_checks.py`, WS-P2.20), not two.
  (2) **Do NOT close it by deleting the green short-circuit.** Green claims then fall through to the
  subset test, which is False whenever `accepted` does not cover `touched`; WS-P2.31 measured 28 of
  28 production conformance blocks as `status: "green"` with **0 echoes**, and the canonical
  cross-repo fixture `tests/fixtures/runner_authority_envelope.json` is
  `green / touched=['project'] / accepted=[]` — so the pinned envelope shape **both repos agree on**
  would be refused and every dispatch would stop. HQ proposed exactly that removal on 2026-08-03
  and was wrong; the error was checking what a permit stops permitting without reading what the
  fall-through then does — the WS-P2.18 Inc-4 withheld-refusal fail-open, mirrored into a
  fail-closed halt. Closing it honestly means the orchestrator OBSERVING conformance, which it
  structurally cannot: `compute_conformance_claim` needs a repository checkout, the orchestrator is
  push-only and checks out nothing, and the only other producer is the runner — i.e. the runner
  attesting to its own compliance. Backlogged P2 `7874128ae3ac` with a named trigger.

- **`GET /api/v1/status-ledger` defaults `include_inactive=false` and every production unit is
  terminal, so the bare call returns `[]`.** `routes.py:1217` / `services/status_ledger.py:25,95`.
  This is the most misleading read on the production API surface, because an empty list **looks
  like an answer** rather than like a filter — the same shape as the estate-wide rule that a search
  zero is not evidence of absence. Pass `include_inactive=true` when the question is "what does the
  ledger hold", and reserve the bare call for "what is live now". (Verified 2026-08-03.)

- **`Adjudication.evidence_id` and the adjudicating actor's ROLE are readable from NO production
  API — a measurement written from the obvious surfaces matches ZERO events and reads as "no
  adjudication cites evidence", which is false and alarming.** `GET /work-units/{id}/history`
  filters `Event.subject_id == unit_id`, and an `adjudication.recorded` event's subject is the
  **adjudication**, so the command payload — which carries both fields — never appears there. The
  evidence pack projects `failed_evidence_id` and not `evidence_id`. The sound substitutes, both
  used by WS-P2.32: **`decided_by`** for the actor, and **an adjudication on an `(unit, ac_id)` with
  no evidence row provably carries `evidence_id = NULL`** (from `_validate_evidence_reference`'s
  subject check) as a lower bound. A WS-P2.32 handoff asked for "whether the referenced evidence row
  is the current evidence-chain head" and that is **not obtainable from production at all** — HQ
  specified a measurement the read surface cannot answer. Backlogged P2.

- **To tell a `verify_work_unit` adjudication from a hand-written one, classify the RATIONALE — and
  the machine set has CHANGED OVER TIME, so a classifier keyed on today's strings misreads history.**
  The verifier writes the evaluator's own `reason`, a closed set.
  `"named check and assertions passed"` was the machine reason from `9f86cf7` (2026-07-15) until
  WS-P2.20 (`8e13258`) replaced it with `"the named check was observed to conclude success"`. Keying
  only on the current string misclassifies **nine** production adjudications as hand-written.
  `git log -S "<the string>"` before assuming a vocabulary is stable — this is the same class as the
  three vocabulary mismatches documented above, in the one place where the vocabulary is a
  *historical* record rather than a live contract.

- **`ReconciliationCondition` has exactly ONE production read surface — the traceability chain's
  `conditions` hop — so nothing can corroborate a divergence from a second surface.** No `/api/v1`
  GET exists (only the two `POST …/detect` routes); a `reconciliation.required` event's subject is
  the **condition**, not the unit, so it never appears in a unit's evidence-pack `events`; the
  release pack carries revision/units/release_artifacts/deployments; `consistency-check` reports
  evidence-head, completion and waiver findings only; the SLO report has no reconciliation metric;
  and `graduation_ledger` counts them per unit but is reachable only from `/review` HTML.

- **But the revision-anchored and unit-anchored traceability answers are DIFFERENT query paths and
  can disagree — so "no second surface" is not "no second reading".** `resolve_anchors` branches,
  and the reconciliation runner writes on a schedule, so asking the unit-anchored query about the
  same units is a genuine second reading rather than a restatement. **Concluding that production
  serves no corroborating surface, and stopping there, is what shipped WS-P2.41's severe defect** —
  a carrier scan that failed to exclude the release's own units, so a release whose unit carried a
  divergence its chain omitted PASSED, citing that very divergence as proof it had none. A
  discriminator that consumes the thing it is meant to detect is the sharpest form of the
  correct-about-the-wrong-noun family; reproduce it under a passing exit before fixing it.

- **The wave-exit manifest breaks in TWO directions and they exit DIFFERENTLY — verify the one you
  mean.** Truncating the **plan's** bar (the authoritative text loses a clause while the manifest
  still declares it) reaches **exit 3 `PIN BROKEN`** with every clause suppressed — measured three
  times, and it is the WS-P2.39 acceptance test. Truncating the **manifest** (dropping a clause,
  or lowering `clause_count` to match) never reaches the pin at all: it is refused at load by
  `clause_count`, then by the separator-accounting guard, **both exit 1**. Rewording a clause also
  reaches `PIN BROKEN`. A WS-P2.41 correction stated only the manifest direction and read as a
  blanket denial of exit 3; both halves are true of different edits. **Collision worth deciding:
  a manifest that fails to LOAD exits 1, the same code as "a clause was measured and is not met" —
  a broken tool and an honest failure are indistinguishable by exit code.**

- **A control written before a fix can survive the fix and stop discriminating.** Two of WS-P2.41's
  21 mutations initially survived because controls written hours earlier pinned behaviour the fix
  changed, and passed either way. Re-run the mutation set *after* the last behavioural change, not
  once when the tests are written — a green control is evidence only about the code it was last
  run against.

- **A derived constant cannot pin the judgment that lives inside it.** WS-P2.40 shipped 86 tests
  over an eight-hop traceability chain where every fixture built itself by iterating `ALL_HOPS` —
  so the fixtures shrank with the list, and review *measured* that dropping `conditions`, or
  `intent`, or `commit`, left all 86 green. The hop list WAS the judgment the workstream existed
  to make, and it was pinned by nothing. Where a value is both the judgment and the fixture
  generator, it needs a **literal** assertion of its members plus a second assertion that the
  consumer emits exactly them; neither alone catches what the other does. This is WS-P2.39's own
  lesson recurring inside its successor — a tool built against reasoning-from-a-summary whose
  central judgment was derived rather than declared.

- **A shape guard placed one level too high fails OPEN while looking like the fix.** Same
  workstream: a guard added *specifically* to stop an absent key becoming a verdict was attached to
  the report rather than to the metric under judgment, so a `budget_breach` object that lost its
  `status` key read as instrumented. Its sibling: `len()` on whatever a hop lookup returned, where
  `len("sha-abc")` is 7 — a scalar read as a populated hop. **Name the exact value the verdict is
  computed from and guard THAT, not its container.** Same family as the WS-P2.18 Inc-4
  withheld-refusal fail-open and WS-P2.34's `KNOWN_FIELDS` mis-keying: a check that is correct
  about the wrong noun. Note both of these fail OPEN, where all seven defects the author found
  himself failed closed — which is what adversarial review is for.

- **In `wave-exit-manifest.toml`, a clause rationale goes in `note`, never in `proves`.**
  `run_command_check` writes `proves` into the retained record **only when the check passes**, so a
  rationale placed there on a `fail` or `unavailable` clause is dead text by construction — which
  covers every clause that currently needs one. `_attest_clauses` writes `note` unconditionally.
  Corollary: **nothing dated or result-specific belongs in either field** — the day the check
  passes, the record would assert both the pass and a note saying it does not. Dated numbers belong
  in a build report. (HQ prescribed `proves` in the WS-P2.40 handoff and was wrong.)

- **"Not applicable" is a distinct answer from "not met", and this estate has now rediscovered
  that in THREE subsystems in one week.** `repo.protection` reported `violation` for private repos
  on a plan that does not offer the feature, which made a Wave-3 clause look unsatisfiable until
  project-standards PR #14 taught it `not-applicable`. ADR-0015 asks for the same treatment of
  `runner.caller` on a repo deliberately declared not-a-factory-target. And now the traceability
  chain's `conditions` hop: `ReconciliationCondition` records *a divergence between pushed reality
  and stored lifecycle state*, so requiring it makes the clause satisfiable only by a release that
  went wrong. **HQ ruling 2026-08-05: a hop that can only be populated by something going wrong is
  NOT required of a healthy release** — the chain must be able to carry conditions (the query must
  join them), and their absence on a clean release is the correct answer, not a missing hop. The
  probe has no per-hop `not_applicable` yet; until it does, `conditions` is a known
  over-requirement and must not be silently deleted. **Generalise: whenever a check reports a
  binary verdict over a population, ask whether some members can never satisfy it for reasons that
  are facts about the world rather than defects.**

  **FOURTH instance, 2026-08-13, on a different axis: DELIBERATELY REFUSED is distinct from COULD
  NOT BE MEASURED.** The estate-landing agent classified a pull request held on
  `landing_pace_exhausted` (the daily budget spent) or `landing_outside_change_window` (the clock)
  as a finding, driving exit 3 — so the one control watching autonomous landings reported *"something
  could not be measured"* about three things it had measured perfectly well and refused on purpose.
  Devon's ruling: **a deliberate refusal is not a finding.** The mechanism already existed for the
  adjacent case (`_SETTLED`), which is the tell — when a control needs a second suppression set, ask
  whether the first one's SEMANTICS transfer. Here they do not: `_SETTLED` is tested with
  INTERSECTION, correct because a settled subject's other refusals are meaningless, and copying that
  shape for deliberate refusals silences every co-occurring real condition (`pace_exhausted`
  co-occurs on every held pull request once the budget is spent). Deliberate refusals need SUBSET
  semantics — not a finding only when EVERY refusal is deliberate. Note the ruling does **not** turn
  the control green: `#48` remains held forever on `landing_update_type_unparseable`, the ADR-0018
  requirement-range gap that was decided and deliberately left. **Devon closed that second question
  the same day with a SECOND, different ruling: a record that cannot land under CURRENT POLICY is an
  EXCEPTION, not a finding** — *"while WE can learn from it, it's not a finding that we would expect
  the system to ever auto-correct."* Keep the two as separate named categories rather than one
  suppression set: a deliberate refusal WILL clear at the next window, an exception NEVER will and
  waits on a person, and collapsing them says "quiet" about both while losing which is which.

  **THIRD RULING, 2026-08-14, and it exists because the SECOND fix created it: freshness beside an
  exception is itself non-finding.** Once the lane brings up to date the branches it stales, it
  deliberately declines to freshen a pull request that can never land — so a permanent exception
  permanently acquires `landing_head_not_current_with_base` as well, and under the two-category rule
  that resurrects it as a finding forever. `change-manager#48` is the live case: the increment's own
  correctness reproduces the permanently-red control the first ruling was made to prevent. Devon's
  ruling: **a refusal the system produced by deliberately declining to act carries no information a
  reader could act on.** Freshness is therefore suppressed WHEN AND ONLY WHEN an exception is
  present — never generally, or `{head_not_current_with_base, checks_not_clean}` would go quiet,
  which is a real condition and must stay a finding.
  **KEY IT ON THE EXCEPTION, NOT ON THE LANE'S DECLINING — those read as the same rule and are
  not.** The build session sharpened this and it is the load-bearing distinction: the lane declines
  to freshen **anything it cannot clear**, including `landing_checks_not_clean`, so a rule keyed on
  *"we chose not to freshen it"* silences a failing check. **The discriminator is DURABILITY: red
  checks can go green, an exception never clears.** HQ's handoff gave the weaker formulation.
  Shipped 2026-08-14 as `#168`; measured live against production on the same two subjects minutes
  apart — `#48` reads `held` on `main` and `exception` on the branch, and the agent exits **3**
  and **0** respectively. The nightly control is green. **Confirmed in production overnight
  2026-08-15: the scheduled 02:15 run reported `#48` as `exception` and the job exited 0** — the
  first time that control has been green in a real run rather than in a differential. Note the shape, because it will recur: **each
  fix in this family has generated the next category**, and each time the fail-open is the
  over-general version of the correct rule.

  **What `pace_exhausted` actually is, since it reads like a failure and is not: one landing per
  repository per occurrence of the change window.** Record 52 landing `#50` at 05:17 consumed
  `change-manager`'s landing for that night, so every sibling pull request reported it for the rest
  of the window and none of them was in any way wrong. It resets when the window reopens.

  **And `landing_update_type_unparseable` is not a parser defect** — `update_type_of`'s own
  docstring says a requirement-range or grouped bump is *"correctly unlandable by this lane: neither
  states a single delta that any rule about update types could be applied to."* `#48`
  (`update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1`) is `mergeable=clean` and simply
  unclassifiable; a human merges it. Before calling such a refusal a bug, read the function that
  emits it — this one documents its own intent.

- **Copying a derivation pin transfers the MECHANISM, not the PROPERTY — and the difference is
  whether the pinned artifact has one decomposition or many.** WS-P2.39 built an exit manifest
  pinned to the program plan's prose exit bar, copying the three existing pins (the envelope
  contract, the brief contract, WS-P2.38's routing policy). All three pin **JSON**, where
  byte-identity and structural identity coincide: one document, one parse. **Prose has many
  byte-identical decompositions.** The manifest's clause split was the entire point of the tool,
  and the first pin protected the bar's *text* while leaving the *decomposition* unguarded —
  three separate routes let a clause vanish with the bar still hashing identically, a fourth
  because the pin was line-scoped so a clause appended on the next line was invisible, and nothing
  asserted the clause COUNT. Found by adversarial review, not by the author, who had just written
  a tool against reasoning-from-a-summary and then did exactly that inside it. **When copying a
  pin onto a new artifact type, ask what the pin must make unique — not what the exemplar hashed.**
  Two fail-opens in the same review are worth remembering as a pair: an all-`not_applicable` run
  reported success having demonstrated nothing, and a clause could be excused while its checks ran
  and their failure was discarded.

- **The architecture-guard family has a SEVENTH member: `tests/architecture/test_drill_scripts.py`,
  and it is the one that catches drill dishonesty.**
  `test_a_drill_changes_state_only_through_the_public_api` forbids `INSERT|UPDATE|DELETE|TRUNCATE|
  ALTER|DROP` via `scratch_sql`/`docker exec` in any `scripts/drill-*.sh`, and its sibling
  `test_only_the_lease_helper_may_write_sql` pins harness SQL writers to **exactly**
  `["expire_lease"]` — so the obvious workaround is closed too. That single exception is warranted by
  **wall-clock impossibility** (`DEFAULT_LEASE` is 15 real minutes, there is no override, and policy
  may only lengthen it), which is far narrower than "this is only fixture setup" — do not reach for
  it as precedent. It fired on WS-P2.32's first drill fix, which seeded criteria with SQL, and was
  right to. Note it is absent from every guard inventory in this file until now: an inventory of
  guards is itself a vocabulary that drifts.

- **The WS-3.1 bootstrap lane (`POST /api/v1/revisions`) could declare WHICH `ac_id`s a revision
  requires and never what any of them WAS — producing a required criterion decidable by NO actor.**
  `human_may_adjudicate(None, …)` refuses an absent criterion by design and `load_required_criteria`
  raises `verification_subject_invalid` for the whole revision, so such units were completable
  **only** through the verifier bypass — which is what drill 4 was quietly demonstrating for months.
  Closing the bypass exposed it rather than causing it. WS-P2.32 gave the lane an optional
  `acceptance_criteria` list with three guards, each of which is the interesting part: the declared
  set must **equal** the required set (a subset recreates the very shape the feature eliminates while
  looking equipped), the `evidence_type` must be in `SUPPORTED_CRITERION_EVIDENCE_TYPES` (the
  *other* writer of `package_acceptance_criteria` enforces it, and disagreement between two writers
  of one table is silent), and a divergent restatement on re-registration is **refused** rather than
  silently dropped. The shape cannot occur on the intake-born path: intake derives the snapshot's
  list *from* the criteria, so the two cannot disagree.

- **[CLOSED 2026-08-03 by WS-P2.33 (`04e98cd` here, factory-runner #38). Read the closing note —
  the fix's SHAPE was forced, not chosen.]** **factory-runner required `constraints.mutation_commands`
  whenever `command.run` was allowed —
  UNCONDITIONALLY — while the orchestrator required it only for `change_class:
  "dependency-update"`. So the orchestrator ADMITTED envelopes the runner REFUSED, and nothing saw
  the disagreement until a real dispatch.** Orchestrator:
  `kernel/runner_authority.py::dependency_update_authority_violation` opens
  `if envelope.change_class != "dependency-update": return None`. Runner:
  `factory_runner/authority.py::_validate_commands` requires it under
  `if _allowed(envelope, "command.run")` with no change-class condition. Measured 2026-08-03: a
  `maintenance-remediation` unit cleared **every** orchestrator admission term — readiness `ready`,
  authority approved, change class allowed, target repo allowed, reach admitted — and the run died
  in 14 seconds with `AuthorityError: constraints.mutation_commands must be a non-empty list of
  non-empty strings`.
  **The mismatch is the symptom; the model is the finding. The runner assumes the MUTATION IS A
  COMMAND.** That fits `dependency-update` (`uv add`) and does not fit edit-shaped work, where the
  diff is produced by the coding agent and no command mutates a tracked file — so there is no
  honest value for the field. A fig-leaf entry (`uv sync`, which only touches `.venv`) would make
  the envelope lie about what mutates, and **the envelope is what a human's authority approval
  attests.** This blocked Wave-3 exit criterion #1 for every non-dependency-update software profile.
  Note the envelope AND the brief are both pinned cross-repo contracts and **neither pinned this
  rule**, which is exactly why byte-identical fixture tests stayed green while the two sides disagreed.
  **CLOSING NOTE (WS-P2.33).** One predicate now lives in both repos
  (`kernel/runner_authority.py`, renamed `runner_command_authority_violation`; runner
  `_validate_commands`): `allowed_commands` required whenever `command.run` is allowed for EVERY
  class (the early return had skipped it — the same defect one field over); `mutation_commands`
  required iff `change_class == "dependency-update"`; a present `mutation_commands` always
  validated (well-formed + subset), any class; absent outside dependency-update = valid, runner
  derives `()`. **The conditional is keyed on `change_class` because the frozen pilot envelope
  left no other discriminator** — shapes (b)/(c) from the handoff each needed a positive field a
  fingerprinted, unre-authorable envelope could never gain, so they were foreclosed by the
  acceptance test itself, not judged inferior. Pinned by a SECOND byte-identical golden fixture
  (`runner_authority_envelope_edit.json`, `CONTRACT_SHA256_EDIT = 90b73de6…`) with rule-level
  positive AND fires-negative tests both sides, each demonstrated to red under a one-sided
  loosening — the byte pin alone provably cannot catch a rule disagreement. Direction invariant,
  stated in the kernel docstring: the orchestrator may be STRICTER than the runner, never looser.
  Proven end-to-end 2026-08-03: package revision 4 unit `327920cd` completed via
  intent-packages#55 (+1/−1 caller-pin diff, named check observed green).

- **A push that touches `.github/workflows/**` requires the `workflow` scope (classic PAT) or
  Workflows: Read-and-write (fine-grained) on the pushing credential — and `FACTORY_PR_TOKEN`
  lacked it, which killed two pilot units AFTER their coding and verification succeeded.** The
  rejection is remote (`! [remote rejected] … refusing to allow a Personal Access Token to create
  or update workflow … without workflow scope`), arrives only at finalize's `git push`, and every
  caller-pin remediation — the maintenance-remediation profile's founding queue — edits exactly
  such a file. Three traps inside the fix: GitHub Actions secrets are WRITE-ONLY, so nothing can
  confirm which token a secret holds (Devon's first in-place scope edit landed on a classic token
  while the secret held a fine-grained one); fine-grained PATs do NOT report `x-oauth-scopes` on
  API responses (that header is classic-only), so the settings page is the only scope check; and
  the token had NO BWS record at all (P1 `237b8599e7a1` — it now does: `a3240c2e…`, SDS Operator
  project). **The pattern that broke the loop: verify the credential with a DISCRIMINATING PROBE
  before spending a work unit** — a throwaway branch workflow that pushes a workflow-file-touching
  commit using the secret costs one minute and no units (probe run `30842959171`).

- **`budgets.max_llm_calls` is a write-once ratchet that environmental failures consume exactly
  like real work — and `budget_exceeded`'s named recovery (`approve_retry`) CANNOT cure it, so an
  over-budget unit is permanently dead.** `is_over_budget` sums `attempt.cost_recorded` across ALL
  attempts against the fingerprinted envelope's ceiling and gates claim, requeue, and reclaim;
  the ceiling has no mutation path (write-once envelope). `retry-authorization` refuses while
  attempts remain (`attempts_not_exhausted`), and even a granted retry halts straight back to
  FAILED at claim on the same budget check — the recovery hint names a cure that does not exist
  for this refusal. Measured 2026-08-03: units `c609dac5` (9/6) and `992560d5` (26/24) both died
  this way, each costing a full package revision plus fresh human approvals; a single coding
  attempt burns 8–18 calls (18 when the verifier fails mid-run and the agent investigates).
  **Authoring rule: the ceiling must cover `max_attempts × ~20`, not the optimistic single run**
  — revision 4 shipped 60 and finished in one attempt at 8.

- **Decomposition-proposal mechanics learned driving revisions 3–4 by hand:** the route's
  `expected_version` is a route-level must-be-0 formality (`_require_zero_expected_version`), not
  a revision-version check; a SECOND proposal for the same revision is accepted while none is
  approved (only `decomposition_already_approved` blocks), so a wrong pending proposal is
  recoverable by submitting a corrected one and approving THAT — the stale one becomes permanently
  unapprovable, inert debris. And in intent-packages, **a package revision changes the package
  hash, and `tests/fixtures/package_hashes.json` must move in the same commit** — revision 3
  landed without it and broke the target repo's own `make check`, which the factory run's
  finalize step then correctly refused (the clean-clone control identified it as pre-existing in
  one step).

- **`allowed_commands` is ADVISORY to the coding agent and MANDATORY at finalize.** It reaches the
  agent only as prompt text (`cli.py` `_prompt`, `"\n".join(f"- {command}" …)`) — nothing blocks the
  agent from running something else — and it becomes `verification_commands` at finalize
  (`cli.py:839`), which re-executes the ordered list before checking `git status`. Two consequences:
  an unlisted command the agent improvises may make a run *appear* to work, and a listed command
  that cannot run makes finalize fail **however well the coding phase went**. Read this together
  with the ordering rule (mutators first, verifier last) — the ordering matters because of the
  re-execution, and every listed command must therefore be idempotent.

- **The reusable workflow syncs the RUNNER CLI, never the TARGET repository — so any envelope whose
  verifier needs project dependencies must authorize the sync itself.** `factory-runner.yml` runs
  `setup-uv` and `uv tool install git+…@job.workflow_sha` (the runner), then `actions/checkout` puts
  the *caller's* repo on disk with **no `.venv`**. A verifier like `make check` then hard-fails
  through the portfolio Makefile's `need` macro. Proven by control 2026-08-03 against
  `intent-packages`: a tree from `git archive HEAD` under `env -i PATH=/usr/bin:/bin` gives
  `make check: ruff not found — install it with: uv sync`, rc=2. **Both conditions are required on
  this machine** — ruff/pytest are on the global PATH and the Makefile re-prepends `.venv/bin`, so
  neither alone reproduces a CI checkout. The correct envelope is `["uv sync", "make check"]`.
  HQ authored a first envelope without this dry-run, which the repo's own invariants require, and it
  cost a whole package revision to fix — the envelope is inside the authority fingerprint, the human
  approval is bound to it, and there is no supersede route for an approved decomposition.

- **A package approval requires BOTH a hash-bound ledger entry AND a `package.approved` event in the
  tamper-evident factory-events chain — a hand-written `lineage.yaml` approval can NEVER verify, by
  design.** `operations.py::verify_approval` fails closed on either half, and its own docstring says
  *"a forged/edited ledger entry cannot pass this — it isn't in the chain."* The audited path is the
  **`intent_packages` CLI** (`approve`, `revise`, `transition`, `verify-approval`) — a *different*
  CLI from `factory` — which emits the chain event FIRST and writes the ledger only after, so an
  unaudited approval cannot exist. Do not hand-edit lineage; HQ tried on 2026-08-03 and the guard
  refused it, correctly.

- **`factory decompose` only speaks dependency-update: its interface is
  `--tooling {pip,uv,npm} --package --from --to`.** It cannot express any of the other four profiles,
  so `maintenance-remediation`, `software-delivery`, `infrastructure-change` and
  `non-software-operational` **decompositions** must be hand-authored against
  `POST /api/v1/package-intakes/{revision_id}/decomposition-proposals`.
  **NARROWED 2026-08-04: this is true of the DECOMPOSE step only, and an earlier reading of it as
  "the factory is mechanically served for one profile" overstated the gap.** Package *authoring*
  is tooled for every registered profile — `factory create --profile <any> --reach <members>`
  scaffolds it and takes `reach` as a first-class flag — and `factory submit` stages the intake.
  So the hand-authored surface is the decomposition proposal, not the package. Phase-3 WS-P3.1
  (Dependabot → proposed packages) remains the lane `decompose` can feed end to end.

- **There is NO `(READY, CANCELLED)` transition for ANY role, so a misfired READY unit is permanent
  debris — but dispatch is EXPLICIT-ONLY, so the debris is inert.** `HUMAN_EDGES` carries
  `CLAIMED→CANCELLED`, `EXECUTING→CANCELLED`, `AWAITING_APPROVAL→CANCELLED` and
  `FAILED→CANCELLED`; `READY` appears in none of them, for any role. Confirmed fresh on 2026-08-03
  when a superseded package revision left unit `136c6c64` stranded in `ready` forever. The reason
  this is survivable: `dispatch_work_unit` has exactly one caller —
  `POST /work-units/{unit_id}/dispatch` — with no sweeper and no cron, so an open window dispatches
  **nothing** on its own. Read "if the target unit is the only one in the system there is nothing
  else an open window can dispatch" as a statement about blast radius, not about automatic pickup.
  A `FAILED` unit CAN be cancelled, so letting a bad unit fail is the only route to retiring it.

- **`POST /work-units/{id}/dispatch` requires `expected_version`** — omitting it is a FastAPI 422
  before any service code runs, not a `DomainError`. Same for most command routes; read the
  `detail[].loc` in the 422 rather than guessing which field is missing.

- **The package-intake id IS the revision id.** `/review/intakes/{id}` and
  `/review/revisions/{id}/evidence-pack` carry the same UUID, and it is what
  `POST /api/v1/package-intakes/{revision_id}/decomposition-proposals` wants. There is no separate
  revision UUID to hunt for.

- **The conformance kit's `repo.protection` is an ADVISORY check, not an ADMISSION one — it never
  affected `admission_passed`.** `readiness_schema.py` puts `git.current`, `project.manifest`,
  `code.onboarded`, `ci.executed`, `security.clean`, `runner.caller`, `profile.declared` in
  `ADMISSION_CHECKS`, and `deps.dependabot`, `repo.protection`, `backlog.hygiene`,
  `standards.pinned` in `ADVISORY_CHECKS`. **HQ asserted on 2026-08-03 that exit criterion #2 was
  "unsatisfiable" because protection blocked admission; that was wrong.** The real admission
  blockers are `runner.caller` (6 repos), `profile.declared` (4), `security.clean` (1),
  `code.onboarded` (1) — all fixable. Before calling a gate unsatisfiable, read which set the check
  is in.

- **Branch protection is UNAVAILABLE on private repos on this plan, and the API says so with a 403
  whose body matches neither "Branch not protected" nor "Not Found".** Six of eight candidate repos
  are private; `GET/PUT repos/{r}/branches/main/protection` answers
  `403 "Upgrade to GitHub Pro or make this repository public to enable this feature."` A naive probe
  that branches only on those two strings falls through to its `else` and reports the six as
  **PROTECTED** — the exact inverse of the truth, which is how HQ first mis-answered it. The kit now
  distinguishes four outcomes (`pass` / `violation` / `not-applicable` / `unknown`, project-standards
  PR #14); `factory-runner` was the one genuinely-unprotected public repo and was protected
  2026-08-03 with **required status check `Quality` + no force-push + no deletion and NO review
  requirement**. **`enforce_admins` was FALSE until 2026-08-04; Devon then turned it ON for
  `factory-runner` only (ADR-0015 sibling decision), so CI now genuinely gates that branch.**
  **THE WARNING LINE IS IDENTICAL IN BOTH STATES — you cannot tell enforced from unenforced by
  reading it, and HQ misread it once for exactly this reason.** Measured both ways on the same
  repository: with `enforce_admins: false` a direct push printed
  `remote: - Required status check "Quality" is expected.` and **landed anyway**; with it true the
  same line appears followed by `GH006: Protected branch update failed` and
  `! [remote rejected] main -> main (protected branch hook declined)`, and nothing lands. Read the
  `remote rejected` line or the exit state, never the "is expected" warning. Note the deadlock this
  creates: if `Quality` itself breaks, no fix can merge until it passes — the escape is to disable
  protection, push, re-enable. `infraops-mcp-server` is deliberately left at `enforce_admins:
  false` (no blast radius), and the six private repos are deliberately unprotected — Devon opted
  out of that level rather than buying Pro or going public. Never add
  `required_approving_review_count` — a solo account cannot approve its own PR, so the value `1`
  (which the kit suggests) would make `main` unmergeable and strand every Dependabot PR. That
  hazard is now live rather than theoretical on `factory-runner`, since admins no longer bypass.

- **Branch protection across the six factory repos, and WHY each setting is what it is — because
  the reasoning going missing is how a setting becomes folklore.** Applied 2026-08-06 after the Pro
  upgrade made protection available on private repos. Every repo: a **required status check that
  actually runs on pull requests**, no force-push, no deletion, **`required_approving_review_count`
  absent**, `strict: false`, `allow_auto_merge: true`.
  - **The required check must be the JOB name, not the workflow name**, and it must be verified
    against a real open PR — a context string that does not match blocks every pull request
    forever, silently. The six: orchestrator `Quality` + `Runner consumer compatibility`;
    intent-packages `Lint, type-check, and test` + `Routing policy compatibility` + `validate`;
    infraops-mcp-server `build` + `Lint, type-check, and test`; project-standards and
    security-standards `Lint, type-check, and test` (+ `scan` for the latter); factory-runner
    `Quality`.
  - **NEVER set `required_approving_review_count: 1`** — a solo account cannot approve its own pull
    request, so it makes `main` unmergeable and strands every Dependabot PR. The conformance kit
    suggests it; do not take the suggestion.
  - **`allow_auto_merge` with an EMPTY required-check list merges instantly** — there is nothing to
    wait for. `infraops-mcp-server` was already in that state (protected, zero checks) and would
    have merged on enablement. Always read the check list back before enabling auto-merge.
  - **`strict: false` everywhere, deliberately.** `strict: true` requires a branch to be up to date
    before merging, so with auto-merge live and ~27 open Dependabot PRs each merge staled the rest
    and serialised them behind rebase + re-run cycles — hours on orchestrator's 25-minute suite.
    What it buys is protection against two bumps that pass separately and fail together: real,
    uncommon, and immediately visible when `main` goes red. factory-runner carried `strict: true`
    from its initial 2026-08-03 application rather than from a decision, and was flipped to `false`
    on 2026-08-06 for uniformity.
  - **`enforce_admins` is TRUE on `factory-runner` alone, and that asymmetry is the considered
    part.** It is the one repo where a bad merge stops every dispatch in the estate, and its CI is
    ~20 seconds, so enforcement is nearly free. It is off elsewhere chiefly because orchestrator's
    suite is 25 minutes and enforcing there taxes every CLAUDE.md, ADR and backlog commit — the
    lowest-risk commits, hardest. Note `enforce_admins` does **not** affect auto-merge, which
    merges through GitHub once required checks pass either way.

- **The capability vocabulary has FOUR copies, not two — and only two of them are pinned.**
  Verified 2026-08-09 (WS-P3.7 Inc 3). The pinned pair is `src/orchestrator/capability_vocabulary.py`
  and factory-runner's own module, both held to the byte-identical
  `tests/fixtures/runner_envelope_contract.json`. The third is that fixture itself; the **fourth is
  `intent-packages`' `profiles/dependency_update.py::CAPABILITIES`**, six entries, pinned to
  nothing. It is safe because it is a *producer* rather than a validator — a name it emitted that
  the orchestrator did not know is refused at ingress — so it fails closed, and it deliberately did
  **not** gain `github.pr.merge`. Before widening the vocabulary, grep the whole portfolio for the
  capability strings rather than the two repos you expect to own them; this is the same lesson the
  BWS-UUID move taught, in a different vocabulary.

- **THE FACTORY CLOSED ITS OWN LOOP ON 2026-08-10, and the three things that run established are
  each invisible from reading the code.** Commit `b3f1522f` reached `AlobarQuest/intent-packages`
  `main` merged by `alobar-sds-dispatch[bot]` (type Bot), with Devon's authority approval at
  13:55:44 the **last human act in an eleven-row history** — everything after it
  `orchestrator-system`, `factory-runner`, `orchestrator-verifier`. AC-001 resolved from observed
  `verifier.github.named_check` evidence, never by a person. One attempt, 9 LLM calls of 120, 40
  seconds of coding, `uv.lock` +3 −3.

  1. **`factory decompose` structurally CANNOT produce a merge-granting envelope**, for any
     package, any repository, any version. `build_envelope` emits
     `profiles/dependency_update.py::CAPABILITIES` verbatim — a fixed six-entry dict with no
     `github.pr.merge` — so its envelopes refuse at `merge_capability_not_authorized`. **Every
     ADR-0020 landing hand-authors its decomposition proposal until that profile changes**, so the
     one profile with end-to-end tooling is the one profile that cannot close the loop. Separately
     `_uv_discover` scans `pyproject.toml` sections only, so a **transitive** dependency has no pin
     site and `decompose` refuses before emitting anything — which is exactly the property that
     makes a transitive bump the safest possible subject. Everything decompose does *around* the
     envelope stays reusable: the criterion-UUID map, the conformance scan, brain enrichment, the
     routing rationale, the three fail-closed validations.
  2. **A `human_review` acceptance criterion ANYWHERE on a package the factory is to land refuses
     the landing — including one RETAINED rather than mapped to the unit.**
     `verifier_decided_completion`'s fifth disqualifier (`decision_outside_required_criteria`)
     disqualifies the **unit**, because ADR-0020's condition is "with no human adjudication", not
     "none among the criteria that happened to be required". **`factory create --profile
     dependency-update` scaffolds exactly this trap as AC-002, and every prior dependency-update
     package in the estate carries it.** The failure is invisible until `pr-merge-admission`
     refuses a unit that has **already completed** — i.e. after the write-once envelope and both
     human approvals are spent, which costs a whole new package revision. Author a landing package
     with no human-judgment criterion at all.
  3. **Driving the `factory` flow needs TWO BWS identities.** The orchestrator bearers are in the
     `SDS Operator` project, readable by the narrow `sds-operator` account; the Code and Infra
     Brain keys the enrichment step needs are in the `brains` project, which that account **cannot
     read**. The failure is a `CredentialError` naming only a secret UUID. No document said so.

  Also: **the `/review` intake and decomposition pages return `[BLOCKED: …]` for several fields
  when read through browser automation** — page text, a commit sha, an authority block — because
  the redactor cannot tell a secret from a hex string. Anything verified through that surface must
  be verified from the API or from git as well.

- **ADR-0020 IS CLOSED. The estate's first autonomous merge carries a permission basis that is
  CHECKED rather than asserted** — `factory-approved-no-deploy`, 1 of 440 landings, recorded on
  first observation with zero reclassification (`skipped: 0` is the number that proves it: a
  single drifted fact would have been refused as `observation_conflict` and exited 3). Detector A
  verifies the claim against the orchestrator's **durable record** — unit completed, criteria
  verifier-decided from observed evidence, the merged record naming this repository, pull request,
  merge commit and head under the fingerprint the unit still carries. It deliberately does NOT
  re-ask `pr-merge-admission`, which is a *live* answer that legitimately drifts.
  **THE RESIDUAL, and it is the one to know before editing anything: the check is RECOMPUTED PER
  READ against constants in the running image, not stored.** `verifier_decided_completion` is
  derived on every evidence-pack read, so **narrowing `OBSERVED_EVIDENCE_TYPES` in a future release
  retroactively flips already-audited landings into findings.** That is much narrower than
  re-asking admission and the alternative cannot see which criteria a revision required, so it is
  the right trade — but it is a trade, and it is invisible until an old landing starts failing an
  audit nobody changed.

  Four things the build of it established, none derivable from reading the code:
  1. **A `DomainError` reaches the wire NESTED, and a 404 check written from `main.py` matches
     neither 404.** The handler reads `error.code.endswith("_not_found")`, which looks top-level;
     production answers `{"error":{"code":"work_unit_not_found",…}}`, a route the deployed image
     does not serve answers FastAPI's `{"detail":"Not Found"}`, and a proxy answers no JSON at all.
     A client distinguishing "this subject is absent" from "something else said 404" must read
     `body["error"]["code"]` — and must, because this estate HAS served a release whose routes
     production did not carry, and reading every 404 as absence turns that into a finding accusing
     the orchestrator of losing every subject at once.
  2. **`UnitPrMerge.status` has five writers across three values, and only `merged` asserts the
     orchestrator made the landing.** `already_merged` covers both the lost-response retry (ours,
     reconciled) and — *before the merge call* — a pull request somebody else had landed.
     `refused` covers both the genuinely ambiguous outcome and a confirmed non-landing. The two
     cases are indistinguishable within a status, so **no status carries authorship**, and a
     consumer keyed on the name alone is wrong in both directions. Two adversarial reviews
     falsified this from opposite sides in one afternoon.
  3. **The squash body is a REPOSITORY SETTING, not the merge call.** `pr_merge.py` sends no
     `commit_message`, so whether the landing commit carries the branch's messages — and therefore
     factory-runner's `SDS-Unit:` trailer — is governed by `squash_merge_commit_message`, a web
     form. All eight ledger repositories are `COMMIT_MESSAGES` today (2026-08-10). Anything reading
     a trailer off a landing commit needs a fall-back to the pull request head: the failure is
     silent — no trailer, no claim, no basis, and no detector looking.
  4. **Every string a landing observation puts in `facts` is frozen at the first row carrying it.**
     `idempotency_key` is content-addressed over the facts while `source_reference` is not, so
     correcting a `reason` afterwards is an `observation_conflict` on a landing where nothing
     changed: the write is refused, `record` counts a skipped landing, and the daily pass exits 3
     until it is settled by hand. Prose in `facts` must say only what stays true — never where a
     value was read, never a count, never anything dated. Same discipline as
     `wave-exit-manifest.toml`'s `note`/`proves` rule, in a different artifact.

  Two smaller ones: the ledger's `is_machine` keys on the `[bot]` login suffix where GitHub answers
  properly with `merged_by.type == "Bot"`, so a machine account without the suffix records as a
  person; and `orchestrator-observer` IS a valid credential key id as well as a registry actor id,
  verified against production.

- **`runner.caller` asks "can the factory send work INTO this repo?", and it is three different
  faults under one name.** It requires `.github/workflows/factory-runner-pilot.yml` calling
  factory-runner's reusable workflow at a full SHA equal to `RECOMMENDED_CALLER_PIN` (a one-line file
  at factory-runner's root). On 2026-08-03: `change-manager` and `brain` were BEHIND the pin
  (`b8049127` vs `b0305b51`); `intent-packages` and `security-standards` used **`@main`** — the
  GAP-4 class, pinned to nothing; `project-standards` and `factory-runner` had **no caller at all**.
  Since it is really a *dispatchability* check, applying it to every onboarded repo is a category
  error — `factory-runner` needing a caller means the runner would verify changes to itself using a
  pinned older copy of itself, a trust loop that should be decided rather than acquired by default.

- **`ActorRole` has FIVE members: an OBSERVER role exists and its entire write surface is
  `POST /api/v1/observations`.** WS-P3.6 Inc 1, live on `51c5a57-wsp36inc1-amd64` since
  2026-08-07. `orchestrator-drift-reporter` now holds it (was `system`), which closes the
  Phase-3 exit-criterion-3 hole where the one external producer held the role that drives
  `commands/ready` and dispatch. **Every future observe-and-report producer uses this one
  credential** — per-producer identities are deliberately not used, because the observation row
  already carries `source_system` / `source_reference`, so the row says who spoke and the
  credential does not have to. Registry actor `orchestrator-observer`, profile `observer-v1`
  (one capability `event_emit`, fourteen explicit prohibitions).
  **The confinement is at ONE place — `api/dependencies.py::_confine_observer` — and that is
  load-bearing, not stylistic.** It keys on the matched route TEMPLATE, fires above request
  validation, and an unmatched route yields `None`, which is not in the allowlist, so the unknown
  case refuses. Reads are deliberately unconfined. **Confining it by the ~20 service-level
  allowlists instead would have failed**, because four POST routes carry no role check at all —
  `work-units/{id}/preflight` and the three `/event-publications/*` — and `services/context.py`
  and `services/event_publications.py` contain **zero** `ActorRole` references between them. "The
  service layer gates writes" is not a property the service layer provides. Those four are a live
  defect for every other role (backlogged); OBSERVER is simply not exposed to them.
  **Proven against production 2026-08-07, not just in tests:** `commands/ready`, `dispatch`,
  `verify`, `preflight`, `event-publications/queue` and `/export` all **403**; `POST
  /observations` reaches request validation and a valid post returns **201** attributed to
  `drift-reconciler`; `GET /observations` returns 200. Note approval-shaped routes answer **302**
  from outside — they sit behind the human forward-auth chain at the proxy, so the request never
  reaches the app; that surface is covered by the in-process architecture test over all 49
  confined routes, not by an external probe.

- **A build-session worktree gets a DIFFERENT Python than CI unless you pin it, and the digest in
  a handoff is stale the moment anything merges.** Two release-time traps, both hit on 2026-08-07.
  (1) `uv sync` in a fresh worktree picks the newest interpreter satisfying `requires-python =
  ">=3.12"` — it chose **3.14.3** while `quality.yml` pins **3.12**, so the session's green
  `make check` was measured on an interpreter CI never uses. Add `uv venv --clear --python 3.12`
  to the worktree recipe. Also note this repo is on **psycopg 3**: `TEST_DATABASE_URL` must be
  `postgresql+psycopg://`, not `+psycopg2://`, which fails with a bare `ModuleNotFoundError` that
  reads like a broken environment.
  (2) **`artifact_sha256` cannot be computed before the merge SHA and cannot be carried across a
  rebase.** `SOURCE_REVISION` is hashed into the bundle, so the digest is a function of the
  security-standards commit. The Inc-1 report quoted `6fee03e9…` for `abfaf41`; unrelated merges
  advanced `main`, the branch rebased to `85125a1`, and the real digest was `9cea9814…`. Always
  recompute from the **merged** revision:
  `scripts/shape_registry_context.py --source <checkout> --revision <sha> --output <dir>` then
  `build_registry_bundle.py`'s `artifact_digest`. Verify by assembling the bundle and reading back
  `source_revision` and the actor list before pinning. The in-build gate fails closed on a wrong
  digest, so the cost of getting it wrong is a failed build rather than a bad image — but it is a
  25-minute failed build.

- **`uv sync` installs what the repository PINS, so a remediation whose whole point is to adopt a
  proposed version cannot be produced from the checkout — and the envelope that authorises only
  `uv sync` and the verifier looks entirely correct while being unsatisfiable.** The first live
  execution of `docs/operations/dependency-remediation.md` (2026-08-07, `intent-packages` #50, ruff
  0.15.22 → 0.16.1) died on exactly this: revision 1's `allowed_commands` was `["uv sync", "make
  check"]`, `make fix` was a no-op because the tree's own ruff already considered those seven files
  correct, and the coding agent ran `make check` seven times and then spent its remaining turns
  trying to research what 0.16 had changed. **The fix is `uvx <tool>@<version>`** — it fetches the
  proposed version for the run without committing the bump, so Dependabot's own PR still lands the
  version change, which is the ADR-0016 composition. Three consequences worth carrying:
  (1) the repo's documented dry-run rule ("prove the mutator yields a diff") must be read as
  **prove it from the RUNNER's environment**, since a local machine that happens to have the newer
  tool proves nothing; (2) `allowed_commands` reaches the coding agent only as prompt text, so the
  unit's **`outcome` is what actually steers it** — revision 2 named the command and said `make fix`
  is a no-op here, and finished in 90 seconds on 10 LLM calls against a 60 budget, where revision 1
  burned 40 turns and $1.39; (3) **an attempt that ends on `error_max_turns` is under-specified, not
  under-budgeted** — the 40-turn ceiling is a literal in factory-runner's workflow and is unrelated
  to `max_llm_calls`, which was barely touched. And the price of getting it wrong is the documented
  one: an approved decomposition cannot be superseded, so this cost a whole new package revision
  plus both human approvals again.

- **`change_class` is a FREE STRING matched against `ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES`,
  and that list was `["dependency-update"]` alone until 2026-08-03**, when Devon approved adding
  `maintenance-remediation` (standing, not per-run). `_optional_change_class` validates shape only —
  there is no closed vocabulary — and `_change_class()` falls back to `required_capability` when the
  field is absent, so an envelope with no `change_class` is matched on its capability name and is
  refused just the same. Widening this list is a standing authority change and outlives any window.

- **The orchestrator's `KNOWN_FIELDS` and the runner's declared envelope fields differ by exactly
  ONE member, in the fail-open direction — and a gate keyed on the wrong one admits the shape an
  operator is most likely to author.** `KNOWN_FIELDS` (`kernel/authority.py`) contains
  `unknown_fields`, deliberately, so `normalized()` is a fixed point; the runner's
  `AuthorityEnvelope` is `extra="forbid"` and does not declare it. So an envelope carrying
  `"unknown_fields": []` has an **empty** `envelope.unknown_fields` set — a predicate reading that
  set waves it through — and dies at pydantic **before** `validate_authority`, i.e. as a crash
  rather than a named `AuthorityError`. That is not a synthetic input: it is precisely what
  `normalized()` emits, hence what the `/review` unit page and the breakdown-proposal body render,
  hence what gets copy-pasted into the next hand-authored breakdown. WS-P2.34's first draft made
  exactly this mistake **inside the function written to close the same class of defect**, and two
  independent adversarial reviewers found it. Key any such gate on
  `kernel/runner_authority.py::RUNNER_ENVELOPE_FIELDS`, which is pinned to the runner's pydantic
  model by `tests/fixtures/runner_envelope_contract.json`, never on `KNOWN_FIELDS`.
  **UPDATED 2026-08-09: that byte-identical fixture now carries the CAPABILITY NAMES too**, not
  just `envelope_fields` and `levels` — `RUNNER_CAPABILITIES == frozenset(golden_contract()
  ["capabilities"])`, and the two golden ENVELOPES became subset-checked specimens rather than the
  definition. So a vocabulary addition moves `CONTRACT_SHA256_SURFACE` and leaves
  `CONTRACT_SHA256` and `CONTRACT_SHA256_EDIT` **alone** — the opposite of what this file used to
  imply, and the reason a merge-granting envelope never had to become the copyable example. Corollary:
  `runner_payload(envelope)` (`normalized()` minus that key) is what the no-raw-payload fallback
  must store — it previously stored `normalized()`, i.e. an unparseable envelope by construction.

- **There is ONE composed predicate for "would the runner refuse this envelope?" —
  `kernel/runner_authority.py::runner_authority_violation` — and FOUR surfaces must ask it.**
  Breakdown ingress, unit registration, the human authority approval (both `record_approval` and
  the `/review` form-gating path through `evidence_pack`), and admission. Before WS-P2.34 each
  asked a different hand-written subset, which is how the level and field rules ended up enforced
  at one surface out of four while the command rule had all four. **A unit's envelope is
  write-once and there is no supersede route for an approved breakdown**, so an ingress-only rule
  is structurally blind to every envelope authored before it existed — and that legacy population
  is exactly the one that produced this defect family. Capability *names* are deliberately NOT in
  the composition: the orchestrator's set is a superset (it authors work no runner performs), so
  the name refusal (`capability_outside_runner_vocabulary`) belongs only on the surface that knows
  the unit is runner-bound, which is admission.

- **Capability LEVELS fail OPEN where unknown NAMES fail closed — the asymmetry is why levels went
  unvalidated for so long.** `level_for` returns `"prohibited"` for a name it does not know, so an
  unknown name was already refused (late, as `capability_not_authorized`). But it compares against
  `"allowed"`, so a *mistyped level* on a capability the work does not even need reads as a
  prohibition and satisfies every orchestrator gate, while the runner — which validates the level
  of every entry — refuses the whole envelope. `requires_approval` is the shape that actually
  occurs: it is the PACKAGE-authority vocabulary of ADR-0001, and projecting package authority into
  unit capabilities is left to the breakdown author, i.e. to a human writing JSON by hand.

- **`GET /work-units/{id}/evidence-pack` serves a PROJECTION of the authority envelope; the runner
  brief serves the STORED COLUMN. Measure at the consumer's surface.** The pack renders
  `normalize_authority(unit.authority).normalized()`, which always emits `unknown_fields` and every
  other declared key — so a census taken there reports a shape no runner ever sees. WS-P2.34's
  first sweep did this and concluded every stored envelope carried an `unknown_fields` key;
  re-measured through `runner-brief` (which serves `unit.authority` verbatim), **41 of 41 carry
  none**, and stored envelopes legitimately omit optional keys (`change_class` on 35, `conformance`
  on 32). This is the same failure the `response_model` invariant describes, one layer out: the
  surface you read is not necessarily the surface the consumer reads.

- **Onboarding a factory target has FOUR parts, and the fourth is invisible until a run dies at
  checkout: the fine-grained PAT's REPOSITORY ACCESS LIST.** The three obvious parts are the caller
  workflow (`.github/workflows/factory-runner-pilot.yml` at `RECOMMENDED_CALLER_PIN`), the four
  Actions secrets, and the orchestrator's `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` entry.
  All three can be correct while every run fails, because `FACTORY_PR_TOKEN` is a **fine-grained**
  PAT and a fine-grained PAT is scoped to an explicit list of repositories chosen when it was
  issued. Setting the secret in a new repo copies a token that has no access to that repo.
  Measured 2026-08-07 onboarding `project-standards`: caller, secrets and allowlist all in place,
  and the probe run failed in 35 seconds at `actions/checkout` with
  `fatal: unable to access '…/project-standards/': The requested URL returned error: 403` — never
  reaching the claim call. Confirmed by control: the token answers 200 on all seven other repos and
  DENIED on that one. **Extending the list is a settings-page operation on the account that owns the
  PAT; no API does it, and no amount of re-setting the secret helps.**
  **Two consequences.** (1) Fire a throwaway `workflow_dispatch` at a new caller with a well-formed
  but nonexistent `work_unit_id` before believing it works — it costs 35 seconds, mutates nothing
  (the runner claims before it codes, and here it does not even get that far), and it is the only
  thing that distinguishes *configured* from *working*. This is the same class as the 2026-08-03
  failure where the PAT lacked `workflow` scope and it surfaced only at `git push`, after coding and
  verification had already succeeded, costing two work units. (2) **Two sets are now tracked and
  they disagree in both directions** — repos holding a `FACTORY_PR_TOKEN` Actions secret
  (`orchestrator`, `intent-packages`, `infraops-mcp-server`, `security-standards`, `change-manager`,
  `brain`, `project-standards`) versus repos the PAT can reach (the same list minus
  `project-standards`, plus `factory-runner`, which holds no secret because it is not a target).
  Neither set is derivable from the other.

- **`FACTORY_PR_TOKEN` has a BWS record and a rotation trail** — `a3240c2e-92d7-4b32-a726-b49b0135565a`,
  `SDS Operator` project, documented in **factory-runner's** `.bws-secrets.toml` (not this repo's).
  **SEVEN repos hold copies as write-only Actions secrets** as of 2026-08-07 — `orchestrator`,
  `intent-packages`, `infraops-mcp-server`, `security-standards`, `change-manager`, `brain`,
  `project-standards`. (This bullet said "four" until 2026-08-07; the count had drifted twice.)
  A rotation means re-setting all seven; a copy left behind is
  dead on the next push with no signal until a run fails at auth. Verify any rotation with the
  discriminating probe, never with a green `gh secret set`: a throwaway branch workflow triggered
  on `push:` that checks out with the secret and pushes a commit **touching
  `.github/workflows/**`**, which is the exact operation a token without Workflows:
  Read-and-write is rejected for. (Rewired and probed for all four, 2026-08-04, WS-P2.34.)

- **The named-check evidence lane is closed to any criterion not declared `automated_check` — at
  INGESTION, not only in intent-packages' `factory verify` pre-check.** `record_named_check_evidence`
  (`services/verifier_evidence.py`) raises `evidence_subject_invalid: acceptance criterion is not a
  mapped automated check` unless the criterion's declared `evidence_type` is exactly
  `automated_check`. The deterministic-permitted floor of `automated_test` (WS-P2.17) does NOT make
  the observed-check lane reachable for it: the floor governs how EVALUATION may resolve, the
  ingestion gate governs which evidence can ARRIVE, and they key on different things. Measured live
  2026-08-04 (WS-P2.35): the software-delivery profile mapped `ci:` → `automated_test`, so the
  pilot's named-check POST 409'd and AC-001 completed via clause-(b) human adjudication (evaluator
  reason: "runner.pr.opened has no deterministic evaluator"). **The profile-side fix shipped the
  same day (WS-P2.36, intent-packages PR #57) and the lane is now PROVEN for software-delivery** —
  unit `a1493627…` completed with AC-001 resolved from observed `verifier.github.named_check`
  evidence, evaluator reason *"the named check was observed to conclude success"*. **The ingestion
  gate described above is unchanged and still the rule**: it was the profile that was wrong, not
  the gate. `check_name` must be the JOB name (`Lint, type-check, and test` in intent-packages,
  where the WORKFLOW is called `Quality`), and one head legitimately carries two identically-named
  runs (push + pull_request) which the evaluator resolves by unanimity.
  Two adjacent discoveries from the same pilot: (1) the dispatch
  window has a FOURTH env-keyed admission gate this file's window recipes omitted —
  `ORCHESTRATOR_DISPATCH_ENABLED_CAPABILITIES`, which `unit.required_capability` must be a member of
  (blocked reason `capability_not_enabled`; widened standing to `["repo.edit","github.pr.create"]`,
  Devon 2026-08-04, alongside `software-delivery` joining the change-class list). (2) Recording a
  human adjudication does NOT complete a unit: `AWAITING_REVIEW → COMPLETED` is its own designed
  HUMAN gate (a second `/review` click), and `/verify` refuses an `awaiting_review` unit with
  `invalid_transition … recovery: submit`.

- **A workflow RUN's conclusion cannot distinguish "nothing was deployed" from "production was
  deployed and is broken", and those two want opposite remedies.** Measured 2026-08-10 across
  every failing rollout ATTEMPT in `change-manager` and `brain` — **six of them across three
  runs**, and the attempt granularity is itself the finding. **Three never reached production**:
  two change-manager attempts (one where the test job failed and the rollout job was `skipped`,
  one where the Coolify webhook call itself failed) and brain run `27847308046` attempt 1, where
  `build-and-push` failed and `deploy` was skipped. Acting on a run-level `failed` would, in
  those three, have made the rollback the day's only production mutation. **And one failing
  rollout attempt sits inside a run whose conclusion is `success`** — brain pull request #2,
  attempt 1 died at the trigger step and attempt 2 passed. So the run conclusion is neither
  necessary nor sufficient for "did the rollout fail", in both directions. A first draft of this
  bullet said "all three carry `conclusion: failure`", counting failing RUNS and calling them
  failures; the correction came from a reviewer re-deriving the census rather than reading it.
  **Read JOBS and STEPS** (`/actions/runs/{id}/attempts/{n}/jobs`, which also returns
  `steps[].conclusion`) — the attempt, not the run, because a re-run supersedes its predecessor
  and `/runs/{id}/jobs` answers about a different attempt than the row you are writing. Note the
  App has no `checks` permission so the Checks API 403s; this is the Actions API and needs only a
  plain token. `services/github_checks.py` already reads jobs and documents why.

- **GitHub populates `merge_commit_sha` on OPEN pull requests with a throwaway test-merge commit
  — a real, fetchable object that passes every shape check there is.** `change-manager` PR #42
  carries `6a7c99a94c52…` ("Merge b30708ce into ef671eeb") while unmerged, against a base that has
  since moved, and it resolves through `GET /contents/{path}?ref=` and every other hop. **`merged`
  / `merged_at` is the field that decides**; a reader that trusts the sha will walk a whole
  pipeline successfully and produce a confident answer about a landing that never happened.

- **GitHub Actions run ids passed 2^31 long ago** (`31426195637` is one of this estate's), so a
  column holding one must be `BigInteger`. `Integer` is accepted silently by SQLite and raises
  `ERROR: integer out of range` only on Postgres — increment 1's int4 finding, one column over.

- **change-manager's DECISION lifecycle is open to a proposed-source change; only the EXECUTION
  lifecycle is closed.** ADR-0019 increment 1 guarded `claim`/`outcome`/`handoff` with
  `has_authorized_executor`; `approve`/`defer`/`wontfix`/`resolve`/`reactivate` never call it, and
  `resolved`/`wontfix` are terminal. So a deploying-merge change CAN reach a terminal state — by
  decision, not by execution — and increment 1 deliberately renders both buttons for an approved
  record with no executor. **The increment 2 handoff asserted the opposite** ("no way to reach a
  terminal state at all") and built its whole central design problem on it; all three options it
  offered were answering a problem that does not exist. The real constraint is narrower: an
  observation must not BE an outcome. It writes no `ChangeAttempt` and performs no transition, so
  the executor guards need no change — nothing is loosened, so nothing can be loosened by mistake.

- **A mutation control that PRESERVES FILE SIZE can be invisible to Python's bytecode cache, and
  the harness then reports SURVIVED for a mutation the interpreter never loaded.** `if early:` →
  `if False:` is byte-for-byte the same length, and a `.pyc` is validated on (size, mtime to the
  second). Observed 2026-08-10: one control alternated between killed and survived across
  otherwise identical runs, and killed reliably when run by hand seconds later. Any mutation
  harness must set `PYTHONDONTWRITEBYTECODE=1` (or clear `__pycache__` per mutation) — without it
  the false answers run in BOTH directions. Relatedly, the estate's "never run two pytest suites
  concurrently" rule extends to mutation runs for a sharper reason: the mutation IS a tree edit,
  so any concurrent reader imports a half-mutated tree.

- **`_RESULT_MAP` in `security-standards/src/factory_events/adapters/change_manager.py` is keyed on
  `event_type` and its keys are `{applied, approved, failed}` — of which exactly ONE, `approved`,
  is an event type change-manager actually emits.** So 14 of its 15 event types on `main` — 15 of
  16 once ADR-0019 increment 2 adds `deploy_observed` — reach the tamper-evident factory-events
  chain as `result: "unknown"`, **including `attempt_failed`**: the
  chain records that something happened and not that it failed. Pre-existing and portfolio-level;
  do not "fix" it by adding a special case for one new event type, which hides the shape of the
  defect. Note also that a new change-manager event type must be snake_case — `envelope.validate_event`
  enforces `^[a-z0-9_]+\.[a-z0-9_.\-]+$` on the composed action, so a camelCase or spaced event
  type HALTS the 03:30 adapter rather than being skipped.

- **`httpx` raises THREE unrelated exception families for a malformed URL, and the third is a
  `ValueError`.** `except (httpx.HTTPError, httpx.InvalidURL)` looks total and is not: IDNA
  encoding of a malformed HOST raises `UnicodeError` — a `ValueError`, neither an `HTTPError` nor
  an `InvalidURL` — at `client.get`, before any body guard. Triggers are ordinary environment-
  variable typos: a **doubled dot** (`https://host..example`), a **DNS label over 63 characters**,
  a trailing dot. Both `services/change_record.py` and `services/estate_landing.py` promise in
  their own docstrings that nothing raises, and both were wrong until 2026-08-11; the escape
  reaches a **bare HTTP 500** from every caller, because only `DomainError` and
  `APIAuthenticationError` have registered handlers — i.e. an admission gate that has stopped
  deciding. The correct tuple is `(httpx.HTTPError, httpx.InvalidURL, ValueError)`.
  **The transmissible half is how it survived.** A mutation deleting `InvalidURL` from the tuple
  was KILLED by the control written for exactly this class — because that control used a trailing
  *newline*, which `InvalidURL` already covers. **The mutation and its control shared one
  incomplete model of what the library raises**, so a 22/22 mutation pass proved the code
  implements the tests' model of `httpx` rather than `httpx`. Found by probing the real library,
  not by reading. Generalise: **a mutation set can only question the model its tests already hold;
  the falsifying input lives outside the tree.** Any "nothing raises" claim needs a probe against
  the real dependency, and a URL control needs a HOST shape, not only a whitespace shape.

- **A time-dependent control passes for the wrong reason most of the day.** A single
  "outside the change window" assertion cannot kill a term that ignores its injected clock, because
  whenever the real clock is also outside the window the mutant and the original agree — and
  `live_estate`'s window is four hours, so the control is honest for 17% of the day. This recurred
  *inside the fix for itself*: the mutation added to guard the acting path was pinned to the
  out-of-window case alone and survived for the same reason one increment later. **Pin a
  clock-dependent guard to a PAIR of cases whose answers must differ** — in-window admits,
  out-of-window refuses — so a term that never reads the clock reddens at any real time.

- **`TransactionClock.now()` does not advance: it is `transaction_timestamp()`, frozen at
  transaction start.** Measured 2026-08-11: two reads two seconds apart in one transaction return
  the identical instant, while `clock_timestamp()` moves. Every admission term in this repository
  reads it, so a change window is judged at the instant the transaction OPENED — and
  `_land_unit_pull_request` then makes up to four outbound calls (App Brain, change-manager, and
  two GitHub calls) before it acts. The drift is seconds and the direction is the same as the
  pipeline overrun already accepted, so it stands as a decision rather than a defect; but "is the
  window about when the transaction started or when the act fires" is a real question and
  `clock_timestamp()` is the other answer.

- **`change_window` is OPTIONAL in `factory-policy.toml` and two of the four rows carry none, so
  `window_refusal` answers "no objection" for a row that loses one — and the assertion that fixes
  it belongs to the ARTIFACT, not to either caller.** `window_refusal` `continue`s past a row with
  no window, so deleting or renaming `[reach.live_estate.change_window]` silently un-gates
  `live_estate` work at every hour, with the document still loading and nothing red. The obvious
  fix — assert a window inside `reach_admission.change_window_refusal` — is **wrong**: that call
  site composes over whatever reach a package declared, and `source_repository` and
  `external_system` deliberately have no window, so requiring one would make two-thirds of the
  authored population unrunnable. `tests/services/test_factory_policy.py::
  test_the_live_estate_row_declares_a_change_window` is the guard, and it covers both readers
  because it is about the file.

- **change-manager's listing route hides proposed sources from any caller that does not name one,
  and applies `status` as a SQL filter — so `?status=approved` makes a PENDING record
  indistinguishable from a record that does not exist.** Measured 2026-08-11:
  `/api/items` → 43 rows, **zero** of them deploy records; `?source=deploy` → the one that exists;
  `?source=deploy&status=pending` → **zero rows for a record that is there**; an out-of-vocabulary
  status → zero rather than an error. Pending is the ordinary steady state of a record awaiting a
  person, so a consumer that filters server-side reports "nothing has been routed" about the
  common case. **Ask for the pipeline alone and branch on status client-side.** The join key is
  `(target_repository.lower(), pull_request_number)` — `pr_url` is an older lane's field, never set
  by the proposal route, and unvalidated. There is no lookup-by-pull-request route, and
  `GET /api/items` is unpaginated with no `limit`.

- **A client that re-checks two of three scoping dimensions and trusts the server on the third is
  defensive about the wrong things.** `change_record.py`'s first draft verified the repository and
  the pull request number on each row and took `source` from the query — the one dimension that
  makes the answer about the right pipeline at all. FastAPI ignores an unknown query parameter
  silently, so a renamed parameter would have handed admission a record from another pipeline.
  Same family, one field over: an **ambiguity guard keyed on a fully-parsed row** is defeated by a
  malformed twin, which the parser skips, dropping the tally below two and letting the survivor
  through as unambiguous. **Detect ambiguity on the MATCH KEY and read the rest only from rows that
  already matched.**

- **The report surface and the ACTING surface are different tests, and only one of them changes a
  repository.** Every case in `tests/services/test_pr_merge.py` passed an inert estate, so when
  ADR-0019 Increment 3 added routed terms the surface that actually lands a pull request was
  covered for `inert` alone — and could not be covered, because `land_unit_pull_request` took no
  clock while `admission_for` did. A refusal test there must assert the **gateway was never
  reached**: an admission answer that arrives after the act is not a gate.

- **An auto-merge armed with `secrets.GITHUB_TOKEN` triggers NO `on: push` workflow — so a lane
  whose whole value is that merging causes CI is inert when armed that way.** Measured 2026-08-11:
  `intent-packages` #50, `infraops-mcp-server` #70 and `factory-runner` #42, all merged by
  `github-actions[bot]` through the estate's `dependabot-auto-merge.yml`, carry **zero** `push` runs
  on their merge commits; the control — `intent-packages` #58, merged by a human identity on the
  same repository and workflows — carries **two**. This is documented GitHub behaviour (events
  triggered by `GITHUB_TOKEN` do not create a new workflow run) and it killed ADR-0019 increment 4's
  specified design: for `change-manager` and `brain`, where merging is supposed to BE deploying, an
  auto-merged Dependabot pull request would land and `deploy.yml` would never run — `main` and
  production diverging silently, and `brain`'s `push`-gated `build-and-push` never building the
  per-SHA image its rollback plan names.
  **BE PRECISE ABOUT WHAT IS MEASURED HERE, because the obvious next sentence is not.** What was
  measured is (a) `GITHUB_TOKEN`-armed auto-merges suppress push runs, and (b) a **direct** merge by
  a human identity fires them. **Nobody has yet measured an auto-merge ARMED with the Dispatch App
  or a PAT actually firing push runs** — GitHub attributes the eventual merge to the arming
  identity, so it should, but "should" is what this file exists to stop being inherited as fact.
  **That probe is the first thing the landing-path increment must run**: a throwaway repository with
  an `on: push` workflow, auto-merge armed by the non-`GITHUB_TOKEN` credential, confirming the push
  run appears. Until then, prefer a **direct** merge by the App (which ADR-0020 already proves fires
  push runs) over arming auto-merge at all. Two corollaries: the five non-deploying repositories have
  been **skipping `main`-push CI on every auto-merged landing** since their lane opened, so `main`
  can be red there with nothing reporting it; and the defect was invisible for exactly the reason
  the estate already documents — the lane was proven only where a missed push run does not matter,
  which is *validate the classifier against the population* one more time.

- **Commit-status semantics for branch protection, measured rather than inferred** (disposable repo,
  `enforce_admins: true`, 2026-08-11 — these outlive the design they were taken for). A required
  context **never reported** blocks and the merge API answers **405**; `pending` blocks identically
  with the message naming the context; `success` releases to `clean`; **re-posting `pending` after
  `success` re-blocks**, so a status is genuinely revocable and auto-merge banks nothing; and a moved
  head SHA carries **zero** statuses, so a Dependabot rebase is fail-closed by construction.
  **The one that matters: auto-merge fires the instant the LAST required context turns green,
  however stale the others are.** Reproduced deliberately — a pull request with an armed auto-merge
  and a `success` posted while a second required context was still pending **merged within seconds of
  that other context greening**, at a moment nothing re-evaluated the first. So any scheme where one
  context encodes a time-bounded permission is fail-open unless that context is provably the last to
  green — and *reading which contexts are required* needs `administration`, which neither
  `GITHUB_TOKEN`'s `permissions:` vocabulary nor the Dispatch App has.

- **A required status check puts the availability chain in front of EVERYONE; the orchestrator
  landing pull requests itself puts it in front of MACHINES ONLY.** ADR-0019 increment 4 analysed and
  rejected a required window check, and Devon asked the reasoning be kept as the standing argument
  against proposing one again. With `enforce_admins: true` a green check would depend on GitHub's
  scheduler, the poster workflow, the orchestrator app, its database, the policy artifact in its
  image, and change-manager — any one down freezes both repositories to every actor, with no in-band
  recovery, including the fix to the poster and including a `change-manager` hotfix (the third
  self-reference instance after ADR-0015 and ADR-0016). Two triggers made that concrete rather than
  theoretical: a 10-minute cron on two **private** repositories is ~8,640 billed Actions
  minutes/month against 3,000 included on this plan, and **scheduled workflows are auto-disabled
  after 60 days of repository inactivity**, at which point the last posted status persists forever.
  **`enforce_admins` is TRUE on `change-manager` and `brain` as well as `factory-runner`** — the
  branch-protection bullet above saying enforcement is on `factory-runner` alone is wrong, and was
  wrong when written.

- **`app.routes` does NOT contain an included router's routes in current FastAPI — it holds a single
  `fastapi.routing._IncludedRouter`.** So the obvious completeness scan (filter `app.routes` for
  `APIRoute`) sees only what was registered directly on the application: in change-manager that is
  exactly ONE route, and the whole `/api` surface reads as absent. **The failure is silent and
  flattering** — a test built that way asserts nothing while looking thorough. Enumerate from
  `app.openapi()["paths"]`, which is flattened and authoritative and is what this repo's own scope
  guards already use, and **cross-check any completeness claim against a set of routes known to
  exist**, which is the only reason this was caught. (Verified 2026-08-11, ADR-0019 increment 4.)

- **`httpx` raises at the CONSTRUCTOR for some malformed URLs and at REQUEST time for others, so a
  guard on one half is not a guard.** A control character is refused by `urlparse` inside
  `httpx.Client(base_url=…)` immediately; a doubled dot or an over-long DNS label survives to IDNA
  encoding at `client.request`. This repository already documents the three exception families
  (`HTTPError`, `InvalidURL`, and `ValueError` via `UnicodeError`) — this is the same family one
  layer out, and the practical shape is that an environment-variable typo crashes an out-of-process
  program with a traceback instead of reporting a finding. Guard **both** construction and request,
  and write the control to span both, since which shape raises where is not guessable.

- **A `source_reference` that is NOT content-addressed is right for an IMMUTABLE subject and wedges
  a producer permanently for a re-runnable one.** `services/observations.py` refuses a second
  observation at the same `(source_system, source_reference)` with different facts —
  `observation_conflict`, no supersession model, no delete route — so the producer's every
  subsequent pass fails. The landing ledger deliberately does not content-address its reference and
  is correct: a commit on a branch is immutable, so a changed fact means something is wrong and
  raising is the point. **A ROLLOUT IS NOT IMMUTABLE.** It is re-run (six failing rollout attempts
  across three runs in `change-manager`/`brain` alone), and what a green run *attests* moves too the
  day somebody transcribes a workflow revision nobody had classified. ADR-0022's first draft copied
  the ledger's rule; the first re-run would have exited 3 on every hourly pass **forever** while the
  successful attempt was never attributed — the permanently-red control that ADR rebuilt inside its
  own second half. The fix is change-manager's `observation_key`, one repository over: identify the
  ATTEMPT and carry the fact digest, so a re-run appends and an unchanged pass replays. This is the
  estate's *copying a derivation pin transfers the MECHANISM, not the PROPERTY* rule in a third
  artifact — **ask what the reference must make unique, not what the exemplar hashed** — and it was
  found by two reviewers, one of whom measured it against a migrated database rather than reading it.
  Two smaller facts from the same surface: `record_observation` **RETURNS** its `DomainError`s
  rather than raising them, so a test reaching for `.id` fails with an `AttributeError` naming an
  attribute instead of naming the conflict (narrow with `isinstance` first); and `_fact_identity`
  covers `status`, `severity`, `observed_at`, `summary` and `facts`, so all five are part of what
  must not move.

- **Adding a source file under `src/` adds THREE parametrized cases to
  `tests/architecture/test_wsp21_invariant_scan.py`, not two** — `test_no_tracked_source_carries_a_secret`,
  `test_nothing_in_the_repo_calls_a_merge_method` and `test_nothing_in_the_repo_merges_a_pull_request`.
  The existing bullet above says two; measured 2026-08-13, two new `src/deploy_watcher/` modules
  added exactly six. The reconciliation method it prescribes (diff node ids between `main` and the
  branch) is right and is what produced this correction.

- **`scripts/sds-token.sh` RESPECTS an already-set `BWS_ACCESS_TOKEN`, so a launcher that needs TWO
  BWS identities must not source it alongside a `${BWS_ACCESS_TOKEN:-…}` default.** One ambient
  value then becomes BOTH identities and **no value of it works**: exported broad, the narrow
  project's fetch is denied; exported narrow, the broad project's is. Under launchd nothing is
  exported and it works, so the failure appears only in the shell an operator debugs the job from —
  and it names BWS rather than the cause. Read each Keychain item **directly**
  (`BWS_ACCESS_TOKEN_VPS_BACKUP` broad, `BWS_ACCESS_TOKEN_SDS` narrow) and give each override a
  distinct variable name, as `run-estate-landing.sh` does with `BWS_ACCESS_TOKEN_BROAD`. Found by
  two reviewers independently; proven with a pre-fix control that fails in both directions.
  Related and pre-existing across these launchers: the exit-code fold `for rc in 1 3 2` lets any
  code outside `{0,1,2,3}` — `127` for a missing binary — fall through to `exit 0`.

- **The scheduled local jobs read the MAIN TREE's working copy, so MERGING CHANGES NOTHING ON THIS
  MACHINE.** `com.devon.deploy-watcher` (and its siblings) invoke
  `~/Projects/orchestrator/scripts/run-*.sh`, whose `REPO_ROOT` resolves off `BASH_SOURCE`, and the
  program is `$REPO_ROOT/.venv/bin/<name>`. The step that is easy to forget is `git pull` in the
  main tree; **no `uv sync` is needed for a new module**, because the editable install is a bare
  `.pth` path append. The failure of forgetting is silent in the worst direction: the old launcher
  sets no new environment variable, the old CLI requires none, and the job keeps exiting 0 while
  the thing you shipped never runs.

- **The deploy-change-record population is DEPENDABOT BY CONSTRUCTION, so anything keyed on a
  change record can never see factory work.** `src/change_proposer/` is the only writer of
  `source=deploy` records and refuses `if not pull.get("is_bot")` (derived from
  `user.type == "bot"`); factory-runner opens pull requests with `FACTORY_PR_TOKEN`, a fine-grained
  PAT on the AlobarQuest **user** account, so GitHub reports `type: "User"`. Two consequences that
  are not obvious from either side alone: ADR-0022's unit-scoped observation can never fire, because
  the watcher only looks at rollouts that have a record; and **the factory lane into
  `change-manager` is blocked at `change_record_absent` for the same reason**. ADR-0022 and its
  handoff both name a different remaining condition ("a factory unit lands into a repository that
  deploys") and that condition is not sufficient. Backlogged P1 `6a98cb85fbae`. Confirmed against
  the one real landing: `2ba9f7f2`'s message carries `SDS-Change-Record:` and `SDS-Policy-Version:`
  and **no `SDS-Unit:`**.
  **The P1's open question — on what POSITIVE fact a factory pull request could be recognised — is
  answered, measured 2026-08-13 at source rather than guessed.** factory-runner stamps **two**
  machine-readable marks on every pull request it opens, both unconditional: the TITLE is
  `f"SDS {brief.work_unit.id}: {brief.work_unit.title}"` (`src/factory_runner/cli.py:911`), and the
  BODY opens `## Factory Runner Evidence` with a `Work unit:` line (`pr_body.py:24`) followed by
  package, package hash, source commit and authority fingerprint. Verified on all three factory pull
  requests in `intent-packages` — `#58`, `#62`, `#66`. So recognising one needs **no** change to
  factory-runner and no loosening of the bot filter: the positive assertion the P1 asks for is
  already being made. Opening as the Dispatch App remains the option that makes the *identity* true
  rather than the *marking* true, and it is the one that touches `FACTORY_PR_TOKEN`.

- **The estate-landing agent's exit 3 does NOT mean a record went unsettled — a closed pull request
  is classified `settled` and contributes no finding.** `_SETTLED`
  (`src/estate_lander/cli.py`) is `{landing_already_recorded, landing_pull_request_not_open}`, and
  the classifier tests it BEFORE `satisfied`, so a record whose pull request is gone exits the
  report rather than becoming an unknown. Measured 2026-08-13 from the agent's own first launchd
  run: *4 considered, 0 landed, 3 held, 1 settled* — the settled one was record 52, and the exit 3
  came from `#48`, `#49` and `#51` held on `landing_pace_exhausted`,
  `landing_update_type_unparseable` and `landing_checks_not_clean`. Settling record 52 was correct
  for ADR-0022's lifecycle argument and moved the exit code not at all (*3 considered, 0 settled*,
  still exit 3). **HQ asserted the opposite in both the ADR-0022 body and the increment handoff,
  from reasoning rather than from the log**, and a build session measured it before writing code.
  What would move that exit code is a DECISION — whether a pull request held on
  `landing_pace_exhausted` or `landing_outside_change_window` is a finding at all — not a fix. Read
  the per-pull-request lines before attributing an exit code to any one record.

- **Ruff 0.16 formats Python code blocks inside MARKDOWN, and 0.15 did not — so a ruff bump reds
  `make check` on documentation, not on code.** Measured 2026-08-13 across the seven factory repos
  with `uvx ruff@0.16.2 format --check .`: **50 files would be reformatted and 49 are `.md`** —
  `orchestrator` 31, `security-standards` 8, `change-manager` 4, `infraops-mcp-server` 4 (the only
  repo with a genuine `.py`), `project-standards` 2, `factory-runner` 1. **CORRECTED 2026-08-13
  during the fix: this bullet first said 58/57 and `security-standards` 16.** Eight of that repo's
  sixteen live in `.worktrees/deploy-policy-actor/`, an untracked stale worktree carrying its OWN
  `pyproject.toml` — so ruff resolves config from it and never sees the repo-root exclusion, and CI,
  which checks out a fresh tree, never sees any of it. **Measure this class of thing on a clean
  clone (`git archive HEAD`), not a working tree**, or you are counting scaffolding. Every repo
  pinned at `0.15.20` is green today and goes red the moment Dependabot bumps it; `change-manager#51`
  is the first of **five**, not six — `infraops-mcp-server` has no ruff dependency at all, no pin and
  no lockfile entry, so nothing can bump it and it could never have gone red. `intent-packages` reads
  0 because its 2026-08-07 remediation to
  0.16.1 already reformatted seven files. **Measured 2026-08-13, no longer an inference: that was
  `intent-packages#62`, titled `SDS ca1a9ddd…: Reformat embedded code blocks for ruff 0.16`,
  +371/-160, and every file was a Markdown plan or spec under `docs/superpowers/`.** The estate had
  already rewritten one repo's historical documents this way **through the factory**, with an
  authority envelope and two human approvals — the package author knew they were embedded code
  blocks, the title says so, so it was a choice that was simply never surfaced as a portfolio
  decision. Devon's ruling reverses it going forward and deliberately does not revert it. The affected population is ADRs and historical plan documents, i.e. **the record**,
  which is why this is a decision and not a fix. The remedy is `[tool.ruff] extend-exclude =
  ["*.md"]`, proven both directions against a clean clone: `--check` drops to 0, and a deliberately
  misformatted `.py` is still caught (a remedy that silenced everything would look identical
  without that control). `pyproject.toml` is **not** vendored by `code-standards`, so this cannot be
  pushed centrally — it is one edit per repo, **including for every repo onboarded after this date**.
  **CLOSED 2026-08-13: shipped to all seven factory repos plus `code-standards` itself, and recorded
  as code-standards ADR-0009** (`docs/decisions/0009-ruff-format-excludes-markdown.md`), which
  constrains ADR-0003 — that settled *which* formatter, not *which file types*. End-to-end proof:
  `change-manager#51` was red on `4 files would be reformatted, 86 files already formatted`, and
  after the exclusion landed and Dependabot rebased it, both its runs pass at **job** level.
  Two things the fix established that reading the config cannot tell you. (1) **A repo's `make check`
  can make this whole class of finding unreachable** — `infraops-mcp-server` has **no
  `pyproject.toml` at all** (it is declared `languages = ["ts", "shell"]`) and every ruff line in its
  Makefile is gated on `[ -f pyproject.toml ]`, so ruff never runs there. Creating one to hold
  `[tool.ruff]` **switches that gate on**, and `ruff check .` reports **20 errors** on its one
  never-linted script — turning a one-line hygiene change into a red gate. Use a root `ruff.toml`
  (bare top-level key, no table header) in any repo with no `pyproject.toml`. (2) **`ruff.toml`
  SUPERSEDES a `pyproject.toml` `[tool.ruff]` table entirely rather than merging with it**, so a repo
  that later gains one must fold the settings together or the pyproject's are silently ignored.
  Spec: `~/docs/software-delivery-system/2026-08-13-ruff-016-markdown-spec.md`; build report:
  `…/2026-08-13-ruff-markdown-exclude-build-report.md`.

- **Ruff CHANGED ITS `format --check` WORDING between 0.15 and 0.16, so a grep-based probe returns a
  silent false NEGATIVE across every repo.** 0.16 prints `unformatted: File would be reformatted`
  with the path on a following `--> path:line:col` line; 0.15 printed `Would reformat: <path>`. A
  sweep grepping the old wording reported **0 for all six repos** — clean, everywhere, and wrong.
  It was caught only by running the same command form against the one repo already **known** to
  fail, which is the estate's own *a probe must discriminate* rule paying for itself inside a
  five-minute measurement. Strip ANSI codes (`sed 's/\x1b\[[0-9;]*m//g'`) and match
  `^unformatted:`. Generalise past ruff: **when a tool's version is the variable under test, its
  OUTPUT FORMAT is part of what changed** — never carry a parse across the version boundary you are
  measuring.

- **The Dependabot auto-merge lane is deployed to 5 of 17 repositories, and the 35 pull requests
  stuck outside it are a COVERAGE gap, not a cascade defect — the census proves the cascade
  correct.** Measured 2026-08-13: 44 open pull requests estate-wide (41 Dependabot, 3
  `upstream-sync`). The five repositories carrying `dependabot-auto-merge.yml`
  (`intent-packages`, `security-standards`, `project-standards`, `infraops-mcp-server`,
  `factory-runner`) hold **6** open Dependabot pull requests between them and **every one is a
  major-version or requirement-range bump** — zod 3→4, eslint 9→10, typescript 5→7, checkout 4→7,
  setup-uv 5→7, a setuptools range. **Zero patch or minor bumps are stuck anywhere the lane
  exists.** So ADR-0018's cascade is doing exactly its job unattended, and the open queue is
  explained entirely by which repositories never got the workflow — `orchestrator` itself is the
  largest at 10 open with no lane, as are `change-manager` and `code-standards`.
  **But the lane CANNOT be vendored uniformly, and the reason is already measured elsewhere in this
  file: an auto-merge armed with `GITHUB_TOKEN` fires no `on: push` workflow.** Asked of App Brain
  the same day, landing is **inert** for `orchestrator` and `claude-octopus`, **redeploys** for
  `change-manager`, `brain` (four applications from one repository), `community-atlas`, `Contacts`
  and `agent-sites`, and **unknown / `no_app_record`** for `code-standards`, `rtk` and
  `n8n-as-code`. Native auto-merge is safe only in the inert set; every `redeploys` repository would
  land without deploying and diverge `main` from production silently. That the five laned
  repositories are all inert is why nobody has hit it. Deploying repositories belong on the ADR-0019
  landing lane instead, which is a policy decision (policy v1 names one repository deliberately) plus
  a change-proposer scope widening — `community-atlas`, `Contacts` and `agent-sites` have no change
  records at all. Plan: `~/docs/software-delivery-system/2026-08-13-toil-surface-onboarding-plan.md`.

- **A landing stales every sibling pull request, and `update-branch` clears it synchronously where
  `@dependabot rebase` takes ~14 hours.** `_freshness_term`
  (`services/estate_landing_admission.py`) calls `commits_behind_base` and refuses on `behind > 0`
  — correct, because checks are deliberately not up-to-date-gated estate-wide, so a squash of a
  behind head produces a tree nothing executed, and on a deploying repository that tree is what
  starts serving. But the lane therefore CREATES the condition it refuses on: `change-manager#51`
  landed 02:15 on 2026-08-14 and the three remaining windows that night could only re-report the
  same two staled siblings; `#49` had already sat **29 hours** behind.
  **Measured 2026-08-14, both mechanisms.** `@dependabot rebase` was posted on `#49` at
  2026-08-12 19:02 and Dependabot acted at 2026-08-13 09:20 — **14 hours** — rebasing onto main as
  it was then, which the next landing staled again; `dependabot.yml` there is `interval: weekly`,
  so unrequested it can wait a week. By contrast `PUT /repos/{owner}/{repo}/pulls/{n}/update-branch`
  took `#49` from `behind_by=3` to `behind_by=0` within about twelve seconds (head `487d6767` →
  `34a2fe1c`, `ahead_by` 1 → 2 as the merge commit lands, checks re-running).
  **BUT THE CONTRACT IS 202 ACCEPTED, NOT 200, AND THAT DISTINCTION IS LOAD-BEARING.** The endpoint
  accepts the request and performs the work afterwards; a client copying the sibling merge call's
  `!= 200` check reads **every success as a refusal** — silently, in the direction where the lane
  simply stops working while reporting that the remote declined. Nothing may re-read to confirm
  either, because the work is not done when the call returns. HQ wrote "seconds, synchronous" into
  the handoff by generalising a single probe observation into a claim about the contract; a build
  session caught it, and it was the one handoff error that would have shipped a broken lane. **An
  observed latency is not an API contract.** It needs `contents: write`, which
  the Dispatch App holds, and depends on nothing honouring a comment — note `@dependabot rebase`
  additionally assumes Dependabot obeys a **GitHub App**, which is unproven.
  Pass `expected_head_sha`: it is optimistic concurrency and refuses rather than clobbering a
  rebase that landed in between.
  **The rule for WHICH pull requests to update is the whole design: only one whose sole remaining
  obstacle is freshness**, i.e. every other refusal is one that clears on its own (the *deliberate*
  category). A pull request also carrying `landing_checks_not_clean` or
  `landing_update_type_unparseable` can never land whatever is done to its branch, so updating it is
  pure CI waste that reads as progress. `change-manager#48` (a requirement-range bump, permanently
  unclassifiable) is the standing live control: it must never be touched.

- **All four brain applications pull the SAME moving `:latest` tag, so one app pinned elsewhere
  would hang every deploy for the full verification deadline.** Established 2026-08-14 while giving
  `brain`'s rollout a revision check. `ci.yml`'s `build-and-push` pushes `${IMAGE_NAME}:latest` and
  `:${{ github.sha }}`, and the `deploy` job fires four Coolify webhooks — `infra`, `open`, `app`,
  `code` — against that one image, **skipping any whose `COOLIFY_APP_UUID_*` secret is empty**. So a
  revision poll must require confirmation only from the apps a run actually triggered: a skipped app
  keeps its old image and can never report the new revision, and requiring all four unconditionally
  turns a deliberate configuration into a 600-second hang. Separately, **Coolify's own health check
  is enabled on all four against `/api/health` with no response-text match**, so adding a field to
  that response is safe — worth knowing before extending any health endpoint the platform polls.
  Note `brain` has **no `deploy.yml`**: the deploy job lives in `ci.yml`, which is also the path any
  `WorkflowPin` must name (blob `c5c08871…` on `main` as of 2026-08-14).
  **SHIPPED 2026-08-14 (`#47`, merge `1d9e7d38`), and the run PROVES the per-app check was not
  fussiness.** The four brains swapped at **different times** — `infra-brain` reported the merged
  revision at 19:13:06 while `open`, `app` and `code` were still answering
  `<no revision reported>`; `open-brain` followed at 19:13:22. A poll that checked one brain and
  generalised would have passed at 19:13:06 with three of four still serving the previous image.
  That is the failure the design was written against, observed on its first live run. The whole
  swap took about 50 seconds from webhook to four `[OK]`s, against a 600-second deadline.
  Independently probed afterwards: all four report
  `{"status":"ok","revision":"1d9e7d38…"}` where the pre-merge baseline was `{"status":"ok"}` alone.

- **A mutation control's ATTRIBUTION is itself a claim, and it can be wrong while the mutation set
  still passes.** WS freshness-beside-an-exception, 2026-08-14: HQ's handoff named
  `{behind, checks_not_clean}` as the row that must red under "suppress freshness unconditionally".
  Measured, that row gives the **same answer with or without an unconditional subtraction** — it
  catches only the early-return form and misses both "add it to the suppressed set" forms the same
  handoff described. The row that actually carries the load is `{pace, behind}`, which the
  specification never mentioned, and it kills **five of ten** mutants. So three of the four
  fail-open forms were attributed to a control that cannot see them. **Compute which control kills
  which mutant as arithmetic before writing code, then confirm against the harness's own
  attributions** — a green mutation set says every mutant died, never that the control you *believe*
  killed it did. Same family as *a mutation set can only question the model its tests already hold*.

- **THE LANDING LANE HAS DRAINED `change-manager`'s LANDABLE QUEUE — three consecutive autonomous
  deploying landings, every one production-confirmed and self-settled.** Records 51, 52 and 53:
  `#50` merge `2ba9f7f2` (2026-08-13), `#51` merge `7fa3f829` (2026-08-14), `#49` merge `90306306`
  (2026-08-15 06:15:14Z) — each merged by `app/alobar-sds-dispatch` inside the window, each followed
  by production `/api/health` reporting that exact commit, each settled by the watcher with
  `attests=revision_confirmed` and no human acting. What remains open in that repository is `#48`
  alone, the permanent requirement-range exception. **A caveat worth carrying, because the counters
  say so: the freshness-update rule shipped in `#167` has fired ZERO times in production**
  (`0 updated, 0 would-update` on every run).
  **CORRECTED 2026-08-16, and the first word was the wrong one: the rule is NOT LIVE. It is merged
  and UNDEPLOYED.** Production's `EstateLandingAdmissionResponse` serves exactly seven keys —
  `change_record_id`, `head_sha`, `policy_version`, `pr_number`, `refusals`, `repository`,
  `satisfied` — and none of Increment 6's. The lander reads `branch_update_qualifies`, gets nothing,
  and **skips every record in its branch-update pass**. So `0 updated, 0 would-update` was never
  evidence about the rule's behaviour; it was the field being absent. `brain#33`–`#35` qualify
  today and are not being freshened.
  This is the estate's own **MERGED IS NOT DEPLOYED** invariant, walked past by HQ while reading
  those very log lines every morning and reasoning from them. The check is one command and it is
  the same one that bullet already prescribes:
  `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json; print(sorted(json.load(sys.stdin)['components']['schemas']['EstateLandingAdmissionResponse']['properties']))"`.
  **A log line reporting zero is not evidence the code that would report non-zero is running.**

- **Landing into `brain` deploys the service the landing lane CONSULTS — and the self-reference is
  safe, measured, in one direction only.** `estate_landing_admission.py` asks the estate what
  landing on a repository's default branch does (`landing_estate_source_unconfigured` /
  `_unreadable` / `landing_estate_unknown`), and that source is **App Brain**, one of the four
  applications a `brain` landing redeploys. Three facts make it survivable, and the third is the one
  to keep. (1) The admission read happens **before** the merge, so the deciding answer comes from
  the running container. (2) Coolify's swap is rolling, so App Brain answers throughout —
  **measured on `brain#47`**: `app-brain` reported `<no revision reported>` at 19:12:35 and 19:12:51,
  i.e. the old container serving, before reporting the new revision. The lane reads App Brain's
  *answer about landing behaviour*, which no deploy changes, not its revision. (3) It **fails
  closed**: an unreadable estate source refuses, and that refusal is in neither the deliberate nor
  the exception set, so it is a **finding** and the nightly control goes red.
  **The consequence to know: a `brain` deploy that left App Brain down would halt the landing lane
  for EVERY repository, not just `brain`** — the estate term is evaluated per subject and would fail
  for all of them. Nothing lands wrongly, and the control reports it the same night. This is the
  fourth self-reference in the programme after ADR-0015, ADR-0016 and the change-manager hotfix
  case; unlike a required-status-check scheme, this one has an in-band recovery, because the lane
  refusing does not prevent a human merging the fix.

- **A rollout workflow is a TRANSCRIBED artifact in another repository — changing it stales a
  cross-repo transcription and silently halts the producer that reads it.** `brain`'s `ci.yml` is
  transcribed in the orchestrator's `src/deploy_watcher/workflows.py` (`RolloutWorkflow` keyed by
  blob id, plus a verbatim copy of the step body), and `change_proposer` DERIVES a record's
  acceptance criteria from that transcription. Merging `brain#47` on 2026-08-14 moved the blob
  `6cad4cf9` → `c5c08871`, so from that hour every hourly pass refused all five `brain` pull
  requests: *"the rollout workflow revision for alobarquest/brain is not transcribed, so what a
  green run would prove is unknown; refusing to guess"* — 5 findings, exit 3, for a day, unnoticed.
  It fails closed and it says exactly what is wrong, which is the only reason this was cheap.
  **HQ merged that pull request having CAPTURED THE NEW BLOB SHA for the policy pin minutes
  earlier** — i.e. observed the blob had moved and did not ask what else consumed the old value.
  The estate already records this lesson for BWS UUIDs (*grep the whole portfolio for the UUID, not
  the repos you expect to own it*); it is the same rule in a different vocabulary. **When a pinned
  or transcribed artifact moves, grep every repository for the OLD value before merging**, and read
  the producer's log afterwards — the estate-landing and deploy-watcher logs were both green that
  morning while the proposer had been refusing for a day.
  Consequence for sequencing: a deploy-policy version admitting a repository is **inert without the
  matching transcription**, because the criteria a record must conform to are derived from it. The
  two land together; either order is safe.

- **`docker` is excluded from the Dependabot auto-merge cascade (ADR-0023), and the reason is that
  DOCKER TAGS ARE NOT SEMVER.** Dependabot maps a tag's digits onto semver positions mechanically,
  so a parseable tag like `postgres:16.2 → 16.4` reports `semver-patch` and `python:3.12 → 3.14`
  reports `semver-minor` — and ADR-0018's cascade arms on both "in every ecosystem".
  **CORRECTED 2026-08-15: `orchestrator#3` (`python:3.12-slim → 3.14-slim`) emits NO update-type at
  all** — `3.14-slim` does not parse as semver — so it is refused under the old condition too and
  **cannot be the acceptance test**, though HQ wrote it as one into the handoff, ADR-0023 and this
  file. Measured by running synthetic tags through GitHub's own expression engine. The decision is
  unaffected; the worked example was. That would auto-merge a language-version replacement that
  removes standard-library modules. The second ground fails too: the cascade permits github_actions
  *majors* because the gate exercises them, and for a base image it does not. **Measured, and
  correcting a first reading of mine that said nothing gates a Dockerfile change: `quality.yml` runs
  on `pull_request` and DOES `docker build` the real Dockerfile, so `uv sync --frozen` would fail on
  a dependency with no wheel for the new interpreter.** What it never does is RUN the image — no
  container is started and the suite executes on `setup-python` 3.12 — so a package that installs
  cleanly and fails at import on a removed module passes everything.
  **`orchestrator` is the ONLY repository declaring the `docker` ecosystem**, and none of the five
  carrying the cascade declares it, so the exclusion is a no-op until the lane is vendored to
  `orchestrator`. The workflow is not vendored by `code-standards` — one edit per repository, which
  is the clause a future onboarding will forget. Running the image in CI is what would earn the
  permission back and is deliberately not a prerequisite.

- **Vendoring the auto-merge cascade to `orchestrator` MADE ITS `main`-PUSH CI STOP RUNNING on every
  auto-merged landing, and HQ's own acceptance criterion for that increment was therefore
  unsatisfiable.** The cascade arms with `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`
  (`dependabot-auto-merge.yml`) and `quality.yml` triggers on `push: branches: [main]` — and a
  `GITHUB_TOKEN`-armed auto-merge fires **no** `on: push` workflow. Measured 2026-08-15 as a clean
  differential in one repository: the last push-triggered `Quality` on `main` is `080f23c6`, the
  Tier A merge **a human performed**, while the three commits the cascade merged minutes later
  (`484cf201` typer, `f1a1219e` alembic, `20973236` ruff 0.15.20→0.16.2) have **no `Quality` run at
  all**. The handoff asked the build session to *"watch main afterwards and say whether it stayed
  green"*; there was nothing to watch.
  The estate had already recorded this for the five original lane repositories — *"main can be red
  there with nothing reporting it"* — and it was not carried forward when the lane was extended to
  the repository with the largest suite, the one every build session branches from. **`main` was
  verified by running `make check` by hand: 3504 passed, 1 skipped.** That is the only check those
  three bumps have ever had together.
  **PROBE ANSWERED 2026-08-15, from data already in hand rather than a throwaway repository: an
  auto-merge armed by a NON-`GITHUB_TOKEN` identity DOES fire push runs.** Same repository, same
  workflow, same day, one variable. `#167` was armed with `gh pr merge --auto` under a **user**
  identity at 15:50 while `Quality` was still pending; GitHub merged it at 16:04 when the check
  passed, and a push `Quality` fired on the merge commit with `actor=AlobarQuest`. The three
  commits the cascade merged with `GITHUB_TOKEN` minutes later carry **zero** push runs. So the
  suppression is specific to `GITHUB_TOKEN` — GitHub's recursion guard — and not a property of
  auto-merge. **Before building a probe, check whether the experiment has already been run**: three
  weeks of merge history contained the differential, and the question had been open since
  2026-08-11 as something needing new apparatus.
  This proves the **PAT/user** half. The Dispatch App is a different identity and remains
  unmeasured, though the mechanism (a guard on `GITHUB_TOKEN` specifically) predicts it fires. The
  fix is therefore to arm with something other than `GITHUB_TOKEN`, and the open question is only
  *which* credential — a PAT (simpler, already present in six repositories, broader) or the App
  (the estate's machine actor, needs an App id and private key per repository).

- **In a batch of armed Dependabot pull requests, THE FIRST MERGE DISARMS THE REST, and nothing
  re-arms them.** Measured 2026-08-15 in `orchestrator`: four github_actions majors were armed at
  17:44 (all four gate runs `success`); `#5` merged at 17:59:48Z; and at **18:06:02Z** the timeline
  of `#4` records `auto_merge_disabled by github-actions[bot]`, the same for `#73` and `#112`. Six
  minutes after the first landing, the other three were clean, mergeable, green — and **unarmed**,
  with no further gate run, because auto-merge is disabled when a pull request transiently becomes
  unmergeable and is never re-enabled. They sit permitted-green-unarmed indefinitely, which is
  exactly the condition the landing ledger's audit reports as a finding.
  **This is the SAME defect the landing lane already has a fix for, in the other lane.** There, a
  landing stales its siblings and `update-branch` brings them up to date (WS `#167`); here, a
  landing *disarms* its siblings and nothing re-arms them. The self-healing that exists is
  Dependabot's own rebase, which fires a `pull_request` event and re-runs the gate — but that is on
  Dependabot's schedule, `weekly` in most of these repositories, so a batch drains one item per
  Dependabot cycle rather than one per merge. HQ cleared the three by merging them **by hand**,
  which also fires the push CI the cascade suppresses.
  Two consequences for onboarding a repository to the cascade: a queue does not drain by itself at
  the rate the arming suggests, and **the number of eligible pull requests that land unattended in a
  day is one**, not N.

- **A `schedule:` trigger added INSIDE a `code-standards:managed` block is deleted by the next
  `code-standards sync`, with nothing reporting it — and four of the six lane repositories were in
  a state where that would have happened.** Found 2026-08-15 while adding a daily `main`
  verification run, by a question the handoff never asked. Measured on `main` before the change:
  `security-standards` and `infraops-mcp-server` carried `code-standards:managed` markers wrapping
  the whole of `quality.yml`, so a trigger inside would be silently reverted; `intent-packages` and
  `project-standards` classified **adoptable**, a state `sync` writes markers into, reaching the
  same end by a slower route. `orchestrator` and `factory-runner` were already locally owned. The
  fix was ADR-0008's documented escape hatch — remove both marker lines, record in a header why and
  how to restore them — verified with the tool's own classifier (four would-write → all six local).
  **The shared template cannot carry the trigger instead**, because the cron minute is staggered per
  repository and most repositories vendoring that file are not in the lane it exists for.
  **Same run surfaced a pre-existing defect: `infraops-mcp-server` classifies STALE**, because
  Dependabot bumped four actions to v7 *inside* its managed block — so a `sync` there would have
  reverted all four. Named, not fixed.
  Generalise: **before adding anything to a vendored file, ask what re-vendoring does to it.** The
  failure is silent in the direction that matters — the trigger disappears and the check simply
  stops running, which is the permanently-quiet twin of a permanently-red control.

- **The daily `main` verification runs at 10:23–11:03 UTC, staggered one repository per 8 minutes,
  and the minute is chosen so an overnight landing is checked the same morning.** 10:39 UTC is
  06:39 EDT — just after the 02:00–06:00 window in which this estate lands changes unattended. Cost
  measured rather than estimated: **~28 billable minutes/day, ~850/month against 3000 included**,
  of which `orchestrator` is **89%** (its `Quality` ran 29 minutes on the very pull request that
  added this, above every previously sampled run). `infraops-mcp-server` and `factory-runner` are
  public and free. **A `schedule:` cron cannot be proven before merging**, because it only fires
  from the default branch — the confirming check is
  `gh run list --workflow=quality.yml --event=schedule` the following morning, and a first run
  arriving ~26 minutes late is GitHub's scheduler rather than a broken cron.

- **BEING BEHIND BASE CAUSES A CONDITION THAT DISQUALIFIES A PULL REQUEST FROM BEING BROUGHT UP TO
  DATE. That is a deadlock, and it is blocking every `brain` landing right now.** Found 2026-08-16,
  the morning after policy v3 admitted `brain`. The chain:
  `_rollout_term` reads the pinned rollout workflow's blob on **both** the base and the head — and
  the head read is load-bearing, because a pull request whose own diff edits the rollout workflow
  passes a base-only check by construction. `brain#31`–`#35` were opened before `brain#47` changed
  `ci.yml`, so their heads carry the OLD blob `6cad4cf9` against a pin of `c5c08871`, and every one
  refuses with `landing_rollout_moved`. Measured: base blob **matches** the pin, head blob differs,
  nothing has touched `ci.yml` since 08-14.
  **`landing_rollout_moved` is not a deliberate refusal and not an exception, so it is a real
  condition — and the freshness rule updates only a pull request whose SOLE remaining obstacle is
  freshness.** So the five are behind base, refused for being behind base, and ineligible for the
  one mechanism that would bring them up to date. `update-branch` would merge `main` into each head,
  carrying `c5c08871` with it, and the pin would then match.
  **The fix is narrow and the guard survives it:** when the BASE blob matches the pin and the head
  is behind, the mismatch is staleness rather than divergence, so `landing_rollout_moved` is
  self-clearing and must not block freshening. A pull request whose own diff edits the rollout
  workflow still differs after the update and is still correctly refused.
  Generalise: **when a refusal can be CAUSED by the condition another rule exists to clear, the two
  rules deadlock.** The eligibility test must be written against refusals that are genuinely
  independent of freshness, not merely against the ones that were live when it was written.