# CLAUDE.md invariants archive

Incident history, superseded text and the full original wording of every rule CLAUDE.md restates. Each entry keeps its original bullet number, which CLAUDE.md's History pointers use. Entries are verbatim and are not maintained: where one disagrees with CLAUDE.md or the code, those are current.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #1

- Dependency-update repo.edit authority is not executable unless the fingerprinted envelope
  declares a non-empty mutation_commands list that is an ordered subset of allowed_commands.
  Proposal admission and dispatch both enforce this; existing approved envelopes are never
  rewritten to comply.

### #2

- **Everything in this section must stay BELOW `<!-- code-standards:end -->`.**
  `code_standards.stanza.inject_stanza` replaces the whole `start`…`end` block in
  place and preserves only what surrounds it, and the canonical stanza template
  contains no invariants section. Until 2026-07-09 these bullets lived *inside*
  the block, one `code-standards init`/`sync` away from silent deletion.
  Verify with: re-rendering the block over this file must be a no-op.

### #4

- Generic authority approvals satisfy work-unit readiness only. Authority-expanding
  standing-context updates require a named human approval bound to the exact
  standing-context fingerprint — enforced by `classify_context_update()`
  (`kernel/context.py`) via `services/lifecycle/context.py::_effective_decision`.
  **That is the STANDING-CONTEXT check, and it is not the whole story.** It compares
  capability *sets* and authority-profile *rank*. It does **not** check capability
  *levels* or *budgets*. **Work-unit envelope expansion — including budget expansion —
  has NO detector at all.** `is_expansion()` was that detector; it had zero callers and
  WS-P2.15 deleted it. This is safe only because the envelope is **write-once** (assigned
  at construction, in exactly two places), which
  `tests/architecture/test_authority_write_once.py` now enforces. **If you add a path that
  raises a unit's budget or capabilities by mutating the envelope, that test will fail —
  and you must ship a fail-closed expansion check with it.** Do not "fix" the test.

### #5

- Protocol smoke tests may manipulate time or lease expiry as deterministic fixture
  setup. Runtime recovery behavior itself must go through public API/CLI surfaces,
  not private service shortcuts.

### #8

- Generated post-deploy acceptance criteria are verifier-owned. Public
  adjudication must reject generated post-deploy AC IDs, so post-deploy completion
  always flows through the WS-5.1 verifier evaluators and lifecycle guards.

### #11

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

### #13

- The work-unit authority envelope is a **cross-repo contract** with
  `AlobarQuest/factory-runner`, not a local data shape. It is pinned by a
  byte-identical `tests/fixtures/runner_authority_envelope.json` in both repos
  and the same `CONTRACT_SHA256` in `tests/contract/test_runner_envelope_contract.py`
  here and `tests/test_orchestrator_envelope_contract.py` there. Changing the
  envelope means changing both repos together; a one-sided edit fails the repo
  that was not updated. Before WS-6.4.0 no test crossed this boundary, and the
  two sides had silently diverged into mutually unsatisfiable fixtures.

### #14

- **Dispatch and execution attempts have independent ordinals.**
  `DispatchRecord.runner_attempt` counts dispatch decisions, including skipped
  decisions; `WorkUnit.attempt_count` counts worker claims. Bind verifier evidence
  to the exact dispatch row and current claim artifacts, and never require these
  counters to be equal; see `docs/operations/verifier.md`.

### #15

- `AuthorityEnvelope.normalized()` defines what a human's authority approval
  actually attests. Fields outside `KNOWN_FIELDS` contribute only their *names*
  to the fingerprint, never their values — so a field carrying real authority
  (where code ships, which change class, what conformance was claimed) MUST be a
  known field. **Adding to `KNOWN_FIELDS` rewrites every authority fingerprint**,
  so its cost is proportional to the live ledger: free on 2026-07-09 (2 completed
  units, empty ledger), expensive later. No data migration is needed for existing
  units — every `authority_fingerprint()` call site is at write/activation time,
  and readiness compares stored columns rather than recomputing.

### #16

- factory-runner refuses to act unless `target_repo == current_repo`: it may only
  mutate the repository it checked out (`factory_runner/authority.py`). Dispatch
  must therefore resolve the target repository **per work unit**, from
  `authority.constraints.target_repository`, never from a process-global setting.
  A global target does not fail closed — it silently misroutes every fan-out unit
  to whichever repo was configured at process start.

### #17

- **[NARROWED 2026-10-01 by ADR-0051: the ws32/ws33/ws34 word guards read CODE only --
  identifiers, imports, and string constants with no whitespace that are not docstrings.
  Prose, docstrings included, no longer reddens them, ws33's bare `merges` is gone, and
  ws52/ws53/ws61 are deleted. What follows is the record of the old behaviour.]**
  **The scope guard covers ALL of `src/orchestrator/`, not just `kernel/`** — an earlier
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

### #18

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

### #23

- **`constraints.allowed_commands` is an ordered command list the worker re-executes,
  not a permission set.** `finalize-run` runs **every** entry, in envelope order,
  and only then checks `git status` before committing. So (a) anything authorized
  *will* run again at finalize — there is no coding-phase-only grant; and (b) a
  mutator listed after the verifier means the recorded evidence (`"make check:
  passed"`) attests to a tree that is not the one pushed. Order mutators first,
  the verifier last.

### #27

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

### #30

- **`work_units.version` has exactly THREE writers, and recording an adjudication is not one of
  them.** They are `services/lifecycle/lifecycle.py::_perform_transition`,
  `services/lifecycle/claims.py::_transition` and
  `services/verifier/evidence.py::_system_fail_without_new_attempt` — every one a state transition.
  Consequently a submission's single `expected_version`, checked once against the locked unit row,
  stays valid for every criterion in it: it guards against another actor **transitioning the unit**
  between render and submit, not against a sibling criterion. Any note claiming that the old
  per-criterion adjudication forms staleness-broke each other is **wrong**; the real WS-P2.13 AC-002
  defects were the missing atomicity (a refusal on the third criterion left the first two committed
  — fixed in WS-P2.17 Increment 3) and a `<select>` whose first option defaulted to `passed`. Note
  the Increment 3 docstring on `record_adjudications` says "the only two writers", omitting
  `_perform_transition`; the count is three. (Verified 2026-07-31 by grep, WS-P2.17 Inc 4.)

### #31

- **`work_units.updated_at` cannot be back-dated — a DB trigger rewrites it on EVERY update.**
  `set_work_unit_updated_at` (migration 0001) sets `NEW.updated_at = now()` on any UPDATE, so a
  test or drill that "ages" a unit by writing `updated_at` is silently overwritten and its
  assertion quietly tests nothing. Exercise staleness by **shrinking the threshold**, never by
  ageing the row (`reconcile_split_brain_stall_seconds` and
  `dead_letter_stalled_approval_seconds` are both env-overridable for exactly this). The upside:
  for a unit parked in an approval state, nothing else touches the row, so `updated_at` genuinely
  IS "when it entered that state".

### #34

- **Ordinary terminal lifecycle commands release active claims in the same transaction.** A
  WORKER transition to `FAILED` uses `work_unit_failed`; a HUMAN transition to `CANCELLED` releases
  the latest unreleased claim, when present, with `work_unit_cancelled`. Both paths go through the
  sole `services.lifecycle.claim_release.release_claim` primitive and reuse the transition
  timestamp. Keep idempotent replay before release and preserve the unit-then-claim row-lock order,
  or retries can mutate terminal metadata and concurrent lifecycle operations can deadlock.

### #35

- **This system had THREE vocabulary mismatches, and at the time nothing checked any of them.**
  Wherever two vocabularies must agree, assume they don't until you have grepped both sides. All
  three below surfaced in a single workstream (WS-P2.15) and none was caught by any test. Read the
  per-item corrections: #1's consequence was closed by WS-P2.17 and #3's orchestrator half by
  WS-P2.16; #2 stands unchanged. The lesson, not the inventory, is the durable part.

  1. **`evidence_type: automated_test` resolves to `judgment_required` in the verifier.**
     `DETERMINISTIC_TYPES` is `{test, tests, pytest, runner.verification, gate.summary,
     security.scan, github.checks, health.probe, automated_check, …}` and `JUDGMENT_TYPES` is
     `{human.review, code_review, judgment, manual, automated_test, human_review,
     external_attestation, observation}` (`services/verifier/verifier_evaluators.py`). **MECHANISM
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
     `DETERMINISTIC_PERMITTED_TYPES` / `floor_for()` live in
     `services/verifier/verifier_evaluators.py`; an unknown or absent criterion type floors to
     human, fail-closed. **`JUDGMENT_TYPES` had THREE consumers and Inc 1 moved only one —
     evaluation — which opened a fail-open that reached `main`: a human could record `passed` on an
     `automated_test` criterion the verifier would now resolve.** WS-P2.17 Inc 2 closed it. All
     three now route through `human_may_adjudicate(declared_type, evidence, unit_state)`
     (`services/verifier/verifier_evaluators.py`): evaluation, authorization
     (`evidence._authorize_outcome`), and the `/review` form's per-criterion flag (`web.py`, renamed
     `is_judgment` → `human_may_decide`), the last pinned to the first by set-equality test. A human
     may decide when **(a)** the floor is `human`, **or (b)** the floor is deterministic-permitted,
     the current evaluation is `judgment_required`, **and the unit is in `awaiting_review`**. Clause
     (b) is load-bearing, not a convenience: Inc 1 made deterministic-floored-but-asking a common
     state, and without it those criteria are adjudicable by **no actor at all** — the unit can
     neither complete nor be failed. It also replaces the old `# A-static:` comment's protection,
     guarding the `automated_check`-before-CI window **by timing** rather than by declared type,
     which was only ever a proxy for it. A HUMAN may now also record `failed` (it was VERIFIER-only,
     and the verifier records nothing on a criterion it deferred, so nobody could fail a judgment
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
     decomposition proposal want the criterion's **database UUID**
     (`services/intake/decomposition.py` builds its lookup on `str(criterion.id)`), while **evidence
     and adjudication want the human string** `"AC-001"` (`criterion.ac_id`). Same field name,
     opposite meanings, and the failure is a bare `package_acceptance_criterion_not_found` with no
     hint.
  3. **`github.pr.create` is validated as a NAME and ignored as a PERMISSION — and the orchestrator
     does neither.** Be precise here, because a first draft of this entry was wrong:
     - **orchestrator: STALE as of WS-P2.16 — corrected 2026-07-31.** This used to read
       "`grep -rn "github.pr.create" src/` → **zero hits**; nothing reads it, and nothing validates
       capability names at ingress at all (`_validate_unit_constraints` checks `constraints` and
       `conformance` only); the orchestrator will accept **any string** as a capability." All three
       clauses are now false. `github.pr.create` is a member of the capability vocabulary
       (`capability_vocabulary.py`) and IS read as a permission —
       `services/lifecycle/lifecycle.py` gates PR-opening on `envelope.level_for("github.pr.create")
       == "allowed"`. Names ARE validated at ingress: `validate_unit_capabilities`
       (`capability_vocabulary.py`) is called from both `services/intake/packages.py` and
       `services/intake/decomposition.py`, so an unknown capability string is a named error at the
       gate. ADR-0001 still defers the package-authority → unit-capability projection (`pr_open` →
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

### #36

- **A FOURTH vocabulary mismatch, and its failure mode is the opposite of the first three: the
  correct answer WAS known, written down, and commented — in a sibling file — and nothing carried
  it across.** `intent-packages`' per-profile `TAG_TO_EVIDENCE_TYPE` maps evidence tags to the
  orchestrator's criterion vocabulary. `dependency_update.py` maps `ci:`/`gate:` →
  `automated_check` above an explicit comment: *"Never automated_test: it resolves to
  judgment_required in the verifier … which is exactly what automated_check evaluates
  deterministically against."* `maintenance_remediation.py` matches it. **`software_delivery.py`
  maps EVERY automated tag — `ci:`, `gate:`, `scan:`, `health:`, and even `review:` — to
  `automated_test`**, and the orchestrator's named-check ingestion refuses anything but
  `automated_check` **server-side** (`services/verifier/verifier_evidence.py:271`, not merely in a
  CLI verb — a reviewer placed it in the CLI and was wrong). Consequence, measured by the WS-P2.35
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

### #44

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

### #45

- **`claim_unit` is NOT the only place a unit is granted an attempt — `reclaim_expired_claim`
  bypasses it.** `reclaim_expired_claim` → `_perform_reclaim` → `_acquire_reclaimed_claim`
  (`services/lifecycle/claims.py`) transitions an expired unit and grants a fresh CLAIMED attempt
  **without ever calling `claim_unit`**. So any per-attempt gate placed only in `claim_unit` (e.g. a
  budget cap) is silently bypassed when a lease expires. The choke point for "may this unit get
  another attempt?" is the shared `_readiness_eligibility_error` (`claims.py`), used by BOTH reclaim
  and requeue — it is where `attempts_exhausted` lives and where WS-P2.4 Inc 2 added the
  `is_over_budget` gate. Any future "can this unit run again" rule belongs there, not (only) in
  `claim_unit`. (Verified 2026-07-25, WS-P2.4 Inc 2 — the final whole-branch review caught an
  over-budget unit running past its cap via the reclaim path; per-task reviews and the plan's own
  claim-only decision missed it.)

### #46

- **The evidence-pack `/api` is authentication-only (any authenticated actor reads any unit's full
  pack); the markdown relayed onto a possibly-public PR comment is deliberately REDACTED, the JSON
  is not.** `GET /api/v1/work-units/{id}/evidence-pack` (JSON) and `/evidence-pack/markdown` take
  `_actor: ActorDep` with no role gate — the runner's worker credential reads them, consistent with
  `runner-brief`/`status-ledger`/`history` (all auth-only). Because factory-runner posts the
  **markdown** as a comment on the target repo, **which may be public**, the markdown renderer
  (`services/reporting/evidence_pack.py::render_evidence_pack_markdown`) omits approver
  identities and waiver rationale (`decided_by`, `rationale`, `approved_by`, `reason`, event
  `actor_id`) while keeping the facts. The **JSON stays full-fidelity** (auth-gated, for
  WS-P2.6/audit). The redaction is hand-edited per section — a new markdown section that
  interpolates those fields must redact them by hand until a structural allowlist exists
  (backlogged). A `text/markdown` route also needs an entry in `NON_JSON_SUCCESS_PATHS`
  (`tests/api/test_lifecycle_api.py`) to satisfy the every-success-response-has-a-json-schema
  invariant. (Verified 2026-07-25, WS-P2.5 Inc 1 — the public-exposure decision was the final
  review's one Important finding.)

### #48

- **A FastAPI `response_model` silently DROPS every key the service returns but the model does
  not declare — so "the service returns it" is never evidence "the worker receives it".**
  `runner_brief_route` declares `response_model=RunnerBriefResponse`. WS-P2.12 added an
  `enrichment` key to `services/intake/runner_brief.py`, every service-level assertion passed, and
  the HTTP body carried nothing, because the response model had not been extended. This is the
  WS-P2.1 shape (service correct, wire empty) in a new place, and it is invisible to exactly the
  test you would reach for: a cross-repo contract test that asserts on the **service dict**
  rather than the **served body** has its blind spot precisely where the consumer reads.
  factory-runner parses the body. Two consequences: (1) adding a field to any service backing a
  `response_model` route means editing the model in the same change; (2) a contract test for such
  a route must pin the model — `tests/contract/test_runner_brief_contract.py` asserts
  `set(RunnerBriefResponse.model_fields) == set(golden_brief())`, which needs no HTTP client and
  cannot drift. Note the failure direction is silent-drop, never an error.
  (Verified 2026-07-30, WS-P2.12.)

### #49

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

### #54

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

### #57

- **Alembic revision ids must be ≤32 characters — `alembic_version.version_num` is `varchar(32)`.**
  A longer `revision = "…"` string does not fail at authoring time; it fails at RUNTIME when the row
  is stamped, with `psycopg2.errors.StringDataRightTruncation` / `value too long for type character
  varying(32)`, aborting `alembic upgrade`. Keep the descriptive-but-short form (e.g.
  `0019_wsp27_tracker_recon`, 24 chars — not `0019_wsp27_tracker_reconciliation`, 33). `down_revision`
  points at the prior head's real (already-valid) id, so only a NEW revision id can trip this.
  (Verified 2026-07-27, WS-P2.7 Inc 2 migration 0019.)

### #58

- **[The local drills and the seeding routes named below were deleted by ADR-0049 on 2026-09-28;
  the four findings stand as constraints on any replacement suite.]**
  **Four things the LOCAL recovery drills structurally cannot exercise, all found by running them
  against production on 2026-07-27 (ADR-0005 disposition A, 5/5 PASS).** The local harness seeds
  and asserts in ways production does not permit, so a green local suite is silent on all of these.
  (1) **`seed_unit`'s seeding ROUTES are UNREACHABLE in production — but the functions behind them
  are not dead, so be precise about which is which.** `POST /api/v1/revisions` and
  `/revisions/{id}/work-units` both call `_require_human` (`services/intake/packages.py`) but sit on
  the M2M-only `orchestrator-api` Traefik router — so a browser gets 401 (identity stripped) and a
  SYSTEM bearer is rejected as non-human. **No actor can reach those two routes.** Their only
  callers are the `orchestrator register-revision` / `register-unit` CLI commands and
  `scripts/drill_common.sh`; the defaults `intake_source="manual_ws31"` /
  `activation_source="legacy_manual"` mark them as the WS-3.1 manual bootstrap path, superseded by
  intake → decomposition in WS-3.2. The *service functions* `register_revision` and
  `register_approved_unit` remain load-bearing — reached constantly via
  `services/intake/package_intake.py` (POST `/package-intakes`) and
  `services/intake/decomposition.py` (decomposition approval). So production units must be born
  through intake → decomposition → `/review` approval, and the two shipped CLI commands above cannot
  work against production at all. Corollary: an intake needs a genuinely
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

### #60

- **The traceability query's observation hop is unit-scoped, so most observation producers are
  invisible to it.** `services/reporting/traceability.py` filters observations on
  `subject_type="work_unit"` AND the unit id. An observation about a service, endpoint, monitor or
  environment — which is what every external monitor naturally produces — lands in
  `GET /api/v1/observations` and in nothing else. Do not treat "wired an observation producer" as
  "exercised the traceability chain's observation node"; WS-P3.0 wired the first producer and that
  node remains unexercised.

### #63

- **`deployment_observation` summaries are EXACT-key-set bounded, and the secret detector matches
  key NAMES, not just values.** `_require_keys` uses `set(payload).issubset(allowed)`, so any extra
  key is `deployment_observation_invalid: "… contains unbounded fields"`. **CORRECTED TWICE ON
  2026-08-25. First: there are FIVE summaries, not the four originally listed — `probe_summary` was
  omitted. Then, hours later, `deployment_observations` gained a SECOND SHAPE and this bullet now
  describes only one of them.** The five-summaries-all-mandatory rule holds for
  `kind = "container_image"` and is FALSE for `kind = "machine_local"`, which requires an
  `activation_summary` and **refuses all five**. A machine-local row also carries no `base_url`, no
  `deployment_url`, no `deployer` and **no post-deploy verification unit**, so
  `post_deploy_work_unit_id` is nullable and `Session.get(WorkUnit, None)` is reachable — on
  SQLAlchemy 2.0.52 that returns `None` after a `SAWarning` saying it *"may raise an error in a
  future release"*, i.e. a warning today and a break later. Its `environment` is pinned to
  `operator_machine` by a database CHECK, deliberately: the column is otherwise free-form under a
  regex, and one mistaken payload naming `production` would put a working copy into the answer for
  what is serving production. For the container shape: That last part is the load-bearing
  half: a machine-local activation has honest values for none of them, so the record as it stands is
  shaped for a hosted deploy only. `probe_summary` = `{probes}`, a non-empty list whose members are
  each exactly `{endpoint, method, name, status_code, expected_status_min, expected_status_max,
  observed_at}`; `auth_summary` = `{missing_m2m_status, configured_m2m_status}` (and
  `missing_m2m_status` **must** be `401`); `route_summary` = `{routes}`, each route exactly
  `{path, present}`; `dispatch_summary` = `{dispatch_enabled}`; `status_summary` =
  `{status, summary}`. Separately, a
  key merely *called* `missing_credential_status` is rejected as
  `deployment_observation_secret_rejected` — the detector reads the JSON path, so avoid `credential`
  / `token` / `key` in key names even when the value is an integer. Every one of these is a clean
  `DomainError`, never a 500. Same for adjudication `expires_at`, which must carry a timezone
  offset. (Verified 2026-07-28, WS-P2.8 deploy.)

### #67

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

### #74

- **`record_approval` enforces NO lifecycle state, for either subject type — an approval's reach is
  bounded by what CONSUMES it, not by what the service refuses.** Verified 2026-07-31 against
  `services/intake/packages.py`: its entire guard set is `_require_human`, `subject_type ∈
  {authority, action}`, unit exists, the `dependency_update_authority_violation` check (authority
  only), idempotency replay, and `expected_version`. **A human can record either approval on a
  `cancelled` or `completed` unit and a row is written.** Nothing about the unit's state stops it.
  What bounds the approval is downstream: an `action` approval is fingerprinted to `unit.version`
  and satisfies exactly one guard on exactly one edge (`AWAITING_APPROVAL → READY`), and an
  `authority` approval is consumed only when a unit is admitted for work. So on a settled unit both
  are inert rather than refused. **Reading the route alone gives you the opposite impression** — HQ
  asserted in a WS-P2.17 Inc 7 handoff that a cancelled unit's five action forms were "every one of
  which the service would refuse", and that was false for two of them. The `/review` page hides
  those two anyway, which is the one place it is deliberately narrower than the service; the
  justification is inertness, not refusal, and it is the increment's single judgment call.

### #77

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

### #78

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

### #80

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

### #81

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

### #85

- **[NARROWED 2026-10-01 by ADR-0051: the ws32/ws33/ws34 word guards read CODE only --
  identifiers, imports, and string constants with no whitespace that are not docstrings.
  Prose, docstrings included, no longer reddens them, ws33's bare `merges` is gone, and
  ws52/ws53/ws61 are deleted. What follows is the record of the old behaviour.]**
  **`coolify` is a forbidden runtime string literal in `src/orchestrator/`, in addition to the
  `dispatch` / `deploy` / `merges` tokens documented above.** WS-P2.18 Inc 1 reddened both
  `test_ws32_scope_guards` and `test_ws34_scope_guards` on the phrase "a Coolify application or
  database" in a **description string**. The full ws32 forbidden sequence list is `factory-event/v1`,
  `merge_pull_request`, `workflow_dispatch`, `factory-runner`, `production mutation`, `auto_merge`,
  `productionmutation`, `coolify`, `dispatch`, `deploy`; ws34 adds `gh pr merge`,
  `git push origin main`, `merge_to_main`. Compounds tokenize, so `post-deploy` matches `deploy`.
  **Reword; never add an allowlist entry.**

### #87

- **[REMOVED 2026-09-28 by ADR-0047 (schema 6) — historical until an image carrying it runs.]**
  The table and `require_live_subject` are gone from `main`; a document carrying `[grandfathered]`
  no longer loads, and every revision is refused for undeclared reach. The hazard below stands for
  production ONLY until that image is deployed.
  **The grandfathering table deletes itself, which couples it to deployment: if its last live
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

### #88

- **Minted follow-up units are created in `AWAITING_REVIEW` and are not normally admitted at all.**
  So requiring reach at mint time is right for a different reason than "they will be admitted": the
  point is that minting is the last moment a human is in the loop before the unit exists, not that
  the unit dispatches straight away. Note also the denominator — **7 approved revisions carry a
  `follow_up` block** (not 17 packages, which is a different set that `mint_due_follow_ups` does not
  iterate). Because minting now refuses rather than inheriting an unknown reach, none of those 7 can
  mint until reach is supplied — which is also why the grandfathering list needs one entry instead of
  twenty-one.

### #89

- **A lapsed lease does not merely permit a second claimant — it stops the FIRST worker recording
  what it already did.** `validate_active_claim` (`services/lifecycle/claims.py`) raises
  `claim_not_active` when `claim.lease_expires_at <= now`, and it is shared by evidence recording
  and PR-binding reporting. So shortening a lease is a correctness hazard, not a scheduling
  preference: a run that outlives its hold cannot submit its own evidence, and the natural first
  design — *give the fastest reach the shortest hold* — is the one that breaks runs. This is why the
  WS-P2.18 Increment 6 policy lease may only ever **lengthen** (`kernel/leases.py` bounds it
  strictly above `DEFAULT_LEASE` and at or below `LEASE_CEILING`) and why a reach set composes by
  **maximum**, the opposite arrangement from the change window, which composes by intersection. Both
  compose toward more restraint; restraint points the other way for a hold than it does for an hour.
  Neither the WS-P2.18 spec nor the Increment 6 handoff mentioned this, and without it the direction
  looks arbitrary. See ADR-0013.

### #90

- **`work_units`' lease has THREE writers, not the two the reclaim trap suggests.**
  `claim_unit`, `renew_claim` and `reclaim_expired_claim` → `_perform_reclaim` →
  `_acquire_reclaimed_claim` (`services/lifecycle/claims.py`), all now reading
  `services/lifecycle/lease_policy.py::claim_lease`. The documented trap is the third — reclaim
  never calls `claim_unit`, so a per-claim rule placed only there is ignored on exactly the path a
  lapsed lease leads to. **`renew_claim` is the one that gets forgotten after that**, because it
  extends rather than grants: a renewal that reset the hold to the kernel default would silently
  undo a considered one, on the path a long-running attempt takes by definition. Any future
  per-claim rule must name all three. Separately, `RENEWAL_CADENCE` (a 5-minute constant in
  `kernel/leases.py` since WS-3.1) had **no reader in either repository** and was deleted in
  Increment 6 — factory-runner renews only on an explicit `local-heavy-renew` command, so nothing
  renews on a cadence at all.

### #91

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

### #93

- **Adding a module under `src/orchestrator/` adds TWO collected tests by itself, so
  "baseline + tests I wrote" always under-predicts the collected count.**
  `tests/architecture/test_wsp21_invariant_scan.py` parametrizes
  `test_no_tracked_source_carries_a_secret` and `test_nothing_in_the_repo_merges_a_pull_request`
  **one case per source file**. WS-P2.18 Inc 8 added 17 tests to a 2148 baseline and collected
  **2167**. Reconcile a collected-count discrepancy by diffing node IDs between `main` and the
  branch (`pytest --collect-only -q | grep :: | sort`, then `comm`) rather than explaining it away
  — the two extra lines carry the new module's path in their parameter id, so the diff names them
  outright.

### #95

- **The gate-cleared population is smaller than the unit census, and reasoning from the census
  overstates it.** On 2026-08-02 there were 43 work units and **35** with a human authority
  approval bound to the unit's current fingerprint. The eight without are the ones no human ever
  gated: generated post-deploy verification units, a minted follow-up unit, and the three WS-P2.15
  units. Any question of the form "how has the gate performed" has 35 as its denominator, not 43 —
  the WS-P2.18 Inc 8 handoff used 43 and overstated the evidence base by a fifth.

### #96

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

### #97

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

### #99

- **A claim is NOT released when a unit COMPLETES — only on failure and cancellation — so
  "unreleased claim" carries no information about whether anything is wrong.** `release_claim`
  (`services/lifecycle/lifecycle.py`) is called with `terminal_reason="work_unit_failed"` and
  `"work_unit_cancelled"` and for nothing else; success leaves the row unreleased. Verified in
  production 2026-08-02: **29 of 43 units carry an unreleased claim whose hold lapsed days ago, every
  one of them on a finished unit.** WS-P2.19 found this by asking production before building, and it
  killed the obvious stall predicate — *unreleased claim + lapsed hold* would have reported the
  estate's entire history on its first run. It also means `released_at IS NULL` has no reachable
  failure case to guard, so a test asserting it passes for the wrong reason. Whether the asymmetry is
  intentional is undocumented; the claim is inert on a terminal unit, so it is harmless in itself —
  the damage is entirely in predicates built on it.

### #102

- **[NARROWED 2026-10-01 by ADR-0051: the ws32/ws33/ws34 word guards read CODE only --
  identifiers, imports, and string constants with no whitespace that are not docstrings.
  Prose, docstrings included, no longer reddens them, ws33's bare `merges` is gone, and
  ws52/ws53/ws61 are deleted. What follows is the record of the old behaviour.]**
  **`test_ws34_scope_guards` forbids the literal `github.actions`, and the CLAUDE.md list of ws34's
  forbidden strings omits it.** The full set in
  `test_ws34_adds_no_factory_runner_or_workflow_dispatch_code` is `workflow_dispatch`,
  `factory_runner`, **`github.actions`**, allowlisted only in `services/execution/dispatch.py`
  (the other three entries were unneeded and came out on 2026-09-28, #301) — a different and
  *smaller* allowlist than ws32's.
  WS-P2.20 reddened it on a constant whose value was `"github.actions.jobs"`; reworded to
  `"github.workflow_jobs"`. This is a *substring* match on the lowercased file text, not the
  whole-token tokenizer ws32 uses, so `github.actions.jobs` matches where `deployment` would not.
  Reword; never allowlist. (The separate ws34 list this file already documents — `coolify`,
  `gh pr merge`, `git push origin main`, `merge_to_main` — belongs to a *different test* in the same
  module.)

### #103

- **`evidence` rows are append-only at the DATABASE level, so a test that mutates a stored payload
  must do it in memory and never commit.** A `reject_append_only_mutation()` PL/pgSQL trigger raises
  `IntegrityConstraintViolation: evidence is append-only` on any `UPDATE`. The established pattern
  (`test_named_check_evaluator_revalidates_all_payload_bounds`) assigns `evidence.payload = …` on the
  ORM instance and calls `evaluate_criterion` directly — never `session.commit()` and never through
  `verify_work_unit`, which commits. A payload-corruption test written the obvious way fails on the
  trigger rather than on the assertion, which reads as a bug in the change under test.

### #108

- **`_blocked_reason` normalizes the authority envelope exactly once, and a test enforces it.**
  `services/execution/dispatch.py`. Any new admission term that needs the unit's target repository
  must be evaluated **inside** `_blocked_reason`, not computed by the caller and passed in — a second
  normalization is a second reading of the envelope, and the envelope is what a human's authority
  approval attests. WS-P2.28 added the reach term inside it for this reason.

### #109

- **[CLOSED 2026-08-03 by WS-P2.32 — the FIRST half only. Read the closing note at the end of this
  bullet before relying on it.]** **A VERIFIER credential could drive a unit to COMPLETED with ZERO
  evidence rows — the completion
  guard reads adjudications and structurally cannot read evidence.** `_completion_satisfied`
  (`services/lifecycle/lifecycle.py:473`) takes `(required_ac_ids, adjudications, occurred_at)`:
  there is no evidence parameter, so completion is decided on adjudication rows alone.
  `_authorize_outcome`'s VERIFIER branch (`services/verifier/evidence.py:966`) is `allowed = outcome
  in NON_WAIVER_OUTCOMES` with no evidence requirement, and `_validate_adjudication_fields` demands
  evidence only for `waived` (a `failed_evidence_id`) — `passed` needs a rationale string, and
  `evidence_id` is validated only when non-null. `(SUBMITTED→COMPLETED)` is a verifier-held edge. So
  POSTing `passed` with prose on each required AC completes the unit, and **everything WS-P2.20
  built — the App-token observation of the named check, unanimity, `failed_closed` on divergence —
  is bypassed by one POST.** The `orchestrator-verifier` credential is standing in production. This
  may well be intentional (the verifier as an out-of-band trusted actor) but nothing in the code
  says so, and it makes every WS-P2.20 guarantee conditional on a credential that also holds the
  bypass. The second half of the same hole: **the reconciliation lane detects reality CHANGING,
  never reality having been MISREPORTED** — `_detect_check` (`reconciliation_detection.py:321-342`)
  needs a prior *observed* success at the armed head before it will report a failure as a flip, and
  a claim that was never observed leaves no such prior, so the predicate is False and the detector
  returns silently without even incrementing `skipped_correlations`. There is no downstream net
  under this. (Found by WS-P2.31 2026-08-03, independently re-verified by HQ the same day.)
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

### #110

- **`budgets.max_attempts` is DECORATION; the enforced cap is `unit.max_attempts`, a different
  value on a different column reached from a different API field — and the name collision is what
  makes it invisible.** The envelope budget is parsed (`kernel/authority.py:105`), contributes to
  the authority fingerprint the human approves, and **has no enforcement reader**: the only
  `.budgets.` access in `services/lifecycle/budget.py` is `max_llm_calls`. What actually bounds
  attempts is the `work_units.max_attempts` column (`persistence/models.py:235`, defaulted from
  `DEFAULT_MAX_ATTEMPTS` via `services/intake/packages.py`), checked at `claims.py:79`, `:552` and
  `:590`, and raised by `authorize_retry` with no reference to the envelope at all. Nothing ever
  compares the two. **CORRECTS an earlier claim of HQ's** — a WS-P2.31 handoff asserted
  "`max_attempts` IS enforced, so one of the two budget fields is decoration", offered as the
  contrast that made `max_llm_calls` look like the outlier. Both halves were wrong: both envelope
  budget fields are decoration, and this is the worse of the two because the name collision hides
  it. (Verified 2026-08-03.)

### #111

- **The conformance anti-tautology rule is PROSE, not code — and the branch it would guard has
  never been reached in production.** `services/execution/dispatch.py`'s conformance gate carries a
  docstring saying `accepted_standards` "must come from a real waiver source … never echoed from
  `standards_touched`, or the subset branch below admits everything." **Nothing enforces it.** The
  gate is `if status == "green": return None` / `if touched and touched <= accepted: return None` /
  `return "conformance_not_green"`. Two consequences. (1) Anyone told "the anti-tautology precedent
  already exists in this repo" will go looking for a check that is not there — there is **one**
  exemplar of the observed-not-attested move (`services/verifier/github_checks.py`, WS-P2.20), not
  two. (2) **Do NOT close it by deleting the green short-circuit.** Green claims then fall through
  to the subset test, which is False whenever `accepted` does not cover `touched`; WS-P2.31 measured
  28 of 28 production conformance blocks as `status: "green"` with **0 echoes**, and the canonical
  cross-repo fixture `tests/fixtures/runner_authority_envelope.json` is
  `green / touched=['project'] / accepted=[]` — so the pinned envelope shape **both repos agree on**
  would be refused and every dispatch would stop. HQ proposed exactly that removal on 2026-08-03
  and was wrong; the error was checking what a permit stops permitting without reading what the
  fall-through then does — the WS-P2.18 Inc-4 withheld-refusal fail-open, mirrored into a
  fail-closed halt. Closing it honestly means the orchestrator OBSERVING conformance, which it
  structurally cannot: `compute_conformance_claim` needs a repository checkout, the orchestrator is
  push-only and checks out nothing, and the only other producer is the runner — i.e. the runner
  attesting to its own compliance. Backlogged P2 `7874128ae3ac` with a named trigger.

### #121

- **In `wave-exit-manifest.toml`, a clause rationale goes in `note`, never in `proves`.**
  `run_command_check` writes `proves` into the retained record **only when the check passes**, so a
  rationale placed there on a `fail` or `unavailable` clause is dead text by construction — which
  covers every clause that currently needs one. `_attest_clauses` writes `note` unconditionally.
  Corollary: **nothing dated or result-specific belongs in either field** — the day the check
  passes, the record would assert both the pass and a note saying it does not. Dated numbers belong
  in a build report. (HQ prescribed `proves` in the WS-P2.40 handoff and was wrong.)

### #124

- **[RETIRED 2026-09-28 by ADR-0049 with the drill scripts it guarded; kept as the rule a
  replacement drill suite should carry.]**
  **The architecture-guard family has a SEVENTH member: `tests/architecture/test_drill_scripts.py`,
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

### #125

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

### #126

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

### #128

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

### #134

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

### #138

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

### #140

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

### #143

- **`runner.caller` asks "can the factory send work INTO this repo?", and it is three different
  faults under one name.** It requires `.github/workflows/factory-runner-pilot.yml` calling
  factory-runner's reusable workflow at a full SHA equal to `RECOMMENDED_CALLER_PIN` (a one-line file
  at factory-runner's root). On 2026-08-03: `change-manager` and `brain` were BEHIND the pin
  (`b8049127` vs `b0305b51`); `intent-packages` and `security-standards` used **`@main`** — the
  GAP-4 class, pinned to nothing; `project-standards` and `factory-runner` had **no caller at all**.
  Since it is really a *dispatchability* check, applying it to every onboarded repo is a category
  error — `factory-runner` needing a caller means the runner would verify changes to itself using a
  pinned older copy of itself, a trust loop that should be decided rather than acquired by default.

### #144

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
  `work-units/{id}/preflight` and the three `/event-publications/*` — and
  `services/lifecycle/context.py` and `services/release/event_publications.py` contain **zero**
  `ActorRole` references between them. "The service layer gates writes" is not a property the
  service layer provides. Those four are a live defect for every other role (backlogged); OBSERVER
  is simply not exposed to them. (The three event-publication routes were deleted on 2026-09-28,
  Tier 3 item 24a; `preflight` is the one left.) **Proven against production 2026-08-07, not just in tests:**
  `commands/ready`, `dispatch`, `verify`, `preflight`, `event-publications/queue` and `/export` all
  **403**; `POST /observations` reaches request validation and a valid post returns **201**
  attributed to `drift-reconciler`; `GET /observations` returns 200. Note approval-shaped routes
  answer **302** from outside — they sit behind the human forward-auth chain at the proxy, so the
  request never reaches the app; that surface is covered by the in-process architecture test over
  all 49 confined routes, not by an external probe.

### #147

- **`change_class` is a FREE STRING matched against `ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES`,
  and that list was `["dependency-update"]` alone until 2026-08-03**, when Devon approved adding
  `maintenance-remediation` (standing, not per-run). `_optional_change_class` validates shape only —
  there is no closed vocabulary — and `_change_class()` falls back to `required_capability` when the
  field is absent, so an envelope with no `change_class` is matched on its capability name and is
  refused just the same. Widening this list is a standing authority change and outlives any window.

### #148

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

### #149

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

### #150

- **Capability LEVELS fail OPEN where unknown NAMES fail closed — the asymmetry is why levels went
  unvalidated for so long.** `level_for` returns `"prohibited"` for a name it does not know, so an
  unknown name was already refused (late, as `capability_not_authorized`). But it compares against
  `"allowed"`, so a *mistyped level* on a capability the work does not even need reads as a
  prohibition and satisfies every orchestrator gate, while the runner — which validates the level
  of every entry — refuses the whole envelope. `requires_approval` is the shape that actually
  occurs: it is the PACKAGE-authority vocabulary of ADR-0001, and projecting package authority into
  unit capabilities is left to the breakdown author, i.e. to a human writing JSON by hand.

### #154

- **The named-check evidence lane is closed to any criterion not declared `automated_check` — at
  INGESTION, not only in intent-packages' `factory verify` pre-check.** `record_named_check_evidence`
  (`services/verifier/verifier_evidence.py`) raises `evidence_subject_invalid: acceptance criterion
  is not a mapped automated check` unless the criterion's declared `evidence_type` is exactly
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

### #157

- **GitHub Actions run ids passed 2^31 long ago** (`31426195637` is one of this estate's), so a
  column holding one must be `BigInteger`. `Integer` is accepted silently by SQLite and raises
  `ERROR: integer out of range` only on Postgres — increment 1's int4 finding, one column over.

### #158

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

### #160

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

### #161

- **`httpx` raises THREE unrelated exception families for a malformed URL, and the third is a
  `ValueError`.** `except (httpx.HTTPError, httpx.InvalidURL)` looks total and is not: IDNA
  encoding of a malformed HOST raises `UnicodeError` — a `ValueError`, neither an `HTTPError` nor
  an `InvalidURL` — at `client.get`, before any body guard. Triggers are ordinary environment-
  variable typos: a **doubled dot** (`https://host..example`), a **DNS label over 63 characters**,
  a trailing dot. Both `services/landing/change_record.py` and `services/landing/estate_landing.py` promise in
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

### #163

- **`TransactionClock.now()` does not advance: it is `transaction_timestamp()`, frozen at
  transaction start.** Measured 2026-08-11: two reads two seconds apart in one transaction return
  the identical instant, while `clock_timestamp()` moves. Every admission term in this repository
  reads it, so a change window is judged at the instant the transaction OPENED — and
  `_land_unit_pull_request` then makes up to four outbound calls (App Brain, change-manager, and
  two GitHub calls) before it acts. The drift is seconds and the direction is the same as the
  pipeline overrun already accepted, so it stands as a decision rather than a defect; but "is the
  window about when the transaction started or when the act fires" is a real question and
  `clock_timestamp()` is the other answer.

### #164

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

### #168

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

### #170

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

### #173

- **A `source_reference` that is NOT content-addressed is right for an IMMUTABLE subject and wedges
  a producer permanently for a re-runnable one.** `services/release/observations.py` refuses a
  second observation at the same `(source_system, source_reference)` with different facts —
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
  found by two reviewers, one of whom measured it against a migrated database rather than reading
  it. Two smaller facts from the same surface: `record_observation` **RETURNS** its `DomainError`s
  rather than raising them, so a test reaching for `.id` fails with an `AttributeError` naming an
  attribute instead of naming the conflict (narrow with `isinstance` first); and `_fact_identity`
  covers `status`, `severity`, `observed_at`, `summary` and `facts`, so all five are part of what
  must not move.

### #177

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

### #178

- **The estate-landing agent's exit 3 does NOT mean a record went unsettled — a closed pull request
  is classified `settled` and contributes no finding.** `SETTLED`
  (`src/lander/core.py`, shared by both landers since Tier 3 item 27) is `{landing_already_recorded, landing_pull_request_not_open}`, and
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

### #179

- **Ruff 0.16 formats Python code blocks inside MARKDOWN, and 0.15 did not — so a ruff bump reds
  `make check` on documentation, not on code.** Measured 2026-08-13 across the seven factory repos
  with `uvx ruff@0.16.2 format --check .`: **50 files would be reformatted and 49 are `.md`** —
  `orchestrator` 31, `security-standards` 8, `change-manager` 4, `infraops-mcp-server` 4 (the only
  repo with a genuine `.py`), `project-standards` 2, `factory-runner` 1. **CORRECTED 2026-08-13
  during the fix: this bullet first said 58/57 and `security-standards` 16.** Eight of that repo's
  sixteen live in `.worktrees/deploy-policy-actor/`, an untracked stale worktree carrying its OWN
  `pyproject.toml` — so ruff resolves config from it and never sees the repo-root exclusion, and CI,
  which checks out a fresh tree, never sees any of it. **Measure this class of thing on a clean
  clone (`git archive HEAD`), not a working tree**, or you are counting scaffolding.
  **That worktree is GONE as of 2026-08-23 — do not go looking for it, and do not read its absence
  as the hazard being gone.** Removed after checking what removal would cost: branch
  `adr0019-deploy-policy-actor` at `9dd6847`, clean, twelve days old, and **both** its commits'
  content byte-identical to `origin/main` — landed via PR #35's SQUASH merge, which is why
  `git merge-base --is-ancestor` said "not in main" and was the wrong test. **Ancestry is always the
  wrong test in this estate**: squash-merging guarantees a branch commit is never an ancestor of
  `main` even when its content is there. Compare the FILES.
  The hazard itself was already narrower than this bullet implies: `make check` prunes `.worktrees`
  via `PRUNE_DIRS`, and ruff skips it via gitignore, so the repo's own gate was never distorted.
  What a stale worktree distorts is **ad-hoc** measurement — a bare `find`, a `grep`, a hand-run
  ruff — which is exactly how it produced the wrong count corrected above. 707 Python and 37
  Markdown files sat there for anything that walked the filesystem without pruning. Every repo
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

### #181

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

### #183

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

### #185

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

### #187

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

### #188

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

### #190

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
  **CORRECTED 2026-09-25 (ADR-0045), both halves of the "same defect" paragraph.** The landing lane
  no longer brings every staled sibling up to date — for Dependabot it edits one branch per
  repository at a time. And "the self-healing that exists is Dependabot's own rebase … on
  Dependabot's schedule" was inferred and is now measured, and it is weaker than stated:
  **Dependabot does not rebase a merely-behind branch at all** (0 of 51 weekly runs; 1 rebase from
  580 unrelated pushes). It pushes to a branch it owns only on conflict (about two minutes after
  the conflicting push), on a newer version, or on a user's request.

### #197

- **A factory pull request's change record is `change_class: factory-delivery` and sits OUTSIDE
  deploy policy v3, deliberately.** Shipped 2026-08-16 (`#179`). Reusing `dependency-update` would
  have had `_apply_policy` approve every factory record the instant it was proposed — a standing
  grant that machine-written changes may land unattended into a redeploying repository, made
  silently by a program rather than decided. So a factory record is created **pending**, and the
  ADR-0020 landing lane now refuses at **`change_record_not_approved`** rather than
  `change_record_absent`: P1 `6a98cb85fbae`'s blocker is closed and a different, deliberate gate is
  what remains. The estate lander does not see these records at all (`_ASK_ABOUT = {"approved"}`),
  so no new nightly findings. **The open decision is whether that approval is a policy version
  admitting `factory-delivery` or a human click per record** — and it is a decision about standing
  authority, not a configuration gap.
  Note the population was empty when this shipped: no open factory pull requests in either
  redeploying repository, so the branch is proven by controls, the cross-repo pin and a
  byte-identical live differential for the bot population — not yet by a live factory pull request.

### #198

- **"Is this a factory target?" had THREE answers that disagreed pairwise, and ADR-0015 already
  ruled which one is authoritative — the ruling went unimplemented for thirteen days and then
  shipped the same evening this was written, adding a FOURTH surface. TWO CLAIMS BELOW ARE
  CORRECTED; read to the end before citing this, and note that `src/pin_watcher/github.py` and
  `src/revision_watcher/subjects.py` both cite this bullet for "four disagreeing answers" — the
  fourth is the `factory_target:` frontmatter, which did not exist when the three below were
  counted.** Measured 2026-08-17:
  `delivery_profile` in `PROJECT.md` says orchestrator/intent-packages/security-standards/
  infraops-mcp-server/change-manager/brain; the orchestrator's
  `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` said intent-packages/security-standards/
  change-manager/brain/**project-standards**/infraops-mcp-server; and the presence of
  `.github/workflows/factory-runner-pilot.yml` said a third thing. `factory-runner` is the only
  repository all three agreed on. `orchestrator` declares itself a target and cannot be dispatched
  to; `project-standards` was **deliberately excluded by ADR-0015 on 2026-08-04** and was allowlisted
  and given a caller on **2026-08-07** — by HQ, in commit `6aeff6f`, three days later.
  **THIRD CORRECTION, 2026-09-11: "`orchestrator` … cannot be dispatched to" IS FALSE, and it was
  false when written.** Its caller fired **22 times** (20 by `alobar-sds-dispatch[bot]`, 2 by
  `AlobarQuest`), **four concluded `success`**, and unit `7a81c2c2-0835-5bba-a308-36e868719b62`
  opened `orchestrator#135` — merged `a62f9637` on 2026-08-03, and cited twice as the Wave-3 exit
  manifest's dependency-update proof. The estate carried the same capability claim in
  `run-activation-sweep.sh`, in ADR-0030's enrolment paragraph and in ADR-0015's own amendment-2
  consequence; all four are corrected together. **Devon ruled 2026-09-11 that `orchestrator` is
  `factory_target = false` on the ground of DEFERRAL** — *"I had thought we would build out the
  automation for SDS itself's updates later"* — and the caller was deleted with the declaration
  (ADR-0015 amendment 3). Do not reach for factory-runner's self-reference argument here: that one
  is structural (the harness ships through itself), and production runs a **built image**, so the
  orchestrator that dispatches a change is not the tree being changed. **The fourth answer is now
  `factory-target.toml` at a repository's root**, which superseded the `PROJECT.md` frontmatter this
  bullet's own correction below names; both `src/pin_watcher/github.py` and
  `src/revision_watcher/subjects.py` still cite this bullet for "four disagreeing answers".
  **CORRECTED 2026-09-10 ON TWO POINTS, both of which this bullet got wrong in the direction that
  makes the estate look worse than it is.** (1) This said the caller was added *"in a sweep that
  never consulted the decision sitting in the repository it was working in."* `6aeff6f`'s own
  message names ADR-0015, distinguishes its two exclusions, reverses one and explicitly leaves the
  other, and cites Devon deciding; the amendment eight minutes later quotes his reasoning. The
  decision was read and the reversal was ratified. What actually happened is **momentum**:
  `8de11eb` records that Devon added five repositories to the allowlist that day and
  *"four needed only the allowlist entry; project-standards needed a caller workflow too."* The
  repository was carried in on a batch. That is still an argument for the declaration — nothing
  in the mechanism required the consultation that happened to occur — but it is not an unread
  checklist, and the wrong version has now been inherited into a task brief.
  (2) *"That mechanism has never been built"* was false four seconds after the reversal.
  **ADR-0015's implementation note shipped 2026-08-17 in `project-standards#24` (`6980d97`)**:
  `factory_target: <bool>` in `PROJECT.md` frontmatter, read by `runner.caller`, which reports
  `not-applicable` with the declared reason — and stays a `violation` for a declared non-target
  that still hosts a caller, which is the inverse the ADR never named. Both `project-standards`
  and `factory-runner` declare it today. `not-applicable` satisfies admission
  (`ADMISSION_SATISFYING`), so admission-clean and factory-reachable are now different questions.
  **The half that is genuinely unbuilt is a DIFFERENT consumer than the ADR's note:** the
  orchestrator has no checkout, so a repo-local declaration cannot reach *dispatch admission*,
  which still consults the hand-maintained `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES`
  — a build-time bundle like the actor registry, App Brain, or a sync job would be needed.
  The reversal itself stands as recorded: 2026-08-17, Devon reaffirmed ADR-0015, caller removed
  (`project-standards#23`), allowlist cut to five, verified from inside the container — and
  re-verified 2026-09-10, still five.

### #199

- **THE SEVEN FACTORY REPOSITORIES ARE PUBLIC as of 2026-08-17, and the trigger was Actions minutes,
  not a change of posture.** GitHub bills Actions only on private repositories. The estate ran out
  mid-afternoon and every private repository's CI began failing in **2–5 seconds** at "Set up
  Python" — before any repository content is read — while the two public ones
  (`infraops-mcp-server`, `factory-runner`) passed CLEAN. That correlation IS the diagnosis: a
  workflow failing at environment setup, split exactly along visibility, is quota rather than code.
  **HQ first misread it as a GitHub outage**, because `githubstatus.com` genuinely reported a
  Partial System Outage at the same time and the Actions API was 404ing estate-wide. Both were true
  and unrelated; the 404s cleared on their own, the quota did not.
  Consequence to know: with branch protection requiring a check that cannot run, **no private-repo
  pull request can merge and the autonomous landing lane stops** (it refuses on
  `landing_checks_not_clean`). Publishing restored all of it at once.
  **Before publishing, the estate's own scanner is the gate, and read the ALLOWLIST rather than
  trusting its reasons.** Four repositories scanned zero findings. `security-standards` carried 12
  allowlisted BLOCKs — 6 tracked-file fixtures, 5 pinned git-history commits, 1 the write-guard
  hook's own detection pattern. What settles it is that every one, tracked and historical, carries
  the fixture prefix `0.45eb08` while the live bootstrap token is `0.838d18`: a different token, so
  no credential was ever committed. The allowlist pins each finding's exact redacted match INCLUDING
  LENGTH, so a different token in the same file still BLOCKs — it fails closed. Note what this does
  **not** establish: that every commit in history was read. The scanner's detection is the basis,
  which is sound for the repository that IS the scanner and is not an exhaustive audit.
  **Revisit anything sized against Actions minutes.** The 2026-08-15 scheduled `main` checks were
  costed at ~850 min/month against 3000 included, with `orchestrator` 89% of it — that constraint no
  longer exists, so the cadence should be re-decided on its merits rather than left sized for a
  limit that is gone.

### #200

- **`POST /api/v1/package-intakes` was M2M-UNREACHABLE, so the merged code change alone was inert
  until a Traefik router was deleted.** Measured 2026-08-19 with a control:
  `GET /api/v1/status-ledger` answered **200** with the SYSTEM bearer while
  `POST /api/v1/package-intakes` answered **302** to Authentik, because `orchestrator-intake-human`
  (priority 230, exact `Path` + `Method`) forced that one route through the forward-auth chain.
  ADR-0027 falsified that router's stated premise — its own comment said intake *"requires a
  registered HUMAN actor"* — so the merge, the deletion and this correction are ONE operation.
  **Check where the human form actually posts before deleting a human router.** Deleting this one
  sends the path to the identity-stripping M2M router, which would have removed the human escape
  hatch had the form used it. It does not: `templates/intake_new.html` posts to `/review/intakes`,
  covered by `orchestrator-review`. That check is the difference between a safe deletion and
  silently removing a fallback nobody notices is gone.
  Pre-deletion backup: `/data/coolify/proxy/dynamic/.orchestrator.yaml.bak-adr0027`.

### #206

- **A full `--register` pass of the work lane exits 3 on change record 62, and that is NOT the
  retirement lane failing.** Two defects wear the same `409 idempotency key belongs to a different
  operation`, and only one is closed. **61's** — work done, nothing retired the record — is fixed by
  ADR-0029. **62's** is different and cannot be fixed by it: the record was already carried, its work
  is NOT complete, and `intent-packages`' `main` moved underneath it, so the fixed key
  `work-carry-62-2` now carries a different `source_commit` than the intake it registered
  (`10b13d47…` against a `main` of `155c50d…`). The retirement rule is keyed on COMPLETION, so the
  watcher correctly reports 62 as `[WAITING]` and not as a finding — the acting pass touches only
  records whose work is built. Backlog item **150** is the open half. Written down because a red
  signal needs its reason beside it: without this, the next reader sees exit 3 the morning after the
  fix shipped and concludes the lane is broken. Note the cause is new rather than incidental — it
  exists *because* the lane now runs fast enough that `main` moves between a carry and its
  completion (record 61's unit went registration → completed in **5m33s**).

### #209

- **DECISION (Devon, 2026-08-21): `resolved` is TERMINAL BY DESIGN in change-manager, and is not
  going to be made recoverable.** It was considered and declined, so do not re-open it.
  `resolved` means what it says — the issue is resolved, outside of any automation — and its whole
  job is to tell change-manager the work is done and there is nothing further to worry about.
  `reactivate` is guarded to `wontfix` (`app/transitions.py`) because reversing a *decline* is
  natural and reversing a *thing that happened* is not.
  **The recovery model is the world, not the machine.** In Devon's words: worst case something is
  marked resolved in error, and *"whatever raised the signal raises it again, because it was not
  resolved."* Read **signal** as the underlying condition — an outdated dependency is still
  outdated, a drift is still drifted — not as the producer re-proposing. The condition persists and
  resurfaces; a closed record does not make the world forget.
  **Record 59 is NOT a counter-example, and an agent finding it will think it is.** It was
  `resolved` by a mis-click during the window when `/review` offered no Approve button — a build-
  session-identified defect, remediated in change-manager#65 — not a mode of operation. Recovering
  it cost a hand-authored package revision 2 plus fresh approvals, which is the price of a defect
  that no longer exists, not the standing cost of this design. Do not cite it to argue for
  recoverability; that argument was made from it on 2026-08-21 and was wrong on both counts.
  What IS true and worth knowing: because a `work`-lane record's identity is the package revision,
  a re-proposal of the same bump replays onto the existing record (200) rather than minting a new
  pending one. That is a fact about the producer's idempotency, not about whether the underlying
  problem recurs.

### #224

- **On `GET /api/v1/traceability`, ADDING `source_repository` to a `pr_number` anchor can turn a
  real answer into `chains: []` — the two forms route through DIFFERENT TABLES, and the more
  specific one is the one that fails.** `_resolve_pr` (`services/reporting/traceability.py`) reads
  **`ReleaseArtifactBinding`** when `source_repository` is supplied and **`UnitPrBinding`** when it
  is not. A factory landing writes the second and not the first, so qualifying the query with the
  repository — the natural thing to do, and the thing that looks more careful — reports that the
  chain does not exist at all. Measured 2026-08-24 on `infraops-mcp-server#81`:
  `pr_number=81&source_repository=…` → `chains: []`; bare `pr_number=81` → the full chain, intent
  through pr. **Always run the bare form too before concluding a chain is absent**, and read
  `anchor.matched_on` in the response, which names which resolution ran.
  **The general shape: a narrower query is not a safer query when the extra term changes the JOIN.**
  Same family as the estate's other correct-about-the-wrong-noun defects.
  **The discriminating control for any "the chain is empty" claim is an anchor known to be
  populated** — `environment=production` returns two full chains
  (`wsp28-production-deploy-verification`, `phase5-production-closeout`, both carrying
  `commit,artifact,deployment`), which is what separates *the query is broken* from *this subject
  has no binding*. Without it, an empty result proves nothing, exactly as a search zero does not
  prove absence.

### #226

- **Criterion 3's negative tests, re-established 2026-08-24 and the population is now FOUR
  producers, not one.** The 2026-08-07 proof covered `orchestrator-drift-reporter` alone. The
  observe-and-report producers now share ONE credential by design — `orchestrator-observer`
  (`f793576f-…`) is used by the **activation sweep, the deploy watcher and the landing ledger** —
  so a single negative test covers all of them, which is the payoff of the deliberate
  one-credential decision. Re-proven against production: `commands/ready`, `commands/fail`,
  `pr-merge`, `estate-pr-merge`, `dispatch`, `verify` and `release-artifacts` all **403
  `role_forbidden`**; `approvals` **302**; `GET /observations` **200** as the positive control that
  the credential is live and the refusals are authorization rather than a dead token.
  **Separately, and NOT covered by that test: five scheduled producers hold the SYSTEM bearer** —
  `estate-landing`, `work-carrier`, `follow-up-mint`, `run-tracker-projection` and
  `run-tracker-reconciliation`. SYSTEM *can* transition a unit and merge, by design (ADR-0020's
  landing lane merges; ADR-0028's carrier registers intakes), so whether criterion 3's phrase
  "external producer" reaches them is a CLASSIFICATION question and not a test result. Do not test
  it by attempting a transition — that mutates production.

### #228

- **`UnitPrMerge` has NO ROW for a pull request a PERSON landed, so it cannot be the estate's source
  of "which commit landed this unit's work".** It records the orchestrator's OWN act (ADR-0020), and
  those are a minority of landings. ADR-0030 assumed otherwise and would have made its own
  acceptance subject permanently unreachable — PR #81 was landed by a person. The answer that covers
  every landing route is the **landing ledger's observation** (`source_system: github`,
  `observation_type: landing`, `subject_type: repo`,
  `facts.what_changed.{repository, pull_request, head_commit, commit}`), derived from GitHub by an
  independent program. **Confirm it against the orchestrator's own worker-written
  `UnitPrBinding.head_sha` before using it** — a pull-request number alone would bind any unit that
  shared it, and a `SDS-Unit:` trailer selects rather than answers. Note the ledger writes from
  `src/landing_ledger`, which `src/orchestrator` cannot import; the two vocabularies are pinned by
  `tests/contract/test_landing_fact_contract.py`, because a renamed key empties the candidate stream
  in silence.

### #229

- **Making a column of a UNIQUE constraint nullable SILENTLY SWITCHES THAT CONSTRAINT OFF.**
  Postgres treats NULLs in a unique constraint as **distinct**, so a migration whose stated subject
  is "these columns are now optional" also stops the constraint deduplicating every row that uses
  the new option — invisibly. `UNIQUE NULLS NOT DISTINCT` (Postgres 15+, SQLAlchemy
  `postgresql_nulls_not_distinct=True`) restores it; migration `0030_adr30_binding_kind` does this
  and says so in its own header. **The test must write ROUND the service**: a service that compares
  with `IS NULL` in Python dedupes whether or not the database would, so a service-level test
  asserts the wrong noun — measured, by a mutation that deleted `NULLS NOT DISTINCT` and survived.

### #238

- **`orchestrator`'s own machine-activation bindings are permanently `superseded`, and that is
  correct rather than a finding.** They were written at a HEAD the machine has since moved past, and
  the observation asserts that THIS artifact is what the next start executes — no longer true of
  that tree. Not curable and **cannot recur**: the lane now binds and observes in the same pass. Do
  not treat a superseded binding on `orchestrator` as drift; it is the record of the one window in
  which the two steps were separate.

### #257

- **`git push origin main` pushes the local `main` REF, not `HEAD` — so from any other branch it
  exits 0 having published nothing, and the failure wears the shape of success.** Measured
  2026-08-31 building ADR-0033: `push_exit=0` with `HEAD` one commit ahead of `origin/main` and
  nothing published. A producer that commits and then pushes this way from a topic branch or a
  detached head goes on to name a sha and propose a record for a commit only that machine holds —
  precisely the state the push was added to end. **The spelling cannot give way**: the repo-wide
  merge guard scans for the literal `git push origin main`, and reaching for `HEAD:main` takes the
  act out of the one register that watches it, which is evasion by another route. So the BRANCH is
  what must be checked. Three consequences, none of them obvious from the command:
  **(1)** hold the command as a STRING and split it at call time. `MERGE_EXEMPT_PATHS`' rot check
  removes an entry whose file no longer contains one of `MERGE_ACTIONS`, and that scan reads the
  file's TEXT — so `["git", "push", ...]` matches nothing, passes the guard without an exemption,
  and then has the exemption withdrawn as unneeded, leaving the act in place with nothing watching
  it. Not evasion by rewording, which the guard's message forbids, but by tokenisation, which
  amounts to the same thing. Written as one value the scanned bytes and the executed command
  cannot outlive each other.
  **(2)** derive the branch name out of that command rather than spelling `main` a second time; a
  second spelling is the one place the refusal can silently stop describing the command it guards.
  **(3)** a refused publish must be refused AGAIN at the start of the next pass. The replay path
  skips the committing step, so the finding is reported once and then goes quiet forever while the
  commit sits local and the lane reports clean runs — and `origin/main..HEAD` answers it from disk,
  with no fetch, so the refusal depends on this program's own unfinished act rather than on
  somebody else's landings.

### #269

- **REMOVING THE AUTO-MERGE CASCADE IS A PAIRED OPERATION: ALL SIX repositories carry a guard that
  asserts the workflow EXISTS, and its own message asks to be deleted in the same change.**
  `tests/test_automerge_cannot_bypass_ci.py` (TypeScript in `infraops-mcp-server`) opens with
  `test_the_auto_merge_workflow_exists`, whose docstring reads: *"If auto-merge is ever withdrawn
  from this repository that is a decision worth making visibly — delete this assertion in the same
  change, and say why."* Its purpose is that the module's remaining assertions become vacuous passes
  once the file is gone. Measured 2026-09-01: deleting only the workflow turned three default
  branches red within minutes, and the two protected repositories' removal pull requests went
  `BLOCKED`. **The guard did exactly its job** — it is the reason a half-removal was loud instead of
  silent — so the lesson is not about the guard but about the operation: when a file's absence is
  asserted somewhere, deleting it is one commit, not one push followed by a repair.
  **CORRECTED HOURS LATER, and the correction is the more useful half: this bullet said
  `orchestrator` was "the one repository with no such guard". It has THREE, and all three fired.**
  `test_no_automatic_merge.py::test_the_native_auto_merge_exemption_is_load_bearing_and_scoped`,
  `::test_the_exempted_command_only_ever_arms` and
  `test_ws33_scope_guards.py::test_the_native_auto_merge_exemption_is_load_bearing_and_scoped` each
  read `.github/workflows/dependabot-auto-merge.yml` and died `FileNotFoundError`, so `main` went
  red on the removal commit at 11:33Z and stayed red — every subsequent commit failing identically,
  and every pull request into the repository with it, because `Quality` is a required check.
  **The reason the census was wrong is worth more than the number.** The other five assert the
  workflow's PRESENCE, which greps for the filename and finds it. Orchestrator's assert an
  EXEMPTION for it — the same dependency inverted — so a census looking for "a test that says the
  workflow exists" does not match them, and this repository's own guard family is the one HQ
  searched least carefully because it was the one being edited. **When you count which repositories
  depend on a file, grep for the FILENAME, not for the shape of the assertion you expect.**
  Fixed by deleting the exemption in both files rather than restoring the constant: the scans are
  now unconditional, which is strictly tighter, and each file's comment says what restoring an
  exemption would cost.

### #276

- **A UNIT PARKED IN `submitted` WAS REPORTED BY NOTHING, and the two surfaces that could have
  each missed it for a DIFFERENT reason.** Found 2026-09-03 by asking production what was in
  flight: `98d07af9` had been `submitted` since 2026-08-19 with a failing pull request the whole
  time. `dead_letter._stalled_approvals` keys on `APPROVAL_STATES = ("awaiting_approval",
  "awaiting_review")` — WS-P2.15 widened that view to the gates a HUMAN owes and a verifier-owed
  state was never in scope. `reconciliation_detection._detect_stalled_verifications` DOES key on
  `SUBMITTED` but joins `DeploymentObservation.post_deploy_work_unit_id`, so it sees only the
  units a release mints and an ordinary implementation unit is excluded by the join. Neither was a
  decision — no ADR covers it and the module docstring reasons explicitly about approval gates.
  **CLOSED 2026-09-03 (PR #221):** `dead_letter` now also reports `stalled_verification` over
  `VERIFICATION_STATES = ("submitted", "verifying")`, kept SEPARATE from `APPROVAL_STATES` because
  the two differ in who owes the decision and therefore in the remedy — an approval gate needs a
  human to decide, a stalled verification needs the verifier run, and telling an operator the
  wrong one is worse than silence. `dead_letter_stalled_verification_seconds` is a required plain
  int, default one day, capped at 30 days, following its sibling's no-off-switch discipline.
  **`revision_required` remains a THIRD silent state, deliberately uncovered**: its only exit is
  `SYSTEM → ready`, nothing drives that automatically, and it is SYSTEM-owed — a third owner with
  a third remedy. Named here so its absence reads as a decision rather than an oversight.

### #282

- **TWO NAMED CAUSES OF TURN WASTE, both in the runner rather than in the model, measured across
  five attempts of one unit and fixed 2026-09-03.** Worth knowing because the symptom is
  indistinguishable from an under-specified `outcome`, which is where a reader will look first.
  (1) **The command policy refused SILENTLY.** `allowed_commands` is exact-match — an added flag,
  pipe, redirect or `&&` makes it a different command — and the hook said only that the command was
  not authorized. Runs spent **~11 turns each** discovering the boundary by trial. The refusal now
  states the matching rule and points at Read/Grep/Glob, which need no authorization; an existing
  test bounds that stderr under 200 characters and forbids echoing the policy, so the message is
  189 chars by construction. (2) **The prompt read as an instruction to prove idempotency.** The
  agent knew `finalize-run` re-executes the whole list, so it re-ran the list by hand to check —
  7 turns in one attempt, 10 in another. The prompt now says the runner does that re-execution, the
  agent must not, and once each verifying command has passed ONCE it should stop and say what it
  changed.
  **Both were found by RUNNING the factory, not by reading it**, and neither is visible in any
  test: the policy's tests assert what it authorizes, and nothing measures what a refusal costs.

### #286

- **THE THREE LAUNCHERS THAT NEVER GOT A DEAD-MAN SWITCH WERE EXACTLY THE THREE THAT NEVER GOT THE
  `bws --color no` GUARD, AND THE SECOND FACT EXPLAINS WHY THE FIRST WENT UNNOTICED.** Measured
  2026-09-05: of twelve `scripts/run-*.sh`, nine were guarded and scheduled and three —
  `run-tracker-projection.sh`, `run-tracker-reconciliation.sh`, `run-follow-up-mint.sh` — were
  neither. **TWO OF THOSE THREE WERE DELETED HOURS LATER by ADR-0040**, so the guard survives only
  on the follow-up minter; the finding is kept because it is about the correlation, not the files. This file has NAMED those three files as carrying the defect since 2026-08-02 and the
  one-flag fix was never applied to them, because nothing runs them and a lane nobody runs cannot
  report that it is broken.
  The differential, same secret and one flag apart: bare output began `1b 5b 33 38` (an ANSI
  escape) and `json.load` died at byte 0; guarded output began `7b 0a`. Under the fix the projection
  lane reaches a NAMED prerequisite instead — `set TODOIST_PROJECT_ID` — which is the honest state
  and is still not a working lane. **The guard did not make it work; it made it say what it needs.**
  **DO NOT read the trigger as having changed.** A first pass here saw the bare form emit escapes
  with no forcing variable apparently set and nearly recorded that `bws` now colours pipes
  unconditionally — which would have contradicted a measured invariant. `FORCE_COLOR=3` was set by
  the agent session itself. The documented cause stands: the trigger is the environment, not the
  version. It fires in every agent session on this machine, which is how it was found and why an
  operator running these by hand may never have seen it.
  **A `grep` for `color no` MISSES a Python call that passes `["--color", "no"]` as list
  elements**, so a portfolio-wide count taken that way over-reports. `scripts/exit_probe.py` was a
  false positive in exactly that way. Count shell and Python separately, or match on `--color`
  alone.

### #287

- **A DELETION CAN BE BOUNDED BY AN ATTESTATION, AND THE BOUND IS NOT CAUTION — IT IS THE RECORD.**
  Retiring the tracker lanes (ADR-0040) began as a 24-file deletion: the adapter, its tests, three
  routes, two services. That is wrong, and what says so is `docs/operations/wave-exit-manifest.toml`.
  **Wave 2's exit bar was MET, and its clause 3 attests exactly those artifacts** — a
  `routes_served` check naming all three tracker routes, and a `command` check whose probe executes
  `tests/tracker_projection_adapter` to prove the adapter imports nothing from the orchestrator.
  `.github/workflows/attest-wave-exit.yml` re-measures it on demand and states the consequence in
  its own header: *"a route a Wave-2 clause depends on going missing reds this job."*
  So the deletable set is the LANES — the launchers — and not the code they invoke. Deleting the
  code would either red that guard or force a met bar to be rewritten, and **rewriting a bar that
  was met is the back-dating mistake ADR-0014 names**: a decision made once, on evidence true then,
  does not become false because the estate later stopped using what it attested.
  **Generalise: before deleting anything, grep the exit manifests and the evidence directory, not
  just the source tree.** An artifact can have no operator and still be load-bearing, and the thing
  bearing on it is a claim about the past that nothing in `src/` mentions.
  The residual is named in ADR-0040 rather than implied: the adapter is now reachable only by its
  own tests and by that probe. If Wave 2's manifest is ever retired, this becomes deletable with it.

### #290

- **A CONSTANT WHOSE COMMENT NAMES ANOTHER REPOSITORY'S LITERAL WILL DRIFT, AND THE COMMENT IS NOT
  WHAT STOPS IT.** `intent-packages`' `max_llm_calls` is `max_attempts × max_turns × CALLS_PER_TURN`
  and was 240 for `3 × 40 × 2`. factory-runner raised `max_turns` 40 → 60 in `abd72db` and `#73`
  advanced `RECOMMENDED_CALLER_PIN` onto that revision, so **every caller ran a 60-turn cap against
  a ceiling sized for 40 for three days**. The coupling was not implied — the runner's comment
  beside its own new literal says *"RAISING THIS IS COUPLED TO budgets.max_llm_calls … At 60 that is
  3 x 60 x 2 = 360, and intent-packages moves with it. Raising one alone reproduces the failure that
  killed a unit permanently."* It said so at the moment of the change and nothing compared the two
  values. Identical in shape to the 120-versus-4 divergence `approval-policy.toml`'s own block
  already records: **a comment that names a coupling is not a check.**
  **CLOSED 2026-09-06 (intent-packages `#87`).** Both sites moved to 360, and
  `scripts/check_routing_policy_compatibility.py` now vets a **second surface of the file it was
  already fetching** — `max_turns` sits on the same `claude-code-base-action` step as the `model`
  literal, so the check cost one more extraction and no new job. Live differential at the real pin:
  360 → rc=0 *"covers the floor of 360 (margin 0)"*, 240 → rc=1 naming the shortfall. Mutation 11/11.
  Three things about it that generalise. **(1) The relation is `>=`, not equality, and the asymmetry
  is the design** — this repo's standing rule that `<=` passes against the defect it exists to catch
  is about a value that must MATCH; here the property is that the recoverable gate binds before the
  unrecoverable one, over-provisioning costs nothing, and equality would red on a safe margin.
  **(2) Both arms are evaluated and reported every run**: a model divergence must not hide a budget
  shortfall, or the second defect is only discoverable after the first is fixed. **(3) Adding the arm
  DISARMED both existing controls, silently** — their fixtures carried a `model:` and no
  `max_turns:`, so once the second arm existed they returned 1 whatever the model said, and
  `test_the_comparison_fires_on_a_divergence` went on passing while discriminating on nothing. That
  is this file's own add-a-term-to-a-conjunction rule, met inside the change that triggered it.
  **The job's name (`Routing policy compatibility`) is now narrower than what it checks, and is left
  wrong deliberately** — it is a required status check, so renaming it must move the protected
  context in the same operation or every pull request is blocked by a context nothing reports. Same
  trade the orchestrator's `Runner consumer compatibility` job already carries.

### #291

- **THE DEPENDENCY-UPDATE BUDGET NUMBER HAS FIVE COPIES, AND THE ONE NOBODY WAS WATCHING HAS MADE
  ADR-0011's KNOWN-GOOD MECHANISM INERT SINCE 2026-08-19.** Measured 2026-09-06, after a morning
  spent closing what looked like the whole problem and was three fifths of it.

  | # | site | value |
  |---|---|---|
  | 1 | `intent-packages` `profiles/dependency_update.py::BUDGETS` | 360 — what is STAMPED |
  | 2 | `intent-packages` `approval-policy.toml` ceiling | 360 — what may be DECLARED |
  | 3 | each `packages/*/package.yaml` `authority.budgets` | 240 on the zod package (legacy, ≤ ceiling, valid) |
  | 4 | **`orchestrator` `factory-policy.toml` known-good pattern** | **4** — what may be RECOGNISED |
  | 5 | `tests/fixtures/runner_authority_envelope.json` | 4 (cross-repo contract specimen) |

  Sites 1 and 2 are held together by a test, and site 1 is now held to factory-runner's `max_turns`
  by `check_routing_policy_compatibility.py`. **Nothing watches site 4.** `_within` is
  `value <= ceiling` (`factory_policy.py:627`), so `360 <= 4` is False and **no dependency-update
  envelope has been recognised as known-good since the profile budget left 4 on 2026-08-19.** It
  fails CLOSED — every envelope draws `authority_envelope_novel` and the human gate fires — so
  nothing unsafe happened; the mechanism simply has not existed.

  **ITS CONTROL IS GREEN AND FALSE, and the way it is false is the transferable part.**
  `test_the_shipped_pattern_recognises_the_envelope_the_profile_emits_today` says in its own
  docstring that it takes `CAPABILITIES` and `BUDGETS` from the profile *"verbatim"*. It does not:
  `uv_bump()` deep-copies the contract FIXTURE and overwrites only `constraints`, so the budgets it
  tests are the fixture's `4` — which matches the pattern's `4` exactly. Measured both paths:
  `within(4, 4)` True, `within(360, 4)` False. **A docstring asserting a provenance the code does
  not have is worse than no docstring**, because it is what a reader checks instead of the code.

  **FIXING SITE 4 IS A STANDING-AUTHORITY DECISION, NOT A DEFECT FIX — do not treat it as tidying.**
  Raising that ceiling ACTIVATES a mechanism whose entire effect is to WITHHOLD
  `authority_envelope_novel`, i.e. to suppress a human authority gate. This file already records the
  known-good mechanism as *"inert and therefore safe"* precisely because it recognises nothing.
  Fixing the test alone is honest and turns `main` red until the policy question is answered, which
  is the same decision wearing different clothes.
  **CLOSED 2026-09-06 (`#237`) and RECORDED 2026-09-08 as ADR-0043. The ceiling is 360, the test
  takes its budgets from `PROFILE_BUDGETS` rather than from the frozen contract specimen, and
  `scripts/check_profile_budget_agreement.py` holds the pattern, that constant and
  intent-packages' `BUDGETS` at `main` to one value — measured PASS at
  `{'max_attempts': 3, 'max_llm_calls': 360}`.**
  **THIS BULLET SAYING "Open" TWO DAYS AFTER IT CLOSED IS THE HAZARD THE FILE WARNS ABOUT, AND IT
  COST A WRONG REPORT.** On 2026-09-08 HQ was asked what was next, read this line, and named the
  ceiling as an outstanding decision for Devon — the fix having landed in the same session, hours
  earlier. The estate already records that a speculation hardens into an inherited fact; a CLOSED
  item still labelled open is the same failure with the polarity reversed, and it is likelier,
  because closing something and updating the note about it are two acts and only the first has a
  test. **Read the artifact before reporting a bullet's status: one `grep` of
  `factory-policy.toml` would have settled it.**

  Generalise past budgets: **when you find N copies of a value, the count is a lower bound until you
  have grepped every repository that consumes it** — the estate already learned this for BWS UUIDs
  and capability names, and learned it again here inside the change written to close it.

### #299

- **[NARROWED 2026-10-01 by ADR-0051: the ws32/ws33/ws34 word guards read CODE only --
  identifiers, imports, and string constants with no whitespace that are not docstrings.
  Prose, docstrings included, no longer reddens them, ws33's bare `merges` is gone, and
  ws52/ws53/ws61 are deleted. What follows is the record of the old behaviour.]**
  **A MULTI-TOKEN `FORBIDDEN_SEQUENCES` ENTRY MATCHES ORDINARY SPACED PROSE, so quoting a commit
  message or a pull-request title in a docstring reddens the ws32 guard.** Measured 2026-09-14
  against the guard's own functions. The entries are `(label, token-tuple)` pairs —
  `("merge_pull_request", ("merge", "pull", "request"))` — `_tokenize` lowercases, splits camelCase
  and splits on every non-alphanumeric run, and `_contains_sequence` looks for those tokens
  CONSECUTIVELY. So all four of these are one match:

      'Merge pull request #1 from AlobarQuest/branch-a'  -> merge_pull_request
      'merge_pull_request'                               -> merge_pull_request
      'merge-pull-request'                               -> merge_pull_request
      'mergePullRequest'                                 -> merge_pull_request

  and, as the discriminating controls, `'merge the pull request'` and `'request a pull merge'` match
  NOTHING — it is a consecutive-token test, not a bag of words.
  **The first line is GitHub's DEFAULT merge-commit subject**, which is exactly the string you reach
  for when documenting what a merge-commit landing produces. `#264` did precisely that — its
  docstring quoted its own probe's resulting commit — and reddened **ws32 and the ws33 phrase guard
  together**, at the full gate, after a pre-flight scan for the literal `merge_pull_request` had
  reported clean.
  **Why a careful reader still gets this wrong.** This file documents the tokenizer for the
  SINGLE-token entries — `post-deploy` matching `deploy` — and separately documents ws34's list as a
  SUBSTRING match. Neither prepares you for a multi-token entry reaching prose, and the single-token
  examples make the tokenizer look like a rule about compounds rather than about word sequences.
  **The cheap check is to run the guard's own functions on the string** rather than to grep for the
  literal: import `_tokenize` and `_contains_sequence` from
  `tests/architecture/test_ws32_scope_guards.py` and pass it the exact text. A grep for the literal
  cannot see any of the four forms above except the second.
  Reword; never allowlist — the standing rule for this family is unchanged, and an exemption here
  would excuse a file rather than a sentence.
