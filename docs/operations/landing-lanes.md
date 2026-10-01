# Landing lanes

How the estate and inert landing lanes, the landing ledger, change records and the deploy policy behave, and how to read what they report.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

### #100

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

### #106

- **App Brain's landing route answers with TWO fields and never 404s — `reason` is load-bearing, not
  decoration.** `GET /api/apps/default-branch-landing?github_repo=Owner/Repo` returns a value plus a
  reason, and an unregistered repository is a normal `unknown` + `no_app_record` response rather than
  a missing resource. So `no_app_record` (nobody registered it) and `not_assessed` (registered,
  nobody determined it) are **different states that a consumer must be able to tell apart** — a check
  keyed on the value alone cannot distinguish "not an app we know" from "an app we never looked at".
  The route also **folds multiple app records**: `AlobarQuest/brain` is one repository serving four
  applications and resolves as one answer. HQ's WS-P2.28 handoff described three landing values and
  that read as the whole contract; it is not.

### #122

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
  **CORRECTED 2026-09-25 (ADR-0045): "the lane brings up to date the branches it stales" is no
  longer unconditional.** For Dependabot pull requests the lane now edits at most one branch per
  repository at a time, and a sibling it leaves alone reads **`waiting`**, a separate non-finding
  status. That does not reopen this ruling: `waiting` is keyed on an OBSERVED holding sibling
  (the served `branch_update_withheld_for_sibling`), never on the lane declining, so the
  durability discriminator above stands unchanged. And the family's pattern held once more — the
  status was first spelled `withheld`, which contains `held`, and had to be renamed.

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

### #139

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

### #142

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

### #165

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

### #182

- **A landing stales every sibling pull request, and `update-branch` clears it synchronously where
  `@dependabot rebase` takes ~14 hours.** `freshness_term`
  (`services/landing/terms.py`) calls `commits_behind_base` and refuses on `behind > 0`
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
  **AMENDED 2026-09-25 (ADR-0045): the lane now edits at most ONE edited, landable Dependabot
  branch per repository.** Update-branch merges base into head under the App's identity, and
  Dependabot then refuses to rebase the branch ("edited by someone other than Dependabot"), and the
  App cannot ask it to recreate one. Two such branches in one repository plus a landing is the
  whole precondition of the deadlock, measured in all four stuck cases (`brain#70`,
  `change-manager#87`, `#93`, `factory-runner#71`); in every one the lane freshened BOTH members of
  the pair seconds apart in one pass, and in two of them both were first edits — which is why
  ownership reads the event log (an update under ten minutes old at the current head) as well as
  GitHub's commits. So both acts withhold a FIRST edit while another edited Dependabot pull request
  in the repository still holds (`…_branch_update_sibling_holding`, served as
  `branch_update_withheld_for_sibling`, reported by both landers as **`waiting`**, not a finding),
  and refuse as a FINDING when they cannot establish that (`…_branch_update_siblings_unreadable`).
  An already-edited branch may always be freshened again.
  **Freshening cannot be handed back to Dependabot** — that was the 2026-09-20 decision, reversed
  by measurement: across 51 weekly runs Dependabot rebased 0 merely-behind branches, and stretches
  behind reached about 20 days. The "sole remaining obstacle" rule above still decides WHETHER a
  branch may be freshened; the sibling rule decides WHICH ONE, and lives at the acts, never in the
  shared predicate.

### #186

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

### #196

- **ADR-0022's unit-scoped observation needs the change record to EXIST, not to carry a unit id —
  and HQ's handoff asserted the opposite.** Corrected 2026-08-16 by the build session that
  implemented it. `deploy_watcher/cli.py::_observe_unit` derives the unit from the merge commit's
  **`SDS-Unit:` trailer** (`deploy_watcher/units.py::claimed_unit`) and confirms it against the
  orchestrator's own `pr_merge` history; the change record contributes only
  `(target_repository, pull_request_number)`, i.e. **which pull request to watch at all**. Verified
  independently: factory-runner emits `SDS-Unit: {id}` in the commit message
  (`src/factory_runner/cli.py:441`, `:454`), and the eight ledger repositories are
  `squash_merge_commit_message = COMMIT_MESSAGES`, so a landing commit carries it.
  **And the watcher reads every deploy record whatever its status** —
  `deploy_watcher/change_manager.py::deploy_changes` applies no status filter — so a **pending**
  factory record is sufficient. Phase-3 criterion 1's second half therefore does **not** wait on
  the approval decision below. Storing the work-unit id on the record is still right, because
  parsing the title is how the pull request is recognised at all; it is simply not what unblocks
  the criterion.

### #204

- **A `work`-source change record's status is human-only, and no producer credential can move it.**
  `src/bump_proposer/change_manager.py` is propose-and-read by construction — `/api/work-changes`
  and `/api/items`, anchored so no `/api/items/{id}/…` verb matches — and change-manager's
  `propose` scope refuses every status-moving route server-side whatever the client sends. That is
  ADR-0028's human decision, not an oversight. The `deploy` source is different: it has a
  `deploy-retirement` route whose closed vocabulary has exactly one member
  (`pull_request_closed_unmerged`), because its outcome cannot be chosen — it takes a fact and lets
  the server decide. **So retiring a `work` record whose bump turned out to be unadoptable is a
  human click, and there is no machine path to it.** Verified 2026-08-19 on records 59/60/61.
  Note also the credential lives in a BWS project the narrow `sds-operator` identity cannot read —
  `bws` answers `404 Resource not found`, which reads like a missing secret rather than a denied
  one. Use the broad identity for it; this is the two-identity rule again, in a third place.

### #210

- **`mergeable_state` is ONE WORD covering FOUR causes, and only one of them means the change is
  bad.** A genuinely failing check, a run abandoned mid-flight, a run still going, and a required
  context that never reported **all answer `blocked`**; only a green head answers `clean`. Measured
  2026-08-22 on a disposable repository, one variable at a time. So any consumer that needs the
  *cause* must read the workflow runs at the head — and specifically the **workflow-run listing**,
  not the check-runs API, which answers **403** because the `Alobar SDS Dispatch` App holds no
  `checks` permission. The landing lane collapsed all four into `landing_checks_not_clean` and
  therefore held three clean Dependabot bumps for four days on the strength of runs GitHub had
  cancelled when the estate's Actions quota ran out. It now separates them:
  `landing_checks_not_clean` (a real failure), `landing_checks_awaiting_verdict` (abandoned — does
  NOT disqualify the branch update, because updating is what produces the verdict), and
  `landing_checks_in_flight` (still running — DOES disqualify, because freshening would abandon the
  run being waited on).

### #212

- **A refusal excused for ACTING is not automatically excused for REPORTING, and the two consumers
  read different criteria.** In the landing lane, `qualifies_for_branch_update` excuses
  `landing_checks_awaiting_verdict` — the update is exactly the remedy. `freshness_derived_refusals`
  deliberately does NOT, because it asks whether the head's *position* caused the refusal, and an
  abandoned run is not a position. **Folding a code into a shared criterion to excuse it for one
  consumer silently excuses it for the other**, which is how a suppression written for a report
  becomes a permission to act. Related and already recorded: the deliberate-refusal categories are
  tested with SUBSET semantics for the same reason a shared set would be wrong.

### #248

- **FIXING A READER RE-DERIVES FACTS THAT ARE ALREADY FROZEN, so a reader fix has a second
  consumer: the writer.** `landing_ledger` observations are content-addressed over `facts` at an
  immutable `source_reference`, so improving what the reader *derives* changes the facts for rows
  already stored. The write then hits the orchestrator's same-source/different-facts branch —
  `observation_conflict` → `LedgerWriteError` → counted `skipped` → `incomplete` → **exit 3 every
  night** for as long as those commits sit inside the pass's `--days` lookback.
  Measured 2026-08-29: the trailer-fallback fix (`#204`) would have done exactly that to six stored
  landings and kept `sds-landing-ledger` red rather than returning it to up — the opposite of the
  change's own purpose. HQ's spec exempted the six in the AUDIT and missed the recorder entirely,
  having written "observations are immutable and content-addressed" four times the same day.
  **Before shipping a change to what a producer derives for a content-addressed, immutable record
  type, enumerate every stored row whose derivation moves.** The reader fix and the old population's
  handling are one change, not two.

### #251

- **A REPORTED FINDING KIND THAT IS A SUPERSTRING OF ANOTHER BREAKS EVERY SUBSTRING READER,
  including the estate's own discriminating tests.** `test_audit_pass.py` proves a suppression is
  real with a pair of assertions over one `json.dumps` report — a kind ABSENT on one pass and
  PRESENT on the next — and an operator greps the nightly report the same way. A kind containing
  another satisfies the "present" half on its own name while breaking the "absent" half for an
  unrelated reason. Found 2026-08-29 when a new kind was drafted as
  `update_metadata_unreadable_at_recording`, which contains `update_metadata_unreadable` verbatim;
  renamed to `..._absent_at_recording`. `test_no_reported_kind_is_a_substring_of_another` now guards
  the whole vocabulary and found a **pre-existing** collision on its first run:
  `rule_revision_unknown` ⊂ `current_rule_revision_unknown`, exempted as named debt.

### #253

- **GITHUB ATTRIBUTES AN AUTO-MERGE TO THE ARMING IDENTITY, SO THE ARMING CREDENTIAL IS ALSO THE
  LANDING LEDGER'S ATTRIBUTION — MOVING ONE SILENTLY MOVES THE OTHER.** `basis_of`
  (`landing_ledger/record.py:207`) requires the gate to have succeeded **and**
  `is_machine(landed_by)`, where `is_machine` is `login.endswith("[bot]")` (`:161`) fed from
  `merged_by.login`. Measured 2026-08-30, same repository, one variable: `#174` armed with
  `secrets.GITHUB_TOKEN` → `merged_by = github-actions[bot]` (Bot) → basis `auto_merge_rule`;
  `#167` armed with a user PAT → `merged_by = AlobarQuest` (**User**) → **basis `human`**.
  Consequence: re-arming the cascade with a non-bot identity makes every future cascade landing
  record as landed by a person, after which `audit_landing` returns `(), (), ()` for any basis that
  is not the rule (`audit.py:355`) and **Detector A stops auditing the native lane silently**, its
  `permitted` denominator at zero. **And it cannot be corrected afterwards** — `permitted_by` lands
  in an immutable observation with a non-content-addressed `source_reference` and no delete route,
  so a fix is an `observation_conflict`.
  **This was known three times over and specced into an ADR anyway** (ADR-0035, blocked before it
  shipped): the `[bot]`-suffix hazard is a backlog item from 2026-08-10, `basis_of`'s own docstring
  names it, and HQ quoted that backlog item to Devon the day before writing the ADR. **Before
  changing any credential that performs an act this estate records, grep for what READS the identity
  of that act** — the arming credential, the merging identity and the recorded basis are one thing
  wearing three names.

### #302

- **A ROLLOUT WORKFLOW HAS TWO RATIFIED COPIES IN TWO REPOSITORIES, AND THE ONE THIS REPOSITORY
  HOLDS IS NOT THE ONE THAT APPROVES A RECORD. A transcription that is current says nothing about
  whether the policy ratified it.** Measured 2026-09-16, after brain's Dependabot queue sat for six
  days with nothing reporting a finding.
  `brain#62` (2026-09-07) moved `.github/workflows/ci.yml` from blob `c5c08871` to `7cf6ca2d`.
  `src/deploy_watcher/workflows.py` gained the new revision in the same change, so
  `change-proposer` printed `:: transcribed` and `0 findings` on every pass — **and that is the
  half that misleads**, because what approves a record is change-manager's
  `app/deploy_policy.py`, which still ratified version 3's criteria text and still pinned the old
  blob. `objections()` compares the record's derived `acceptance_criteria` **byte-for-byte**
  against the ratified copy, so every brain record drew `acceptance_criteria_not_ratified` and
  `_apply_policy` left it `pending`.
  **THE SYMPTOM IS SILENCE, NOT A FINDING.** The estate lander's `_ASK_ABOUT` is `{"approved"}`,
  so a pending record is never considered and never appears in its report. Three green pull
  requests from 2026-09-10 were invisible to every lane: the proposer reported them `replayed
  … status=pending` and counted zero findings, the lander did not mention them at all, and the
  ledger audited 875 landings with nothing to say. **Read `status=pending` in the proposer's
  replay lines as the stall**; nothing else in the estate says it out loud.
  **BOTH HALVES OF A VERSION MOVE TOGETHER.** The ratified criteria and
  `landing.rollout_workflows[repo].blob_sha` are separate terms with separate consumers — the
  criteria decide APPROVAL here, the pin is refused at the ACT as `landing_rollout_moved` — so
  bumping one leaves the queue stalled one step further along.
  **NOTHING NEEDS RE-APPROVING BY HAND AFTER A BUMP, and that is worth knowing before anyone plans
  a migration around it.** `_apply_policy` runs on every repeat proposal, and it carries a branch
  for a record that still conforms under a newer version. Measured across the v8 bump: brain's
  three records went `pending → approved` under 8, change-manager's two were re-approved from 7 to
  8 with an event recording the fresh grant, and the two records belonging to already-merged pull
  requests were left on versions 6 and 7 untouched, because the proposer skips a merged pull
  request and `_apply_policy` never runs for it.
  **Two probe traps met the same day.** `GET /api/deploy-policy` serves `repositories`,
  `change_classes`, `risks`, `landing`, `inert_landing`, `version`, `decided` and `rationale` —
  **not `acceptance_criteria`**, so a probe reading that key finds nothing and proves nothing about
  what was ratified. And **a bare invocation of `run-change-proposer.sh` or `run-estate-landing.sh`
  is a DRY RUN**: each plist passes `--submit` explicitly, so a hand-run pass that prints
  `would-propose` has written nothing and left the stall in place.
  Shipped as deploy policy **v8** (change-manager `#88`, merge `4f7298f9`, 2026-09-16). The
  ratified text must equal what `change_proposer.criteria.acceptance_criteria` derives at the
  pinned revision, and the cheap way to know is to import both sides and compare rather than to
  read the two documents side by side.

### #303

- **EXACTLY TWO ESTATE HOSTS SIT BEHIND CLOUDFLARE, AND `change-mgr.alobar.net` IS ONE OF THEM —
  so a 5xx from change-manager may never have reached the origin, and a client with a default
  Python User-Agent is refused before the app sees it.** Measured 2026-09-16/17 by reading the
  `server` header of every estate host, because this file already records that `sds.alobar.net` is
  NOT proxied and a reader generalises that to the estate:

  | host | address | `server` |
  |---|---|---|
  | `change-mgr.alobar.net` | 104.21.75.189 | `cloudflare` (+ `cf-ray`) |
  | `id.alobar.net` | 172.67.180.223 | `cloudflare` (+ `cf-ray`) |
  | `sds.alobar.net` | 178.156.247.239 | `uvicorn` |
  | `app-brain` / `code-brain` / `infra-brain` / `open-brain`.devonwatkins.com | 178.156.247.239 | `uvicorn` |

  **THE PORTFOLIO-WIDE 1010 BULLET IS RIGHT ABOUT THE MECHANISM AND WRONG ABOUT THE POPULATION.**
  It says Cloudflare 403s default Python User-Agents "on proxied `alobar.net`/`devonwatkins.com`
  endpoints". Measured today in both directions: `change-mgr.alobar.net/api/health` answers **403
  `error code: 1010`** to `urllib`'s default agent and **200** to a named one, while
  `app-brain.devonwatkins.com/api/health` answers **200** to the default agent — because no
  `devonwatkins.com` brain is proxied at all. Read the `server` header, never the domain.

  **CONSEQUENCE 1, and it is the one that costs a diagnosis: a 5xx from change-manager is not
  evidence the origin failed.** On 2026-09-16 the deploy watcher recorded
  `change-manager rejected POST /api/items/52/deploy-observation: 502: <non-json>` and exited 3,
  which pinged `/fail` and took `sds-deploy-watcher` down. The origin container had **zero
  restarts**, logged only 200s, and the VPS proxy logged **zero** 5xx in that window. **`<non-json>`
  is itself the tell**: change-manager answers JSON on every path, so a non-JSON body at the client
  is an error page from in front of it. Check the origin before reading a 5xx as an application
  fault — `docker logs` on the container and the proxy, which is one `vps_exec` call.

  **CONSEQUENCE 2: a new client that speaks to change-manager is one line away from a 403 that
  reads like an auth failure.** Every module that reaches it today sets a User-Agent —
  `services/landing/change_record.py`, `services/landing/inert_landing_policy.py`, and `change_manager.py` in
  `deploy_watcher`, `work_carrier` and `bump_proposer`; the two landers reach it only through
  clients that set one, and neither lander package imports an HTTP client of its own. So the hazard
  is covered and stays covered only while that is true. A 403 carrying `error code: 1010` and no
  `WWW-Authenticate` header never reached the application.

  The one-command check is the header, not the address: `curl -sI https://<host>/ | grep -i '^server:'`.

### #304

- **IN change-manager, A FIELD-LIST TUPLE IS ALSO THE CONSTRUCTION PAYLOAD, so excluding a field
  from it silently stops the field being STORED — and the two decisions that tuple makes are
  different questions.** Measured 2026-09-18 building the signal→work contract's increment 1.
  `propose_work_change` (`app/work_changes.py`) builds `proposed = {f: getattr(body, f) for f in
  _ASSERTED_FIELDS}`, uses it for the `WorkChangeConflict` comparison in `_existing`, **and then
  splats the same dict as `**proposed` into `ChangeItem(...)`**. The deploy sibling is the same
  shape one tuple over: `_PROPOSED_FIELDS = _ASSERTED_FIELDS + _DERIVED_FIELDS`, and `_proposed`
  selects out of the body. So *"should this field participate in conflict detection?"* and *"should
  this field be stored?"* are answered by ONE list, and excluding for the first reason silently
  answers the second. A field deliberately kept out of the comparison must be passed as an
  **explicit constructor kwarg**, or it is declared on the schema, accepted at the door, and never
  written — the inbound twin of a FastAPI `response_model` dropping an undeclared key, and just as
  silent.
  **THE WORK LANE HAS NO REFRESH PATH AT ALL, and a plan that says otherwise is describing the
  deploy sibling.** `_existing` selects, raises, and returns the row **unmutated**; nothing
  re-asserts onto an existing work record, so a repeat proposal is a genuine no-op. Only the deploy
  ingress re-asserts, via `_refresh_derived` over `_DERIVED_FIELDS`. The orchestrator's own plan for
  that increment offered "what a re-proposal re-asserts onto an existing record" as a candidate
  mechanism and it was **false for this lane** — caught only because the plan told the session to
  read `propose_work_change` rather than assume. Read it again before keying anything on that tuple.
  **The standing consequence for any field added to either tuple:** both producers replay their
  whole population on every scheduled pass, and every row predating the column stores null, so a
  null-versus-value comparison answers **409 forever**, which `bump_proposer` classifies as
  `refused` — a finding on every pass, on a write-once row whose status only a human can move, with
  no supersede route. Additive fields belong in neither tuple unless the whole standing population
  can satisfy them.

### #307

- **A HOLDING DEPENDABOT BRANCH WHOSE CHECKS NEVER FINISH STALLS ITS WHOLE REPOSITORY, and that is a
  chosen residual, not a defect to test away.** ADR-0045 lets the lane edit only one Dependabot
  branch per repository at a time, and a sibling waits while that branch HOLDS — while every refusal
  it names is one that clears without anyone acting. Three members of the holding set are there
  only for the minutes after an update, and each can outlive them: `landing_checks_awaiting_verdict`
  on a head that is already current (runs cancelled — the Actions quota did it on 2026-08-17 and
  2026-08-22 — and nothing in the estate re-runs an abandoned check), `landing_checks_in_flight`
  that never gets a runner or an approval, and `landing_mergeability_unknown` that never resolves.
  They stay in the set anyway: without them the branch just freshened would stop holding in the very
  pass that freshened it, and the sibling behind it would be edited too — the defect itself.
  **How it surfaces:** once, as `held` on the holding branch's own line, because none of the three
  is in either lander's non-finding set; the siblings behind it read `waiting` and do not multiply
  it. The remedy is a person re-running the checks on that one branch. **Do not write a test
  asserting that every holding member clears on its own** — three of them demonstrably need not,
  and a test saying otherwise would pin a claim the design explicitly declines to make.
