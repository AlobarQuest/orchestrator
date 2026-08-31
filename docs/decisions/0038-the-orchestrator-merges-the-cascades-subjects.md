# ADR-0038 — The orchestrator merges the cascade's subjects

- **Status:** Accepted
- **Date:** 2026-08-31
- **Decided by:** Devon
- **Relates to:** ADR-0018 (the gate is a cascade, not a disjunction), ADR-0019 (SDS-initiated
  production deploys route through change-manager), ADR-0020 (the factory closes its own loop),
  ADR-0026 (the signal to work record), ADR-0034 (the cascade and the factory split on outcome),
  ADR-0035 (**superseded, not taken**), ADR-0036 (the landing lane admits a bump that states no
  delta), ADR-0037 (a landing is attributed to the rule that armed it)

## Decision

**The GitHub-native Dependabot auto-merge cascade is removed from all six repositories that carry
it, and the orchestrator becomes the merger for its subjects.** Devon, 2026-08-30: *"I don't
understand why we are still creating identities to arm a gate."* The Dispatch App is already
installed account-wide with `contents: write` and already merges in `pr_merge.py` and
`estate_pr_merge.py`; an arming identity is needed only while the cascade WORKFLOW is the merger.

Six parts.

**1. The rule moves to change-manager's deploy policy, as a declaration beside the deploying one.**
A new field on `DeployPolicy` names the population where landing on the default branch is inert and
the conditions on landing there. `repositories`, `change_classes`, `acceptance_criteria`,
`rollback_plans` and `objections()` are **untouched** — they are the deploying lane's and they stay
that.

**Devon ruled the naming, 2026-08-31, and it is recorded in words because "option 1" pointed at two
different numbered lists in the conversation that produced this: the inert block joins the existing
deploy policy under its current name and its current route, and the document is renamed later if the
name starts misleading people.** The two alternatives are recorded under *Rejected* below.

**2. A distinct admission lane in the orchestrator for inert-repository update-bot pull requests.**
No change record, no change window, no pace. It shares the record-independent terms with the
existing lane and applies neither the record terms nor the rollout-pin terms.

**3. The landing ledger gains a fourth attribution basis, and Detector A an arm for it.** The lane
stamps a trailer into the squash body naming the policy version that permitted the landing, exactly
as `estate_pr_merge` already stamps `SDS-Change-Record` and `SDS-Policy-Version`; the ledger reads it
back out of the commit. **Detector B is taught the policy rule or retired** — see Consequences.

**4. `bump_proposer` reads the rule from change-manager instead of from `landing_ledger.rules`.**
Both isolation guards are untouched. It gains a READ-scoped change-manager credential so a dry run
still does not hold the credential that could write.

**5. `rules.py` stops growing and survives as historical attribution** for the 40 rule-attributed
landings already stored. It is not deleted.

**6. The workflows are removed and every leftover auto-merge arm is disarmed, in one operation,
after parts 1–4 are live.**

## Why the rule goes to change-manager, and not to any of the three obvious places

`bump_proposer`'s whole subject is *"a bump the cascade refuses becomes factory work"*, and it learns
what the cascade refuses by importing `landing_ledger.rules` — a transcription — **because
`test_the_producer_cannot_reach_the_orchestrators_api_at_all` forbids the strings `/api/v1/`,
`sds.alobar.net` and `package-intakes` in that package.** Remove the cascade and it returns
`no-cascade`, which is in `FINDING_STATUSES`: exit 3 permanently, against three live standing
packages. So "where does the rule live" is not a tidiness question; it is what makes the removal
shippable.

**change-manager's policy module was built for this and says so.** On `LandingConditions`:
*"Served on `GET /api/deploy-policy` so the orchestrator reads them rather than holding a second
copy — one holder, one reader. Increment 3 established that a policy value copied into a second
service is a fail-open."* And on the division of labour: *"This service has no GitHub egress and
cannot attest a caller … Facts about the change itself are therefore NOT decided here; they are
declared in `LANDING_CONDITIONS` and enforced by the party that can read GitHub, at the moment of the
act."* That is exactly the split this lane needs — change-manager declares, the orchestrator
enforces because it can read GitHub, and the proposer reads the same declaration to learn what would
be refused. Three readers, one holder.

**The proposer can already read it.** `GET /api/deploy-policy` returns **200** for the propose-scoped
credential its launcher already fetches; a garbage bearer on the same path returns **401**, so the
read is authorized rather than open. A `READ` scope already exists in `app/scopes.py` and already
covers that route, so part 4 costs a BWS secret and an env var, not a change to change-manager's
auth.

### Rejected, with the reason each fails

- **Narrow the producer's API ban to writes**, so it GETs the orchestrator's admission answer. The
  ban IS broader than its stated reason — ADR-0026 §5 declines to decide *"a machine approving its
  own proposal"*, which is a write that creates canonical work, and a GET is not that. It was
  rejected on cost, not on principle: the orchestrator bearer lives in the narrow `SDS Operator` BWS
  project while that launcher bootstraps with the broad identity, which is the documented
  two-identity trap that fails as a bare `HTTP 400` naming nothing; and it makes routine dependency
  hygiene depend on `sds.alobar.net` in a second place. **This is the fallback** if the policy
  document turns out not to carry two populations.
- **A shared rule module outside both packages.** A new top-level package, both isolation allowlists
  changed, and a third artifact to keep in sync with a policy document that already states this rule.
- **Reverse it — the orchestrator proposes the work record itself.** Measured: the orchestrator's
  change-manager client is read-only (`client.get` only), so this needs a new outbound write, a
  propose-scoped credential and an outbound-allowlist entry. It also puts refusing, proposing and
  dispatching in one process, moving toward ADR-0026's undecided question rather than away from it.
- **Widen the deploy policy's `repositories` set** — the shape this ADR first proposed. Measured
  in-process against the live policy, with both controls: a repository outside the set answers
  `('repository_not_in_policy',)`; one the policy covers answers
  `('acceptance_criteria_not_ratified', 'rollback_plan_not_ratified')`; and **the set widened with no
  criteria and no rollback plan raises `KeyError`.** `objections()` indexes
  `acceptance_criteria[key]` and `rollback_plans[key]` unguarded after the membership check, and it
  runs on the item-listing route for every record — so widening that set does not over-grant, it
  crashes the listing. Avoiding the crash means inventing acceptance criteria and a production
  rollback plan for repositories that have no rollout and nothing serving to roll back.
- **A separate sibling document and route.** Cleanest naming, and it reintroduces the second holder
  of one rule that the module's own docstring exists to prevent.

## The lane, and the two design choices inside it that carry reasoning

**Population is declared by policy and confirmed by the estate, and the disagreement refuses.** The
policy names which repositories a human admitted; App Brain says whether landing there is still
inert; a mismatch is a refusal. This is the existing lane's arrangement inverted — `_estate_term`
already passes only on an explicit `redeploys` and its docstring says the work-unit landing's term
*"exists only for the ones where it does not"* — so it fails closed in both directions: a repository
that quietly starts redeploying stops being landable by this lane rather than being landed wrongly.

**It requires freshness and imposes no pace, and freshness is what serialises it.** Requiring
`require_head_current_with_base` is a tightening over the cascade, which required nothing (branch
protection is `strict: false` estate-wide, deliberately). It is warranted here for a reason that is
about `main` rather than about production: a squash of a behind head produces a tree nothing
executed, and `main` is what every build session branches from — and, after this change, what
`main`-push CI now runs on. Given freshness, a landing stales its siblings, so at most one pull
request per repository is landable per pass and the rest are freshened for the next one. **A pace
rule would be a second mechanism producing an effect the first already produces.** Recorded because
the deploying lane DOES carry one, and the difference is not an oversight: there, pace bounds how
often something already serving may change, which is a fact about production and not about staleness.

## What is given up, and what is gained

**Given up: the cascade is GitHub-native and lands even if the orchestrator is down.** Routine
dependency hygiene now depends on `sds.alobar.net`. Devon accepted this explicitly when ruling the
direction.

**Gained, and measured rather than argued: `main`-push CI switches on for all six.** Across the
estate's merge history — 38 cascade merges by `app/github-actions` with **zero** `push` runs, against
18 merges by the Dispatch App with **18**. The table returns both answers from one query shape, so
the zero is not a broken filter. The six daily scheduled `main` verification runs added 2026-08-15
exist only because that CI is currently skipped; they become redundant. **Keep them until the lane
has run for a few weeks** — retiring them is a separate decision, not a consequence of this one.

## Consequences

- **Removal is SILENT unless part 3 ships first, and that is the sharpest hazard here.** With no gate
  run at the head, `rule is None`; the merger is a machine so the landing is not `human`; there is no
  claim and no change record; the basis falls to `unattributed`. `audit_landing` returns `(), (), ()`
  for any basis but `auto_merge_rule`, so **Detector A stops auditing the native lane with its
  `permitted` denominator at zero, and nothing says so.** That is the ADR-0035 hazard shape reached by
  a different route, and it is why the ordering below is not a preference.
- **Detector B emits a caveat that becomes false.** `CAVEAT_NO_RULE_INSTALLED` reports how many open
  updates "will not land unattended" for a repository with no installed rule — true today, false the
  moment the lane exists. Teach it the policy rule or retire it explicitly. Leaving it is the quiet
  twin of the Detector A gap.
- **Leftover arms survive workflow removal, and two are live right now.** The arm lives on the pull
  request, not in the workflow file. Measured 2026-08-31: `factory-runner#28` and
  `infraops-mcp-server#71` each carry an auto-merge armed by `app/github-actions`, both `BLOCKED`;
  `orchestrator#3` and `factory-runner#31` carry none. If those checks later go green after the
  workflows are gone, GitHub merges them through the leftover `GITHUB_TOKEN` arm — no push CI, no gate
  blob at the landing commit, and a landing recorded `unattributed` on day one. **Disarming every open
  arm is part of the removal operation, not a follow-up.**
- **This supersedes backlog item `a0dd438`** (record the arm outcome rather than only acting on it).
  No future landing has an arm outcome to record. It must also not ship first: adding `arm_outcome` to
  `permitted_by` moves the fact digest for every landing in the ledger's rolling window, after which
  retiring the registry becomes digest-moving too. Today it moves nothing.
- **ADR-0037 is not reversed.** Its mechanism stays, and stays load-bearing, for the 40 landings
  already attributed to a rule revision. What changes is that no new landing acquires that basis.
- **`factory-runner` must be assessed before the lane can admit it.** It answers `unknown` /
  `no_app_record`, which the estate term refuses. The determination must read all three mechanisms —
  every workflow, the repository's webhooks, and the hosting platform's own git integration — because
  checking one surface fails closed in one direction and fail-OPEN in the other.
- **The proposer's exit-code vocabulary changes.** `no-cascade` and `gate-not-transcribed` retire,
  and `scripts/run-bump-proposer.sh`'s header is part of the change: the seven launchers do not share
  an exit-code vocabulary, and this one's header is the whole of what a scheduled run reports.
- **Ordering is ACTIVATION order, not merge order.** The ledger and the proposer run from the main
  tree's working copy on Devon's machine, so merging changes nothing there until it is pulled. A
  landing recorded `unattributed` before that pull is content-addressed and frozen forever. So: parts
  1–4 merged **and pulled and active on this machine**, then part 6. The switch flips last so the two
  mergers never race.

## What this deliberately does not do

It does not change what the rule IS — ADR-0034 decided that, and the policy's own version-5
rationale already records that both lanes gate on the required checks passing. It does not touch the
deploying lane, whose population, criteria, rollback plans and pace are unchanged. And it does not
retire the six scheduled `main` verification runs, which is a decision to take once the gained CI has
been observed rather than predicted.
