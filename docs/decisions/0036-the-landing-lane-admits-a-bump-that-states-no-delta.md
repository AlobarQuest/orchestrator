# ADR-0036 — The landing lane admits a bump that states no delta, when its checks pass

- **Status:** Accepted (widened 2026-08-30 — see "The question this ADR opened, and its answer")
- **Date:** 2026-08-30
- **Decided by:** Devon
- **Relates to:** ADR-0019 (SDS-initiated deploys route through change-manager), ADR-0034 (the
  cascade and the factory split on outcome, not update type), ADR-0016

## Decision

**A Dependabot pull request may be landed by the estate-landing lane when its required checks
pass — whatever its version delta, or its absence of one.** Deploy policy gains **version 5**
expressing that grant, and the orchestrator's `_update_type_term` stops refusing on the delta
itself.

This is ADR-0034's rule applied to the deploying half of the estate: the outcome decides, with one
principled exclusion. Both mechanisms already gate on the required checks passing; the update-type
condition sat on top of that gate and said nothing about whether the bump works.

**The exclusion carries over unchanged, and is the same principle ADR-0034 kept: exclude where the
required checks do not exercise what changed.** On a deploying repository the rollout job runs on a
push to `main` and never on a pull request, so a bump reaching it would first be exercised during
the very rollout it is meant to gate. Its current instantiation is the workflow-automation
ecosystem, which is what V1 named and what the visibly-skipped `build-and-push` and `deploy` jobs
demonstrate on every pull request.

## The population, measured 2026-08-30

Five green pull requests, stuck: `change-manager#67` (uvicorn) and `#62` (setuptools);
`brain#55` (pydantic-settings), `#53` (greenlet) and `#52` (fastmcp). All requirement ranges, all
checks passing, none touching a rollout workflow.

Read from the production lane's own 05:15 pass rather than from a daytime dry run, **every other
admission term already passes on all five** — records exist, are approved, carry a current policy
version, have no live objections, checks are clean, and both rollout pins match live `main`. Of
thirty-one refusal codes the lane can emit, the substantive one here is
`landing_update_type_unparseable`. (`landing_head_not_current_with_base` also appears on four and
is self-clearing: the lane deliberately declines to freshen a pull request carrying a permanent
exception.)

## Why this is not a `deploy_policy.py` value change

**Relaxing `update_types` alone cannot land any of them, and this is the fact that shapes the
work.** `_update_type_term` (`estate_landing_admission.py:902`) is:

```python
kind = update_type_of(pull.title)
if kind is None:                          return _Term(False, (LANDING_UPDATE_TYPE_UNPARSEABLE,))
if kind not in conditions.update_types:   return _Term(False, (LANDING_UPDATE_TYPE_NOT_PERMITTED,))
```

`None` returns **before** the membership test and consults no policy value. So adding members to
`update_types` is inert for a requirement range. This is a two-repository change to admission
logic, not a version bump — HQ first specced it as the latter and was wrong.

## What the backlog item asked for, and why it is retired rather than done

`PROJECT.md`'s 2026-08-07 P1 proposed arming GitHub auto-merge in these two repositories, hooking
it to the change window, and actively disarming at window close, with a design fork over which
credential could arm in each repo.

**That mechanism was never built and the hazard it guards against does not exist here.** The lane
calls `PUT /pulls/{n}/merge` synchronously (`estate_pr_merge.py:567`), inside the transaction that
evaluated the window — there is nothing standing to fire at 06:30 and nothing to disarm. Neither
repository carries a cascade. Its one live concern is already honoured: the window is read from
`factory-policy.toml`'s `live_estate` block, not re-typed into a cron. And its closing "also
consider whether a windowed merge should raise a change record" is precisely what ADR-0019 built.

The item is superseded by this ADR. `factory-runner#28` — armed 2026-08-07 by `app/github-actions`
and still armed 23 days later — is the live instance of the mechanic it describes, in the other
lane, where arming-and-waiting is the design and there is no window to be outside of.

## The question this ADR opened, and its answer

A first draft admitted a bump stating *no* delta while still refusing a `semver-major` that states
one — treating the less classifiable change more permissively than the more classifiable one. That
incoherence was named rather than shipped, and Devon resolved it on 2026-08-30 by widening to the
outcome rule. Four grounds, measured:

- **The unexercised jobs are visible on the subject itself.** On `brain`, every pull request's own
  check list shows `build-and-push=skipped` and `deploy=skipped` beside `test=success` and
  `Lint, type-check, and test=success`. So "the required checks did not exercise this" is an
  observation about each pull request, not an inference from an ecosystem name — a firmer footing
  than ADR-0034's docker exclusion, which rests on knowing the image is built and never run.
- **The tests genuinely run.** Two passing test jobs on every `brain` pull request, one on every
  `change-manager` one. This also closes a 2026-08-07 backlog worry that `brain`'s pull requests
  reporting SKIPPED beside SUCCESS meant its gate was attesting rather than executing; the skips are
  the deploy jobs correctly not running on a pull request.
- **There is a post-landing net here that the inert repositories lack.** This policy pins acceptance
  criteria requiring production to report the merged commit, and a rollback plan; the deploy watcher
  observes the rollout hourly. On an inert repository a bad landing sits until somebody looks.
- **Two rules across two lanes is a second vocabulary that must agree with the first and is checked
  by nothing** — the defect this estate repeats more than any other.

**The residual, accepted knowingly:** a `semver-major` whose tests pass but which breaks at runtime
in a way those tests do not cover would reach production. It is bounded by one landing per
repository per occurrence of the window, a pinned rollback plan, and the watcher — and it is the
same exposure already accepted for patch and minor, at a larger blast radius.

## Consequences

- **Two repositories, one operation.** The orchestrator's admission term and change-manager's
  version 5 must ship together; either alone leaves the five refused.
- **A version bump refuses every currently-approved record until it is re-approved.**
  `_record_term` refuses `landing_policy_version_superseded` when a record's stored version is not
  the one in force. For an open pull request the producer re-approves a still-conforming record on
  its next pass, so the binding lasts about an hour and lifts without anyone looking; a *narrowing*
  the record no longer conforms to is fully bound. This is a widening, so expect the former.
- **Version 5 declares its own conditions verbatim.** The module's editing contract retains every
  prior version and forbids editing one in place, so V5 restates its criteria, rollback plans and
  rollout pins even where the values are identical to V4's.
- **Two tests hard-pin the number** (`test_deploy_policy.py:397` and `:480`), and both must move.
- **This removes one of two guards keeping factory-authored pull requests out of this lane.** V4's
  own rationale says `update_types` is what keeps them out, and calls that *"a lane separation that
  depends on how somebody happened to word a title is not a separation."* The other guard is
  `estate_lander`'s `_LANE_CLASSES`, which selects on change class and is unaffected — so the door
  stays shut, but on one belt rather than two. V4 named this as the condition of its own grant.
- **The stuck queue is invisible in the exit code and stays that way.** `landing_update_type_unparseable`
  is in the lander's `_EXCEPTION` set, so all five report `exception` and the nightly control has
  exited 0 every night while holding them — correct under Devon's 2026-08-13 ruling that a record
  unlandable under current policy is an exception rather than a finding. This ADR empties the
  current queue; it does not make the next one visible. That is a separate question.
- **`brain`'s acceptance criteria are transcribed in two repositories** — change-manager's policy
  and the orchestrator's `deploy_watcher/workflows.py` — and compared literally. This change touches
  neither, and must not.
