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
  those paths — see the `orchestrator-promotion-human` router (WS-6.3). Quirk:
  the first same-origin POST behind forward-auth can return the app's 401
  (fetch follows the auth 302 and degrades to GET); the immediate retry works.
- Production observation ingestion requires an `ActorRole.SYSTEM` actor. The
  standing M2M credential is worker-role, so closeout-style observations use a
  temporary credential: merge into `ORCHESTRATOR_M2M_CREDENTIALS` + map it in
  `ORCHESTRATOR_M2M_ROLES`, restart, use, then revert. Verify EVERY env write
  landed before restarting — a roles entry without its matching credential
  fails startup validation closed and takes production down.
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

  1. **`evidence_type: automated_test` matches NOTHING in the verifier.** `DETERMINISTIC_TYPES`
     is `{test, tests, pytest, runner.verification, gate.summary, security.scan, github.checks,
     health.probe, …}` and `JUDGMENT_TYPES` is `{human.review, code_review, judgment, manual}`
     (`services/verifier_evaluators.py`). `automated_test` — which is what intent packages
     actually declare — is in **neither**, so `evaluate_criterion` falls through to
     `judgment_required` for **every automated AC, however good the evidence**. This is the real
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

- **There are TWO kinds of approval, and the `/review` button records the one readiness
  does not want.** `POST /review/units/{id}/approval` (`web.py`) hardcodes
  `subject_type="action"` — which satisfies the `AWAITING_APPROVAL → READY` transition
  guard. But readiness and dispatch both require an **`authority`** approval:
  `subject_type="authority"`, bound to `subject_revision_or_fingerprint ==
  unit.authority_fingerprint`, setting `unit.authority_approval_id`
  (`persistence/repositories.py::exact_authority_approval`). **There is no authority-approval
  form anywhere in `/review`**, and `record_approval` calls `_require_human` while **no HUMAN
  M2M credential exists** — so `orchestrator record-approval` can never run against
  production either. Every unit's authority approval is therefore given by pasting a
  `fetch()` into browser devtools. Same shape for package intake (`_require_human`, no POST
  route). **Three gates require a human; one has a form.**

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
