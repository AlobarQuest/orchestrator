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
