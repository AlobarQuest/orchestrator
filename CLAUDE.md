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
  transaction (`arm_verification_head`, called from the SUBMIT transition) must never commit. A
  test that asserts persistence must `expire_all()` and re-read, or it is asserting that a call
  returned an object.
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

- **This system has THREE vocabulary mismatches, and nothing checks any of them.** Wherever two
  vocabularies must agree, assume they don't until you have grepped both sides. All three below
  surfaced in a single workstream (WS-P2.15) and none was caught by any test:

  1. **`evidence_type: automated_test` resolves to `judgment_required` in the verifier.**
     `DETERMINISTIC_TYPES` is `{test, tests, pytest, runner.verification, gate.summary,
     security.scan, github.checks, health.probe, automated_check, …}` and `JUDGMENT_TYPES` is
     `{human.review, code_review, judgment, manual, automated_test, human_review,
     external_attestation, observation}` (`services/verifier_evaluators.py`). **MECHANISM
     CORRECTED 2026-07-28:** this bullet used to say `automated_test` was in *neither* set and
     fell off the end of `DETERMINISTIC_TYPES`. That was true when written; **WS-P2.16 U4 moved it
     INTO `JUDGMENT_TYPES`**, so it is now a *named* judgment type — the same outcome by a
     deliberate route rather than by omission, which is exactly the difference between "a typo"
     and "a decision". **The consequence is unchanged and still binding:** `evaluate_criterion`
     returns `judgment_required` for **every automated AC, however good the evidence**, so package
     authors must declare `evidence_type: "test"` and never `automated_test` until remediation
     2.1/2.2/2.3 ship together (`docs/operations/production-drill-adaptations.md` carries the
     authoring rule). This is the real
     root of the known "judgment_required ACs must be passed out-of-band via the verifier M2M
     credential / no adjudication form in `/review`" gap. **It is a vocabulary gap, not a UI gap** —
     fixing the UI would not fix it. `automated_check` is now a deliberately narrower supported
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
     - **orchestrator:** `grep -rn "github.pr.create" src/` → **zero hits.** Nothing reads it, and
       **nothing validates capability names at ingress at all** (`_validate_unit_constraints`
       checks `constraints` and `conformance` only). ADR-0001 defers the
       package-authority → unit-capability projection (`pr_open` → `github.pr.create`) to "the
       decomposition author". So the orchestrator will accept **any string** as a capability.
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
  approved Keychain helper `~/Projects/vps-backup/bws-token.sh` (service `Claude`, account
  `BWS_ACCESS_TOKEN_VPS_BACKUP`), then `bws secret get <uuid>` — never echo any value.
  **SYSTEM** (`orchestrator-system`, decomposition-submit / `commands/ready` / dispatch):
  `221a48d5-3f29-4898-b300-b4820140c880`. **VERIFIER** (`orchestrator-verifier`,
  `verifier-evidence/named-check` + `/verify`): `660d5846-abcb-4751-be86-b483012899eb`. Every
  M2M call sends both `Authorization: Bearer <token>` and `X-Credential-Key-Id: <key-id>`.
  Read endpoints (`status-ledger`, `runner-brief`, …) also require the SYSTEM bearer — a
  bare GET is `401`. (Verified 2026-07-22, AC-003.)

- **`factory decompose` (intent-packages) needs three env pieces the tool does not set itself,
  and the module has no `__main__`/installed console script.** Invoke it as
  `python -c "import sys; from intent_packages.factory_cli import main; sys.exit(main(sys.argv[1:]))" decompose …`
  (a bare `python -m intent_packages.factory_cli` imports but runs nothing → exit 0, no output;
  `.venv/bin/factory` is not installed). Required env: (1) the orchestrator console script on
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

- **Adding a route — `/api` OR `/review` — or a new `src/orchestrator/` module trips a FAMILY of
  architecture guards. There are FIVE, and three are exact set-equality inventories that fail CI on
  a missing entry.** The `test_ws32_scope_guards.py` word guard (bare tokens `deploy`/`dispatch`,
  with the `WS42_DISPATCH_PATHS`/`WS53_POST_DEPLOY_PATHS` allowlists) is the one this file documents
  elsewhere — but it is not alone. (1) **`test_ws33_scope_guards.py` forbids the bare word `merges`
  (and merge-path phrases) anywhere under `src/orchestrator/` with NO allowlist** — a module docstring
  saying "never dispatches, deploys, or merges" reddens it; reword (e.g. "writes to git"). Note the
  tokenizer matches whole tokens: `merges`→forbidden, but `deployment`/`deployments` do NOT match
  `deploy` and `dispatches` does NOT match `dispatch` (only the exact bare token does). (2)
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
  `docker buildx build --push` to `ghcr.io/alobarquest/orchestrator:<short-sha>[-<label>]-amd64`.
  **The workflow only builds and pushes — it never deploys.** Pointing Coolify at the new tag
  stays a separate, manual gate, same as before.
  Two digests, two different jobs, not competing checks: the **bundle digest**
  (`REGISTRY_ARTIFACT_SHA256`, currently `7aea8471…` per the pin file) is the **build-time,
  security-critical** gate — the Dockerfile's `registry` build stage recomputes it from the
  shaped context and **fails closed** on any mismatch (wrong/tampered actor registry), whether
  the build runs in CI or by hand. The **image SHA / running container's `RepoDigest`** is the
  separate **deploy-time identity** check Devon still does by hand after the Coolify swap —
  proving prod is running bit-for-bit what the workflow pushed. Bumping either digest requires
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
  digest after Coolify swaps. A plain `docker build .` with no `registry` context fails at
  `COPY --from=registry` — that is expected, not a Dockerfile bug. (Verified 2026-07-25, WS-P2.5
  Inc 2 deploy. Automation added 2026-07-26, image-build-automation workstream.)

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

- **Closing the bounded dispatch window RESTARTS the orchestrator, and a restart while a dispatched
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
  `is_over_budget` is consulted only inside `_readiness_eligibility_error` (`services/claims.py`),
  which runs on reclaim and requeue, so it decides whether a unit may be *claimed again*. Nothing
  checks spend mid-run. GAP-4's envelope declared `max_llm_calls: 4` and attempt 3 recorded
  **15** (`attempt.cost_recorded`, 23 turns, $0.176) and completed normally. The practical cap on a
  single attempt is the workflow's `max_turns` literal, which is a separate number in
  factory-runner's workflow YAML and is not derived from the envelope. Read the field as
  "budget remaining before another attempt is allowed", not as a spend cap. (Verified 2026-07-29.)
