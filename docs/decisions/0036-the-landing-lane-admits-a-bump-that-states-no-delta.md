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

## How it was built, and three things the spec had wrong

Recorded by the build session, 2026-08-30. The decision above is unchanged; what follows is what
implementing it established.

### The orchestrator half needs a DEPLOY, and the brief said `git pull`

`estate_lander` composes nothing — its own docstring says *"Every term lives inside the
orchestrator"* — and `estate_lander/orchestrator_client.py` calls
`https://sds.alobar.net/api/v1/estate-pr-merge-admission`. So the admission terms run in the
**deployed image**, and `git pull` in the checkout activates only the relay, which this change does
not touch. The failure shape of getting this wrong is quiet: an inert orchestrator leaves the five
refused on `landing_update_type_unparseable`, which is in the lander's `_EXCEPTION` set, so the
nightly control exits 0 and reports nothing. That is the 2026-08-16 episode, in this same lane.

**Verify the deploy by asking production what it SERVES**, not by health or digest — neither can
see a missing field.

### The two repositories must ship in one ORDER, and it is not "close together"

`change-manager` redeploys on merge, so merging it IS serving version 5. The order is: merge the
orchestrator, build and deploy it, verify, and only then merge change-manager. The reverse leaves
the previous image reading a shape it may not understand, and this version is built so that even
that is survivable — see the floor below — but the ordering is what makes it a non-event rather
than a bounded incident.

### The grant is carried by ONE new served field, and the old one stays

`LandingConditions` gains `excluded_ecosystems`, and its PRESENCE is the grant: a version that
serves it decides on the outcome, one that does not decides by update type. That is why the
orchestrator's two update-type refusals are not dead code — every retained policy version still
declares that rule, and the reader is served those shapes whenever it runs ahead of the party
holding the policy, and after any rollback of it.

`update_types` is still served under version 5, as an **empty list**. Dropping it would make the
previous build unable to parse the conditions at all (`_conditions` answers `None`, which becomes
`landing_conditions_unreadable` and refuses every record in both repositories); keeping it
well-typed and empty keeps the shape readable and permits nothing under it, which is the right
answer for a reader that cannot see this version's rule. It is a floor for that reader, not a
statement that version 5 permits no delta.

### The exclusion is provable only synthetically, and that is a fact about the population

Neither repository declares the `github_actions` ecosystem in `.github/dependabot.yml` — both
configs say *"github-actions is intentionally omitted"* — nor `docker`. So no workflow-automation
bump can arise in this lane today, and the refused direction is a unit-level proof rather than a
live one. The ADR's first ground was measured on a live subject instead: `brain#53`'s CI run shows
`test=success`, `build-and-push=skipped`, `deploy=skipped`, and `change-manager`'s rollout workflow
produces no run on a pull request head at all.

The exclusion is read from the second segment of the update bot's branch name, which is the same
fact the estate's landing ledger reads. Spelled with an **underscore**: the ledger transcribes a
gate revision that compared the hyphenated form and therefore permitted nothing, silently, and both
repositories now pin the spelling with a test.

### The queue drains over three nights, not two

The pace term is one landing per repository per OCCURRENCE of the window, and the window is one
occurrence per night. `brain` holds three of the five, so it needs three nights; `change-manager`
needs two. Four of the five are also behind their base, and the branch-update pass clears that —
which changes the nightly exit code before it empties the queue. Expect the control to report
FINDINGS (exit 3) on the first night or two: a pull request refused on freshness alone, with no
exception beside it, is a finding by design. That is the lane working, not breaking.

### The exclusion does NOT carry over unchanged, and the difference is a narrowing

The decision above says the exclusion "carries over unchanged". Building it made the difference
visible and it is worth recording, because it is the one place version 5 refuses something version 4
permitted.

Versions 1 to 4 excluded the workflow-automation ecosystem only in the sense of **withholding the
cascade's major allowance**: patch and minor were permitted in every ecosystem, that one included.
Version 5 excludes the ecosystem outright, at every delta. So `bump actions/checkout from 4.1.0 to
4.1.1` was landable and now is not.

That narrowing is the principle applied honestly rather than a side effect. What the exclusion is
about is whether the required checks exercised the change, and a patch bump to an action the rollout
uses is exercised by nothing on a pull request, exactly as a major is. The delta was never the
thing that decided it.

Its practical reach today is **empty**, and measured rather than assumed: neither repository
declares the `github_actions` ecosystem in `.github/dependabot.yml` — both configs say
*"github-actions is intentionally omitted"* — so no such pull request can arise. It is also
narrower than it looks even if one did: since version 2 the rollout pin has refused any pull request
whose head changes the pinned workflow's bytes, so a bump reaching the rollout was already refused
at every delta. What version 5 newly refuses is a workflow-automation bump touching some OTHER
workflow — which is right for one this estate never runs on a pull request, and conservative for the
workflow that IS the required check.

**Two refusal codes this adds are in neither of the lander's suppression sets.** So if that
ecosystem is ever declared here, such a pull request reports as a nightly FINDING rather than
sitting quietly as an exception. Under Devon's 2026-08-13 ruling it is arguably an exception — a
record no current policy can land — and classifying it is left open rather than decided here,
because it cannot occur until a dependabot config changes.

### The population that is admitted is WIDER than the five, and three of the four were not measured

`update_type_of` answers `None` for four distinct populations, not one, and the outcome rule admits
all four. The decision's population section measured only the first:

- **a requirement range** — the five subjects;
- **a grouped bump**, which changes several packages in one pull request and whose title
  (`"bump the minor-and-patch group across 1 directory with 5 updates"`) the delta pattern cannot
  match at all;
- **a downgrade**, or a title whose two versions are equal;
- **a version string the parser cannot read** — more than three dotted components, a pre-release
  suffix, a calendar version.

Admitting them follows from the rule rather than escaping it: what decides is whether the required
checks passed, and they exercised every package in a group exactly as they exercise one. But a
grouped bump is a materially wider ACT than any subject the decision measured, and it now lands
unattended into a repository where merging redeploys production. It belongs in the residual above
alongside the `semver-major` case, and it is asserted rather than left to be discovered:
`test_a_GROUPED_bump_is_admitted_under_the_outcome_rule`.

The docstring on `update_type_of` said all four were *"correctly unlandable by this lane"*. That was
true when written and is false now, and it is corrected in the same change rather than left as a
comment that describes a rule the code no longer applies.

**RULED 2026-08-30 by Devon: a grouped bump is fine, and this is not to be re-decided.** The reason
is the one this section already gives — the checks exercised every package in the group exactly as
they exercise one, so it is the rule's own logic rather than an exception to it, and a grouped bump
is the most routine thing the update bot produces. Measured the same day: **neither repository
configures grouping** — `change-manager` declares `uv` weekly and `brain` declares `pip` weekly,
with no `groups:` block in either — so the reach of this ruling is empty today and becomes live the
day someone adds one. It was put to Devon precisely because the approval that produced this ADR
rested on five requirement ranges, and a grouped bump is a wider act than any of them.

**The other two populations were NOT ruled on and are left latent, deliberately.** A downgrade and
an unparseable version string are admitted by the same logic, neither occurs in these repositories
today, and neither was measured. If one appears it is worth a look rather than a rule change — a
downgrade in particular usually signals something unusual upstream, which is a reason to read it,
not a reason for the lane to refuse it.
