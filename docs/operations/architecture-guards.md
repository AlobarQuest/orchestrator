# Architecture guards

What the whole-repository guards in `tests/architecture/` check, what reddens them, and how to reconcile a collected-test count.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #29

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

### #50

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

### #51

- **[NARROWED 2026-10-01 by ADR-0051: the ws32/ws33/ws34 word guards read CODE only --
  identifiers, imports, and string constants with no whitespace that are not docstrings.
  Prose, docstrings included, no longer reddens them, ws33's bare `merges` is gone, and
  ws52/ws53/ws61 are deleted. What follows is the record of the old behaviour.]**
  **Adding a route — `/api` OR `/review` — or a new `src/orchestrator/` module trips a FAMILY of
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
  The `api/routes/<domain>.py` and `api/schemas/<domain>.py` modules that need it are in
  `WS42_DISPATCH_PATHS`, so their *words* are exempt; a module that does not need the exemption
  is not listed, and a new one that does must be added — but the route-inventory sets are NOT word guards and apply to every route regardless.
  `web.py` is in no allowlist: keep its route bodies free of the bare words (delegate to a service).
  Jinja `.html` templates are not scanned at all.
  (Verified 2026-07-25, WS-P2.5 Inc 2 — the ws33 "merges" guard and the GET-route inventory caught
  mid-build. Extended 2026-07-28, gap-closure session 1: adding ONE `/review` POST route and ONE
  scheduled workflow reddened three of these five at the final `make check`, all invisible to the
  per-task loop and the diff-scoped Stop hook.)

### #55

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
  like the retired `src/reconciliation_runner/` before it) must (a) register its egress files in that
  allowlist and (b) ship its own isolation test asserting it imports nothing from `orchestrator.*`
  and confines its third-party deps. Both of these are whole-repo scans: only a full `make check`
  runs them, so a per-task loop can look green and still break CI. (Verified 2026-07-26, WS-P2.7 —
  both reddened the final gate after every per-task review passed.)

### #56

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

### #92

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

### #107

- **`REACH_VOCABULARY` must stay a dict of STRING LITERALS — naming a single member silently blinds
  the cross-boundary scanner to the entire vocabulary.** `test_cross_boundary_vocabulary` finds
  vocabularies by AST-scanning for module-level string collections. Key one member by a named
  constant instead of a literal and the collection stops matching the pattern, so the registry loses
  its view of **all** of it — not just the renamed member. The failure is silent in the direction
  that matters: the guard stops guarding and nothing says so. Caught by that guard during WS-P2.28.
  **The fix is a pinned duplicate, never an allowlist entry** — an exemption here would read "a
  legitimate second copy", which is the predicate being wrong rather than the entry being justified.

### #117

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

### #174

- **Adding a source file under `src/` adds THREE parametrized cases to
  `tests/architecture/test_wsp21_invariant_scan.py`, and a new `scripts/*.sh` adds TWO** —
  `test_no_tracked_source_carries_a_secret`,
  `test_nothing_in_the_repo_calls_a_merge_method` and `test_nothing_in_the_repo_merges_a_pull_request`.
  The existing bullet above says two; measured 2026-08-13, two new `src/deploy_watcher/` modules
  added exactly six. The reconciliation method it prescribes (diff node ids between `main` and the
  branch) is right and is what produced this correction.
  **The shell figure was measured 2026-08-24: the merge-method scan is Python-only, so a script
  draws two cases rather than three.** That day 5 Python files plus 2 shell scripts added exactly
  19. Use the multiplier only as a cross-check — the node-id diff remains the answer.
  **A `scripts/*.py` ALSO draws TWO, and it is a DIFFERENT two — reading "a script draws two" as
  one rule for both kinds of script is the mistake.** Measured 2026-09-09 (`#248`, one new
  `scripts/*.py`, exactly two added cases). The three parametrizations key on different lists:
  `test_no_tracked_source_carries_a_secret` is `PYTHON_SOURCES` (`src/**.py`) + `SHELL_SOURCES`,
  `test_nothing_in_the_repo_calls_a_merge_method` is `MERGE_SCAN_SOURCES` (`src/**.py` +
  `scripts/*.py`), and `test_nothing_in_the_repo_merges_a_pull_request` is `MERGE_SCAN_SOURCES` +
  `SHELL_SOURCES`. So a `scripts/*.py` draws the two MERGE scans and misses the SECRET scan; a
  `scripts/*.sh` draws the secret scan and one merge scan and misses the merge-METHOD scan, which
  is Python-only. Both are two, sharing exactly one member.
  **The secret scan is NOT `src`-only and its gap is NOT a decision** — a first draft of this
  bullet said both and was wrong twice in one clause. It is `PYTHON_SOURCES + SHELL_SOURCES`, so it
  covers `src/**.py` **and `scripts/*.sh`**, which the very next clause here already implies; and
  the module's own comment calls the remaining hole *"the secret scan's identical blind spot is
  left alone here; it is not this change's subject"* — an acknowledged gap, not a design. The
  difference is load-bearing, because "by design" is what would stop the next reader closing it,
  and the uncovered set is exactly the `scripts/*.py` that read `GITHUB_TOKEN` from the
  environment. The module's
  own header says why `scripts/*.py` is a separate list: widening `PYTHON_SOURCES` to reach it
  would red the egress scan and force four new `OUTBOUND_ALLOWLIST` entries, weakening a
  structural chokepoint to strengthen the merge guard.

### #250

- **`pytest --collect-only -q` prints node ids and NO `rootdir:`; non-quiet prints `rootdir:` and NO
  node ids — so the collected-count reconciliation needs BOTH runs, and getting it wrong reads as
  success.** CLAUDE.md already records that `-q` suppresses the header. The other half is worse:
  non-quiet `--collect-only` prints a `<Module>`/`<Function>` tree rather than node ids, so
  `pytest --collect-only | grep '::' | sort` matches nothing and the `comm` against `main` reports
  **0 added, 0 removed** — a reconciliation that reads as "the counts are explained" while having
  diffed two empty files. Hit 2026-08-29 against a real +56 delta. **Assert the node-id file is
  non-empty before trusting the diff:** zero added and zero removed beside a non-zero count delta is
  a broken measurement, not a clean one.

### #259

- **`-q` IS CUMULATIVE, and a repo whose `addopts` already carries one turns the documented
  collected-count recipe into per-file counts.** `pytest --collect-only -q` in a repository with
  `addopts = "-q"` is `-qq`, which prints `path: N` lines instead of node ids — so
  `grep '::' | sort` matches nothing and the `comm` against `main` reports **0 added, 0 removed**
  against a real delta, which reads as "the counts are explained" while having diffed two empty
  files. Measured 2026-08-31 with controls in both directions, both repos on pytest **9.1.1**:
  `change-manager` (`addopts = "-q"` in `pyproject.toml`) prints per-file counts under an explicit
  `-q` and **node ids when the explicit one is dropped** (412 of them); `orchestrator` (no
  `addopts`) prints node ids under one `-q` and **nothing matching `::` under `-q -q`**.
  **THE ATTRIBUTION IS THE PART TO CARRY.** This was first diagnosed as "pytest 9 broke the recipe
  in both documented modes", which is false and would have retired a working technique estate-wide
  — the recipe is fine anywhere `addopts` does not already quieten. **Read `[tool.pytest.ini_options]
  addopts` before adding `-q`**, and keep the existing rule that a node-id file must be asserted
  non-empty before its diff is believed: that assertion catches this without needing to know the
  cause. Same family as the ruff 0.15→0.16 wording change — but note the difference, because it is
  the useful half: there the tool's output genuinely moved, here it did not and a per-repo config
  made it look as though it had. An observed behaviour is not yet a cause.
