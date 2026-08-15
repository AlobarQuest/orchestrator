# ADR-0019 — SDS-initiated production deploys route through change-manager

- **Status:** Accepted (principle). Implementation not started.
  **THREE OF THIS DOCUMENT'S OWN PREMISES WERE MEASURED WRONG on 2026-08-10** — see
  `~/docs/software-delivery-system/2026-08-10-adr0019-implementation-plan.md`.

  **INCREMENT 1 DONE 2026-08-10** — change-manager #46, merged `06f9268`, which deployed itself
  (run `31426195637` success; production reports serving `06f9268b5160`). A deploying-merge change
  can now be proposed, and is refused without acceptance criteria or a rollback plan. Production
  item **44** records Dependabot PR #42, one of the six waiting, and is **deliberately left
  `pending`**: approving it would assert that a human approved a deploying merge, and none has.
  Verified against a pre-increment baseline: `GET /api/items` returns the baseline unchanged
  (43 items, max id 43) because proposed sources are withheld when no source is named, and
  `GET /api/items?status=approved` returns **0** — so it is invisible to the 04:00 executor.
  **Adversarial review produced two kills**, each found independently by more than one reviewer:
  a scan batch could adopt the deploy record by colliding on `identity` while every guard was
  keyed on `source`; and the migration's `downgrade()` was a guaranteed foreign-key violation on
  Postgres, green in CI because migration tests run on SQLite where foreign keys are off. Both are
  captured as portfolio invariants. **Sequencing constraint carried into increment 2 or 3:
  infraops' `source !== 'security'` denylist must become an allowlist BEFORE anything can approve
  a deploy record** — approval is what puts an item into the executor's query (backlogged
  `63380218065d`, P1).

  **INCREMENT 2 DONE 2026-08-10** — orchestrator #158 (`fccb2a3`, deployed nothing) and
  change-manager #47 (`23aefb69`, deploy run `31443858930` success, production moved to it). The
  rollout watcher observes what a deploying merge caused and records it against the change record;
  `com.devon.deploy-watcher` runs **hourly**, proven under launchd rather than only in a shell.
  Increment 1's guards re-verified on the new build: `claim`, `outcome` and `handoff` all 409
  `'deploy' changes have no authorized executor`, and the executor's own query still returns 0.

  **The build session overrode this HQ handoff on the central design point, and was right.** The
  handoff said to read the **run conclusion** on `main`. It reads the **specific attempt's jobs**
  instead, because *"a run conclusion cannot distinguish 'the tests failed and nothing was
  deployed' from 'production was deployed and is broken', and those two want opposite remedies"* —
  and it addresses the attempt rather than the run, since a re-run supersedes its predecessor.
  **Validated independently by the only two deploy failures this repository has ever had**
  (2026-06-14): run `27501092950` was `test: failure` / `build-and-deploy: skipped` — nothing
  deployed; run `27501401154` was `test: success` / `build-and-deploy: failure` — production
  deployed and broken. Both carry run conclusion `failure`. The handoff's instruction would have
  collapsed them into one answer. 48 of 50 deploy runs succeeded.

  Three verdicts are deliberately distinct — `NotSettled` (pending, exit 0, "come back in an
  hour"), `Unmeasurable` (exit 3, the question could not be asked) and `ReadError` — so a
  still-running sibling never exits 3 under a diagnosis naming the wrong problem.

  **Residuals, stated in the module rather than discovered later:** an observation is *asserted*
  by the watcher and the server cannot verify it, and the server cannot tell a watcher from
  anything else holding the shared M2M secret. Per-caller identity remains the open question, and
  Increment 3 or 4 may not be able to keep deferring it. In short:
  `WindowRun` is a log of runs that happened, **not** a window definition, so the change-window
  concept does need inventing; a change record cannot attest who made it, because every `/api/*`
  route shares one static bearer and `actor` is caller-declared free text; and **there is no
  chokepoint through which production deploys pass** — Coolify's `/deploy` is reachable four ways,
  one of them an ungated MCP tool — so this rule cannot be implemented as a gate and must be
  observe-and-report, the shape the landing ledger already proves in production. The decision
  stands unchanged; what changes is how it can be built.

  **INCREMENT 2 BUILT 2026-08-10, NOT YET MERGED** — the watcher. change-manager
  `adr0019-inc2-deploy-observation` (`15623fb`) gains an append-only `deploy_observations`
  table and `POST /api/items/{id}/deploy-observation`; the orchestrator repository gains
  `src/deploy_watcher/`, an out-of-process program in the `landing_ledger` shape that reads
  GitHub and appends one observation per rollout. **It reports and acts on nothing** — no
  revert, no tag re-point, no redeploy, and no state transition, so increment 1's executor
  guards needed no change and were re-proven by replaying the executor's exact calls against
  production. **The backfill answers the question nobody had: 67 merge-caused rollouts across
  both repositories, 1 failed (`brain` #1, its first merge). But only 3 of the 67 were verified
  to the standard these acceptance criteria assert** — 31 `change-manager` deploys proved only
  that a webhook returned 2xx, and every `brain` deploy proved only that a domain answered.
  **Adversarial review produced ELEVEN kills across two stages** — six at the design, four after
  implementation, and one from the advisor — and the two sharpest were measurements rather than
  arguments. A run-level conclusion cannot tell "the tests failed and nothing deployed" from
  "production is broken": of the **six failing rollout attempts** in estate history three never
  reached production, so acting on `failed` would have made the rollback the only mutation of the
  day (fixed with a second axis read from jobs and steps). And `liveness_confirmed` was not a rung
  at all, because Coolify's swap is a rolling update taking 43–73s while `brain` polls 30s in and
  breaks on the first 2xx, so the container that answered was the one already running. Also
  killed at design: freezing the merge commit, which was permanent unrepairable poisoning given
  that GitHub puts a test-merge commit on OPEN pull requests (PR #42 carries one now); and
  treating a failure during an overlapping run as real, when both repositories redeploy a floating
  tag with no concurrency group. Then after implementation: a replay that swallowed changed facts
  and re-froze a record at `unknown` — the same dead end one level down, firing on an ordinary
  event; an absent-job branch whose only real population was registry drift, answering "nothing
  was deployed" about a rollout that may have succeeded; a 301 that hid a merged deploying pull
  request at exit 0 forever; and a reduction that ignored which landing its rows described.
  **The mutation harness was itself found lying twice**, in the reassuring direction both times.
  **This document's premise that a deploy change "has no way to reach a terminal state" is
  wrong** — the decision lifecycle was never closed, only the execution one.
  Report: `~/docs/software-delivery-system/2026-08-10-adr0019-inc2-build-report.md`.

  **INCREMENT 3 DONE 2026-08-11** — orchestrator #159, merged `84308b4f`. **It deployed nothing**,
  as designed: only `Quality` fired, `Release image` is `workflow_dispatch`, so **production still
  runs the previous image and does not serve these terms yet.** Merged is not deployed, and the
  three new settings are deliberately unwritten in production, so the estate's behaviour is
  unchanged in both directions until somebody releases and configures. The factory lane honours
  the record and the window. `pr-merge`'s blanket `merge_target_repository_redeploys` refusal is replaced: the
  estate's `redeploys` answer no longer refuses on its own, it **routes** to eight refusal codes
  covering an approved change record for that pull request and the hours policy declares for
  changing something already serving. **Unconfigured refuses**, so the release needs no
  environment write and is inert until one is made. Proof, since nothing could merge: the routed
  terms run against the real change-manager, the real artifact and the real subject of each of the
  ten production units — **all ten refusals changed**, the six with a pull request reaching
  `change_record_absent`, with a discriminating control showing the same source answering
  `met=True` against production's one real record and `change_record_absent` under a wrong
  pipeline name (which is how a silently-broken lookup would look).

  **THE WINDOW FORK IS DECIDED, and the premise it was posed on was false.** The handoff said
  Increment 4's check "asks change-manager, which cannot read that artifact".
  **`GET /api/v1/factory-policy` already exists, is already deployed, and already serves
  `live_estate`'s window in full** — and the enforcing party is a GitHub Actions job, not
  change-manager. So: **the orchestrator is the sole holder, the only readable form is what the
  running process serves, nobody holds a copy, and the record does not carry the window.**
  Increment 4 asks two parties two questions. The recommended option — the record carrying the
  window — was a **fail-open**: it makes policy a caller-declared field at a service that cannot
  attest callers, it would have shipped with no reader, and its cited exemplar carries a *pointer*
  (a git blob sha) rather than the policy's content. A pinned transcription is unbuildable in the
  direction required: the pin would be over git bytes while the bytes in force are baked into the
  running image, and `orchestrator` is private where the exemplar's precondition is a public repo.

  **`change-manager` is NOT excluded from the factory lane**, and the handoff's reason for
  excluding it was measured false (the watcher is hourly and asynchronous; increment 2 classified
  change-manager's own shipping merge correctly). The replacement reason — the remedy lives in the
  system the change could break — fails four ways: the record is in a separate Postgres resource an
  image swap does not touch; `health_check_enabled: true` means a container that cannot answer
  never completes the swap and the old one keeps serving; enforcement reads the record *before* the
  merge; and the rule **covers `brain` too**, since `brain` serves the App Brain answer that routes
  a landing into this lane at all. Measured cost of excluding: **one** of the ten units would ever
  reach the new lookup, against **six** without. **The bootstrap question this document raises
  therefore remains open, named as open.**

  **Adversarial review ran as two numbered stages and killed both of the design's central
  decisions at stage one** — the fork and the exclusion — plus a fail-open window term, an
  unreachable refusal code, and a proposed widening of a scope guard. Stage two could not kill the
  implementation and found a **SEVERE** defect in the property the new module leads with:
  `UnicodeError` is a `ValueError` and escaped a reader promising never to raise, reaching a bare
  HTTP 500 — and **the mutation guarding that `except` was killed by a control sharing the same
  incomplete model of what httpx raises**, so a 22/22 pass proved only that the code matched the
  tests' model. Fixed here and in `estate_landing.py`, where the same escape existed.
  **28/28 mutations killed** on two consecutive baseline-verified passes plus a run against the
  committed tree; evidence retained.
  **Named escalation for Increment 4: change-manager's single `/api` bearer can create and approve
  the records this term reads, so the bound becomes a KILL the moment a producer ships holding it.**
  Report: `~/docs/software-delivery-system/2026-08-10-adr0019-inc3-build-report.md`.

  **INCREMENT 4 RE-SCOPED AND SHIPPED 2026-08-11 — and the re-scope is the finding.** Stage-one
  adversarial review killed the specified design on a measurement: **an auto-merge armed with
  `secrets.GITHUB_TOKEN`, which is what the estate's `dependabot-auto-merge.yml` uses, triggers no
  `on: push` workflow.** Verified 3/3 (`intent-packages` #50, `infraops-mcp-server` #70,
  `factory-runner` #42 — all merged by `github-actions[bot]`, all **zero** push runs) against a
  clean control (`intent-packages` #58, human identity, **2** push runs on the same workflows).
  **So for `change-manager` and `brain` the auto-merge lane does not deploy**, and this document's
  scoping premise — *merging to `main` IS deploying* — is true of those repositories and false of
  that lane. The specified increment would have gated an act that does not occur while creating the
  divergence this document itself warns about; for `brain`, `build-and-push` is `push`-gated, so the
  per-SHA image increment 1's rollback plan names would never be built. The lane was proven only in
  the five repositories where a missed push run is invisible, because none of them deploys.
  **Devon's decision: land these two through the ORCHESTRATOR** (which merges with the Dispatch App,
  the identity whose merges empirically *do* fire push workflows), gated by increment 3's terms —
  **in principle, but not in this increment; the landing path gets its own.**
  **What shipped: task zero and the producer.** change-manager #52 splits the single `/api` bearer
  into `read` / `propose` / `observe` scopes keyed on **(method, route template)**, closing the KILL
  condition increment 3 escalated — the producer cannot approve the records the admission term
  reads. orchestrator #160 adds `src/change_proposer/`, which proposes a record for every deploying
  merge waiting to happen, with acceptance criteria **transcribed** from what each rollout workflow
  actually attests and a refusal rather than a guess for bytes nobody classified.
  **No required status check, no branch-protection change, no auto-merge, nothing merged into either
  repository.** A required check was analysed and rejected, and Devon asked the reasoning be kept as
  the standing argument: it puts a six-component availability chain in front of **everyone**
  (`enforce_admins` is TRUE on both — this document's own portfolio invariant saying enforcement is
  on `factory-runner` alone is wrong), with no in-band recovery and a circular dependency on
  change-manager; orchestrator-lands puts that chain in front of **machines only**. Two concrete
  triggers made it real: ~8,640 billed Actions minutes/month against 3,000 included, and scheduled
  workflows auto-disabling after 60 days of repository inactivity, which freezes the last posted
  status forever.
  **Commit-status semantics were measured in a disposable repository and outlive the design**:
  `pending` blocks (405), `success` releases, **re-posting `pending` after `success` re-blocks**, a
  moved head carries zero statuses — and the fail-open was **reproduced deliberately**, a pull
  request merging seconds after an unrelated required context greened, hours after the window closed.
  **Still open: nothing can APPROVE these records.** Devon's ruling is that approval is *policy*,
  which needs a concrete versioned mechanism whose permission basis names the policy revision. Until
  it exists the producer's records sit `pending` and the factory lane refuses at
  `change_record_not_approved`. Also open: three unattended jobs still hold the full change-manager
  bearer, so the property established is *the producer cannot approve*, not *no machine can*.
  **Stage-two review then found the shipped producer's own headline property decorative**: `propose`
  called the transport directly, so the in-process path guard had NO production write caller — the
  only route into it was a GET. Fixed, and hand-verified rather than trusted to the mutation harness,
  which reported that very mutation as a no-op while a hand-check fails eleven tests (the third time
  a harness has lied in this workstream, in the reassuring direction each time). Also fixed: the
  producer FROZE THE PULL REQUEST TITLE into a write-once record, and Dependabot retitles in place —
  measured, 4 of 19 recent bot pull requests — so every later pass would have 409'd forever on a
  record permanently naming the OLD version; a dead read surface with three surviving mutations,
  deleted; an uncaught `ReadError` that killed the whole scheduled pass; and a case-sensitive
  rollback lookup in a module that folds case one line earlier.
  **A false claim in the producer's own docstring is corrected rather than quietly amended**: it said
  increment 3's factory-lane term was its consumer. It is not — that term reads the pull request
  *factory-runner* opened, authored by a USER account, which the producer's bot filter refuses. The
  producer serves the DEPENDABOT population, read today by increment 2's watcher. **Named and NOT
  fixed:** acceptance criteria derive from the rollout workflow at `main` HEAD, so a single workflow
  merge permanently conflicts every pending record at once — reproduced. That drift is semantically
  correct, which is the argument that a write-once record is the wrong shape for a derived field, and
  the remedy belongs to change-manager rather than to a line here.
  Report: `~/docs/software-delivery-system/2026-08-11-adr0019-inc4-build-report.md`.

  **INCREMENT 5a BUILT 2026-08-11 — approval by policy. 5b SPECIFIED AND NOT SHIPPED, because
  stage-one review exercised its authority to stop it.** change-manager PR #53 and orchestrator
  PR #161. **A deploying-merge record is now approved by the SERVER, inside the proposal
  transaction, when its shape conforms to a pinned versioned policy — and `approve` is refused to
  every caller including the FULL bearer, at the route, at the GUI, and at `transitions.decide`
  where all six callers reach it.** That is wider than increment 4's split, which made only the
  producer unable to approve.
  **The fork was decided against my own recommendation, and the deciding fact is one nobody had
  named.** I proposed deriving approval on read; two reviewers disagreed with each other, so it was
  settled on evidence. `_GRANT_TYPES = {"approved"}` in security-standards' change-manager adapter
  means **change-manager's `approved` EVENT is the only thing this system emits that becomes an
  `authority_grant` in the tamper-evident factory-events chain** — so a derived status would have
  left the single authorization permitting an autonomous production deploy as the one decision
  absent from the chain, while a trivial drift approval still entered it. Two more, each
  independently sufficient: the listing applies `status` as a **SQL predicate on the stored
  column**, so a derived answer selects the wrong rows in both directions (**both reviewers found
  this independently**); and `landing_ledger/rules.py` is re-evaluable only because **the subject
  stores the revision it was decided under**, which the derived design had dropped while citing it
  as precedent. The approver-credential case lost on its own merits and separately — the holder
  would be a second unattended job running the identical predicate, and `actor` on a decision is
  caller-declared free text, which is why production item 44 says `hq-correction`.
  **Two live defects were fixed on the way.** `web.py` called `hand_off` with **no executor guard**,
  unlike its API twin — the button was merely hidden, and a hidden button is not a closed door. And
  the derived fields (`acceptance_criteria`, `rollback_plan`) conflicted on re-proposal, so a single
  rollout-workflow merge would have **permanently bricked every waiting record**: write-once row, no
  supersede route, identity held. They now refresh with an event and re-run the policy, revoking an
  approval whose criteria moved out from under it — which closes increment 4's named-and-unfixed
  SEVERE-1b.
  **Devon's rulings:** policy v1 pins **`change-manager` alone** (*"landing unattended under
  criteria that are documented as unable to detect the failure isn't accepting that risk, it's
  knowingly defeating it"*), **patch and minor only** — stricter than ADR-0018's cascade, because
  that cascade's premise that the gating check IS the thing being bumped fails on a repository
  whose rollout job never runs on a pull request — and **freshness as a versioned policy condition
  rather than `strict: true`**. His correction to the handoff's framing is recorded: the rollout
  watcher reads the same job conclusion, so for `brain` **there is no second net**.
  **Why 5b did not ship.** Review found five terms missing, and the sharpest is that `strict: false`
  means a required check can be green against a **stale head**, so a squash merge produces a tree no
  CI has ever executed — which on these repositories *is* the deploy. As specified it would have
  landed five `brain` pull requests whose checks ran 12–19 days ago onto a base 11 commits ahead.
  Deeper still: **not one policy term is a function of the CHANGE** — `change_class` and `risk` are
  literals the producer writes about every pull request it sees — so every change-specific fact must
  be a landing term. It is also blocked regardless: landing needs an approved record, no record can
  exist until the producer runs, and the producer needs a propose-scoped credential only Devon can
  mint. **Exit criterion 3 was unsatisfiable as written** — all six `change_record_absent` units bind
  merged, User-authored pull requests, and proposing records for them would manufacture a record
  after the fact.
  Report: `~/docs/software-delivery-system/2026-08-11-adr0019-inc5-build-report.md`.

  **INCREMENT 5b DONE 2026-08-12 — the landing path, and the last piece of this decision.**
  change-manager #54 (`e81eb62f`, which deployed itself — rollout green through *Verify the new
  revision is live*, production serving policy **v2** with the rollout pin) and orchestrator #162
  (`99920d49`, **which deployed nothing**: `Release image` is `workflow_dispatch`, and
  `sds.alobar.net/openapi.json` carries no `estate-pr-merge` route, asked rather than inferred).
  **Nothing has been landed.** The lane stays inert until an image is released and
  `ORCHESTRATOR_ESTATE_LANDING_ENABLED` is written — two acts in two systems, either alone
  changing nothing.
  **ITEM 44 IS RETIRED**, in production, by the producer's new sweep: `resolved`, on the fact that
  #42 closed unmerged. Its chain reads *proposed → approved by `devon.watkins@gmail.com` → knocked
  over by a probe → restored → retired by `change-proposer`*, which is the whole increment in five
  lines. **And that chain corrects 5a**: it closed approval to every caller partly on the grounds
  that `actor` is caller-declared free text, citing item 44's `hq-correction` — but item 44's FIRST
  approval carries Devon's SSO email, because change-manager's GUI reads the forward-auth header.
  The service can attest a human, and did; the free-text value is the later repair.
  **The re-approval fix is proven live**: items 50–53 advanced v1 → v2 on the first producer pass
  after the deploy, while item 44 held at `NULL` with the human's name intact.
  **Stage three passed on the deployed build** — `claim`/`outcome`/`handoff` and `approve` all 409
  to the FULL bearer. `POST /api/v1/estate-pr-merge` lands a pull request that has **no work
  unit** into a repository where landing changes something already serving, with the Dispatch App.
  What authorises it is a change record approved by conformance to a policy version a human
  pinned, **re-checked against the version in force at the moment of the act** — the only
  mechanism by which a narrowing binds an approval that already exists, since a stored record is
  re-evaluated by nothing else once its pull request has closed. Every change-specific question is
  a term there, against GitHub: the update bot's own identity (never `type == "Bot"`, which admits
  every GitHub App including this estate's), a head current with its base, a permitted delta parsed
  from the TITLE at the act, the rollout workflow still being the pinned bytes at **both** the base
  and the head, and one landing per repository per window. `ORCHESTRATOR_ESTATE_LANDING_ENABLED`
  defaults false and unconfigured refuses; the sibling path's no-off-switch ruling named a
  scheduled caller as the thing that would void it, and this is that caller.
  **THE FIRST PASS LANDS NOTHING, MEASURED AGAINST LIVE GITHUB, AND THAT IS THE POINT.** A
  composition drill — the real producer, a real change-manager on the branch, live reads — returned
  **10 records, 10 held, 0 admitted**: all four approved pull requests are two commits behind their
  base *while answering `mergeable_state: clean`*, so a squash of any of them produces a tree no
  check has executed, and on this repository that tree is what starts serving.
  **The producer became a reconciler** and retired item 44's exact shape on its first sweep, on the
  fact that #42 closed unmerged rather than on its absence.
  **ADVERSARIAL REVIEW RETURNED ONE KILL, AND IT WAS ABOUT THE ARTIFACT NOBODY REVIEWS.** Two
  defects in the launcher meant the committed LaunchAgent had never been capable of a single pass:
  `sds-token.sh` EXPORTS and prints nothing, so command-substituting it yields the empty string
  under launchd; and **no single BWS identity can read both credentials** — measured 2×2 with
  controls — while the script's own comment asserted the opposite. Neither is visible from the
  code. Fixed and proven under `env -i`. Also SEVERE: the rollout pin was read at the BASE only,
  which a pull request editing the rollout workflow passes *by construction* — the exact state the
  pin was added to prevent, reachable through the pin itself. And both reviewers independently
  found that the re-approval branch treated an **absent** policy version as an old one, which would
  have restamped a human's approval as the policy's and overwritten their name in the chain.
  **A defect found by tracing the composition rather than either half:** policy v2 is a narrowing,
  and `_apply_policy` had no branch for a record that still conforms under a newer version — so all
  four waiting records would have been stranded on v1 and permanently unlandable, which is
  increment 5a's SEVERE-1b one field over.
  37/37 mutations killed on the final tree; eight had to be earned, including the advisory lock's
  **call site**, whose deletion left every test green while the helper's own test passed.
  **A measurement worth carrying beyond this increment:** on a retitled-in-place pull request,
  Dependabot's own machine-readable `dependency-version` trailer is **stale** while the diff it
  describes has moved on — so the title ruling is right, and ADR-0018's cascade classifies via a
  field that can be wrong.
  Report: `~/docs/software-delivery-system/2026-08-12-adr0019-inc5b-build-report.md`.

  **SCOPE SETTLED 2026-08-10 (Devon).** This rule's first implementation is **SDS-initiated
  merges into repositories where merging to `main` IS deploying** — `change-manager` and `brain`.
  How the orchestrator itself gets deployed is deliberately parked. ADR-0020 already gives the
  estate an audited merge for repositories that do not auto-deploy; this picks up the ones that
  do. Two lanes reach it — the factory's `pr-merge` and the Dependabot auto-merge gate — and
  Devon's decision is to **teach the gate workflow to consult change-manager** rather than close
  that lane for those repos. Plan:
  `~/docs/software-delivery-system/2026-08-10-adr0019-implementation-plan.md`.
- **Date:** 2026-08-08
- **Decided by:** Devon
- **Relates to:** ADR-0012 (change windows), ADR-0016 (native auto-merge), ADR-0009 (`reach`),
  ADR-0015 and ADR-0018 (the self-reference pattern)

## Decision

> **"All deploys to production systems, that are initiated by SDS, will need to go through
> change-manager so we can capture change acceptance criteria, change window, and a roll back
> plan if the acceptance criteria, or other related criteria to be defined in change manager,
> are not met."** — Devon, 2026-08-08

Three things the record must carry, and the rollback plan is **the remedy attached to the
acceptance criteria**, not a free-standing field: it is what happens when the criteria, or other
criteria change-manager comes to own, are not met.

## Context

The question that produced it was narrower — *what do we do if a Dependabot change breaks a
running application?* Measuring the estate showed that today it **cannot happen**, and the reason
is a scoping rule rather than a control: the five repositories with auto-merge have no deploy
workflow, and the two repositories where merging deploys (`change-manager`, `brain`) have no
auto-merge. The sets are disjoint by ADR-0016's design.

But the absence of a path is not a rollback story, and it will not survive `change-manager` and
`brain` joining the lane. More immediately, it was never the whole exposure: **the orchestrator
was deployed three times on 2026-08-08 — an image swap, a migration, two environment writes and
three restarts — with no change record anywhere.** Those were operator-agent deploys of a
production system, and nothing captured a window, criteria or a rollback.

## Why "initiated by SDS" is the right boundary

It scopes the rule by **causation, not by repository**, which is what makes it implementable and
what resolves the bootstrap problem.

- An orchestrator-dispatched unit that deploys → in scope.
- An agent operating the SDS performing a production deploy → **in scope, and this is the rule's
  first real subject.** The factory itself has never deployed anything; no authority envelope
  grants a deploy capability. Every SDS-initiated production deploy to date has been performed by
  an operator agent, by hand.
- A Dependabot auto-merge that triggers a deploy, once `change-manager` or `brain` joins the lane
  → in scope, because the auto-merge lane is SDS machinery.
- A human merging a pull request, or clicking deploy in Coolify → **out of scope.**

That last exclusion is what defuses the self-reference. `change-manager` deploys itself on merge
to `main`, and a change record cannot gate the deploy of the system holding the records. Because
those deploys are CI-initiated rather than SDS-initiated, they fall outside the rule by
construction rather than by exemption. Should the SDS ever initiate a `change-manager` deploy,
that is the one genuine bootstrap case and needs deciding then — the estate's third instance of
this pattern, after `factory-runner` not being its own factory target (ADR-0015) and
`orchestrator` being unable to host its own auto-merge workflow (ADR-0016).

## What change-manager already has, measured 2026-08-08

**Present.** `WindowRun` is a first-class model with `POST /window-runs` and
`PATCH /window-runs/{id}`, so the change-window concept exists rather than needing invention.
`ChangeItem` carries `risk`, `plan`, `lane`, `urgent`, a decision lifecycle
(`approve` / `defer` / `wontfix` / `resolve` / `reactivate`) and an execution lifecycle
(`claim` → `outcome`). The `change-window-agent` actor is already in the registry, described as
executing Devon-approved Coolify changes in the 4AM window.

**Missing — the ingress.** Items are created only by `POST /sync`, and every field is
drift-shaped: `instance`, `rule_key`, `provider`, `resource_type`, `resource_uuid`,
`resource_name`. change-manager *derives* changes by scanning infrastructure; nothing can
*propose* a change to it. A deploy is not drift, so this rule requires an ingress that does not
exist.

**Missing — two of the three fields.** There is `plan` (an unstructured JSON blob) and `risk`,
but no acceptance criteria and no rollback plan as distinct fields. Held inside `plan`, nothing
can enforce that a deploy change *has* a rollback plan before it executes — which is the point of
recording it. They want to be real fields with a refusal attached.

## What is derivable rather than authored

This matters because a rule requiring three hand-written fields is incompatible with anything
unattended. For a deploy, all three are largely computable:

- **Window** — from the target's `reach` classification. `live_estate` already declares
  02:00–06:00 in `factory-policy.toml`, and that artifact is the single source of truth; the
  window must be read from it, never restated.
- **Acceptance criteria** — from what the post-deploy checks already verify. `change-manager`
  polls until the deployed build reports the commit just pushed; `brain` polls `/api/health` on
  all four apps. Those *are* the criteria, already executable.
- **Rollback plan** — from the image tagging. Both repositories push a per-SHA tag alongside the
  moving one, and both Coolify apps run the moving tag, so the plan is "re-point the tag at the
  previous `:<sha>` and redeploy" followed by a revert.

The rule for rollback should be that the fast path (re-point) is **always followed by a revert**.
Otherwise `main` and production disagree silently and the next unrelated merge re-deploys the
broken code.

## Consequences

- **The immediate behavioural change is to operator agents, not to the factory.** The next
  orchestrator deploy is in scope, and there is currently nowhere to record it.
- Nothing in the estate is blocked by this today; it is a prerequisite for `change-manager` and
  `brain` joining the auto-merge lane, and a correction to how operator deploys already happen.
- `brain` builds from `requirements.txt` with no lockfile, so its image resolves dependencies at
  build time. **The rollback target there is the image, not the commit** — rebuilding the same
  commit can produce a different dependency set. That distinction does not apply to the uv-locked
  repositories and should be explicit in any generated rollback plan.

## Deliberately not decided

Whether change-manager's additional criteria ("other related criteria to be defined in change
manager") include anything beyond acceptance criteria, window and rollback. Whether a failed
acceptance criterion triggers the rollback automatically or reports and waits — the estate's
established shape is that detectors report and never act, and a deploy rollback is a mutation,
so the default should be reporting until decided otherwise. And whether non-production deploys
acquire a lighter version of the same record.
