# Method lessons

Lessons about testing, probing, measuring and reviewing that apply across repositories. Each is a candidate for Code Brain (`add_lesson`).

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #22

- **`make check` exit 0 does not prove the tests ran.** The vendored Makefile runs
  `pytest; rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi` — exit
  code **5 means "no tests collected"** and is deliberately swallowed so a TS-only
  repo can share the target. A misconfigured `testpaths`, a collection error in a
  `conftest.py`, or a tool resolved from the wrong venv can therefore produce a
  green `make check` having executed nothing. Read the collected-test count, in CI
  as well as locally (`collected N items`), not just the exit code. This is the
  local twin of the portfolio-wide invariant that `uv sync` installs no extras and
  `quality.yml` guards every tool with `command -v`.

### #28

- **A test fixture calling a service is not evidence the service has a caller.** WS-P2.1's PR-binding
  writers had no production call site at all: every reference in the approved plan was a test. The
  binding table was never written, the reconciliation runner (which discovers PRs to poll FROM those
  rows) had nothing to poll, and AC-001/AC-002 detection was never reached — silent, not merely
  blind, since `skipped_correlations` never incremented either. Green unit tests said nothing. When
  adding a service that another subsystem READS, grep for its production caller before believing it
  works — and prefer a drill that drives the public API, which is what actually caught this.

### #32

- **A nullable, default-disabled threshold is how a guard goes silent.** `age_out_human_gates`
  was fully implemented, fully unit-tested, and configured by
  `dispatch_human_gate_age_out_seconds: int | None = None` — it returned `()` immediately when
  `None`, and it was `None` in production. It reported nothing for an entire workstream and had
  no production caller either. WS-P2.15 deleted it and made its replacement's threshold a plain
  `int` with a real default and **no off value**. A reporting obligation that can be switched off
  is one that will be. **A dead config knob is the same defect as a dead function** — delete both
  together.

### #68

- **`gh search` cannot see PR comments on these repos — never measure the Evidence Pack marker with
  it.** `gh search issues "sds-evidence-pack in:comments"` returns 0 with the marker demonstrably
  present, and the index is stale enough that searching a term from a PR's own *title* also returns
  nothing. The Wave-2 clause-1 baseline was taken this way and was only coincidentally right (no PR
  existed at the time); the method reports 0 either way, so it is not evidence of absence. Count
  markers by REST enumeration instead:
  `gh api repos/<owner>/<repo>/issues/comments --paginate --jq '[.[] | select(.body | contains("sds-evidence-pack"))] | length'`.
  More generally: before treating a zero from any search API as proof of absence, run the same
  query against something you *know* is present. (Verified 2026-07-29, GAP-4 closeout.)

### #72

- **Never print a parsed component of a secret-bearing string; persist a generated secret before
  the mutation that depends on it.** Both rules were learned by breaking them in one session.
  `urlsplit()` on a value that fails to parse puts the ENTIRE raw string into `.path`, so a
  diagnostic printing `parts.path` leaked a live DB password to the transcript — emit sha256
  prefixes, lengths and booleans only. Separately, a rotation script that ran `ALTER USER` and
  only afterwards persisted the value left the database on a credential nobody held when the
  next step failed. Order is: generate → persist (0600 + clipboard) → mutate → verify.
  (Verified 2026-07-29, GAP-7.)

### #73

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

### #76

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

### #79

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

### #83

- **An unhashable value in a membership test is an unhandled HTTP 500, not a validation error.**
  Found reviewing WS-P2.18 Inc 3's own diff: a reach member that is not hashable made the
  membership check raise `TypeError`, and since only `DomainError` and `APIAuthenticationError` have
  registered handlers (`main.py`), it surfaced as a bare 500 from the human gate. Pre-existing, now
  guarded. This is the same class as the WS-P2.3 findings — **any route-reachable parse or
  membership test must raise `DomainError`, never let the stdlib raise** — and it generalises: when
  validating a value of unknown shape, `in`/`set()`/`dict[...]` are as dangerous as
  `uuid.UUID(bad)`.

### #86

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

### #94

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

### #104

- **`git stash` does not stash UNTRACKED files, so a stash-based control against `main` is
  contaminated by any new file the branch adds.** WS-P2.20's collected-count reconciliation measured
  `main` at 2187 rather than the true 2185, because the new `src/orchestrator/services/` module
  stayed on disk through the stash and
  `tests/architecture/test_wsp21_invariant_scan.py::PYTHON_SOURCES` is
  `sorted(SRC.rglob("*.py"))` — a **filesystem** walk, not `git ls-files`. So the new module's two
  parametrized scan cases were counted on both sides and silently cancelled out. Use
  `git stash -u` (or `git archive HEAD`) for any control that must not see the branch's new files.

### #114

- **To tell a `verify_work_unit` adjudication from a hand-written one, classify the RATIONALE — and
  the machine set has CHANGED OVER TIME, so a classifier keyed on today's strings misreads history.**
  The verifier writes the evaluator's own `reason`, a closed set.
  `"named check and assertions passed"` was the machine reason from `9f86cf7` (2026-07-15) until
  WS-P2.20 (`8e13258`) replaced it with `"the named check was observed to conclude success"`. Keying
  only on the current string misclassifies **nine** production adjudications as hand-written.
  `git log -S "<the string>"` before assuming a vocabulary is stable — this is the same class as the
  three vocabulary mismatches documented above, in the one place where the vocabulary is a
  *historical* record rather than a live contract.

### #116

- **But the revision-anchored and unit-anchored traceability answers are DIFFERENT query paths and
  can disagree — so "no second surface" is not "no second reading".** `resolve_anchors` branches,
  and conditions arrive whenever a producer reports (the reconciliation runner that was meant
  to write them on a schedule never ran and was retired by ADR-0048), so asking the unit-anchored query about the
  same units is a genuine second reading rather than a restatement. **Concluding that production
  serves no corroborating surface, and stopping there, is what shipped WS-P2.41's severe defect** —
  a carrier scan that failed to exclude the release's own units, so a release whose unit carried a
  divergence its chain omitted PASSED, citing that very divergence as proof it had none. A
  discriminator that consumes the thing it is meant to detect is the sharpest form of the
  correct-about-the-wrong-noun family; reproduce it under a passing exit before fixing it.

### #118

- **A control written before a fix can survive the fix and stop discriminating.** Two of WS-P2.41's
  21 mutations initially survived because controls written hours earlier pinned behaviour the fix
  changed, and passed either way. Re-run the mutation set *after* the last behavioural change, not
  once when the tests are written — a green control is evidence only about the code it was last
  run against.

### #119

- **A derived constant cannot pin the judgment that lives inside it.** WS-P2.40 shipped 86 tests
  over an eight-hop traceability chain where every fixture built itself by iterating `ALL_HOPS` —
  so the fixtures shrank with the list, and review *measured* that dropping `conditions`, or
  `intent`, or `commit`, left all 86 green. The hop list WAS the judgment the workstream existed
  to make, and it was pinned by nothing. Where a value is both the judgment and the fixture
  generator, it needs a **literal** assertion of its members plus a second assertion that the
  consumer emits exactly them; neither alone catches what the other does. This is WS-P2.39's own
  lesson recurring inside its successor — a tool built against reasoning-from-a-summary whose
  central judgment was derived rather than declared.

### #120

- **A shape guard placed one level too high fails OPEN while looking like the fix.** Same
  workstream: a guard added *specifically* to stop an absent key becoming a verdict was attached to
  the report rather than to the metric under judgment, so a `budget_breach` object that lost its
  `status` key read as instrumented. Its sibling: `len()` on whatever a hop lookup returned, where
  `len("sha-abc")` is 7 — a scalar read as a populated hop. **Name the exact value the verdict is
  computed from and guard THAT, not its container.** Same family as the WS-P2.18 Inc-4
  withheld-refusal fail-open and WS-P2.34's `KNOWN_FIELDS` mis-keying: a check that is correct
  about the wrong noun. Note both of these fail OPEN, where all seven defects the author found
  himself failed closed — which is what adversarial review is for.

### #123

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

### #151

- **`GET /work-units/{id}/evidence-pack` serves a PROJECTION of the authority envelope; the runner
  brief serves the STORED COLUMN. Measure at the consumer's surface.** The pack renders
  `normalize_authority(unit.authority).normalized()`, which always emits `unknown_fields` and every
  other declared key — so a census taken there reports a shape no runner ever sees. WS-P2.34's
  first sweep did this and concluded every stored envelope carried an `unknown_fields` key;
  re-measured through `runner-brief` (which serves `unit.authority` verbatim), **41 of 41 carry
  none**, and stored envelopes legitimately omit optional keys (`change_class` on 35, `conformance`
  on 32). This is the same failure the `response_model` invariant describes, one layer out: the
  surface you read is not necessarily the surface the consumer reads.

### #155

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
  plain token. `services/verifier/github_checks.py` already reads jobs and documents why.

### #156

- **GitHub populates `merge_commit_sha` on OPEN pull requests with a throwaway test-merge commit
  — a real, fetchable object that passes every shape check there is.** `change-manager` PR #42
  carries `6a7c99a94c52…` ("Merge b30708ce into ef671eeb") while unmerged, against a base that has
  since moved, and it resolves through `GET /contents/{path}?ref=` and every other hop. **`merged`
  / `merged_at` is the field that decides**; a reader that trusts the sha will walk a whole
  pipeline successfully and produce a confident answer about a landing that never happened.

### #159

- **A mutation control that PRESERVES FILE SIZE can be invisible to Python's bytecode cache, and
  the harness then reports SURVIVED for a mutation the interpreter never loaded.** `if early:` →
  `if False:` is byte-for-byte the same length, and a `.pyc` is validated on (size, mtime to the
  second). Observed 2026-08-10: one control alternated between killed and survived across
  otherwise identical runs, and killed reliably when run by hand seconds later. Any mutation
  harness must set `PYTHONDONTWRITEBYTECODE=1` (or clear `__pycache__` per mutation) — without it
  the false answers run in BOTH directions. Relatedly, the estate's "never run two pytest suites
  concurrently" rule extends to mutation runs for a sharper reason: the mutation IS a tree edit,
  so any concurrent reader imports a half-mutated tree.

### #162

- **A time-dependent control passes for the wrong reason most of the day.** A single
  "outside the change window" assertion cannot kill a term that ignores its injected clock, because
  whenever the real clock is also outside the window the mutant and the original agree — and
  `live_estate`'s window is four hours, so the control is honest for 17% of the day. This recurred
  *inside the fix for itself*: the mutation added to guard the acting path was pinned to the
  out-of-window case alone and survived for the same reason one increment later. **Pin a
  clock-dependent guard to a PAIR of cases whose answers must differ** — in-window admits,
  out-of-window refuses — so a term that never reads the clock reddens at any real time.

### #166

- **A client that re-checks two of three scoping dimensions and trusts the server on the third is
  defensive about the wrong things.** `change_record.py`'s first draft verified the repository and
  the pull request number on each row and took `source` from the query — the one dimension that
  makes the answer about the right pipeline at all. FastAPI ignores an unknown query parameter
  silently, so a renamed parameter would have handed admission a record from another pipeline.
  Same family, one field over: an **ambiguity guard keyed on a fully-parsed row** is defeated by a
  malformed twin, which the parser skips, dropping the tally below two and letting the survivor
  through as unambiguous. **Detect ambiguity on the MATCH KEY and read the rest only from rows that
  already matched.**

### #167

- **The report surface and the ACTING surface are different tests, and only one of them changes a
  repository.** Every case in `tests/services/test_pr_merge.py` passed an inert estate, so when
  ADR-0019 Increment 3 added routed terms the surface that actually lands a pull request was
  covered for `inert` alone — and could not be covered, because `land_unit_pull_request` took no
  clock while `admission_for` did. A refusal test there must assert the **gateway was never
  reached**: an admission answer that arrives after the act is not a gate.

### #169

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

### #171

- **`app.routes` does NOT contain an included router's routes in current FastAPI — it holds a single
  `fastapi.routing._IncludedRouter`.** So the obvious completeness scan (filter `app.routes` for
  `APIRoute`) sees only what was registered directly on the application: in change-manager that is
  exactly ONE route, and the whole `/api` surface reads as absent. **The failure is silent and
  flattering** — a test built that way asserts nothing while looking thorough. Enumerate from
  `app.openapi()["paths"]`, which is flattened and authoritative and is what this repo's own scope
  guards already use, and **cross-check any completeness claim against a set of routes known to
  exist**, which is the only reason this was caught. (Verified 2026-08-11, ADR-0019 increment 4.)

### #172

- **`httpx` raises at the CONSTRUCTOR for some malformed URLs and at REQUEST time for others, so a
  guard on one half is not a guard.** A control character is refused by `urlparse` inside
  `httpx.Client(base_url=…)` immediately; a doubled dot or an over-long DNS label survives to IDNA
  encoding at `client.request`. This repository already documents the three exception families
  (`HTTPError`, `InvalidURL`, and `ValueError` via `UnicodeError`) — this is the same family one
  layer out, and the practical shape is that an environment-variable typo crashes an out-of-process
  program with a traceback instead of reporting a finding. Guard **both** construction and request,
  and write the control to span both, since which shape raises where is not guessable.

### #180

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

### #184

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

### #189

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

### #191

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

### #192

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

### #193

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

### #203

- **`npm install` and `npm ci` DISAGREE, and the target repository's named check runs the second —
  so proving the mutator works is not proving the gate passes.** `npm install` resolves a workable
  tree and writes a lockfile; `npm ci` installs that lockfile **strictly** and refuses it when a
  peer range is unsatisfied. Measured on one tree seconds apart, 2026-08-19: `npm install` rc=0,
  `npm ci` rc=1. So the dependency-update dry run — which proves a real diff, idempotency, moved pin
  sites and a runner-honest verifier — passed on a tree the gate refuses, and the failure surfaced
  only after two human approvals and a spent work-unit attempt. The subject was typescript
  5.9.3 → 7.0.2 into `infraops-mcp-server`, refused by
  `peer typescript@">=4.8.4 <6.1.0" from typescript-eslint@8.67.0`; **no published
  `typescript-eslint` lifts that ceiling, the `canary` tag included**, so the bump is unadoptable
  rather than merely hard. TypeScript 7 itself compiles that codebase clean — the blocker was the
  eslint toolchain, which is why the compiler-shaped checks all passed.
  **Closed by naming `npm ci` in the npm verifier** (intent-packages #73): `dry_run_mutation`
  executes the whole ordered list, so the refusal moved to authoring time — proven against the real
  checkout, `mutation command failed: 'npm ci' exited 1`, with a positive control (typescript
  5.9.2, inside the peer range) still ACCEPTED so the change is not one that refuses everything.
  **Generalise past npm: the estate's dry-run rule says prove the mutator yields a diff and prove
  the verifier executes tools. It does not say prove the TARGET REPOSITORY'S OWN GATE would pass,
  and those are different commands.** Before trusting a dependency-update envelope, read the target
  repo's named-check workflow and ask which command it installs with.

### #207

- **In `change-manager`, no test can see a missing `db.commit()`, and the estate's usual advice does
  not save you.** This repository's standing rule is that a persistence assertion must re-read
  through a DIFFERENT session, because `expire_all()` only defeats the identity map. That is correct
  here and **insufficient in change-manager**: its `db` fixture hands the application the same
  session the test asserts through, and its in-memory engine behind a `StaticPool` gives every
  session ONE connection — so a flushed-but-uncommitted row is visible to a second `Session` as
  well. The discriminating control there is a **file-backed** database plus a second session; only
  that separates the connections. Found 2026-08-21 building ADR-0029's retirement route. Generalise
  past this repo: before trusting "re-read through a different session", check what the fixture's
  engine and pool actually give two sessions — the advice assumes connection isolation the fixture
  may not provide.

### #211

- **A disposable control repository must be PUBLIC, or this estate's Actions quota answers the
  experiment for you.** A private repo with the quota spent fails **every** job in seconds at
  environment setup, and the job carries **zero steps** — which reads as whatever failure the
  control was built to produce. Compounding it: a workflow with no `actions/checkout` runs its
  assertions against an empty directory, so a gate meant to fail on a file cannot see the file.
  Both were live in one control on 2026-08-22 and it returned the expected answer for two
  independent wrong reasons. **Before believing a red control, check `jobs[].steps` is non-empty.**
  This is the estate's own *a probe must discriminate* rule with a specific, cheap check attached.

### #213

- **A backgrounded gate inherits the session's DRIFTED cwd, and a green `make check` over the wrong
  tree is success-shaped — read pytest's `rootdir:` line before believing a gate verified your
  branch.** A build session works in a worktree, but any `cd` to another tree for an unrelated read
  moves the shell, and a later backgrounded `make check` runs *there*. Observed 2026-08-23, the
  ledger-exception build: the session's second gate reported
  `rootdir: /Users/devon/Projects/orchestrator` — HQ's main tree, on `main` — ran **17m42s**, and
  passed. **The danger is that it is GREEN**, so nothing about the output invites suspicion; the
  only tell is one line of pytest's header, and the collected count differs only if the branch
  changed the test count. That run was a legitimate `main` baseline and worthless as evidence about
  the branch. Put an **absolute `cd` inside the backgrounded command itself**, and read `rootdir:`
  beside the collected count. The header survives where the summary does not: `rootdir:` prints in
  the first six lines, so it outlives the output truncation that eats trailing summary lines.
  **BUT `-q` SUPPRESSES THE ENTIRE HEADER, INCLUDING `rootdir:` — so the compact form everyone uses
  for counting is exactly the form that hides the only tell.** Measured 2026-08-24: `--collect-only`
  prints `rootdir:` on line 3; `--collect-only -q` prints node ids and a bare count and no header at
  all. If a count matters, run without `-q`, or read `rootdir:` from a separate non-quiet run.
  **This is the best explanation for an otherwise undetermined observation** (WS parked-checkout,
  2026-08-24): a hooked `uv run pytest` reported **66 collected / 66 passed against a true 83**,
  with per-file counts of 31/14/16/5 — *`main`'s shape exactly* — while a direct
  `.venv/bin/python -m pytest` answered 83 from the same tree seconds later. A drifted cwd reading
  the MAIN tree while the worktree held the edits produces precisely that, and `-q` would have
  hidden it. **Stated as the leading hypothesis, not as fact:** HQ could not reproduce a
  hook-caching effect — from the repo root, hooked and direct agreed at 83 three times running —
  and the build session was right to refuse to name a mechanism its evidence did not carry. Take
  any count that matters from the direct form, non-quiet.
  This is the existing "absolute `cd` every Bash call" rule with the specific tell attached — and
  it is a *third* way `make check` reports a green it did not earn, beside exit-5-no-tests-collected
  and tool-guarded skips.

### #214

- **A single `export A=x B="$A"` expands `$A` to its PRIOR value, never to `x`.** Argument expansion
  happens before any assignment in the same command takes effect. **This is not a shell-specific
  quirk** — measured identically in `zsh` and `bash` 2026-08-24, so do not "fix" it by assuming the
  other shell differs. It bites hardest in the worktree recipe, where `export
  TEST_DATABASE_URL=… ORCHESTRATOR_DATABASE_URL="$TEST_DATABASE_URL"` silently sets the second to
  empty and produces `sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL` across every
  collection target — which reads as a broken environment rather than as a typo one line up. Use
  separate `export` statements.

### #215

- **`git log` obeys the OPERATOR'S GLOBAL config, and `log.showSignature` puts `gpg:` lines on
  STDOUT — so a parse that works on every machine in this estate today is one
  `git config --global` away from breaking on all of them at once.** Measured 2026-08-24 against
  a signed squash merge: `git -c log.showSignature=true log -1 --format=%H%n%cI <sha>` emits
  **three** `gpg:` lines *before* the format output, on stdout, not stderr — so anything counting
  lines or splitting fields silently reads a signature warning as data. Every commit GitHub
  squashes onto `main` here is signed, so the trigger is real rather than exotic. Pass
  **`--no-show-signature`** on any programmatic `git log`; verified to restore exact output.
  **Note the class, which is the durable part: a test suite that scrubs the global git config —
  which it should — is STRUCTURALLY BLIND to every defect of this shape.** The control can only be
  a shape assertion plus a measurement recorded outside the suite.

### #216

- **`git remote get-url` applies `url.<base>.insteadOf` and answers with the URL git will TALK to;
  only `git config --get remote.origin.url` answers with the URL the repository is CONFIGURED
  with.** Measured 2026-08-24 — `remote -v` rewrites too. What *identifies* a repository is the
  configured value; the rewrite is a transport detail of one machine, so a producer keying identity
  on `get-url` reports a different name on a machine with a rewrite rule. Consequence for a
  read-only git allowlist: `config` must be **on** it, and because `git config a b` *sets* a value,
  the subcommand name alone is not a permission — require the `--get` form explicitly.

### #217

- **A `plistlib` failure on a plist TEMPLATE is not evidence the template is broken: launchd is
  more permissive than expat about `--` inside an XML comment.** `scripts/com.devon.landing-ledger.plist`
  raises `ExpatError: not well-formed (invalid token)` under `plistlib.load` and is loaded and
  running under launchd right now (`launchctl list` confirms it). Verified 2026-08-24. **Do not
  "fix" a template on the strength of a `plistlib` failure, and do not add a `plistlib` gate
  expecting it to mean anything.** This also explains a note left open in the 2026-08-21
  cascade-activation spec, which recorded that four orchestrator plists "would not parse with
  `plistlib`" and treated it as an unknown.

### #219

- **FOUR tests for "did this branch land?" all have blind spots in this estate — the GitHub one
  included. Only a NAMED ARTIFACT settles it.** (This bullet said "three" and called the GitHub
  lookup reliable when written on 2026-08-24; the fourth was measured hours later, against the
  residue the first three could not classify.) Squash-merge is the root cause of the first three,
  and each was measured wrong on real branches within 24 hours of 2026-08-24:
  1. **`git merge-base --is-ancestor`** — a squashed branch commit is *never* an ancestor of `main`,
     even when its content is fully landed. This is also why **`git branch -d` refuses these
     branches and `-D` is required**; `-d` runs the same test.
  2. **`git cherry`** (patch-id) — survives a one-commit squash and fails on a multi-commit one.
     `brain`'s `ci/verify-revision` had two commits collapsed into one squash; cherry reported `+`
     (unlanded) while **all four touched files were byte-identical to `main`**. It had landed as
     PR #47 on 2026-08-14.
  3. **Content comparison** — the fix for (1) and (2), and *too conservative* on its own: once
     `main` changes the same file again, a landed branch's tip differs anyway. Applied across the
     six SDS targets it reported **61 of 79 branches as possibly-unlanded**, which is useless.
     It remains the right test for a SINGLE branch you are about to delete (it is safe in the
     conservative direction), and the wrong one for a population.
  **Ask GitHub instead: `gh pr list --repo <r> --head <branch> --state merged`.** GitHub records
  that a pull request merged regardless of how it was merged. On the same population it answered
  **74 merged, 5 without a merged pull request.** But:
  **4. `gh pr list --head <branch>` is authoritative about the branch NAME, and the name is the weak
  link.** It asks *"was there a merged pull request from this exact head?"*, so a rebase, a rename
  or a cleaned redo breaks the association though the work landed. **All 5 of that residue had in
  fact landed, under a different name** — an earlier version of this bullet called them "genuine"
  and was wrong. Every one had a near-twin in the merged list: `deliberate-refusals` beside the
  merged `deliberate-refusals-rebased`, `wsp37-inc4b-act-clean` beside the merged
  `wsp37-inc4b-act`, two `worktree-agent-<hex>` scaffolds whose work landed under proper names, and
  one branch called `backup/local-main-superseded-20260811`. **A near-twin in the merged list is
  the tell.**
  **What settles it is a NAMED ARTIFACT the branch introduced — look for that, never for a diff.**
  A migration file is close to ideal: its name is unique and it either exists on `main` or does
  not. `0022_wsp36_landing_type.py`, `0023_wsp36_landing_audit.py` and `0025_wsp37_pr_merge.py`
  resolved three of the five outright. Failing a migration, a new module or a distinctive constant
  does the same — `_DELIBERATE`/`_EXCEPTION` in `estate_lander/cli.py` resolved a fourth. The fifth
  was **superseded**: `main` carried a *more developed* version of the same reasoning, which no
  equality test could ever have shown.
  **Working order for this estate: (a) `gh pr list --head`, which classified 74 of 79; (b) for the
  residue, name the artifact and check `main` for it.** Content diffing is the wrong tool at both
  steps. With one human on this machine and otherwise only agents, the residue's shape is
  predictable — a session rebases, renames or redoes a branch, the tidy version merges, and the
  original lingers with a broken pull-request link.
  Generalise past branches: **when a local heuristic and the system of record can both answer a
  question, ask the system of record — then check what its answer is KEYED ON, because that key is
  its blind spot.**

### #221

- **A mutation harness must restore from GIT, not from memory — a timeout is not a clean exit.**
  A command timeout sends `SIGTERM`, Python's default handling does not run `finally`, and the
  mutation is left on disk, surfacing later as an edit nobody made. Measured 2026-08-24: a killed
  run left one deleted line in a `cli.py` that read as a source defect an hour later and cost a real
  diagnosis. Restore with `git checkout <ref> -- <path>` and **assert the restore landed** before
  the next mutation. Pairs with the existing rule that a mutation harness must set
  `PYTHONDONTWRITEBYTECODE=1`: both are ways a harness silently reports about a tree that is not the
  one you think you are testing.

### #222

- **`git clone` of an EMPTY repository sets no `origin/HEAD`, and pushing to it never creates one.**
  Only a clone from a non-empty remote, or an explicit `git remote set-head origin -a`, does.
  Measured 2026-08-24. So a fixture built the obvious way — `init --bare` → clone → commit → push —
  **differs from every real checkout in exactly that ref**, and a classifier that reads
  `origin/HEAD` then measures the wrong path in every test while looking thoroughly covered. This is
  the fixture-shaped twin of *validate the classifier against the population*.

### #223

- **`git rev-parse --abbrev-ref origin/HEAD` prints the literal string `origin/HEAD` on STDOUT and
  exits 128 when the ref is absent — so only the EXIT STATUS discriminates absence.** And when
  `origin/HEAD` is a symbolic ref pointing outside `refs/remotes/origin/`, it answers a **bare**
  name (`main`, `v1`), so only a prefix check discriminates that case. Measured 2026-08-24 while
  building the sweep's default-branch read. A reader that trusts stdout classifies every checkout as
  being off its default branch; a reader that skips the prefix check can be accidentally right when
  the symbolic ref happens to point at the local default, which is why only a symbolic-ref-to-a-TAG
  control discriminates.

### #225

- **A NEGATIVE-AUTHORIZATION PROBE THAT FOLLOWS REDIRECTS REPORTS A SECURITY CONTROL AS ABSENT.**
  Measured 2026-08-24 running criterion 3's negative tests. `POST /api/v1/work-units/{id}/approvals`
  sits behind the `orchestrator-authority-approval-human` forward-auth Traefik router, so an M2M
  bearer gets **302 to `id.alobar.net`** and never reaches the app. `urllib` (and `curl -L`, and
  `requests` by default) **follows that redirect**, lands on Authentik's authorize page, and returns
  **200** — which a harness scoring "did this succeed?" reads as *the OBSERVER credential just
  approved a work unit*. It had not; the evidence pack showed the unit's only approval was still
  Devon's original one. **Disable redirect following in any authorization probe** and treat 3xx as
  its own outcome, because the redirect IS the refusal. Re-run with redirects off: 403 on seven
  routes, 302 on the eighth, **0 not refused**.
  Two things this makes concrete. **A 200 in an authorization test is only meaningful if you know
  WHICH SERVER produced it** — read the final URL, or don't follow. And **the estate refuses at two
  different layers**: `_confine_observer` (app, `role_forbidden`) and the proxy's forward-auth
  routing (302). A route protected only by the second would fall through to the M2M router if that
  Traefik entry were ever deleted — as `orchestrator-intake-human` was on 2026-08-19 under ADR-0027.
  `/approvals` is protected by BOTH (`_require_human` and the observer confinement also refuse it),
  so it is defence in depth rather than a single point; **check that property before deleting any
  human router**, which is the same check ADR-0027 required and passed.

### #227

- **`git merge-base --is-ancestor` is the RIGHT tool for "has this machine got that MERGE COMMIT"
  and the WRONG one for "did this BRANCH land" — and without both halves the pair reads as a
  contradiction.** The estate records the second half above. This is its complement: a squash-merge
  collapses a branch into one NEW commit, so a branch tip is never an ancestor of the default
  branch — but the **merge commit is a real commit on that branch** and is an ancestor of anything
  pulled after it. Measured 2026-08-24 in one checkout, both directions: `ac01f838` (the landing of
  `infraops-mcp-server#81`) **is** an ancestor of HEAD; `fcc4f881` (that pull request's own head)
  is **not**. **Say which question you are asking, in a comment**, because the next reader will
  arrive holding the other rule.

### #230

- **A payload composed in one program and parsed in another crosses a boundary no unit test sees,
  and the REQUEST MODEL is a second rule set on top of the service's.** The binding lane's first
  live pass refused all twelve candidates with **HTTP 422, writing nothing**:
  `CommandBase.expected_version` is `int = Field(ge=0)` — required, non-nullable — where the
  service's dataclass is `int | None = None`. FastAPI answered before any service code ran, so no
  named error was reachable and no service test could see it. **The request model is LOOSER than the
  service in some places and STRICTER in others, so neither direction can be assumed.** A test may
  import both sides: validate the composed payload against the route's own model, with a control on
  the exact field. This is the `response_model`-drops-fields invariant in the opposite direction —
  outbound silently loses keys, inbound loudly rejects them — and **the dry run is what makes it
  cheap**, having reported twelve would-bind rows before anything permanent existed.

### #231

- **An unkillable clause is a defect of the test suite, not a free safety margin.** A `kind`
  comparison in `_same_binding_facts` could never differ, because the kind-conditional CHECK forces
  the registry columns NULL for one kind and non-empty for the other, so two rows cannot share a
  source tuple and differ in kind. It survived every mutation and was **removed rather than kept**:
  a clause nothing can falsify sits beside clauses that can, and that is how a mutation set comes to
  report a green it did not earn. Same family as the *allowlist entry that would read "in fact it is
  called"* — when a check cannot fail, the predicate is wrong, not safe.

### #232

- **A rot check keyed on ONE reader breaks when a SECOND reader arrives, and the failure looks like
  the guard working.** `test_the_read_only_surface_is_what_the_reader_actually_uses` asserted that
  `read_checkout` exercises every member of the git allowlist. Adding `merge-base` for the binding
  lane reddened it — correct by its own predicate, wrong as a statement about the estate, since the
  permission *is* watched, by a reader the test did not know about. **Widen such a check to the
  UNION of its readers rather than allowlisting the new member**, which would have exempted a real
  permission from the only thing watching it.

### #233

- **AN ACCEPTED ADR WHOSE MECHANISM WAS NEVER BUILT DOES NOT SIT QUIETLY — IT RESURFACES AS AN OPEN
  QUESTION AND GETS RE-LITIGATED, AND THE SECOND PASS MAY REACH THE OPPOSITE ANSWER.** Twice in one
  week, both times costing real time and one of them nearly reversing a decision Devon had already
  made:
  - **ADR-0015** (2026-08-04) decided that a repository self-declares factory membership in
    `PROJECT.md` frontmatter, *"repo-local and self-describing, rather than a list inside the kit
    that the affected repository cannot see."* Unbuilt for thirteen days; `project-standards` was
    deliberately excluded and then re-onboarded three days into that window. **CORRECTED
    2026-09-10 on both details, and the correction makes it a SHARPER example rather than a
    retired one.** The KIT half shipped 2026-08-17 (`project-standards#24`), so "never built" is
    false — and the 2026-08-24 consequence, six exchanges reconstructing "which repos are in SDS
    scope" by hand, happened a week AFTER it shipped, because the half that answers a scope
    question is the one with no checkout: dispatch admission still reads a hand-maintained
    `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES`. **An ADR half-built is this bullet's own
    thesis at its worst**, because the shipped half makes the unshipped one look done. The
    re-onboarding was also not "a sweep that never consulted the decision" — `6aeff6f` names the
    ADR and cites Devon deciding; it was momentum, see the corrected bullet above.
  - **ADR-0025** (2026-08-17) decided that a `factory-delivery` change record is approved **by
    policy, not by a click** — *"There is no per-record human approval."* Never built:
    `change-manager`'s `deploy_policy.py` reached version 3 with all three versions pinning
    `change_classes = frozenset({"dependency-update"})`. It was carried in the programme plan as an
    OPEN decision, and on 2026-08-25 HQ explained it back to Devon as undecided and **recommended
    the opposite**, on reasoning weaker than the ADR's own — the ADR kills per-record approval on a
    structural ground (change-manager has no GitHub egress, so a record approval *cannot* show a
    human what changed) that the second pass did not have. Devon: *"I thought we had already decided
    this previously."*
  **The rule: an Accepted ADR whose mechanism does not exist must appear as OUTSTANDING WORK, never
  as an open question.** The two read identically in a planning document and are opposites — one
  wants building, the other wants deciding, and filing the first as the second invites a
  contradictory ruling. **Before presenting anything as an open decision, grep `docs/decisions/` for
  its subject.** Both of these had an ADR whose *title* answered the question being asked.

### #234

- **A `# not-a-vocabulary`-style comment that anticipates a future decision is not a record that the
  decision was made.** `change_proposer/cli.py`'s `FACTORY_CHANGE_CLASS` comment ends *"it is the
  name a human would add to a policy version if he decided these records should be pre-approved"* —
  written 2026-08-16, one day before ADR-0025 decided exactly that. The comment still reads as
  though the question is open, because nothing updates a comment when an ADR lands. **When an ADR
  settles something a code comment anticipates, the comment is part of what the implementing change
  must fix**, or the next reader takes the code's word over the decision's.

### #235

- **A CONTENT DIGEST IDENTIFIES A TREE; AN IDEMPOTENCY KEY IDENTIFIES AN OPERATION. They coincide
  only where one tree can produce one operation.** The machine-activation check was first keyed on
  `machine-activation-check:{digest}` — and every unit of one repository shares a digest, six of
  `intent-packages`' candidates sitting at one HEAD. The first observation would have been written
  and **every sibling refused as `idempotency_conflict` on the first live pass, every pass, with no
  way to settle it** (observations have no delete route). Caught by reading, not by a test; the
  sibling binding lane had the right shape — `machine-activation:{work_unit_id}` — all along.
  **Before keying anything on a digest, count how many subjects share it.** Same family as the
  ADR-0022 frozen-facts trap, one field over.

### #236

- **`uv sync --check` is a measurement and `uv sync --frozen --check` is a SAFE one.** Exit 0 means
  synchronized, exit 1 not, and anything else is uv failing rather than answering — so branch on
  those three, never on truthiness. **`--frozen` is what keeps it read-only:** without it the
  command may re-resolve and rewrite `uv.lock`, so a checker would mutate the repository it is
  reading. Measured 2026-08-25 across three working copies, the tree is byte-identical before and
  after. **Two of the three enrolled Python checkouts were out of sync on the day the check
  shipped** — `intent-packages` at ruff 0.16.2 against a lock of 0.16.3, and `security-standards`
  at ruff **0.15.21** against the same 0.16.3, i.e. the repository that runs the scanner and the
  Stop hook was executing across the 0.15/0.16 boundary the estate spent a day on. Not a
  hypothetical. **Note what it does NOT ask:** whether the lockfile matches `pyproject.toml`. A
  repository whose lock is stale relative to its manifest answers `yes`.

### #247

- **Dependabot secret treatment follows the TRIGGERING ACTOR, not the pull request's author — and
  `pull_request_target` resolves secrets from the ACTIONS store even when Dependabot triggers it.**
  Measured 2026-08-29 on `orchestrator#3` with a probe that read a real Actions secret beside a name
  present in no store, so it could report absence.
  Run `33265327300`, `triggering_actor=dependabot[bot]`, head `69c7d38a` (a genuine
  `@dependabot rebase`): **`FACTORY_PR_TOKEN` RESOLVED**, control EMPTY. Run `33265253593`,
  `triggering_actor=AlobarQuest`: also RESOLVED — and that first run is the cautionary half, because
  HQ triggered it by REOPENING the pull request and briefly read it as an answer. A human-triggered
  event on a Dependabot pull request is not a Dependabot-triggered event, and `github.actor` in the
  run's own output is the tell.
  **Consequence:** the auto-merge cascade, which runs `on: pull_request` where a Dependabot-triggered
  event sees the (empty) Dependabot store, can arm with an identity other than `GITHUB_TOKEN` by
  moving to `pull_request_target` — no credential copied into a second store, which was the whole
  cost the 2026-08-25 ruling declined. Safe for this workflow specifically because it checks nothing
  out and executes no pull-request content; it reads metadata through the API and calls
  `gh pr merge --auto`. Note the change moves each gate's blob sha, so `landing_ledger/rules.py` must
  be re-transcribed in the SAME operation or `bump_proposer` refuses every repository with
  `gate-not-transcribed` and the ledger loses the `auto_merge_rule` basis.

### #249

- **A CENSUS THAT FILTERS ON A VOCABULARY LITERAL MUST PRINT THAT FIELD'S OBSERVED DISTRIBUTION
  BESIDE THE RESULT.** The landing ledger's rule basis is spelled `auto_merge_rule`, not `rule`. A
  production census filtered on `basis == "rule"` reported **0 missing across all eight
  repositories over 671 rows** — a confident, complete-looking all-clear that was pure artefact; the
  correct literal returns exactly six. Caught only because a second script printed the `bases`
  distribution and showed no `rule` at all. This is the estate's probe-must-discriminate rule
  applied to a census, and the discriminator costs one line. A zero from a filter is never evidence
  of absence.

### #254

- **An auto-merge honours required status checks regardless of whether the arming identity could
  bypass them.** Measured 2026-08-30 on a disposable public repository with `enforce_admins: false`:
  a pull request armed by an admin with its required context at `failure` stayed OPEN and `BLOCKED`
  for 90 seconds; the same pull request, same arming, with the context flipped to `success` merged
  in under 10 seconds. The second row is the positive control that makes the first mean something.
  So "an admin arms it, therefore admin bypass applies" is false — worth knowing before treating an
  arming-identity change as a safety question, which is the question ADR-0035 did not ask.

### #255

- **A MUTATION RUN HAS THREE WAYS TO REPORT A PASS IT DID NOT EARN, and this estate hit all three
  in two days. A harness must distinguish "the mutation was applied and killed" from "the mutation
  never applied" and from "the suite was already broken."**
  1. **The anchor no longer matches.** `ruff format` rewrites the exact whitespace a
     text-substitution mutant anchors on, so a harness authored before the formatter runs reports
     its most important mutants as untouched. Caught 2026-08-30 only because that harness reported
     *"anchor matched 0 times"* separately from *"tests passed"* — one that silently skipped would
     have reported **12/15 as 15/15**.
  2. **The kill is a COLLECTION ERROR, not an assertion.** Found 2026-08-29 by reading the kill
     rather than the count: a new test inserted immediately above a parametrized one had captured
     its decorator, so that file collected nothing at all. Every mutant reported KILLED against a
     control that was seeing nothing.
  3. **The harness mutates the tree while you commit.** A background run swept two live mutations
     into commits, one under a message claiming to fix the other. **A 0-byte redirect file is not
     evidence a process is dead** — its output was OS-buffered. Restore from git, require the tree
     COMMITTED first, and set `PYTHONDONTWRITEBYTECODE=1` so a same-length mutation is not masked
     by a stale `.pyc`.
  **Read the kills, never the count.** All three produce a green number, and the number is the only
  thing anyone looks at.

### #256

- **An unkillable clause is a defect of the tests, not free safety margin — and the fix is usually
  to strengthen the TEST, not delete the clause.** 2026-08-30: a mutant reading *"trust an arm
  outcome for an untranscribed revision"* could not be killed, because the untranscribed fixture
  carried no arm outcome for the clause to act on. The right move was to give that fixture an arm
  outcome that WOULD have flipped the basis, after which the clause states a real rule instead of
  sitting redundant. Deleting it would have removed a guard; leaving it unkilled would have let the
  mutation set claim coverage it did not have. Same family as the earlier ruling that a clause
  nothing can falsify sits beside clauses that can, which is how a mutation set comes to report a
  green it did not earn.

### #258

- **One identity, two spellings: the update bot is `dependabot[bot]` to the REST API and
  `app/dependabot` to `gh pr view --json author`.** Measured 2026-08-31 on `orchestrator#3`:
  `repos/{r}/pulls/{n}` answers `user.login = "dependabot[bot]"` with `user.type = "Bot"`, while
  `gh pr view --json author` answers `author.login = "app/dependabot"` with `is_bot = true` — same
  pull request, same instant. The cascade workflow keys on the first
  (`github.event.pull_request.user.login == 'dependabot[bot]'`), so anything reproducing or
  declaring that condition must use the REST spelling. **The direction of failure is the reason this
  is worth knowing:** an author condition sits on the PERMITTING side, so a wrong spelling
  under-permits — the lane refuses everything and goes quiet, which is the failure nobody notices,
  rather than admitting something it should not. Same family as the estate's other
  two-vocabularies-for-one-thing entries, and the same rule applies: grep both sides before keying
  anything on an identity string.

### #260

- **A CLEAN-TREE ASSERTION MUST USE `/usr/bin/git`. The command hook has twice been observed
  disagreeing with it, and it does NOT reproduce on demand — so treat the divergence as real and
  its cause as unestablished.** The two observations are independent: an earlier one recorded that
  `diff` and working-tree `git status`/`git diff` can report falsely through the hook, and on
  2026-08-31 the ADR-0038 Increment 2 session caught a stranded mutation on disk **only** after
  switching to the absolute path, hooked `git status --porcelain` having reported the tree clean.
  **Measured the same day, the divergence did not reproduce:** in a worktree, bare and
  `/usr/bin/git` agreed exactly for an untracked file and for a modified tracked file, in both
  `status --porcelain` and `diff --stat`. So *"the hook lies"* is the wrong lesson and would be the
  same attribution error as blaming pytest 9 for a cumulative `-q`; what is established is that the
  two CAN diverge, not that they always do.
  **Key the rule on the direction of failure rather than on a mechanism nobody has pinned:** a
  clean-tree claim is the one where a wrong answer is silent and success-shaped, so it is the one
  that must not be taken from the hooked form. The same session also reported a hooked `make check`
  running against a DIFFERENT worktree and reporting its `rootdir:` and file count — observed once,
  with the numbers recorded, and not reproducible afterwards because that worktree had been removed.
  Read `rootdir:` beside the collected count regardless, which catches it either way.

### #262

- **ADDING A TERM TO A DISJUNCTION SILENTLY DISARMS EVERY CONTROL THAT DISCRIMINATES ON IT, AND THE
  CONTROL STAYS GREEN.** A control proves arm X is load-bearing by satisfying every OTHER arm, so
  that deleting X changes the answer. Add an arm the control's fixture does not satisfy and the
  disjunction is forced regardless: deleting X stops changing anything, the mutant survives, and the
  test still passes — it has simply stopped asking its question. Found 2026-08-31 building ADR-0038
  Increment 3, in `landing_ledger/github.py::read_landing`, whose head-fetch is
  `if claim is None or policy is None or update is None`. Its control
  `test_the_update_arm_reaches_for_the_head_even_when_the_other_two_are_answered` discriminates only
  because the fixture answers the other two on the landing commit; a fourth arm left unanswered
  disarms it. **The rule: when you add a term to a disjunction, extend the fixture of every control
  that discriminates on that disjunction to satisfy the new term, and add the mirror control for the
  new arm.** The same reasoning applies to a conjunction with the polarity flipped.
  **This is the estate's "a control written before a fix can survive the fix and stop
  discriminating" rule reached from the OTHER direction, and the difference is what makes it worth
  its own entry:** there the fix outran a control that had already been written, so re-running the
  set after the last behavioural change catches it. Here the fix DISARMS the control, so re-running
  catches it only if the mutant for the OLD arm is still in the set — a mutation run scoped to "the
  code I changed" will not contain it. Keep the whole set, and treat a mutant that newly survives a
  change as a disarmed control until proven an equivalent mutant.

### #263

- **A SURVIVOR HAS A FOURTH EXPLANATION: the mutant applied and states nothing.** The estate records
  three ways a mutation run reports a pass it did not earn; this is the mirror — a **false
  survivor**, which costs a hunt for a control that is not missing. Found 2026-08-31 (ADR-0038
  Increment 3, mutant M6): the replacement appended a duplicate projection branch BELOW an early
  return without removing the original, so the original still fired and the mutation changed
  nothing. **Its anchor matched once, so a harness's own "anchor matched 0 times" check cannot see
  it** — that guard catches a mutation that failed to apply, not one that applied and asserted
  nothing. The only thing that catches this is reading what the replacement DOES.
  **It interacts with the disjunction rule above and the interaction is the trap.** That rule says
  treat a newly-surviving mutant as a disarmed control until proven equivalent — sound, and under it
  a defective mutant reads as a disarmed control, which is a conclusion about the tests drawn from a
  defect in the harness. So the order is: first establish the mutant expresses its claim, then ask
  whether the tests fail to catch it. Rebuilt to actually MOVE the branch, M6 was killed.

### #265

- **"RE-READ `main` BEFORE YOU GATE" IS INERT WITHOUT `git fetch` FIRST — the check reads as done
  either way.** A session's `origin/main` is whatever it last fetched, so `git log`, `git diff` and
  `git merge-base` against it answer about a snapshot rather than about the remote, and the answer
  is confidently wrong in exactly the situation the check exists for: somebody else merged. Measured
  2026-08-31 on ADR-0038 Increment 2b, which did the check correctly and concluded from
  `git diff --stat <a> <b>` that Increment 3 "had not merged, only its documentation half" — because
  its range closed at 14:42 and that code merged at 18:18. Its supporting evidence was equally
  self-consistent: byte-identical `--collect-only` node-id lists at 4639 on both sides, which is
  what a range containing no executable file looks like. **Nothing in the output says the range is
  stale.**
  Two consequences worth stating separately, because the second is the expensive one. A **conflict**
  is not the hazard — different files merge cleanly. The hazard is a **green gate over a tree that
  is not the one being merged into**, plus a collected-count baseline taken from the wrong `main`,
  both of which look exactly like success. So: `git fetch` immediately before the re-read, and take
  the baseline from the fetched ref rather than from a number recorded earlier in the session. Same
  family as the estate's other stale-input errors — the instruction was followed and the input was
  old, which is why the discipline has to name the fetch and not just the read.

### #266

- **A BEFORE/AFTER DIFFERENTIAL MUST BRACKET ONLY THE VARIABLE UNDER TEST — a baseline taken hours
  earlier silently brackets the world as well.** Measured 2026-09-01 verifying the ADR-0038 deploy:
  the pre-swap production probe was taken at 19:11 and the post-swap one at 07:26, so the interval
  contained not just the image swap but an entire unattended landing window. Two of five subjects
  changed, and the diff **cannot say** whether the deploy or the landings caused it. It was rescued
  only by arithmetic after the fact — the three subjects whose world had not changed were
  byte-identical (that part IS a controlled comparison), and the two that had changed carried
  exactly the settled-state signature the OLD image already produced for two other landed subjects.
  **Take the baseline immediately before the mutation**, and where a subject's own state may move
  under you, prefer subjects that cannot. Same family as the estate's clean-clone rule — *control
  for the environment before blaming the change* — on the TIME axis rather than the tree axis, which
  is the one that is easy to miss because nothing about the output looks stale.

### #268

- **A REPOSITORY RULESET REFUSES A DIRECT PUSH AND IS INVISIBLE TO THE CLASSIC BRANCH-PROTECTION
  API — so `enforce_admins: false` does NOT mean a direct push will land.** Measured 2026-09-01
  removing the auto-merge cascade: `infraops-mcp-server` reports `enforce_admins: false` from
  `branches/main/protection/enforce_admins`, and refused a direct push with
  **`GH013: Repository rule violations found`** and *"Changes must be made through a pull request"* —
  from an active ruleset named `Protect main` (`GET /repos/{r}/rulesets`), a mechanism the classic
  protection endpoints do not report at all. The error code is the tell: **GH006 is classic branch
  protection, GH013 is a ruleset.** Censused the same day: of the eight ledger repositories, that is
  the ONLY active ruleset, and it makes that repository behave like the three carrying
  `enforce_admins: true` despite reporting the opposite. **Any question of the form "can I push
  directly to this repository's default branch" must read BOTH surfaces**, and this estate's recorded
  protection tables were all built from the classic one.

### #277

- **READING `.gitignore` TELLS YOU WHAT IS IGNORED, NEVER WHAT IS TRACKED — and `git ls-files` is
  the one-command check that discriminates.** 2026-09-03, nearly shipped as a prerequisite: seeing
  `outDir: "./dist"` in `infraops-mcp-server`'s `tsconfig.json` beside a `.gitignore` holding only
  `node_modules/`, the conclusion drawn was that an emitting build in an envelope would leave
  `dist/` untracked and finalize's `git add -A` would sweep it into the pull request. **`dist/` is
  TRACKED there — 376 files** — because that repository commits its build output deliberately
  (`start` is `node dist/index.js`). So `npm run build` is correct in that envelope, no
  `.gitignore` change is wanted, and the only real consequence is a large diff that brushes the
  package's own `stop_conditions` wording (*"a file outside the source tree, its manifest and its
  lockfile"*).
  **The general hazard behind the wrong conclusion is real and still open.** `finalize-run` runs
  `git add -A`, so anything a command leaves in the checkout is committed; factory-runner handles
  the class with `_AGENT_ARTIFACTS = ("output.txt", ".factory-runner/", ".sds-local-heavy/")`
  written into `.git/info/exclude`, whose comment states the principle — *"the fix belongs HERE
  and not in each target repository's .gitignore; that shape is a fix you have to remember N
  times, and it had already been forgotten twice"* (it once committed a live `lease_token`).
  **Build output is not in that list.** It does not bite where the output is tracked; it will bite
  the first target whose build emits to an untracked path.

### #279

- **A MUTATION HARNESS'S ANCHOR CAN MATCH A SIBLING FUNCTION, and the honest harness reports that
  rather than a pass.** 2026-09-03, adding a report beside an existing one in `dead_letter.py`:
  two of eight mutants anchored on lines (`WorkUnit.updated_at <= cutoff`,
  `requeue_eligible=_requeue_eligible(unit),`) that appear **identically** in the sibling report,
  so the harness reported *"ANCHOR MATCHED 2 TIMES — mutation not applied"* and refused. Reported
  6/8 until both were re-anchored on text unique to the new function, then 8/8. **A harness that
  applied the first match would have reported 8/8 while mutating the wrong function** — and the
  kill would have been real, just about something else. When adding code that mirrors existing
  code, expect every generic anchor to be ambiguous, and keep the match-count assertion.

### #281

- **The reusable workflow pinned NO Node version until 2026-09-04, so `npm test` ran on whatever the
  hosted image shipped — and a gate that passes in the target repository's own CI failed on the
  runner.** Unit `a78881e2` finished its migration, passed the coding classifier, and died at
  `finalize-run` with `SyntaxError: 'node:util' does not provide an export named 'styleText'` —
  `styleText` landed in Node 22. Pinned to 22 in factory-runner `18f6355c`, measured: five of six
  target repositories pin 22 in their own workflows, and `infraops-mcp-server` pins 22 for its
  **named check** and 24 in a separate job. **Match the named check, because the named check is what
  AC-001 is decided by** — that is the tie-break wherever a repository has two.
  The general form, and it outlives Node: **the runner's toolchain is part of what an envelope
  attests.** A verifier command is honest only if the runner can execute it the way the gate does,
  and nothing about the envelope, the brief or the contract fixtures records a toolchain version —
  so a mismatch is invisible until finalize, i.e. after the coding is done and the attempt is spent.

### #283

- **`npm install` and `npm ci` resolve differently, so verifying in the wrong ORDER locally
  reproduces the exact failure the envelope's `npm ci` placement exists to prevent.** Hit by hand
  on `infraops-mcp-server#93`: eslint, prettier and the tests were run BEFORE `npm ci`, and only
  `tsc` and the tests re-run after — prettier then failed in CI having passed locally, minutes
  earlier, in the same checkout. The estate already records that the two commands disagree; this is
  the operator-side twin, and it is easier to walk into than the envelope one because nothing
  refuses you. **Run `npm ci` first, then every check, in that order** — the same rule the ordered
  `allowed_commands` list encodes, applied to a person.

### #284

- **A NARROWING THAT SEPARATES ONE VALUE'S CAUSES LEAVES EVERY OTHER VALUE COLLAPSED, AND A TEST
  CAN PIN THAT RESIDUAL AS THOUGH IT WERE THE DESIGN.** `checks_term`
  (`services/landing/terms.py`) took `mergeable_state: blocked` apart into a failing
  check, an abandoned one and one still running — the module's own comment records that it "used to
  be raised for every `mergeable_state` that was not `clean`, which collapsed 'a check said no' into
  'a check said nothing yet'". **The fix stopped there**: every OTHER unpermitted value — `dirty`,
  `draft`, `behind`, `has_hooks`, and anything GitHub invents — still fell through to the
  failing-check refusal. Measured live 2026-09-05: `alobarquest/factory-runner#71` carried **two
  `Quality` runs at `success`** while diverged two ahead and three behind, and the lane reported
  `landing_checks_not_clean` about it. A reader following that report goes and stares at green CI.
  **The residual was PINNED**: `test_only_a_blocked_pull_request_is_inquired_into` parametrized over
  `["dirty", "draft", "unstable", "has_hooks", "behind"]` and asserted the failing-check refusal for
  every one — so the wrong behaviour had a passing test and read as deliberate. That is why it
  survived the narrowing that was written to remove exactly this shape.
  Closed by naming the live cause (`landing_pull_request_conflicted`) AND the residual
  (`landing_mergeability_unrecognised`) — the second is the general half, because the defect was
  not that `dirty` lacked a name but that an unrecognised word was given somebody else's. `unstable`
  now gets the SAME second read as `blocked` rather than a cruder answer of its own, since both mean
  a run has not said yes and the runs are what tell the causes apart.
  **Behaviour did not move and could not**: `qualifies_for_branch_update` subtracts a named few and
  disqualifies everything else, so a renamed refusal cannot become permission — which is also why
  this was safe to change without re-deciding anything.
  **Generalise: when you narrow one branch of a fallback, ask what still reaches the fallback**, and
  read the tests over it — a parametrized list of the states you did NOT handle is a pin on the
  defect, not coverage of it.

### #288

- **A CONFLICTED PULL REQUEST RUNS NO `pull_request` WORKFLOW, so a required check on it can never
  REPORT — the pull request is permanently pending, and the branch protection that was supposed to
  make a landing safe is what makes it impossible.** Measured 2026-09-06 on `claude-octopus#8` with
  a positive control, because a zero is not evidence of absence on its own. Subject: head reset at
  10:05Z that morning (the sync branch is rebuilt daily), well after `ci.yml` landed on 2026-09-05,
  and **zero** workflow runs at that head. Control: `#10`, non-conflicted, same repository, same
  workflows — **three** `pull_request` runs on its head. And the branch's own history is the third
  reading: `pull_request` runs on `upstream-sync` up to **2026-07-18** and none since, through
  seven weeks of daily resets. Three independent `on: pull_request` workflows silent on a fresh
  head is the event not firing, not one workflow misconfigured; GitHub has no merge ref to build.
  **`ci.yml`'s own header warns about this blockage twice and names neither route to it.** It
  rejects upstream's suite (a check that FAILS for reasons nobody here owns) and
  `harden-selftest.yml` (PATH-FILTERED, so it never reports outside its paths) — then adopts a gate
  that is `on: pull_request` and `push: [main]`, which never reports on a conflicted pull request
  either. Same permanent pending, third route.
  **The consequence for the upstream-sync lane: resolving the conflict is a strict prerequisite to
  landing anything on that fork, and not for the reason it looks like.** The landing lane's
  `landing_pull_request_conflicted` refusal is the visible one; underneath it, the gate that would
  authorize the landing physically cannot run, so clearing the refusal and clearing the gate are
  the same act. Generalise: **before requiring a status check, ask which events can produce it on
  the pull requests it will govern** — a required context is only as available as its trigger.

### #289

- **ASK THE TARGET REPOSITORY WHETHER THE WORK IS ALREADY DONE BEFORE RE-RUNNING A SUBJECT.**
  2026-09-06: a session was one step from authoring revision 6 of `infraops-mcp-server-npm-zod`,
  with a spec, a handoff and four retired attempts behind it — and the bump had landed. **An
  interactive session, working under Devon's identity, did the migration by hand in `#93`
  (`aaee61e`, 2026-09-04, +79/−95), the day after the fourth factory attempt failed**, and
  Dependabot's own `#71` for the same bump is closed. Read the attribution carefully, because it
  is the sharper half: this was not a human reclaiming a task from the machine — it was **an agent
  bypassing the factory for the one subject the factory existed to prove it could do**. The
  commit's own message is a session's (*"Verified: tsc clean, eslint clean, prettier clean, 524
  tests in 59 files passing, and all four re-run after `npm ci`"*), and this file already records
  `#93` as hit by hand. The package declares
  `from_version: 3.25.76 → to_version: 4.4.3`; `main` is at **4.5.4**, so revision 6 would have
  been a DOWNGRADE whose `from_version` matched nothing in the tree. One `git show
  origin/main:package.json` settled it, and nothing in the spec, the handoff, the change records or
  the carrier's own `[WAITING]` lines pointed at it. This is the estate's *ask production what it
  is running* rule with the noun changed: for a landing question ask production, for a **subject**
  question ask the target repository's `main`. A change record cannot know its work was done by
  another route.
  **The four records stay `[WAITING]` forever regardless** — retirement keys on unit COMPLETION,
  every zod unit is terminal-not-completed, and a `work`-source record's status is human-only under
  ADR-0028. So a subject satisfied elsewhere leaves permanent residue in the carrier's report with
  no machine path to clear it.
  **What the four attempts cost is worth keeping, because each found a real defect and every one
  was fixed:** rev 2 died because its verifier was a `grep` for a version string (closed — the npm
  profile now generates `npm ci` / `npm run build`); rev 3 of `budget_exceeded` at 120 (closed —
  240, then 360); rev 4 because the verifier was forbidden `npm test` (closed — off the deny-list);
  rev 5 at `finalize-run` on `styleText`, a Node-22 symbol (closed — factory-runner pinned Node 22
  in `18f6355c`, the day after). **The lane was one fix from its first real migration when the
  subject was taken off the table by hand.** Read that as unproven, not disproven: nothing here
  shows the factory could not have done it, and nothing shows it could.

### #292

- **A BLANKET "PRESERVE OUR OWN FILES" RULE FREEZES THE OTHER SIDE'S FILES TOO, and the freeze is
  invisible until their tests start asserting things about them.** `claude-octopus`'
  `upstream-sync.yml` did `git checkout origin/main -- .github/workflows/ .github/scripts/` to
  protect the fork's own CI. It also froze three files the fork has **never edited** — `test.yml`,
  `claude-octopus.yml`, `post_reddit_update.py` — at their June versions. Upstream then added a
  `package-integrity:` job to its `test.yml` **and a test asserting that job exists**, so upstream's
  NEW test ran against the fork's OLD workflow and failed on a tree where **each parent passes
  alone**: fork main 5/5, pure upstream 9/9, the merge 1 failed.
  **The fix is to DERIVE the rule, never to list it**: preserve a `.github/` file when upstream has
  none at that path. Self-maintaining both ways — a new fork-owned file is protected without anyone
  remembering, and a file upstream later adopts stops being frozen, which is the same answer the
  fork's `flock` hardening got when upstream wrote `_publish_session_state`. Verified to select
  exactly the six fork-owned files and track upstream for the other twenty.
  Taking upstream's `test.yml` also switched on five jobs the fork had never run — Portability Lint,
  Package Artifact, Classify Changes, Symlinked Path and a two-platform unit matrix. **A frozen CI
  file is not a frozen CI: it is a CI missing everything added since.**

### #293

- **"UPSTREAM SUPERSEDED IT" IS A PER-HUNK FINDING, NOT A PATTERN YOU MAY EXTEND TO THE SET.**
  Four of `claude-octopus#8`'s five conflicts were upstream having adopted the fork's own concern —
  three `flock` patches against upstream's `_publish_session_state` / `_update_session_state`
  (`mktemp` + `mv`, an atomic rename, strictly stronger than `flock`), and one schema change. HQ
  resolved all five that way and stated the generalisation as fact. **The fifth was fork-owned and
  still needed**: `tests/unit/test-github-work-queue-hook.sh`'s `4ddcc276` builds a fixture repo
  with an `nyldn` remote, because `github-work-queue-watch.sh:66` refuses to act unless the
  checkout's remote is upstream's — so on a fork it bails and both assertions fail. That commit's
  own message said so in June: *"a permanent red Unit Tests job in the fork."* Upstream's fix
  (`cd "$PROJECT_ROOT"`) is correct FOR UPSTREAM and wrong for a fork — **upstream-shaped rather
  than wrong**, which is the category that survives a supersession sweep.
  **AND THE LOCAL RUN COULD NOT TELL THEM APART.** Upstream's version and the restored graft BOTH
  pass 5/5 in a fork checkout on this machine. CI is the only environment that has ever shown the
  failure, so CI was the control: red on that file, green after a push changing that file and
  nothing else. **When you cannot reproduce locally, stop trying and say so** — a local green that
  does not discriminate is not weak evidence, it is none.

### #294

- **A TOOL POLICY THAT GATES WRITES AND NOT READS IS NOT A CONFINEMENT — and the push credential
  was sitting in the tree the coding agent was pointed at.** Found 2026-09-10, fixed the same day,
  closed 2026-09-11.
  `factory-runner.yml` set **`persist-credentials: true` explicitly** — a choice, not a default —
  passing `secrets.FACTORY_PR_TOKEN` as `actions/checkout`'s `token`. At the pinned action revision
  that writes the credential to `$RUNNER_TEMP/git-credentials-<uuid>.config`, included from the
  checkout's `.git/config`: same uid as the agent, present for the whole coding phase. The token is
  a fine-grained PAT with write access to eight repositories.
  **The policy could not stop it, and had already half-recognised why.** `command_policy.py`
  registers hooks for **`Bash` and `Edit` only**, and its own refusal text points the agent at the
  ungated `Read`/`Grep`/`Glob`. The Edit gate refuses paths inside `.git/` — *"Edit path is inside
  checkout git metadata"* — so `.git/` was identified as sensitive and protected against WRITING
  while left open to READING. **Whenever a policy names a path as sensitive, ask which verbs it
  names it for.**
  **THE FIX IS TWO HALVES AND THEY MUST SHIP TOGETHER.** `persist-credentials: false` ALONE BREAKS
  THE PUSH: `cli.py`'s finalize was a bare `_run_command(["git", "push", …])` with no token and no
  helper, depending entirely on the persisted config. Finalize now authenticates from the
  environment it already had, via `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` on
  that one subprocess — **never argv**, which is visible in `ps` to anything on the runner, and
  never a file. `gh` was unaffected throughout: it reads `GITHUB_TOKEN` from the environment
  natively and never used the persisted config.
  **A SECURITY FIX IN THE REUSABLE WORKFLOW IS NOT CLOSED WHEN IT MERGES.** Callers pin the
  workflow by SHA and the CLI installs at that same SHA, so a caller on the old pin keeps the old
  behaviour — correct, and it means `factory-runner#75` (`932571f`) was merged and INERT for a day.
  It closed only when `RECOMMENDED_CALLER_PIN` and all six callers advanced. Budget the pin advance
  as part of the fix, not as follow-up.
  **`runner.caller` requires EXACT equality** (`used != pin`) and sits in `ADMISSION_CHECKS`, so
  there is **no ordering of that advance that avoids a violation window** — only a choice of how
  long it lasts and which side is red. Measured 2026-09-11: six callers then the pin, seven squash
  merges, **33 seconds**.
  **AND THE ERROR WORTH CARRYING IS HQ'S.** An assessment put the credential in `$RUNNER_TEMP`. HQ
  read `git-auth-helper.ts` at the pinned SHA, saw the `http.<origin>/.extraheader` mechanism, and
  "corrected" it to `.git/config` — confidently, in a report, calling the correction *"materially
  worse"*. The assessment was right. **Reading how a mechanism works in source is not reading what
  a version does with it**; v7.0.1 writes a separate config file and includes it. The exposure was
  identical either way, which is exactly why the wrong correction survived being made.

### #295

- **A FAILING JOB MAY SAY IN ITS OWN LOG THAT IT IS NOT A FAILURE — read it before treating a red
  as a finding.** `claude-octopus`' `Integration Tests` is a GATE job, not a test run:
  `Integration Tests did not run: blocked by Unit Tests (failure), not an integration failure.` The
  whole chain came from one macOS shard hitting its 45-minute timeout → `cancelled` → the `Unit
  Tests` matrix aggregate reporting `failure` → the gate exiting 1. **No assertion failed
  anywhere**, and three reds on the checks list had exactly one cause. Same family as
  `mergeable_state: blocked` covering four causes: a status is a summary, and a summary is not a
  diagnosis.

### #296

- **SQUASH-MERGING A FORK'S UPSTREAM-SYNC PULL REQUEST FREEZES THE MERGE BASE, AND EVERY LATER SYNC
  RE-CONFLICTS WITH THE FLATTENED COPY.** Measured 2026-09-13 landing `claude-octopus#14`. `#11`
  landed as a MERGE COMMIT and left the base current. **`#12` landed as a SQUASH**, which rewrote
  v11.2.0 onto `main` as a commit `upstream/main` does not contain, so the merge base stayed at
  `3267de84` (v11.0.1) and everything upstream touched since re-conflicted against the squashed
  copy. Proof rather than inference: all 17 conflicted files' fork blobs are byte-identical to some
  upstream commit's blob — the fork had never edited any of them — so every conflict was an
  artifact of the base, not a disagreement. **Say both halves**: the squash is why conflicts existed
  at all; upstream drift is only why the count grew 3 → 18 over one day. The acceptance test for
  the claim is cheap and has not yet been run — **the first sync pull request opened after an
  upstream push should show zero conflicts.**
  **The obvious reading of the numbers is the wrong one.** A session measuring 3 conflicts on one
  day and 18 the next concludes the branch is drifting fast and reaches for urgency; the brief
  written from that reading told a build session to land the same day. The real remedy is a merge
  strategy, and it is invisible from the conflict count.
  **Generalise past this fork: wherever a branch is repeatedly merged FROM a source you do not
  control, the merge base is the asset.** Squash-merging is correct for a topic branch precisely
  because it discards the branch's history — which is what makes it wrong here.

### #297

- **A THREE-WAY MERGE SILENTLY DUPLICATES A SECTION WHEN BOTH SIDES ADD BYTE-IDENTICAL CONTENT AT
  DIFFERENT OFFSETS — no conflict marker, no warning.** Same landing: the base had neither
  `[11.2.0]` nor `[11.1.0]` in `CHANGELOG.md`, upstream had both, and the squashed copy had them
  byte-identically at a different offset, so git took both sides and produced two copies of each.
  Nothing about the resolution looked wrong, because nothing had been left to resolve.
  **`git diff <the branch you merged>` AFTER resolving is what catches it** — a conflict-marker
  sweep and a clean `git status` both report success. Expect it wherever an append-only document
  (a changelog, a decisions log, a release-notes file) is merged from two lineages.

### #298

- **LANDING A BOT PULL REQUEST'S CONTENT VIA A SIDE BRANCH MERGES THE BOT PULL REQUEST TOO, and
  GitHub does it seconds later without being asked.** `#13`'s head was a parent of `#14`'s merge
  commit, so GitHub marked `#13` merged two seconds after `#14` landed and `delete_branch_on_merge`
  removed `upstream-sync`. A brief that says "leave `#13` alone" is honoured and the pull request is
  still gone — **so an instruction not to touch a pull request is not a prediction that it survives.**
  Benign here: `upstream-sync.yml` recreates the branch with `checkout -B` + `push --force`, and the
  06:00Z gate then reads zero new upstream commits and prints *"No new upstream commits — nothing to
  do"*. Note what it means for reporting, which is the part that misleads: two merged pull requests
  now point at one landing.

### #306

- **`/code-review`'s FORKED AGENT INHERITS THE SESSION'S CWD, AND WILL CONFIDENTLY REVIEW A
  DIFFERENT TREE.** Observed 2026-09-19 by the G1+G2 increment-4 build session: it invoked the
  review from its worktree, the forked agent resolved to `~/Projects/orchestrator` — HQ's main tree,
  sitting on `origin/main` — and returned a **detailed, specific, entirely irrelevant** review of
  already-merged PR #278 instead of the branch under test. Re-run with an explicit pull-request
  target and a stated checkout path, it reviewed the right tree and found a real medium defect.
  This repository already records the drifted-cwd hazard for a backgrounded `make check`, where the
  tell is pytest's `rootdir:` line. **A review agent has no such line**, and its failure is worse in
  the direction that matters: a gate reading the wrong tree returns a suspicious green, where a
  reviewer reading the wrong tree returns plausible prose about real code that is not yours — which
  a reader acts on. **Name the pull request and the checkout path when invoking it**, and check that
  what it reviewed is what you changed before believing any finding, including a clean one.
