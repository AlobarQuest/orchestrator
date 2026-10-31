# Landing lanes

How the estate and inert landing lanes, the landing ledger, change records and the deploy policy behave, and how to read what they report.

The entries below moved verbatim from the CLAUDE.md invariants section on 2026-10-01 (Tier 3 item 17). Each keeps its original number. Their dated corrections are the record of what was measured when; read the last correction in an entry as its current state.

**Reading the older entries.** ADR-0038 (2026-08-31) removed the Dependabot auto-merge workflows from the six repositories that carried them, and the orchestrator merges directly. "The cascade" in an entry below means the inert lane, which took the cascade's subjects. An entry that describes enabling or arming auto-merge records what was true when it was written; members now keep auto-merge off, and `membership_check` reports any member that doesn't.

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
  absent**, `strict: false`, `allow_auto_merge: true`. *ADR-0038 superseded `allow_auto_merge:
  true` and the auto-merge bullets below: members have auto-merge off, and `membership_check`
  reports one that doesn't.*
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
  - **`enforce_admins` is TRUE on `factory-runner`, `change-manager` and `brain` (ADR-0038), and
    false on the other repositories measured; the asymmetry is the considered part.**
    `factory-runner` is the one repo where a bad merge stops every dispatch in the estate, and its
    CI is ~20 seconds, so enforcement is nearly free. It is off elsewhere chiefly because
    orchestrator's suite is 25 minutes and enforcing there taxes every CLAUDE.md, ADR and backlog
    commit — the lowest-risk commits, hardest.

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

- **A `work`-source change record is approved by change-manager's work policy when it conforms,
  and by a person otherwise; no producer credential moves one.** change-manager approves a record,
  as `work-policy`, when it is a standing dependency-update package (`change_class`
  `dependency-update`, `package_source_repository` `AlobarQuest/intent-packages`, risk `caution`)
  whose `target_repository` is in its inert or deploying population (ADR-0025 amendments 1 and 2;
  a repository outside both is refused `target_repository_not_in_policy`); every other `work`
  record stays pending for a person. `src/bump_proposer/change_manager.py` is propose-and-read by
  construction — `/api/work-changes` and `/api/items`, anchored so no `/api/items/{id}/…` verb
  matches — and change-manager's `propose` scope refuses every status-moving route server-side
  whatever the client sends; the work watcher's one write is `work-retirement`, which can only
  remove permission. The `deploy` source has a `deploy-retirement` route whose closed vocabulary
  has exactly one member (`pull_request_closed_unmerged`). The propose credential lives in a BWS
  project the narrow `sds-operator` identity cannot read — `bws` answers `404 Resource not found`,
  which reads like a missing secret rather than a denied one. Use the broad identity for it.
- **The bump proposer creates a missing standing package, and alerts on a bump it cannot route.**
  Its lane is every repository the member record grants `dependency_updates` that the landing
  policy declares (inert, or deploying: the top-level `repositories`) or that a standing package
  targets; a policy declaring either population empty or absent stops the pass (exit 2). For a
  failed bump in an ecosystem with tooling (`npm_and_yarn` → `npm`, `pip`, `uv`) and no package for
  its dependency, it runs intent-packages' `create-standing` with `--reach live_estate` for a
  deploying repository and `--reach source_repository` for an inert one (a repository in both
  deploys), and then the usual transition, policy approval, hash, commit, publish and proposal; a
  single-clause uv range with `>=` or `==` carries its bare versions. Whether the landing party
  still has a bump is asked of the repository's own block: the inert block's
  `excluded_ecosystems` (docker) for an inert repository, the `landing` block's (github_actions)
  for a deploying one, so a passing bump in a deploying repository stays with the estate lander
  (ADR-0028 amendment 3). It
  sends one alert-hub alert (sender `sds-bridge`, key
  `dependency-bump:<repository lowercased>:<ecosystem>:<dependency>`, built by
  `alert_hub_client.dependency_bump_key`) instead for a failed bump with no tooling, any other
  range (pip and npm ranges, uv ranges other than `>=`/`==`), a dependency already covered by a
  package in another ecosystem, and a bump with no tooling in an ecosystem its repository's block
  excludes even when it passes (docker in an inert repository, github_actions in a deploying one).
  A key fires once, is kept in
  `~/Library/Application Support/sds/bump-proposer/alerts.json`, and is resolved by the first writing pass that no longer raises it: its pull request merged or closed, or (in an ecosystem the cascade lands) a re-run passed. A re-run that fails again but hasn't settled holds the key, neither resolved nor fired again. A
  repository still in the lane that the pass didn't read (unreadable, in scope through a standing
  package but declared in neither population, `not-declared-landing`, or outside a `--repository`
  run) keeps its keys; one that left the lane (its `dependency_updates` grant withdrawn, or no
  longer declared in either population with no standing package) has its keys resolved by the next writing pass that reaches its alerts (a pass with no
  repository in the lane stops first and sends nothing). An approval refused after a package was created removes that uncommitted
  package directory, so the next pass's clean-checkout check still passes. A dry run sends nothing and writes no state.
  `--submit` refuses to start without `SDS_BRIDGE_ALERT_TOKEN`, which the launcher fetches from
  `SDS Operator` with the narrow identity on every run.

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
- **A LANDING'S CHECKS ARE THE JOB ATTEMPTS THAT HAD CONCLUDED BY THE MERGE, read from each job's
  own `completed_at`, never from the run's `updated_at`.** `_checks_and_gate` reads every
  `pull_request` run's jobs with `filter=all`, every page, and keeps, per job name, the highest
  attempt whose `conclusion` is set and whose `created_at` and `completed_at` are both at or before
  `merged_at` (a skipped job's row can be created after its own `completed_at`). The newest gate run
  that qualifies counts, as of its latest attempt whose jobs had all concluded by then, with that
  attempt's conclusion (`/attempts/{n}` when a re-run followed) and arming step; a conclusion not
  yet readable is a failed read, retried next pass, never a recorded `unknown`. GitHub moves a completed
  run's `updated_at` after the fact with no re-run (change-manager #141: job done 14:29:29Z, merged
  14:29:37Z, run `updated_at` later 14:41:30Z), so the old run-level filter made the facts depend on
  when the landing was read: a row recorded before the bump drew a permanent `observation_conflict`,
  and one recorded after it was stored with its checks missing. The four rows stored that way are
  exempt from re-recording (`record.py::KNOWN_UNDERCOUNTED_CHECK_LANDINGS`, measured 2026-10-27).

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

*ADR-0038 removed the arming workflows, so nothing arms auto-merge now. The attribution mechanics below still decide what the ledger records for any merge: `merged_by` with a `[bot]` suffix is the basis.*

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

### Holds (bridge layer Part 4)

- **Both landing admissions refuse a Dependabot update that moves a held dependency past its
  ceiling.** Holds live in the SDS member record (`sds_member_holds`, written only when a person
  confirms a member proposal on `/review`) and are read in-process through
  `services/membership/members.py::holds_for`, case-folded on the repository and whatever the
  member's state. `terms.version_ceiling_term` is the one term; the comparison is
  `services/membership/holds.py::exceeded`.
- **A repository with no holds never reads a commit message.** With holds, the term reads the pull
  request's commits (`pull_request_commits`, now on `EstateReadGateway`) and takes the message of
  the ONE commit the update bot authored — never the head's, which after the lane's branch update is
  a merge commit naming nothing. A grouped update is refused when any member exceeds a hold; a
  single update's version is the greater of its title's and its block's.
- **Two codes.** `landing_version_ceiling_exceeded` is an **exception** in both landers (it clears
  only when a person lifts the hold), so a held pull request is quiet, its freshness refusal is
  suppressed, and it is never freshened (`qualifies_for_branch_update` treats it as a real
  condition). `landing_dependency_list_unreadable` — no single update-bot commit, no message, no
  readable `updated-dependencies` block, an unreadable branch ecosystem, or a failed read — is in
  neither lander's set, so it is a **finding** (exit 3), and the sibling rule counts it as a read
  failure. Neither is ever read as "not held".
- **The term is asked before each admission's early return** (the inert policy, the estate record),
  so the ceiling code is present whatever else refuses. **A fork's upstream sync is exempt** (an
  author the inert policy declares non-ecosystem); without the policy the term is asked.
- **An acting lander pass closes a held pull request with a comment** (`POST
  /api/v1/held-pr-close`, `services/landing/held_close.py`) whenever the ceiling code is among the
  refusals, beside any other; a dry run prints `would-close`, an acting pass `closed` (or
  `reopened` when the orchestrator answers from its record: a person reopened it), and none is a
  finding. Closes serialise per repository on an advisory lock. The orchestrator re-composes the named lane's own admission in its transaction and
  closes only when it carries `landing_version_ceiling_exceeded` (never on
  `landing_dependency_list_unreadable`), the update bot opened the pull request, the repository is
  the lane's (the inert declaration; an identified change record for the deploying lane), the pull
  request is open at the head the caller read, and the lane is switched on with its credentials.
  The public comment names each held dependency and its ceiling and one fixed sentence, never the
  hold's reason or who placed it. One `Event` (`landing.held_pull_request_closed`) keyed
  `held-pr-close:<repository lowercased>#<number>` — the only key accepted — makes a replay touch
  GitHub no more; a close that fails after its comment records nothing, and the next call finds the
  sentence in the conversation and closes without commenting again.
- **The factory side asks the same matcher through `factory_breaches`.** The factory merge
  (`pr_merge_admission.py::_hold_term`) refuses a dependency-update unit whose revision's
  `profile_fields` bump breaches a hold (`merge_version_ceiling_exceeded`) or can't be read in a
  repository with holds (`merge_dependency_bump_unreadable`), or, with no bound human approval, in
  one with a reverted version. The bump proposer skips a held bump
  (`ceiling-exceeded`, no package, no proposal, no alert), and the work carrier refuses a held
  record (`version_ceiling_exceeded`, `dependency_bump_unreadable`), which is a finding in that
  lane. All three read a uv version as a `>=` floor, so a hold on a uv dependency stops every
  factory update of it.
- **A standing bump lands on the policy path (ADR-0025 amendment 4).** `POST
  /work-units/{id}/pr-merge` accepts a unit with no bound human approval only when it is a
  `dependency-update` unit on a standing bump's revision, reach exactly `source_repository`, where
  the estate answers `inert`, recognised at merge time by dependency-update patterns alone, with
  the `dependency_updates` grant. `ORCHESTRATOR_BUMP_MERGE_ENABLED=false` stops that path, and a
  unit it covers then refuses `merge_policy_path_off`. Other terms report beside that code, so a
  lane reads it as `waiting` only when it is the sole refusal. A landing on a bound approval
  doesn't read the switch. A policy-path landing writes `authority.human_gate_not_required`
  (`at: merge`) beside a `merged` record only, and `authority_recognised_by` on
  `pr-merge-admission` shows what would lift it.
- **Every merge and propose path is classified** in `tests/architecture/test_hold_paths.py`:
  honours (names the function that calls the matcher), covered by an honouring gate, or not a bump.
  A new `MERGE_EXEMPT_PATHS` entry fails until it is classified. The residuals are in ADR-0028
  amendment 2.

### Dependabot's update block

- **The update block is read from the commit the gate reads, never from the head.** The gate's
  `dependabot/fetch-metadata` (v3.1.0) takes the pull request's FIRST commit, `commits[0]` of
  `GET /pulls/{n}/commits`, and uses its message only when the update bot authored it and its
  signature is verified. Every transcribed gate revision leaves both `skip-*` inputs unset.
  `landing_ledger/github.py::_gate_message` mirrors that rule. A branch update puts a merge commit
  with no trailer on top, so reading the head lost metadata the gate still had (brain#107 and
  change-manager#139 on 2026-10-29).
- **Pending updates read only that commit** (`read_pending_updates`, #544), so the bump proposer
  and the ledger's audit see what the gate saw. A requirement range whose gate commit yields nothing
  is `ambiguous` in the bump proposer, never a crash (#542).
- **A landing asks it last** (`read_landing`, #545): the landing commit, then the pull request's
  head (where factory-runner writes its own trailers), then the gate's commit, and that only for a
  pull request the update bot opened. Asking it last means a row that already has metadata can't
  move. Measured on 2026-10-29: re-deriving all 541 stored landings in the 30-day window across the
  13 ledger repositories reproduced every row except the four `KNOWN_UNDERCOUNTED_CHECK_LANDINGS`.
- **The hold term asks a different question.** `services/landing/terms.py::_update_bot_message`
  takes the one bot-authored commit anywhere on the branch. The two agree on every ordinary pull
  request.

### The bump driver (Landing Part 4)

`bump-driver` (`src/bump_driver/`, `scripts/run-bump-driver.sh`, hourly at :50, log
`~/Library/Logs/bump-driver.log`, Healthchecks check `sds-bump-driver`, an outcome alarm) moves
standing dependency-update work as SYSTEM with no person acting. Verification stays with the
`rotation-verifier` lane, as VERIFIER. Check it with `/bin/bash scripts/run-bump-driver.sh --dry-run`.

- **What it reads.** `work` records whose `change_class` is `dependency-update`, in a repository the
  member record grants `dependency_updates`, whose revision is a standing package's. It reads
  `approved` records and `resolved` ones, because `work_watcher` retires a record as soon as its
  units complete, which is before the lane lands them. A unit is touched only if its own envelope is
  `dependency-update`, names no `rotation_unit` and targets that repository.
- **Decompose.** A revision with no units and no proposal: `factory decompose --no-execute` against
  a shallow mirror under `~/Library/Application Support/sds/bump-driver/mirrors/`, posted with key
  `bump-driver:<revision>`. The mirrors run only git, with hooks off and the token in the
  environment, and `fresh` refuses unless the tree is clean and HEAD is `origin/main`. The bump's
  terms come from `enforcement_snapshot.profile_fields`. A proposal a person rejected, sent back or
  superseded is never proposed over; it is reported and isn't a finding, because a person decided.
- **`factory` holds no credential but the Brain keys.** Its conformance claim runs `code-standards`
  inside the mirror with its whole environment, which spawns no linter only because the tree is
  clean. So its environment is an allowlist (`cli.py::_generator_environment`, keys pinned by
  `test_decompose.py`). The lane hands it what those credentials would fetch: `--intake-file`, the
  intake it read through its own client, and `--assume-checkout-current`, resting on `fresh`'s proof.
- **Dispatch.** One run per repository: a `claimed` or `executing` unit holds it, and so does a run
  dispatched but not yet claimed (the unit is still `ready`, and history says so), found across
  every ready unit before anything starts. A record or ready unit that can't be read holds its
  repository for that pass, since it may be the one with the run. Such a run still unclaimed after `UNCLAIMED_HOURS` (2)
  raises `bump-run-unclaimed` and keeps holding: the lane never dispatches it again, and
  `deploy_orchestrator.py` refuses on the same signal. Look for the run in the repository's Actions,
  then dispatch the unit again or retire it (fail it, then cancel it). The key is
  `bump-driver:dispatch:<unit>:<version>`, with `:<n>` added once `n` earlier dispatch records didn't
  start a run: such a record holds its key and moves no version, so reusing the key would replay it
  for ever. So a `dispatch.failed` is asked again every pass until its failure-signature breaker
  opens. `dispatch_disabled` and `outside_change_window` are `waiting`. Each pass while the switch
  is off writes one skipped record per ready unit.
- **Merge.** A completed unit whose bound pull request isn't closed unmerged. The lane reads
  `pr-merge-admission` first, because the act puts its refusals in a message:
  `merge_policy_path_off` or `merge_repository_reverting`, alone or together, is `waiting`, and any
  other refusal is reported. A pull request
  already merged is still asked, so a landing the orchestrator lost track of records
  `already_merged`, but only while the unit's terms hold: admission runs before that branch, so a
  unit that doesn't qualify writes no row, and the lane says nothing about it.
- **Alerts** (sender `sds-bridge`, memory in the state directory's `alerts.json`):
  `bump-unit-failed:<repo>:<unit>` for a unit in `revision_required` (a `failed` unit already holds
  `work_watcher`'s `dependency-unit-failed`), `bump-decompose-refused:<repo>:<revision>` for a
  generator refusal or a proposal of this lane's policy didn't approve (`policy_not_applied`),
  `bump-act-refused:<repo>:<unit>` for a refused dispatch or merge, and
  `bump-run-unclaimed:<repo>:<unit>` above. Each resolves when its subject clears, and is held while
  its repository's records can't be read. A person's own proposal never alerts.
- **One acting pass at a time.** It holds an `fcntl` lock on `pass.lock` in the state directory, so a
  hand-run pass can't reset a mirror under a running `factory`. A second acting pass prints that and
  exits 0, a finding in neither. `--dry-run` takes no lock.
- **Nothing requeues a unit.** `revision_required` has a SYSTEM edge back to `ready`, but `requeue`
  takes only failed or blocked work and no lane drives it, so the alert is the end state and a
  person decides.
- **What `factory` needs from the launcher:** intent-packages' venv and this repository's on `PATH`
  (`factory` shells out to `orchestrator conformance-claim`), project-standards and
  security-standards on `FACTORY_PYTHONPATH`, and the Brain keys as `CODE_BRAIN_KEY` and
  `INFRA_BRAIN_KEY`. A
  `--no-execute` the intent-packages checkout doesn't know is a refusal like any other, so that
  checkout must be current.
- **Deploying the orchestrator.** `deploy_orchestrator.py` refuses while a unit is live or a dispatch
  is unclaimed, and this lane can start one at :50, so deploy away from :50.

### The landers' outbound surface (Landing Part 1)

- **Each lander's whole outbound surface is one row of `tests/architecture/test_landing_edges.py`**:
  the orchestrator, change-manager, GitHub and alert-hub routes its clients permit, under every verb,
  and the bearers its launcher fetches. Writes are POST only (`_permits` refuses PUT, PATCH and
  DELETE on a write path), and the estate lander reads change-manager through its own
  `estate_lander/change_manager.py`, which permits `GET /api/items` and no write. The inert lander's
  GitHub reads go through `deploy_watcher.github`, which bounds the verb and not the route, so its
  row pins the import and the one method `PullRequestSource` declares.
- **Residual: the landers' SYSTEM bearer has no server-side route confinement.** Both landers send
  the `orchestrator-system` credential (BWS `221a48d5-…`), and the orchestrator confines only
  OBSERVER by route (`api/dependencies.py::_confine_observer`), so the bearer could reach
  `/api/v1/work-units/{id}/pr-merge` and every other SYSTEM route. The client predicates and the
  edge table bound what these programs send, not what the credential can do. Confining it needs a
  dedicated lander identity (a security-standards registry change and a new M2M credential), which
  is a separate decision.
- **A pull request held for 24 hours raises one alert-hub alert** (`lander/held_alerts.py`, key
  `landing-held:<lane>:<repository lowercased>:<number>`), resolved on the first pass it is no
  longer `held`, `unreadable` or `error`; a repository the lane can't list fires one alert under
  number 0 after the same 24 hours, and resolves when it lists again. An orchestrator outage of 24
  hours or more turns every in-scope open pull request `unreadable`, so each fires its one keyed
  alert: noise in proportion to the open pull requests, never a repeat.

## Automatic reverts (inert repositories)

The inert lander reverts a bump the SDS landed when the default branch's own checks fail because of
that bump, and holds the dependency at the version before it (ADR-0019 amendment 2). That covers a
Dependabot bump it landed and a dependency-update unit the factory merged. Repositories that deploy
on landing follow "Automatic reverts (deploying repositories)" below.

### The watch

- **What is watched.** A landing of this lane (`inert_pr_merge.merged` or `.already_merged`) for 48
  hours (`WATCH_HOURS`). A revert landing is never watched, and a landing with no record must be the
  update bot's, so a fork's upstream sync is never watched.
- **The factory's merges.** Also watched, for the same 48 hours: a `unit_pr_merge` row with status
  `merged`, of a unit that is a dependency update by both its envelope's class and its revision's
  profile, whose event says the estate answered `inert` at the merge (`landing_inert`).
  `already_merged` is someone else's landing and isn't watched. Nor is any other unit, so an
  `app-change` is never reverted automatically, nor a pull request a record names as its revert, a
  merge that redeployed, or a merge made before `landing_inert` was recorded. Nothing is read to
  select them, and no pull request is read to judge one.
- **Which jobs.** A job is a (workflow, job) pair. It counts when it passed at the pull request's
  head and its workflow has a `push` run at the merge commit. A `pull_request`-only workflow, or a
  job absent from the push run, is never watched.
- **Failed** means `failure`, `timed_out` or `cancelled`.
- **How long.** A landed revert stays in the watch, reported `reverted` (not a finding) with no App
  read, while any of its dependencies is held, by its own hold or a person's on the same dependency.
  A refused or failed revert never leaves; an open revert leaves when it lands.
- **Routes.** `GET /api/v1/landing-watch` reads and judges; `POST /api/v1/landing-revert` acts once
  per judgment. Both are SYSTEM-only, ask the member record's `dependency_updates` grant for the
  repository first, and the act judges again inside its transaction.
  Each lander asks the watch for its own lane (`?lane=inert` or `?lane=estate`; any other value is
  refused `landing_watch_lane_unknown`), so the other lane's landings cost no GitHub read. The
  reads are serial, about 6.5 s per watched landing, so the watch call alone has a 240 s client
  timeout (`WATCH_TIMEOUT_SECONDS`); every other call keeps 60 s.
- **Order.** An acting pass reads the watch and acts on it before its landing pass, so a bad landing
  is rerun or reverted before a sibling lands on top of it.
- **Siblings wait.** While a repository has a rerun asked for (inside the watch) or a revert opened,
  inert admission refuses every other Dependabot bump there `landing_repository_reverting`. Both
  landers report it `waiting`; it clears when the revert lands. The revert itself and a fork's
  upstream sync are not refused, and the branch-update scan never holds the revert behind it.

### The flake rule

A failed job gets one rerun of its failed jobs. The verdict is `bad` only when a recorded job fails
again at a later attempt than the one recorded when the rerun was asked for, and the same jobs
passed on the merge commit's parent. A job that passes on the rerun, a job that was already failing
on the parent, and a job still cancelled after the rerun are not the bump's: the landing stays
`clean` or `pending` and nothing is reverted.

### The revert

The revert is the exact inverse of the squash merge, built through the git data API on branch
`sds-revert/pr-<N>` (title capped at 256 characters). Inert admission lands it only when it is
recorded, is the exact inverse, has clean checks and a current head. Landing it marks the record
`revert_landed`. For a factory merge the inverse is the whole merge, its source fixes included, and
the original's author doesn't matter. It lands only where the inert policy declares the repository.
Elsewhere nothing lands it: the lander reports it `revert-stranded` and raises
`landing-revert-stranded` while it is still open.

**Overlap refusal.** When anything landed on top of the bump touched a path the bump touched, the
orchestrator records `refused_overlap` and opens nothing. Reverting would remove that work too.

### The hold

When the revert lands, `services/membership/revert_hold.py::place` inserts a ceiling hold at the
from-version in the same transaction, with `set_by` `sds-revert` and a `member.changed` event. The
directory comes from the pull request title, else `/`. A factory merge's dependency, from-version
and to-version come from its revision's `profile_fields`, its ecosystem from the package id, and its
directory is `/`, where every form of the generator's commands runs. A grouped bump gets no hold,
because it has no single from-version; the record's `hold_outcome` says why a hold was skipped. To lift a hold,
stage a `lift` with `sds-members stage` and confirm it on `/review`
(`docs/operations/membership.md`, "Automatic holds"). Nothing else removes it.

### The never-again rule

Both landers refuse the exact reverted version for good (`landing_version_reverted`), whether or not
a hold was placed, and even after the hold is lifted. So does the factory merge
(`merge_version_reverted`), for a revert of either origin: exactly for npm and pip, and for uv on any
floor at or below a reverted version, since `uv add '<pkg>>=<version>'` can resolve to it. To take
that version anyway, land it by hand; the SDS does not watch a landing it did not make.

The bump proposer, the carrier and the bump driver don't read reverted versions. They are covered
by the factory merge, so a reverted version that gets as far as a package spends one run, is refused
at the merge, and raises `bump-act-refused`. While a revert is in progress the factory merge also
waits (`merge_repository_reverting`), and the driver reports it `waiting` with no alert.

A unit whose merge was reverted stays `completed` with its one `unit_pr_merge` row. Its record stays
as `work_watcher` left it, the driver's merge step replays and says nothing, and nothing re-runs it.
A later package for the same dependency is stopped by the hold.

### Alerts

The inert lander raises these through alert-hub (`lander/held_alerts.py`, memory in
`revert-alerts.json`). Each fires once. `landing-reverted` stays open while the hold is in force and
resolves when it is lifted. The refused, failed and stranded alerts stay open until a person closes
them, and their lander findings print on every pass while the record stands. A record that names no dependency says no hold was placed and no
version is barred. A pass that cannot read the watch holds every remembered key.

| Key | Meaning | What you do |
|---|---|---|
| `landing-reverted:<repo>:<pr>` | The revert landed. The alert names the failed jobs, the revert pull request and the hold, and says the version stays refused after the hold is lifted. | Read it. Lift the hold when the dependency is safe to take again; land the reverted version itself only by hand. |
| `landing-revert-refused:<repo>:<pr>` | Overlap: something landed on top of the bump. The default branch may still be broken. | Revert by hand. |
| `landing-revert-failed:<repo>:<pr>` | The revert could not be opened. | Read the detail; revert by hand or fix the cause. |
| `landing-revert-stranded:<repo>:<pr>` | The revert pull request closed without landing, or it is open in a repository outside the lane's population, so nothing lands it. This repository's bumps wait (`landing_repository_reverting`, `merge_repository_reverting`). | Reopen it, or land it by hand if it is outside the population. Reverting by hand does not release the bumps yet. |

### Known gaps

- A grouped bump gets no hold, so only the never-again rule stops it returning.
- A uv factory merge records its floor, not the version the resolver landed. While the hold stands,
  every version above the from-version is refused. After it is lifted, the never-again rule knows
  only the floor, so a newer version that was what actually broke can land again.
- A dependency-update unit whose revision names no readable bump is still reverted, with no hold
  and no never-again entry; the record's detail says so.
- A `refused_overlap` or `revert_failed` record stays in the watch, and its alert stays open, until a
  person closes the alert; no act clears the record yet.
- A stranded revert has no clearing act either. Its record keeps the repository's Dependabot bumps
  refused `landing_repository_reverting` until the revert is reopened and lands; reverting by hand
  does not release them.

## Automatic reverts (deploying repositories)

The estate lander points production back at the build before a bad bump, reverts the bump, holds
the dependency, and puts production back on its moving tag (ADR-0019 amendment 3). It covers the
repositories served from a GHCR image by Coolify, such as `brain` and change-manager. The watch, the
flake rule, the revert, the hold and the never-again rule are the inert section's, with the
differences below.

### The signal

A landing is `bad` by its checks (the inert rule) or by what production serves
(`services/landing/revert_watch.py`). The served rule reads `revision_watcher`'s
`production_revision` observations for every application the member record lists for the
repository on its default branch:

- **`bad`** only when every application serves exactly the bump's parent, that has stood for
  `ROLLOUT_STUCK_HOURS` (2) by `received_at`, timed from the later of the newest standing reading
  and `ROLLOUT_SETTLE_MINUTES` (30) after the landing, and the watcher has posted about every
  application within `WATCHER_SILENT_HOURS` (2) (`observation_sources.last_seen_at`).
- **`clean`** when every application serves the merge commit or a later one.
- **`pending`** otherwise: one application of several behind, production on a commit older than the
  parent or off the default branch, a silent watcher, or a check still running.

A served `bad` stands only when the check rule is `clean`.

### The sequence

The estate lander runs hourly. Every pass takes the next step for each watched landing, one step
per pass, and lands the SDS's own revert pull requests; ordinary bumps land only between 02:00 and
06:00 `America/New_York`. Without `--submit` it reads nothing from Coolify or GHCR and reports
`would-repoint`, `pinned` or `would-restore`.

1. **Point back.** The lander resolves the parent's image digest in GHCR, then asks
   `POST /api/v1/landing-repoint` to record `repointed` with `pinned_images` (`{application uuid:
   {tag, digest, restore_tag}}`). The orchestrator judges again and refuses unless the landing is
   still `bad`. Only then does the lander set each application's `docker_registry_image_tag` to the
   parent's commit and start a deploy, and once every tag is set it confirms the pin
   (`POST /api/v1/landing-pinned`), which marks each image `"pinned": "yes"`. A pin that fails
   part-way, or a pass that stops before confirming, is completed and confirmed on the next pass.
   The orchestrator refusing the re-point is `waiting` when its answer moved (the record moved, or
   the landing is no longer `bad`) or the estate lane is switched off; any other refusal, a
   repository not granted among them, raises `landing-not-pointed-back`.
2. **Confirm and open.** Once the pin is confirmed and every application serves the parent, the
   lander asks `POST /api/v1/landing-revert` with expected status `repointed`, and the orchestrator
   opens the revert from the record. Until then the lander deploys again each application whose tag
   is still the parent's; an application whose tag a person changed is left alone and named.
3. **Land.** `change_proposer` files the revert as a `dependency-revert` record, which deploy policy
   14 approves. The estate lane lands it in any pass, outside the pace and the window, and the
   merge path records `revert_landed` and places the hold. The revert merge's own rollout check
   fails while production is pinned; that is expected.
4. **Restore.** Once GHCR holds the revert merge commit's image, the lander sets each application
   back to its `restore_tag` and starts a deploy. Once every application serves the revert's merge
   commit or a later one, it asks `POST /api/v1/landing-restored`, and the record moves to
   `restored`.

The orchestrator refuses `POST /api/v1/landing-revert` on a `bad` deploying landing with no
`repointed` record (`landing_revert_needs_repoint`).

### The Coolify client

`estate_lander/image_repoint.py` sends the dedicated estate-lander Coolify token (BWS, minted by
Devon), which the launcher fetches on `--submit` only (`ESTATE_LANDING_COOLIFY_TOKEN`). Without it
every re-point and restore is refused by name. The client is confined per repository: it lists the
applications, matches each member-record application's `health_url` host to the one Coolify
application whose `fqdn` names it, and then may only read those applications, `PATCH` their
`docker_registry_image_tag`, and `POST` their `/start`. An application that matches no Coolify
application, or several, refuses the whole re-point.

Compose applications (domains in Traefik labels, empty Coolify `fqdn`) refuse the re-point by
design.

### Restore by hand

To put production back yourself, set each pinned application's `docker_registry_image_tag` back to
its recorded restore tag in Coolify and redeploy. The lander reads tags on their restore tags as
your restore only once the pin is confirmed (every image in the watch's `pinned_images` carries
`"pinned": "yes"`); before that they are a pin that never happened, and it points them again. The `landing-production-pinned` alert names each
application's uuid, its pinned tag and its restore tag. Set every pinned application, not some: the
lander acts only when every tag equals its restore tag. What the lander does next depends on the
record's status:

| Status when you restore | What the lander does |
|---|---|
| `repointed` (the revert has not opened; the pin is confirmed) | Records `restored` with detail `restored by hand before the revert opened`. No revert opens; the bad bump stays on `main` as your rollback decision. Bumps resume and the pinned alert resolves. |
| `revert_opened` | Nothing yet. The revert continues; once it lands, the restore step finds the tags already back, deploys any application not yet serving the revert's merge commit, and records `restored`. |
| `revert_landed` | The restore step finds the tags already back, deploys any application not yet serving the revert's merge commit, and records `restored`. |
| `refused_overlap` or `revert_failed` (the pin is confirmed) | Records `restored`. The pinned alert resolves, and so does the `landing-revert-refused` or `landing-revert-failed` alert. Bumps resume. The default branch may still carry the bad bump: fix it by hand before you restore. |

A tag set to anything other than the restore tag is not a hand restore: the record stays as it
is, production stays as you set it, and the pinned alert stays open. The lander never undoes it
while `repointed`, but the restore after a landed revert sets every application back to its
restore tag, overwriting it.

The orchestrator also holds the take-over, and opening the revert, to a confirmed pin: asked of a
`repointed` record whose pin is not confirmed, both are refused `landing_pin_unconfirmed`, which
the lander reports `waiting` while it completes the pin.

To take over without the lander acting at all, switch the estate lane off
(`ORCHESTRATOR_ESTATE_LANDING_ENABLED=false`). The watch then serves every estate-lane landing with
`landing_revert_lane_off` as its verdict, whatever its record's status, so no pass sets a tag,
starts a deploy or restores. The pinned alert stays open, because it reads the record's status and
pins. The inert lane is unchanged: its records past the rerun drive no write of their own.

### What holds bumps

While a record has `pinned_images` and is not `restored`, in any status, estate admission refuses
every other Dependabot bump in the repository `landing_repository_reverting`, reported `waiting`. A
bump landed behind the pin would never be served. The revert itself is not refused. The hold placed
when the revert lands, and the never-again rule, work as for an inert repository.

### Alerts

The inert section's four alerts apply. A `restored` record whose revert landed keeps
`landing-reverted` open, with the same text, while the hold stands; a record restored by hand with
no revert (a take-over, or after a refused or failed revert) raises nothing. The estate lander adds
two (`lander/held_alerts.py`):

| Key | Meaning | What you do |
|---|---|---|
| `landing-production-pinned:<repo>:<pr>` | Production serves the build before the bump. Raised in every status before `restored`; resolves at `restored`. Names each application's pinned and restore tags. | Let the sequence finish, or restore by hand (above). |
| `landing-not-pointed-back:<repo>:<pr>` | The landing is `bad` and the lander could not point it back: no token, an application no Coolify application serves (or two do), the parent's image missing from GHCR, an image not this estate's, or an application already pinned to a commit. Nothing is pinned and no revert is open. Open while the refusal stands. | Fix the cause, or roll back by hand. |

### Known gaps

- A hand restore after the revert opened leaves the pinned alert open and bumps refused until the
  revert lands. A stranded revert with production pinned has no clearing act.
