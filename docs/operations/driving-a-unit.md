# Driving a work unit by hand

The intake, breakdown, approval, dispatch, verification and landing steps for driving a unit through the factory by hand.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #10

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

### #24

- **An envelope that authorizes no mutating command cannot produce a diff, and unit
  `authority` has no mutation path.** While none of the approval's units has been claimed or
  dispatched, a human can supersede the approval from its proposal page in `/review` and submit a
  corrected breakdown (ADR-0052). Once a unit has been worked, a wrong envelope costs a whole new
  package revision plus a fresh human approval per unit. Before spending any of that, dry-run each unit against its
  real target repo, read-only: prove the mutator yields a diff (`uv lock --upgrade
  --dry-run`, `npm outdated`) and prove the verifier actually executes tools (no
  `"… not installed — skipping"`, a real `collected N items`). Verifying a manifest's
  *type* is not verifying an upgrade is *available*: a `==` pin makes `uv lock
  --upgrade` a silent no-op, and the unit then dies on the same `no changes to submit`
  guard the envelope was rewritten to avoid. The general failure is **authored intent
  never validated against executable reality**; WS-6.4 hit it three times.

### #25

- **That dry-run rule is necessary but NOT sufficient — it passed all three WS-6.4 defects.**
  Add three clauses. (1) **Run the ordered list twice** in one checkout: `finalize-run`
  re-executes every `allowed_commands` entry before `git status`, and `uv venv` is not
  idempotent (`uv venv --clear` is). (2) **Name every site of a pin**: `uv` resolves
  `[dependency-groups]` and `[project.optional-dependencies]` jointly, so bumping one of
  two identical `==` pins is *unsatisfiable*, not merely inconsistent. (3) **Control for
  the environment** — run the verifier against an *unmodified* clean clone first, or a
  runner-environment failure reads as an update-induced one.

### #26

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

### #38

- **There are TWO kinds of approval, and the generic `/review` "approval" button records the
  one readiness does not want.** `POST /review/units/{id}/approval` (`web.py`) hardcodes
  `subject_type="action"` — which satisfies the `AWAITING_APPROVAL → READY` transition
  guard, and takes that edge in the same transaction. The form is offered only in
  `awaiting_approval`, and the API's `approve` command is served to no person. But readiness and dispatch both require an **`authority`** approval:
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
  verified 2026-07-22).
  **SUPERSEDED 2026-08-19 (ADR-0027): that router is DELETED and intake is no longer human-only.**
  `POST /api/v1/package-intakes` now falls through to the M2M `orchestrator-api` router, so the
  carrier registers as `orchestrator-system`. The human path is unaffected and was checked before
  the deletion: the `/review` form posts to **`/review/intakes`**, not the API route, and keeps its
  own forward-auth chain. Had it posted to the API route, deleting the router would have removed
  the human escape hatch the asymmetric registrar guard exists to preserve.
  The earlier "no authority-approval form; pasted `fetch()` in devtools"
  claim here is obsolete.

### #39

- **The orchestrator readies a unit itself once its readiness holds (SDS 1.1 item 2d-1).** Any
  write that can make readiness hold takes `DRAFT → READY` in the same transaction
  (`services/lifecycle/lifecycle.py::ready_if_satisfied`):
  - recording the `subject_type="authority"` approval;
  - resolving a dependency to `satisfied`;
  - approving a decomposition whose unit policy already recognises and that has no dependency.

  The event carries the triggering actor's id with the SYSTEM role, and it bumps the unit's
  `version`: a client's next `expected_version` after an approval is one higher than before. `DRAFT → READY` is guarded on
  readiness (`readiness_not_satisfied` otherwise), so `POST /api/v1/work-units/{id}/commands/ready`
  is only a catch-up, for a unit whose readiness came to hold with no event, such as after a
  policy change. Until 2026-10-05 the approval left the unit in `DRAFT` and nothing checked
  readiness on that edge (verified 2026-07-17 dispatching the brain AC-002 unit).

### #40

- **Decomposition approval is human-only and reachable ONLY through the `/review` GUI, never the
  raw `/api` path.** **The raw `/api` approve and reject routes, and their `approve-decomposition` /
  `reject-decomposition` CLI commands, were DELETED on 2026-09-28 (Tier 3 item 24b); the rest of
  this bullet is why they could never have been used.** `approve_decomposition_proposal` calls `_require_decision_actor`, which
  raises unless `actor.role is ActorRole.HUMAN`. But the raw `POST
  /api/v1/decomposition-proposals/{id}/approve` sits on the default `orchestrator-api` Traefik
  router (headers-strip only = **M2M-only**), so a browser session `fetch` to it `401`s **by
  design of the routing table** — not because forward-auth is broken. The human path is the
  `/review/decomposition-proposals/{id}` GUI page, whose form POSTs to
  `POST /review/decomposition-proposals/{id}/approve` (`web.py`, `_human` + CSRF) under the
  `orchestrator-review` router, which *does* carry the forward-auth chain. This is unlike intake
  and authority approval, which each have their own dedicated forward-auth `/api` router
  (`orchestrator-authority-approval-human`; `orchestrator-intake-human` **existed until 2026-08-19
  and was deleted under ADR-0027**) and so *are* done by browser `fetch`. A browser `401` on any orchestrator `/api` route means the endpoint is
  M2M-only, not that auth is down. (Verified 2026-07-17.)

### #42

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

### #62

- **A package that describes its own release recording cannot evidence that recording at
  adjudication time.** `record_release_artifact` raises `work_unit_not_completed` unless the
  implementation unit is already `COMPLETED` (`services/release/release_artifacts.py`), and
  follow-up minting additionally requires every unit of the revision to be settled — so the binding,
  the deployment observation, the traceability answer and the mint all necessarily happen *after*
  the unit whose ACs assert them has completed. There is no ordering that avoids this. Either put
  the recording ACs in a **separate, later package**, or accept the delegation deliberately:
  adjudicate on the ordering, say so in each rationale, and discharge the confirmation in the
  follow-up review unit the revision mints. **Do not reach for `waived` to express the caveat** —
  `waiver_invalid` requires *failed* evidence plus a risk class, follow-up and future expiry, and
  the waiver must name the criterion's current evidence, on which its current adjudication failed
  (or which an earlier waiver, being renewed, already named).
  So a waiver is not a general "accepted with reservations"; for judgment evidence the only honest
  outcomes are `passed` and `not_applicable`, with the caveat in the rationale. (Verified
  2026-07-28, WS-P2.8 deploy.)

### #64

- **`/review/intakes/new` takes its idempotency key from the FORM, not from the pasted payload.**
  Re-submitting the rendered page is therefore a *replay* of the same intake, and a genuinely new
  registration requires reloading the page to mint a fresh key. The payload's own
  `idempotency_key` is ignored for this purpose. Do not debug an unexpected "duplicate" intake
  before checking whether the page was reloaded. (Verified 2026-07-28, the form's first real use.)

### #65

- **The orchestrator assigns the dispatch ordinal; omit `runner_attempt`.** Since SDS 1.1 item
  3a-2, `dispatch_work_unit` (`services/execution/dispatch.py::_next_runner_attempt`) takes
  `max(unit.attempt_count, highest recorded runner_attempt) + 1` with the unit row locked, and the
  response carries the ordinal it took. A supplied ordinal is accepted only if it is that next
  one; anything else is refused `dispatch_attempt_not_next`. Until then a REUSED ordinal returned
  the existing record (HTTP 200, `status: "dispatched"`) and fired no workflow, which is what this
  entry used to warn about (verified 2026-07-29, GAP-4 attempt 3). A skipped dispatch still spends
  its ordinal, and a retry is made safe by reusing the same `idempotency_key`, which replays the
  record. Dispatch and claim ordinals stay independent: they drift apart the moment a dispatch is
  skipped or a claim is reclaimed.

### #66

- **THE BOUNDED DISPATCH WINDOW NO LONGER EXISTS. `ORCHESTRATOR_DISPATCH_ENABLED` is `true`
  PERMANENTLY** (Devon's standing decision, 2026-08-04, container-verified). **Since 2026-09-28
  (ADR-0046) it and both landing switches also DEFAULT true; the variable is only an off switch.**
  **Do not open or close a window; there is nothing to close.** Every "open the window / close it after terminal" recipe
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

### #69

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
  **AUTHORING RULE — CORRECTED 2026-09-04: it is `max_attempts × max_turns × calls-per-turn`,
  and omitting the third factor killed a unit permanently.** The formula below reads as a call
  budget and is a TURN budget: an LLM call is not a turn, and the ratio is above 1. Measured on
  unit `b1e02957` (zod 3→4 into `infraops-mcp-server`), attempts of 40 turns recorded **66 and 65
  llm_calls** — ~1.65 calls per turn. Its ceiling of 120, authored as `3 × 40` and read as three
  attempts, was spent by **two**; `budget_exceeded` then refused the third, and that refusal is
  curable by nothing. Round the factor to **2** and authorise `max_attempts × max_turns × 2`
  (**CORRECTED 2026-09-06: both sites are at 360 for `3 × 60 × 2`, intent-packages `#87` — and
  "both sites" is itself wrong, there are FIVE; see the five-copies entry at the end of this file.**
  They
  read 240 for `3 × 40 × 2` from 2026-09-03 until then — and the contradiction was sitting in THIS
  BULLET, four lines below, where it already said `max_turns` is 60. Nothing compared the two,
  including a reader of this paragraph. A `>=`-relation check against the runner literal at
  `RECOMMENDED_CALLER_PIN` now holds them together; see the entry at the end of this file;
  both sites move together, and a test asserts the stamped envelope equals the grant).
  **The old formula's own reasoning is what makes this dangerous**: it exists to guarantee the
  RECOVERABLE gate binds first, and under-counting inverts exactly that — the unrecoverable gate
  binds while the envelope still reads as having attempts left.
  **`max_turns` is 60 as of factory-runner `abd72db`, measured rather than guessed** — the prior
  40 was chosen before anyone had run the lane. Five attempts at 40 all ran out of turns.

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
  **CLOSED 2026-08-19 for the dependency-update lane, and the shape of the defect is the durable
  part.** `intent-packages`' `approval-policy.toml` granted `max_llm_calls = 120` and its own
  comment said so — *"120 rather than the profile default of 4"*. But the grant is a **CEILING on
  what a package may DECLARE**, while the unit envelope is stamped from
  `profiles/dependency_update.py::BUDGETS`, and that constant was **4**. Nothing compared them, so
  every unit this lane emitted carried a thirtieth of the budget its own package had been approved
  for. **A comment that NAMES a divergence is not a check**, and this one had named it for weeks.
  The constant is 120 now and a test asserts the stamped envelope's budgets EQUAL the grant's
  ceilings — equality rather than ordering, because `<=` passes against the very defect it would
  exist to catch (intent-packages #72).

### #112

- **`GET /api/v1/status-ledger` defaults `include_inactive=false` and every production unit is
  terminal, so the bare call returns `[]`.** `api/routes/reporting.py` /
  `services/reporting/status_ledger.py:25,95`.
  This is the most misleading read on the production API surface, because an empty list **looks
  like an answer** rather than like a filter — the same shape as the estate-wide rule that a search
  zero is not evidence of absence. Pass `include_inactive=true` when the question is "what does the
  ledger hold", and reserve the bare call for "what is live now". (Verified 2026-08-03.)

### #113

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

### #115

- **`ReconciliationCondition` has exactly ONE production read surface — the traceability chain's
  `conditions` hop — so nothing can corroborate a divergence from a second surface.** No `/api/v1`
  GET exists (only the two `POST …/detect` routes); a `reconciliation.required` event's subject is
  the **condition**, not the unit, so it never appears in a unit's evidence-pack `events`; the
  release pack carries revision/units/release_artifacts/deployments; `consistency-check` reports
  evidence-head, completion and waiver findings only; the SLO report has no reconciliation metric;
  and `graduation_ledger` counts them per unit but is reachable only from `/review` HTML.

### #129

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

### #130

- **`allowed_commands` is the coding agent's ENTIRE Bash vocabulary, ENFORCED — and it is also
  re-executed at finalize. A command absent from the envelope is a command the agent CANNOT RUN.**
  **CORRECTED 2026-08-20. This bullet said "ADVISORY to the coding agent … nothing blocks the agent
  from running something else", and that is false; believing it cost a work unit.** The prompt half
  is real and is probably how the error arose — the list does reach the agent as text (`cli.py:215`,
  `"\n".join(f"- {command}" …)`) — but tracing the prompt and stopping there misses the second
  consumer. `cli.py:495` calls `write_tool_policy(permissions.allowed_commands,
  brief.authority.fingerprint, edit_allowed=…)`, whose docstring is *"Write the RUNNER-OWNED hook
  policy and Claude settings outside the checkout"*: it writes `policy.json` (chmod 0400, outside
  the checkout so the agent cannot reach it) plus a `settings.json`, and the reusable workflow hands
  that to the Claude Code action as `settings: ${{ steps.prepare.outputs.settings_file }}`. The
  authorizer is `command_policy.py::authorize_tool` — *"Return an allow decision only for an EXACT
  Bash command or contained Edit path"* — matched per Bash call, failing closed on an unreadable
  policy. Everything it enforces is derived from the envelope: the command list verbatim, the
  authority fingerprint, and `edit_allowed` from `capabilities["repo.edit"]`. Nothing else feeds it,
  and **this is not a dev-machine safety net** — the failure below happened on a GitHub-hosted
  `ubuntu-latest`.
  **The consequence the old bullet named does not exist, and the real one is its opposite.** There
  is no hazard of "an unlisted command the agent improvises makes a run appear to work" — an
  unlisted command cannot run. The hazard is that **an envelope which omits a command the work needs
  makes the work impossible**, quietly: the agent reports being blocked and does what it still can.
  Measured 2026-08-19 on zod 3.25.76 → 4.4.3 into `infraops-mcp-server`, a real migration where the
  MCP SDK's `server.tool()` signature shifts under zod 4's types. The envelope omitted
  `npm run build`; the agent attempted it, was refused by the hook, said so in its own summary, and
  moved the pin — a two-file pull request whose three checks all failed, 35 turns, $0.32, and a
  spent attempt on a write-once envelope that can never be given the missing command.
  **So an envelope must be COMPLETE for the work it authorises**, which collides with the fact that
  `factory decompose` proves a bump is possible by running the envelope's commands against a tree no
  agent has touched. A command that can only pass AFTER the coding phase must therefore be granted
  and excluded from authoring's dry run — `intent-packages`'
  `dependency_update.py::commands_deferred_to_coding`, keyed on what a failure MEANS: `npm ci`
  failing means the dependency graph cannot resolve, which no in-scope source change fixes, so
  authoring runs it; a build failing is the assignment, so authoring does not. Deferring is not
  exempting: finalize re-executes the whole list regardless.
  **The finalize half of the old bullet stands unchanged.** `allowed_commands` becomes
  `verification_commands` at finalize (`cli.py:839`), which re-executes the ordered list before
  checking `git status` — so a listed command that cannot run makes finalize fail **however well the
  coding phase went**, the ordering rule (mutators first, verifier last) matters because of that
  re-execution, and every listed command must be idempotent.

### #131

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
  approval is bound to it, and there was then no supersede route for an approved decomposition
  (there is one since ADR-0052, for approvals with no worked unit).

### #132

- **A package approval requires BOTH a hash-bound ledger entry AND a `package.approved` event in the
  tamper-evident factory-events chain — a hand-written `lineage.yaml` approval can NEVER verify, by
  design.** `operations.py::verify_approval` fails closed on either half, and its own docstring says
  *"a forged/edited ledger entry cannot pass this — it isn't in the chain."* The audited path is the
  **`intent_packages` CLI** (`approve`, `revise`, `transition`, `verify-approval`) — a *different*
  CLI from `factory` — which emits the chain event FIRST and writes the ledger only after, so an
  unaudited approval cannot exist. Do not hand-edit lineage; HQ tried on 2026-08-03 and the guard
  refused it, correctly.

### #133

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

### #135

- **`POST /work-units/{id}/dispatch` requires `expected_version`** — omitting it is a FastAPI 422
  before any service code runs, not a `DomainError`. Same for most command routes; read the
  `detail[].loc` in the 422 rather than guessing which field is missing.

### #136

- **The package-intake id IS the revision id.** `/review/intakes/{id}` and
  `/review/revisions/{id}/evidence-pack` carry the same UUID, and it is what
  `POST /api/v1/package-intakes/{revision_id}/decomposition-proposals` wants. There is no separate
  revision UUID to hunt for.

### #137

- **The conformance kit's `repo.protection` is an ADVISORY check, not an ADMISSION one — it never
  affected `admission_passed`.** `readiness_schema.py` puts `git.current`, `project.manifest`,
  `code.onboarded`, `ci.executed`, `security.clean`, `runner.caller`, `profile.declared` in
  `ADMISSION_CHECKS`, and `deps.dependabot`, `repo.protection`, `backlog.hygiene`,
  `standards.pinned` in `ADVISORY_CHECKS`. **HQ asserted on 2026-08-03 that exit criterion #2 was
  "unsatisfiable" because protection blocked admission; that was wrong.** The real admission
  blockers are `runner.caller` (6 repos), `profile.declared` (4), `security.clean` (1),
  `code.onboarded` (1) — all fixable. Before calling a gate unsatisfiable, read which set the check
  is in.

### #141

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

### #146

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
  one: an approved decomposition could not then be superseded (it can since ADR-0052, before any
  unit is worked), so this cost a whole new package revision plus both human approvals again.

### #205

- **`commands/fail` is the VERIFIER's edge, and `revision_required` is a cul-de-sac for a unit you
  intend to retire.** `(submitted → failed)` and `(verifying → failed)` are VERIFIER edges;
  `(failed → cancelled)` is HUMAN; and **`(revision_required → cancelled)` is not a legal edge at
  all** — from `revision_required` the only way out is `→ ready` under SYSTEM. So a unit whose work
  is genuinely unachievable must be driven to `failed`, not left where a normal `/verify` on failing
  deterministic evidence would put it. Record the observed named-check evidence FIRST — the
  ingestion route accepts `expected_conclusion: "failure"` and records what GitHub actually
  concluded under `observation` — then `commands/fail` with the reason, so the terminal state cites
  an observation rather than an assertion. The cancel is the human's. (Verified 2026-08-19 on unit
  `6bc89d79`, read from `kernel/transitions.py` and `api/routes/lifecycle.py::COMMAND_TARGETS` rather than
  guessed.)

### #239

- **`factory create` SCAFFOLDS AC-002 AS `human_review` / `approver: devon`, AND THAT ONE LINE MAKES
  THE UNIT PERMANENTLY UNLANDABLE BY THE FACTORY.** Confirmed 2026-08-26 for
  **`maintenance-remediation`**, not only `dependency-update` as this file previously recorded — the
  scaffold carries it for both. `verifier_decided_completion`'s fifth disqualifier throws out a unit
  where a human decided **anything at all**, including a criterion merely **RETAINED** rather than
  mapped to the unit, because the condition is *"no human adjudication"* and not *"none among the
  required criteria"*. **The refusal does not appear until `pr-merge-admission` refuses a unit that
  has ALREADY COMPLETED** — after the write-once envelope and both human approvals are spent, costing
  a whole package revision. **Model a landing package on `intent-packages-packaging-bump`** (the
  first autonomous landing, ADR-0020) or `change-manager-resolved-state-display` (the first into a
  deploying repository): each carries **exactly one** criterion, `automated_check`,
  `approver: policy`.

### #240

- **`intent_packages transition` RE-SNAPSHOTS the revision hash; `factory validate` does NOT CHECK
  IT.** A package assembled by hand into a scaffolded directory validates **clean** while its
  `lineage.yaml` still carries the scaffold's hash for a different document — measured 2026-08-26,
  `bec9f9fe…` recorded against a package hashing to `4e3d22c0…`. The `transition` to
  `ready_for_review` corrects it, so the normal path self-heals; a package that skipped the
  transition would carry a lineage attesting a document nobody approved. Check with
  `python -m intent_packages hash <dir>` against the lineage before approving anything hand-edited.

### #241

- **A machine may register an intake ONLY BY NAMING THE APPROVED CHANGE RECORD THAT CAUSED IT.**
  ADR-0027 removed `_require_human` so the CARRIER could complete a signal→record→package→intake
  lane; it did **not** make every intake machine-registrable. A POST without a cause is
  `409 intake_change_record_required` — *"register it with the change record id, or register it as a
  human"*. So a package HQ authors from a backlog item has no such record and **the human paste is
  the provenance**, not ceremony: it is the asymmetric registrar guard preserving the escape hatch
  the `orchestrator-intake-human` router's deletion was checked against. `factory submit` stopping
  at a printed payload is CORRECT for that path, and reading its "human gate (ADR-0006)" message as
  stale is the mistake — HQ made it on 2026-08-26 and the guard caught it.

### #242

- **`constraints.work_unit_id` must be OMITTED from a hand-authored envelope** —
  `authority_work_unit_id_forbidden`, *"assigned by the orchestrator at proposal time"*. The pinned
  cross-repo fixtures carry `"work_unit_id": "__WORK_UNIT_ID__"` as a placeholder, so copying a
  fixture verbatim is a named refusal. Everything else in
  `tests/fixtures/runner_authority_envelope_edit.json` IS the shape to copy for edit-shaped work.

### #243

- **A skipped dispatch spends its ordinal.** A dispatch refused by an admission term still WRITES A
  RECORD at the ordinal it took. Measured 2026-08-26 on one unit: attempt 1 skipped
  (`outside_change_window`), attempt 2 skipped (control), attempt 3 dispatched. The orchestrator
  assigns ordinals (see #65), so a client need not track this; read the ordinal off the response.

### #244

- **`work_units.version` is not served by any read surface a client can rely on, so probing is the
  documented client contract — and the `version_conflict` error carries NO `current_version`.**
  `in-flight-units` excludes DRAFT and terminal units; the evidence pack projects `version: null`;
  the status ledger does not carry it. Measured 2026-08-26: the error's `details` is `null`, so this
  file's advice to *"read `current_version` off the error"* does not work. Probe upward from a
  plausible value — a wrong guess is a harmless 409 — and note that **recording evidence bumps the
  version too**, so a value read before one POST is stale for the next.

### #245

- **Merge admission refuses on FOUR terms while a unit is merely SUBMITTED, and three of them clear
  by running the verifier rather than by waiting.** Reading `pr-merge-admission` (a GET, mutating
  nothing) before attempting a merge is the cheap way to see which. Measured 2026-08-26:
  `work_unit_not_completed`, `criteria_not_verifier_decided`, `criteria_evidence_not_observed`,
  `merge_outside_change_window` → after `verifier-evidence/named-check` + `/verify`, exactly
  `merge_outside_change_window` remained. **Nothing runs the verifier automatically** — HQ drives it
  with the VERIFIER credential, and `check_name` must be the JOB name
  (`Lint, type-check, and test` in `change-manager`, whose WORKFLOW is called `Quality`).

### #275

- **AN ENVELOPE WHOSE VERIFIER DOES NOT EXERCISE THE CHANGE CANNOT REFUSE A BROKEN CHANGE — and
  the runner will honestly report success while the target repository's own gate fails.** Measured
  2026-09-03 from work unit `98d07af9`'s own `runner.pr.opened` evidence, which is where the
  answer was and where nobody had looked for fifteen days. Its `allowed_commands` were
  `npm install zod@4.4.3 --save-exact`, `npm ci`, and **`grep -q '"zod": "4.4.3"' package.json`** —
  all three `passed`, exit 0. **The verifier was a grep for a version string in a manifest.** It
  could not fail for any reason connected to whether the code compiles, so the coding agent's
  refusal by the command hook (`npm run build` was not authorized) produced a two-file pin move
  that the runner correctly certified and that the repository's `build` and
  `Lint, type-check, and test` jobs both failed. This is NOT the same statement as the recorded
  dry-run rule (*prove the mutator yields a diff, prove the verifier executes tools*): both of
  those were satisfied here. The missing clause is that **the verifier must exercise the thing the
  change could break.** `intent-packages` closed it for npm — `_npm_mutation` appends
  `npm run build` when the checkout declares one and `commands_deferred_to_coding` defers it from
  authoring's dry run — so read the generated command list, do not hand-author one.

### #278

- **`GET /work-units/{id}/evidence-pack` PROJECTS ITS `work_unit` DOWN TO FOUR FIELDS — `state`,
  `version`, `attempt_count` and `pr_number` all read `null` there.** Measured 2026-09-03: the
  pack's `work_unit` carries `id`, `title`, `state` and `authority_fingerprint` and nothing else,
  so a census taken from it reports a unit with no version and no attempt history. The surface
  that carries them is **`GET /api/v1/in-flight-units`** (`work_unit_id`, `unit_key`, `state`,
  `version`, `attempt_count`, `work_package_revision_id`, `pr_number`, `head_sha`,
  `verification_read_head_sha`) — and it excludes DRAFT and terminal units, so it answers only for
  a unit that is live. The pack's `events` are likewise projected: `action`, `actor_id`,
  `from_state`, `to_state`, `occurred_at`, and **no `payload`** — so a dispatch ordinal or a
  `dispatch_record_id` must come from `GET /work-units/{id}/history`, which does carry payloads.
  Same family as the `response_model`-drops-fields invariant: the surface you read is not
  necessarily the surface that has the field.

### #280

- **THE ENVELOPE'S VERIFIER MAY NAME THE TARGET REPOSITORY'S OWN GATE WHEN THAT GATE *IS* THE
  ACCEPTANCE CRITERION — and until 2026-09-03 `intent-packages` forbade exactly that, so a unit
  could not verify the thing it was being judged on.** `DENIED_VERIFIER_PATTERNS`
  (`profiles/dependency_update.py`) rejected `make check`, `pytest`, `npm test` and friends on
  runner-honesty grounds: a bare hosted runner may not be able to run a repository's suite, and an
  envelope that names one it cannot run records evidence about nothing. Sound, and it collided with
  AC-001, which for a dependency update **is** the named check. The first zod envelope's verifier
  was therefore a `grep` for a version string in `package.json` — **a command that cannot fail for
  any reason about the code**, only about the edit having been typed.
  **Devon's ruling: the deny-list was right that a bare runner may not be able to run a
  repository's gate, and the fix is to make the runner's environment MATCH rather than to forbid
  the gate.** `npm test` / `npm run test` came off the list; `make check`, `pytest`, `tox`, `nox`
  stay, because this repository's own suite genuinely needs Postgres and `SECURITY_STANDARDS_DIR`
  and a bare runner genuinely cannot run it. The distinction is per-gate and measurable — *can this
  command run on a hosted runner?* — never per-command-name.
  Generalise: **when a guard and an acceptance criterion contradict each other, at least one of them
  is describing the environment rather than the work.** Fix the environment.

### #301

- **DISPATCH ADMISSION READS `factory-target.toml`, AND `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES`
  NO LONGER EXISTS** (ADR-0015 amendment 4, Devon, 2026-09-15). Every earlier bullet that names the
  allowlist as an onboarding step or an admission term describes the estate before that date.
  `services/execution/factory_target.py` reads the file from the target repository's default branch
  with the dispatch App's installation token, in the slot the allowlist held, and refuses as
  `target_repository_not_declared` (declared `false`, or no file on a repository that answers) or
  `target_repository_declaration_unreadable` (anything else, including a malformed file). **To make a
  repository a factory target, land the declaration there**; it still needs the caller workflow, the
  Actions secrets, `FACTORY_PR_TOKEN` access and the estate's landing answer before a unit can run.
  Three things worth knowing before editing it. (1) **There are three readers of that file and one
  pin**: `tests/services/test_factory_target.py` holds this reader's parse to
  `work_carrier/declaration.py`'s over a table spanning all three answers; project-standards'
  `factory_target.py` is the third reader and nothing here pins it. (2) **A leftover
  `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` in the environment is ignored**, not a boot
  failure: `pydantic-settings` reads only declared fields from the environment (measured
  2026-09-15), so removing the variable from the deployment can happen in either order. (3) The
  parser is private because `test_unreachable_guards` does not count a call from a method in the same
  module as reachable; the agreement test reads through the client with a mock transport instead.

## Added since the move

### Test a caller pin before merging it

To move a caller workflow onto a new factory-runner revision, prove the new pin works before any
pull request merges:

1. Push the pin change to a branch in one caller repository, for example `runner-pin-<sha>`.
2. Dispatch that repository's caller from the branch with a well-formed work-unit ID that doesn't
   exist:

   ```bash
   gh workflow run factory-runner-pilot.yml -R AlobarQuest/<repo> --ref <branch> \
     -f work_unit_id=00000000-0000-4000-8000-000000000000
   ```

3. Read the failed step. The run is healthy if "Install pinned factory runner" and "Verify factory
   runner revision" pass, and "Prepare scoped run" fails with
   `404 work_unit_not_found`. That answer proves the runner installed, authenticated to
   production, and reached the orchestrator. A 401 means the credential is wrong; a failure before
   "Prepare scoped run" means the install is broken.

The probe changes nothing: the runner claims before it codes, and an unknown unit has nothing to
claim. Then merge the callers, and advance `RECOMMENDED_CALLER_PIN` last. `runner.caller` requires
an exact match, so the order only shortens the window in which the two disagree.

Measured on 2026-10-01 moving to `fec677b` (Python 3.14): the probe, run 36951954378, installed the
runner on CPython 3.14.8 and stopped at `404 work_unit_not_found`.
