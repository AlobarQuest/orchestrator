# The lane edits one Dependabot branch per repository at a time

**Status:** draft, revised 2026-09-25 after an adversarial review (verdict: accept with changes;
the disposition of every item is in §16). **Repos:** `AlobarQuest/orchestrator`. **Records as:**
ADR-0045 (the number is free on `main`; the superseded draft never merged an ADR).

**Supersedes:** `docs/superpowers/specs/2026-09-20-dependabot-branch-freshening-design.md` and its
plan `docs/superpowers/plans/2026-09-20-dependabot-branch-freshening.md`, both on `main` since #286.
Their probe findings (§1–§2 of that spec) stand and are restated below. Their decision — delete the
estate lane's branch-update act and withhold the inert lane's for `dependabot[bot]` — is replaced.
Those two files are not edited; this document is where the supersession is recorded.

**Format note.** File and symbol references were read on `origin/main` at `6df1d09` (this branch's
`src/` is identical to it). Read the code, not these references, once the tree moves. Every claim
below is labelled **measured** (with the file or command it came from) or **inferred**. The data
directories are `$TMPDIR/dependabot-rebase-measure/` and `$TMPDIR/deadlock-precondition/`.

## 1. What stays from the superseded spec

These were measured on 2026-09-20 and nothing since contradicts them.

- **The update-branch call merges the base into the head under the App's identity**, and Dependabot
  then disowns the branch: asked to rebase, it answers that the PR "has been edited by someone other
  than Dependabot" and offers only `@dependabot recreate`.
- **The deadlock:** an edited branch is conflicted by a competing landing; Dependabot will not
  rebase it because it was edited; the lane will not freshen it because `qualifies_for_branch_update`
  refuses a conflicted head. Neither party can act.
- **The App cannot recover it.** `@dependabot recreate` posted as `alobar-sds-dispatch[bot]` gets
  201 and then Dependabot's reply "only users with push access can use that command". The same
  command from a user identity recreated both stuck pull requests in place (same number,
  `DIRTY` → `CLEAN`). No App-driven recovery exists, and this design does not try to build one.
- **No PAT in the landing path**, for the reason that spec gave: the acting identity is also the
  recorded attribution.

## 2. What the two new measurements say

### Dependabot does not maintain a merely-behind branch (measured, `dependabot-rebase-measure/`)

- 580 pushes to default branches that did not touch a dependency file produced **1** Dependabot
  rebase. Across 51 weekly Dependabot runs where an open, non-conflicted pull request under 30 days
  old was behind its base, Dependabot rebased **0** of them. Stretches behind reach about 20 days
  (`stretch.json`, `table.txt`).
- Dependabot **does** push to a branch it owns: on conflict, about 2 minutes after the conflicting
  push; when a newer version is published; on an `@dependabot` request from a user; and in 43 of 90
  cases after a push that touched the same dependency file.
- Documentation (`mng.md`): Dependabot stops rebasing once someone else has pushed commits to the
  branch, and stops rebasing a pull request left unmerged for 30 days.

**Consequence:** the lane cannot hand freshening back to Dependabot. The superseded spec's §7 said
it did not know how long landing would be delayed by that. It is measured now: indefinitely, for a
branch nothing conflicts. That is why the lane keeps freshening.

### The deadlock has one precondition, and the lane creates it (measured, `deadlock-precondition/`)

- **42** pull requests were freshened by the App (brain 19, change-manager 14, factory-runner 3,
  orchestrator 6), across **72** update events (`table.txt`, `rows.json`). 35 landed clean. 4 got
  stuck: `brain#70` and `change-manager#87` (recreated by a user, then landed by the lane),
  `factory-runner#71` (closed by a user), and **`change-manager#93`, stuck now**.
- **In all four, the conflict came from a sibling Dependabot pull request that the lane itself
  landed.** Each pair shared `pyproject.toml`/`uv.lock` or `requirements-dev.txt`, with changed lines
  next to each other.
- **In all four, the lane freshened both members of the pair seconds apart in one pass, then landed
  one of them** (App commit timestamps in `rows.json`): `brain#69`/`#70` 09-19 06:15:34/:38,
  `change-manager#86`/`#87` 09-17 06:15:32/:36, `change-manager#92`/`#93` 09-22 06:15:33/:37,
  `factory-runner#70`/`#71` 09-04 02:35:21/:24. The pairs differ in one way that matters to §5: in
  **two** (`change-manager#92`/`#93`, `factory-runner#70`/`#71`) both members received their *first*
  edit in that pass; in the other two the first member had already been edited the day before
  (`brain#69` on 09-18, `change-manager#86` on 09-16).
- Dependabot never pushed to an App-edited branch except when a user asked for a recreate. It
  refused 3 rebases and 2 newer-version updates, each time saying the branch had been "edited by
  someone other than Dependabot".
- In the 4 observed cases where a landing conflicted a sibling that had **not** been edited,
  Dependabot rebased that sibling within about 2 minutes.

**So the precondition is precise:** *two branches in one repository are both not owned by
Dependabot, and the lane lands one of them.* An edited branch next to Dependabot-owned siblings is
safe, because a sibling that gets conflicted is rebased by Dependabot. The defect is the lane owning
more than one branch in a repository at the same moment.

## 3. The decision

Devon chose to hold the superseded design and keep freshening, narrowed to the deadlock's
precondition. HQ then refined it with the data above:

> **The lane never makes a Dependabot-owned branch edited while another open Dependabot pull request
> in the same repository is edited and still queued to land.** A branch that is already edited may
> always be freshened again.

In practice there is at most one lane-edited, landable Dependabot pull request per repository: the
one the lane will land next. Every sibling stays owned by Dependabot, and so Dependabot rebases it
when a landing conflicts it.

## 4. Why this costs no landing latency (inferred from the code, and it is the argument for doing it)

Landings are already serialized per repository:

- **Estate lane:** one landing per repository per occurrence of the window (`_pace_term`).
- **Inert lane:** the head must be current with its base, so once one pull request lands, every
  sibling is behind for the rest of that pass. The inert admission module's own docstring says this
  ("at most one pull request per repository is landable per pass").

Freshening any branch other than the next to land was therefore always wasted. Take a night where A
lands and the lane freshens B and C. The next night B lands and C is stale again, so the lane
freshens it a second time, and it lands the night after. Under serialization C is freshened once, on
the second night, and still lands on the third. The queue drains at the same rate with fewer builds.

**The one case where it does cost time** is when the edited pull request at the front of the queue
fails its checks. The sibling behind it is freshened on the first pass after the failure is visible
(§6), not in advance. In the inert lane, which runs every hour, that is one pass later than today.
In the estate lane it usually costs nothing (inferred): the failure is normally visible by the next
hourly pass in the same window, and pace holds any landing to the next window anyway.

## 5. The rule, precisely

### Definitions

- **Dependabot pull request:** open, and authored by `dependabot[bot]` with `author_is_bot`. This is
  the same test `_remote_terms` already applies (`UPDATE_BOT_LOGIN`).
- **Each commit on a Dependabot pull request is classified into exactly one of three classes**, from
  `GET /repos/{r}/pulls/{n}/commits` (the fields below all come in that one response):
  - **Dependabot's:** author login `dependabot[bot]`, **and** committer login `web-flow`, **and**
    `commit.verification.verified` true.
  - **Foreign:** author login present and not `dependabot[bot]`, **or** committer login present and
    not `web-flow`. The second clause catches a Dependabot commit that a person rewrote locally
    (for example a local rebase and force-push): the author is preserved, the committer is the
    person, and Dependabot disowns the branch all the same.
  - **Unclassified:** anything else — no linked author login, no linked committer login, or a
    `web-flow` commit that is not verified.
- **Edited, positively:** at least one of these holds.
  - **(a)** The pull request carries a **foreign** commit.
  - **(b)** The orchestrator's event log holds a branch update for this pull request
    (`estate_pr_branch_update.updated` or `inert_pr_branch_update.updated`, matching `repository`
    and `pr_number` in the payload) whose recorded `head_sha` equals the pull request's **current**
    head, **and** whose `occurred_at` is less than **10 minutes** before the reading transaction's
    clock.
- **Owned, positively:** every commit is Dependabot's, the commit list was read completely (every
  page), the **last commit's SHA equals the head SHA read from the pull request list**, and arm (b)
  does not hold.
- **Unestablished:** neither positively edited nor positively owned. That covers an unclassified
  commit, a commit list whose last SHA is not the head the list call returned (the head moved
  between the two reads), and a commit list that could not be read in full.
- **Holding:** a Dependabot pull request that is positively edited, whose own composed admission was
  read, and whose every refusal is in §6's holding set.

**Why the committer and signature are part of "Dependabot's" (measured, read-only `gh api`,
2026-09-25).** Across 63 Dependabot-authored commits on the 12 most recent Dependabot pull requests
in each of six repositories (`brain`, `change-manager`, `factory-runner`, `orchestrator`,
`intent-packages`, `security-standards`), **63 of 63** carry committer login `web-flow`, committer
email `noreply@github.com`, and `verification.verified: true` with reason `valid`. **The committer
does not tell Dependabot's commits from the App's**: the App's update-branch commits on `brain#73`,
`brain#75` and `change-manager#93` also carry committer `web-flow`, verified. The author already
tells those two apart. What the committer adds is the one case the author cannot see, a Dependabot
commit a person rewrote and pushed from a local checkout. The signature closes the cheap spoof of
that (a committer email set to `noreply@github.com` links to `web-flow` but cannot carry GitHub's
signature). All of it is in the response the rule already reads, so it costs no call.

**What it still cannot see (residual, with a probe).** GitHub's own "Update branch → rebase with
base" rewrites commits server-side. The result very probably keeps the author, has committer
`web-flow`, and is signed by GitHub, so it would read as Dependabot's though a person disowned the
branch. Not measured. **Probe:** on a disposable public repository with Dependabot enabled, use that
button on a Dependabot pull request, read its commits, and ask `@dependabot rebase`. If the commits
are indistinguishable from Dependabot's, the residual is real and is recorded here; it cannot fail
worse than today's lane, which edits every sibling.

**Why the polarity of an unestablished pull request depends on which side it is on.** On the
*target*, an unestablished pull request may not be treated as edited: that would let it be updated
beside a holding sibling and become a second edited branch. On a *sibling*, it may not be treated as
owned: that would let a first edit go ahead beside what may be a second edited branch. So it is
treated as neither, and §5's outcomes below make it a withhold that is reported, never a silent
release and never a silent withhold. Measured, every commit in 68 Dependabot pull requests carried
a linked author (`owned_probe.txt`), so this matters only in theory, but the rule must not depend on
that.

### The rule: three outcomes at the act

At the branch-update act, after `qualifies_for_branch_update` has said yes, and only when the target
is a Dependabot pull request (a sync-bot or other author is never affected, §7), the act reaches
exactly one of these outcomes. They are checked in this order.

1. **Release, because the target is already edited.** The target is positively edited. It has
   already lost Dependabot's ownership, so updating it again costs nothing. This is also what keeps
   day one workable (§10).
2. **Withhold, deliberately.** Some other Dependabot pull request in the repository is positively
   edited **and** holding, and the target is positively owned. Refuse with
   `estate_branch_update_sibling_holding` / `inert_branch_update_sibling_holding`.
3. **Withhold, as a finding.** The act could not establish that no other edited branch is queued to
   land. Refuse with `estate_branch_update_siblings_unreadable` /
   `inert_branch_update_siblings_unreadable`. This happens when any of the following holds:
   - the open-pull-request list, any commit list, or the event query failed, or a page of either
     list could not be read (a truncated read is an unread one);
   - some other Dependabot pull request is not positively owned (edited or unestablished) and its
     composed admission raised, carries a code from §6's read-failure set, or names only refusals
     in the holding set while it is itself unestablished;
   - some other Dependabot pull request is positively edited and holding, but the target is
     unestablished.
4. **Release.** Otherwise: every other Dependabot pull request is positively owned, or is edited
   and names at least one releasing refusal (§6) and no read-failure code.

**Why outcome 3 is separate from outcome 2, and why that is the whole point of having two codes.**
Outcome 2 says a named sibling holds, and that condition clears on its own (§6). Outcome 3 says the
orchestrator does not know, and nothing about not knowing clears on its own. The landers treat the
first as not a finding and the second as a finding (§9). Folding them into one code would make a
repository whose reads are failing look like a repository waiting its turn.

### Where it lives: at the two acts, never in the shared predicate

`qualifies_for_branch_update` is **not** changed. ADR-0024 made it one definition read by two
consumers, the acting lane and the reporting agent, and changing it would move the reporting
consumer's semantics as a side effect. The new check is a separate conjunct in
`update_estate_pull_request_branch` and `update_inert_pull_request_branch`. Each act checks it inside
its own transaction, under the per-repository advisory lock it already takes
(`estate_pr_branch_update:{repository}`, which both lanes share).

The same fact is also served on both admission responses (§9). The act always works it out again
and never trusts a caller's copy, which is how the `branch_update_qualifies` flag is handled today.

It is implemented as **one function**, called by both routes and both acts. It is parameterized by
which lane's admission it composes when testing whether a sibling is holding; the read-failure set
is one set for both lanes (§6). Keep it outside `estate_landing_admission` and `inert_landing_admission`, so that
composing a sibling's answer can never recurse into composing that sibling's siblings.

### Why ownership comes from GitHub and the event log together, and why neither alone works

**GitHub alone misses the edit the lane has just made.** Update-branch answers 202 and does the work
afterwards (CLAUDE.md, measured 2026-08-14). The lander asks about every subject in one pass, seconds
apart. So a read of the first sibling made right after it was updated can still show only
Dependabot's commits, and the second sibling gets updated too. That is the pattern in two of the four
stuck pairs (`change-manager#92`/`#93`, `factory-runner#70`/`#71`), where both members got their
first edit in one pass. In the other two, the first member already carried a day-old App commit, so
arm (a) alone would have held it. Arm (b) closes the gap. The event is written in the same
transaction as the call and under the same repository lock, so the next act in the pass sees it. Its
recorded head is the head *before* the update, and while the platform has not yet delivered the
update, that is still the current head.

**Why arm (b) expires after 10 minutes.** Without a bound, a 202 that GitHub accepts and never
delivers leaves the pre-update head current forever, so arm (b) would call that pull request edited
forever. If it also held, every first edit in its repository would stop for good. The bound limits
that to one pass. The number comes from two measurements: delivery took about 12 seconds when last
measured (`change-manager#49`, CLAUDE.md, 2026-08-14), and sibling acts inside one pass are 3–4
seconds apart (§2's timestamps). Ten minutes is more than 40 times the measured delivery, longer than
a whole lander pass, and shorter than the hourly cadence of both landers, so a never-delivered update
has lapsed before the next pass reads it. **The trade, stated:** a delivery slower than ten minutes
reopens the two-of-four window for one pass; a 202 that is never delivered holds arm (b) on that
pull request for ten minutes and on the repository not at all beyond that. `occurred_at` is a server
`now()` default, which is the writing transaction's start, and the reading side compares it with its
own frozen transaction clock. Both are seconds-scale errors against a ten-minute bound.

**The event log alone never forgets.** A user's `@dependabot recreate` puts the branch back in
Dependabot's hands with a new head (measured on `brain#70` and `change-manager#87`). An event-only
test would call it edited forever and block the repository until the pull request closed. Keying arm
(b) on the *current* head retires the event as soon as the head moves. Once the update is delivered,
arm (a) takes over.

**Why the commit list's last SHA must equal the head.** The list call and the commits call are two
reads. If Dependabot rebases or the App's update lands between them, the commits describe a
different head than the one the rule is reasoning about. Requiring equality makes a moved head an
unestablished answer instead of a wrong one.

**Human edits count, and they need no special case.** Dependabot disowns a branch whoever pushed to
it. AlobarQuest's update commits on 20 pull requests (per the handoff) disowned those branches just
as surely as the App's did. Arm (a) catches them because it reads authors and committers, not
identities the orchestrator knows about.

**Cross-check (measured, `deadlock-precondition/owned_probe.txt`, 2026-09-25):** across 68
Dependabot pull requests in five repositories, `commits == 1` agreed with "every commit authored by
`dependabot[bot]`" in 68 of 68 cases (32 owned, 36 edited). The count would save no calls, since
either read is one request per pull request. It is also a proxy whose wrong answer on the *target*
fails open: a multi-commit branch that Dependabot still owns would read as already edited and get
updated. So the spec reads the commits.

### What it reads, and what that costs

For each act (and each admission read, §9), and only when the target qualifies and is a Dependabot
pull request:

- `GET /repos/{r}/pulls?state=open`, **paginated to the end**: normally 1 call. It returns each pull
  request's author and head.
- `GET /repos/{r}/pulls/{n}/commits`, **paginated to the end**, for the target and for each other
  open Dependabot pull request: normally 1 call each.
- One event-table query.
- For each other Dependabot pull request that is not positively owned, one admission composition to
  test whether it is holding. That is the ~4–6 outbound reads the existing cascade already makes.
  After day one there is normally at most one such sibling.

**The numbers (inferred from the code and the measured queue sizes).** With at most 5 open
Dependabot pull requests per repository, as this estate has had, the scan adds about 1 + 5 + 6 ≈ 12
outbound calls to an act that already makes 4–6. **All of it runs under the repository's advisory
lock with the act's database transaction open**, which is how the act's existing admission reads
already run; at this size that is seconds. Each lander reads admission twice per subject per pass
(the landing pass and the update pass, which re-reads on purpose), and the served fact (§9) runs the
scan on every qualifying Dependabot subject, so a pass that follows a landing performs about 2 × N
scans: roughly 2 × 5 × 12 ≈ 120 calls for a five-deep queue. That is fine at N ≤ 5 and well inside
the App installation's hourly rate limit (inferred). If queues grow, the scan is the first thing to
cache per pass.

Both gateways gain two read methods, `open_pull_requests` and `pull_request_commits` (returning
author login, committer login, verification and SHA per commit), on `EstateReadGateway`. Every fake
gateway must implement them.

## 6. When the edited branch does not land

Every refusal a sibling's composed admission can name falls into exactly one of three sets.

**Holding.** The sibling is still queued to land, and the condition clears without anyone acting:

```
DELIBERATE_REFUSALS                         (pace, window)
∪ freshness_derived_refusals(...)          (behind; rollout pin that differs because behind)
∪ { landing_checks_in_flight,
    landing_checks_awaiting_verdict,
    landing_mergeability_unknown }
```

The pace and window reset on a clock. Being behind is cleared by the lane itself, because the holding
branch is already edited and may always be freshened again. The last three members are what an
edited branch reports in the minutes after an update. Without them, the branch the lane has just
freshened would stop counting as holding in the very pass that freshened it, and the sibling behind
it would be edited too. That would recreate the defect.

**Read failure.** The orchestrator could not establish the sibling's state. Any one of these makes
the act reach outcome 3 (§5), a withhold that is a finding. **One set serves both lanes.** Measured
by grep of both admission modules at `6df1d09`:

`landing_pull_request_unreadable`, `landing_checks_verdict_unreadable`,
`landing_freshness_unreadable`, `landing_rollout_unreadable`, `landing_ecosystem_unreadable`,
`landing_policy_unreadable`, `landing_conditions_unreadable`, `landing_record_source_unreadable`,
`landing_record_source_unconfigured`, `landing_record_ambiguous`, `landing_record_unidentified`,
`landing_estate_source_unreadable`, `landing_estate_source_unconfigured`, `landing_estate_unknown`,
`landing_mergeability_unrecognised`, `landing_app_credentials_missing`,
`inert_landing_policy_source_unreadable`, `inert_landing_policy_source_unconfigured`.

The inert lane reaches only some of these (it has no change record and no rollout pin, and it reaches
several shared codes through `checks_term`, `freshness_term` and `ecosystem_exclusion_term`, which the
estate module defines). Which lane can raise which code is not derivable mechanically, and a member
a lane can never raise is harmless in its set, so a per-lane split would add machinery and a way to
get it wrong for nothing.

The repository-level members (`…_unconfigured`, `landing_app_credentials_missing`) would refuse the
target's own admission too, so the target would not qualify and the scan would never run. They are
listed so the classification is complete, not because they are expected to fire here.
`landing_mergeability_unknown` is deliberately in the holding set, not here: it is the ordinary
transient while GitHub computes mergeability after an update. `landing_mergeability_unrecognised` is
a word GitHub has invented since the code was written, and nothing can be concluded from it.

**Releasing.** Every other refusal. A sibling that names one, and no read-failure code, does **not**
hold, so the queue moves past it. The cases that matter:

- **Red checks** (`landing_checks_not_clean`). Dependabot will not replace a branch it has disowned
  (it refused newer-version updates twice in the data), so a red edited branch stays red until a
  person acts. Holding siblings behind it would stall the repository behind a condition that does not
  clear on its own. It is already a finding on its own line.
- **Conflicted** (`landing_pull_request_conflicted`). This is the deadlock itself. It needs a user's
  `@dependabot recreate`. `change-manager#93` is in this state today.
- **Any positive refusal this spec does not list** (a condition the orchestrator read and named).
  Releasing is exactly the pre-change behaviour, so on a positively named condition the rule can
  never withhold more than today's lane.

**The polarity changed from the first draft, and this is why.** The first draft let "any refusal
this spec does not list, including read failures inside the sibling's own composition" release. That
contradicted §5, which withholds when a read fails: a sibling whose reads failed is exactly a sibling
whose state was not established. Read failures now withhold, and the withhold is reported (§9), so a
repository whose reads keep failing is visible as a finding every pass rather than quietly released.

**A completeness guard, so a new code forces a decision — and the split between runtime and test
is what makes it one.** At runtime, releasing stays the complement: a code in neither the holding
set nor the read-failure set releases, which is the pre-change behaviour and the right polarity for
a positively named condition. A test that only checks "each code is in one of three sets" would be
vacuous against a complement, so the test does something different, the way `_NOT_A_FINDING` and
the report-order list are already held together: it spells out the **releasing set as an explicit
literal** and asserts that holding ∪ read-failure ∪ releasing equals every refusal-code constant
both admission modules define, with the three disjoint. A new code then reds CI until somebody
classifies it, without changing what the runtime does with it. Without that, a new `…_unreadable`
code added next year would silently release, which is the one direction this section exists to
prevent.

**Four members of the holding set can outlive the minutes after an update, and each is a stall
residual.**

- **`landing_checks_awaiting_verdict` on a head that is current.** `checks_term` raises it when runs
  were cancelled or never happened. The shared predicate excuses it only beside `behind`, and nothing
  in the estate re-runs an abandoned check (CLAUDE.md). So an edited sibling whose freshened head had
  its runs cancelled, for example by the Actions quota (seen 2026-08-17 and 2026-08-22), holds the
  repository until a person re-runs them.
- **`landing_checks_in_flight` that never finishes.** A run queued with no runner, or waiting on an
  environment approval, stays in flight.
- **`landing_mergeability_unknown` that never resolves.** GitHub normally answers within seconds;
  nothing guarantees it.
- All three stay in the set anyway, and that is deliberate. They are needed for the window just
  after an update. Whether GitHub reports that window as `landing_mergeability_unknown` (which
  `_remote_terms` checks first, before `checks_term` runs) or as `awaiting_verdict` or `in_flight`
  was not measured and cannot be read from the data.

**How the stall is reported.** The holding branch's own line reads `held`, because none of these
three is in any lander's non-finding set. That is one finding per stalled repository, on the branch a
person has to look at. The withheld siblings read `withheld` (§9), which is not a finding, so they
do not multiply it. A build session must not write a test asserting that every member clears on
its own.

If composing a sibling's admission raises rather than answering, the act reaches outcome 3.

**Residual, stated plainly.** After the queue has moved past a red or conflicted edited branch, a
second edited branch exists. If a person later makes the first one landable (for example a re-run
turns its checks green), the two can deadlock. That takes human action on a branch that was already
a nightly finding, and it is no worse than today.

### Alternatives considered and rejected

- **Strict: any edited open sibling holds, whatever its state.** Rejected. `change-manager#93` would
  freeze every future first edit in that repository until someone recreated it, and a red branch
  would freeze its repository until someone closed it. Neither clears on its own, so the withheld
  siblings could not honestly be reported as not findings (§9). It also turns one repository's
  defect into a stall of everything behind it.
- **Holding means `mergeable_state != dirty` and nothing more.** Rejected. It handles `#93` but not
  a red branch. It would also be a second, hand-written definition of "landable" beside the cascade.
- **Only siblings that share a changed file hold** (HQ's G*). HQ reported that G* catches 4 of 4 and
  withholds 21 of 62. The script behind that figure is not in the data directory, so I did not
  reproduce it. My replay of the same condition (`replay_serialize.py`/`.out`) gives 22 of 72
  withheld events, against 29 of 72 for the strict version of the rule chosen here. Both catch 4 of
  4. G* was rejected because its extra precision buys nothing in steady state (§4: any landing stales
  every sibling, so freshening a non-sharing sibling early is wasted too). It also costs a files read
  per pull request, and in the repositories where the deadlock happens every bump touches the same
  lockfile. Its only benefit is liveness behind a red branch, and the holding gate above already
  provides that for every sibling, not just the ones that don't share a file.
- **Withhold any update to T while an OLDER open Dependabot sibling is queued** (the reviewer's
  alternative). It needs no ownership reads at all, and it closes queue jumping (§13) by
  construction, since nothing younger is ever freshened ahead of something older. Rejected, on three
  grounds. It forbids re-freshening an already-edited branch and a person-edited branch whenever an
  older sibling is queued, which is the case §5's outcome 1 exists to allow and which day one
  depends on (`brain#74`/`#75` would stop being maintained behind `brain#73`). It adds a stall this
  design does not have: an older branch that is owned, current, and carrying abandoned runs holds
  every younger branch, and nothing re-runs its checks, because freshening it is what would. And it
  requires the orchestrator to re-derive the lander's order, which differs between lanes (record id
  in the estate lane, pull request number in the inert lane, §8).
- **This rule plus an "older sibling about to land" clause**, to close queue jumping on top of the
  chosen rule. Considered and not adopted. Queue jumping has **0** occurrences in the history
  (§13). The clause brings the lander-order re-derivation and the stall above with it, restricted
  but not removed. Devon's standing preference is less machinery unless it closes a real, measured
  risk, and this one is not measured. **Trigger to revisit:** the first observed instance of the full
  queue-jumping path (an older owned sibling rebased by Dependabot, landed ahead of the edited
  branch, and conflicting it).

**About the replays, and what 29 of 72 means.** Both are replays over the *historical* event stream,
not simulations. Under either rule the landing sequence itself would have been different. **29 of 72
is a strict upper bound, not the rule's measured cost.** The replay blocks a first edit whenever
*any* other open sibling in the repository had been edited, with no holding gate, because the check
outcomes over time that the gate needs are not in the data. Removing the four conflicted siblings
from the blocker set after their conflict time moves the count by **zero** (recomputed from
`rows.json`/`files.json`), so the gap between 29 and the real figure is made of red-check releases
the data cannot see. By lane, the 29 are 26 of 59 events in the estate lane (`brain` 17,
`change-manager` 9) and 3 of 13 in the inert lane (`factory-runner` 1, `orchestrator` 2). The inert
events are in scope: the rule applies to both acts. What the replay does show is that the rule is
never *less* protective than it needs to be on the four stuck cases.

## 7. The inert lane, and the second author

Deploy policy v8 declares two authors for the inert lane: `dependabot[bot]` and
`octo-upstream-sync[bot]` (`claude-octopus`, `rtk`). The sync bot rebuilds its rolling branch every
day with `checkout -B` and a force-push. An edit does not disown it, and the next rebuild overwrites
the edit anyway.

- **A sync-bot pull request is never withheld.** The rule applies only to a Dependabot target.
- **A sync-bot pull request never counts as a holding sibling.** The rule's premise is Dependabot
  refusing to maintain a branch, and the sync bot refuses nothing.

The one interaction left over: a sync-bot landing can conflict the single edited Dependabot branch.
That belongs to the general residual in §13 ("any other landing"), not to this rule.

The author is available at the act without adding anything to `_RemoteTerms`. The sibling scan's
list call already carries every pull request's author, including the target's.

## 8. Which pull request gets freshened first (inferred from the code)

The orchestrator does not choose; the lander's order does.

- **Estate lander:** `_subjects` sorts by `(target_repository, record id)`. The landing pass then the
  update pass walk that list in that order (`run`: `_pass` then `_branch_updates`). So the first
  qualifying record in record-id order gets the grant. The sibling scan refuses the rest in that
  pass, through arm (b). **Effectively: oldest change record first.**
- **Inert lander:** `_subjects` sorts repositories and then pull request numbers. **Oldest pull
  request first.**

These are also the orders the landing pass lands in. So the branch freshened is the branch that lands
next, with one exception, which is a residual (§13, "queue jumping").

No ordering code changes. This spec deliberately does not make the orchestrator re-derive the
lander's order.

## 9. Reporting

### What the withheld sibling looks like today and without a change

Both landers classify each landing-pass line with `_held_status` (estate) or `_unsatisfied_status`
(inert). There, `landing_head_not_current_with_base` on its own is **held**, which is a finding.

- Estate lane today: siblings staled by a landing read `held (pace, behind)` for one pass, get
  freshened, and read `deliberate (pace)` from then on.
- Under serialization with no reporting change: every queued sibling reads `held` on all four
  passes of each night until its turn, instead of on one. In the inert lane it reads `held` on every
  hourly pass until its turn, and with the stall residuals of §6 that can be many passes.

**The nightly exit code does not change either way.** Today the estate lane already exits 3 on
every night that has a queued sibling. The pass right after a landing reports those siblings `held`
(measured: `4 held` on the 06:15Z pass in `~/Library/Logs/estate-landing.log`), and during any drain
the exit code already cannot say anything new. The dead-man switch is unaffected in both cases,
because the finding code pings success (CLAUDE.md). What serialization changes is how many `held`
lines each queued sibling contributes.

### Is the withholding deliberate? Only under the holding gate, and only when it was observed

Devon's ruling is that a refusal the system makes on purpose, and that clears on its own, is not a
finding. Under §6's gate, a sibling is withheld only while the holding branch's own condition lasts,
and for every member of the holding set except the three named stall residuals, that condition
clears on its own. In the residual cases the stall is still reported, once, on the holding branch's
own `held` line (§6). Under the rejected strict rule it would not clear (`#93`), and there the
withheld line would have to stay a finding. **The gate and the reporting are one decision.**

A withhold the orchestrator could not establish (outcome 3) is **not** deliberate. It is the system
not knowing, and it stays a finding every pass until the reads succeed.

### The mechanism: a served fact, the way `rollout_base_matches_pin` is served

- **Both admission responses** (`EstateLandingAdmissionResponse` and `InertLandingAdmissionResponse` in
  `api/schemas.py`) gain `branch_update_withheld_for_sibling: bool`. **It is true only when the act
  would reach outcome 2**: the target qualifies, is a Dependabot pull request, is positively owned,
  and some other Dependabot pull request was positively observed edited and holding. It is false for
  outcome 3, so a scan whose reads failed never produces a quiet line. It can never co-occur with a
  failing check, which disqualifies. It is a fact about an observed holding sibling. It is **not** a
  record of "the lane declined", because keying on the declining is the fail-open that CLAUDE.md's
  durability ruling names. A read failure inside the scan must not fail the admission read itself:
  the field is simply false, and the act (which the lander then calls) meets the same failure and
  refuses with `…_siblings_unreadable`. The schema docstring must say all of this, and the
  response-model field-set pins in both schema tests must be updated in the same change.
- **Both landers classify the same way, and a withheld sibling gets its own status, `withheld`.**
  When the key is present and `True`, the classifier removes freshness-derived refusals (the
  estate's criterion; the inert lane's single `_FRESHNESS` code) from the unexplained set, exactly
  as each already does beside an exception. If an exception is also present, the line stays
  `exception`. Otherwise, if nothing remains unexplained, the line reads **`withheld`**, a new
  status added to both landers' `_NOT_A_FINDING` sets and their report-order status lists (the
  summary's counts must still sum). Keeping it separate from `deliberate` and `exception` follows
  Devon's ruling that collapsing categories loses which is which: a deliberate refusal clears on a
  clock, an exception never clears, and a withheld sibling clears when the branch ahead of it lands.
  A missing or false key removes nothing, so the line stays a finding. That is the right direction
  when the orchestrator in production is older than the lander. A mirror constant and a pin test in
  each lander hold the key name equal across the isolation boundary, the way `_BASE_MATCHES_PIN` is
  held.
- **Why the inert lander is no longer left unchanged.** The first draft left it alone on the ground
  that the lane has no deliberate refusal and a queued sibling reads `held` for only one or two
  hourly passes. Neither holds up. The stall residuals in §6 can make those passes many, and the
  inert lander already carries the identical conditional suppression beside an exception, so the
  mechanism is not new vocabulary. It must read the served key anyway for its update pass, so the
  mirror constant and pin exist either way. Its module docstring's claim that "this lane has no
  deliberate refusal" stays true and gains one sentence: `withheld` is keyed on an observed sibling,
  not on a clock.
- **Both landers' `_branch_updates`:** act only when `branch_update_qualifies` is true **and**
  `branch_update_withheld_for_sibling` is not true. A withheld subject prints no update line, since
  its landing line has already said why. As a result a dry run reports `would-update` only for what
  a live pass would actually update, which is more accurate than today.
- **Each lander's `_UPDATE_SELF_CLEARING` gains its lane's `…_sibling_holding` code, and only that
  one**, for the race where the served answer and the act disagree. `…_siblings_unreadable` is
  deliberately left out, so an act refused because it could not read stays `held`, a finding. The
  two codes are spelled so that neither contains the other (CLAUDE.md: a finding kind that is a
  superstring of another breaks every substring reader).

This gives the new field four readers: two update passes and two classifiers. It is not a dead knob.

## 10. Day-one population (measured, read-only `gh api`, 2026-09-25T15:11Z)

The open pull requests across the ten repositories checked (`brain`, `change-manager`,
`orchestrator`, `factory-runner`, `intent-packages`, `security-standards`, `project-standards`,
`infraops-mcp-server`, `claude-octopus`, `rtk`) are exactly these four:

| PR | state | commits (author) | files | reads under the rule |
|---|---|---|---|---|
| `brain#73` | open, `clean` | dependabot 09-24 08:17; **App 09-25 07:15** | `requirements.txt` | edited |
| `brain#74` | open, `clean` | dependabot 09-24 07:24; App 09-24 08:15; App 09-25 06:15 | `requirements.txt` | edited |
| `brain#75` | open, `clean` | dependabot 09-24 07:24; App 09-24 08:15; App 09-25 06:15 | `requirements.txt` | edited |
| `change-manager#93` | open, **`dirty`** | dependabot 09-22 05:45; App 09-22 06:15; App 09-23 06:15 | `pyproject.toml`, `uv.lock` | edited, conflicted |

The last estate-landing log passes show `brain#73/#74/#75` as `deliberate (landing_pace_exhausted)`
and `change-manager#93` as `held (landing_pull_request_conflicted, landing_head_not_current_with_base)`.

**`brain#73` lands first** (measured). The estate lander walks subjects in `(target_repository,
record id)` order (`estate_lander/cli.py::_subjects`), and every pass in
`~/Library/Logs/estate-landing.log` prints `brain#73` before `#74` and `#75`, so `#73` has the lowest
record id. Here the record ids agree with the pull request numbers and the opening times (`#73`,
`#74` and `#75` were opened at 07:24:04, :08 and :13 on 09-24; `#73`'s 08:17 Dependabot commit is a
later rebase of its own branch). The record id is still what decides, so the agreement is a fact
about tonight, not a rule.

**What the rule does on day one:**

- **brain.** All three branches are already edited, so the rule never withholds an update to any of
  them: outcome 1 applies to each, and they keep being freshened as today. The rule does not clean
  up this pre-existing state, where the lane owns three branches at once. The rule is about not
  creating more edited branches, not about recovering existing ones. Tonight's window lands `#73`.
  All three change `requirements.txt`, so that landing may conflict `#74`, `#75` or both. A
  conflicted one is stuck and needs a user's `@dependabot recreate`, exactly as today. **Exposure
  that exists before deploy and is not closed by it.** Any *new* Dependabot pull request in `brain`
  is withheld from its first edit while one of the three is holding. Once they drain, the rule is in
  its steady state.
- **change-manager.** `#93` is edited but does not hold, because it is conflicted. So it does not
  block future siblings. It is not recovered by this design. **It needs a user's
  `@dependabot recreate`** (§1: the App's request is refused). After a recreate it is
  Dependabot-owned again, and the lane lands it in the usual way.
- **Every other repository** has no open Dependabot pull request, so the rule has nothing to act on.

**The mitigation that closes tonight's exposure, recorded here and not decided here.** A user's
`@dependabot recreate` on `brain#74`, `brain#75` and `change-manager#93` before the 02:00 EDT window
puts all three back in Dependabot's hands. Then `brain#73` is the only edited branch in `brain`, and
if its landing conflicts `#74` or `#75`, Dependabot rebases them within about two minutes (§2). That
is the steady state from the first night. Before this design deploys, today's lane would still
freshen a recreated sibling that the landing leaves merely behind, making it edited again, so the
recreate removes tonight's exposure and the rule keeps it from coming back. Whether to post the
commands is Devon's call and HQ is asking him separately.

## 10a. Is the exception case live? (measured)

The estate lander's `_EXCEPTION` is `{landing_update_type_unparseable}`, and `_bump_term` only raises
that under a policy version that decides by update type, which means versions before the fifth. Under
v8 the outcome rule decides. `brain#75`, a requirement-range bump, reports only
`landing_pace_exhausted`, which confirms it. So on current policy no edited branch can be held up by
an exception, and this spec does not design for that case. If the lane ever runs against an older
policy version, an unparseable branch would not hold (it is outside §6's holding set). The queue
would move past it, and it would stay an exception on its own line.

## 11. What must be proven

**Tests (unit level, fake gateways):**

1. **The discriminating control.** A target that qualifies, is positively owned, and has one holding
   sibling (edited by arm (a), refusals `{pace, behind}`) is **withheld** at the act with
   `…_sibling_holding`. The gateway's `update_branch` is never called, and the same fixture with the
   sibling positively owned is **updated**. This pair of cases only has different answers if the
   sibling rule is in effect. It is the control that fails if the rule is deleted.
2. **Re-freshening an edited target is never withheld**, including when a holding sibling exists.
   This is the day-one guarantee.
3. **Arm (b) alone.** A sibling whose commits are all Dependabot's, but whose current head equals a
   fresh update event's recorded head, is edited. The same sibling whose head has moved (a recreate)
   is owned. The same sibling with a matching event **older than the bound** is owned. This covers
   the 202 race, the recreate, and the never-delivered 202 separately. The bound needs a pair of
   cases either side of it, so a mutation that ignores the event's age reddens.
4. **Commit classification, row by row.** A commit by `AlobarQuest` is foreign. A commit authored by
   `dependabot[bot]` with a non-`web-flow` committer is foreign. A commit with no linked author, no
   linked committer, or a `web-flow` committer that is unverified is unclassified. **Each polarity
   needs its own control:** an unclassified *target* beside a holding sibling reaches outcome 3 (not
   1 and not 2); an unclassified *sibling* whose answer is holding-only reaches outcome 3 (not 4). A
   single classification passes one of the two and fails open on the other.
5. **Last SHA equals head.** A commit list whose last SHA differs from the list call's head makes the
   target unestablished (outcome 3 when a holding sibling exists) and makes a sibling unestablished
   (outcome 3 when its answer is holding-only). Neither case updates.
6. **The holding gate, row by row.** For each member of §6's holding set, a positively edited
   sibling holds (outcome 2). For `landing_checks_not_clean`, `landing_pull_request_conflicted`, and
   a made-up positive code, it does not (outcome 4). For each member of the read-failure set,
   the act reaches outcome 3, including when the same answer also names a releasing refusal. A
   composition that raises reaches outcome 3.
7. **Read failures reach outcome 3:** the list call fails, a later page of the list fails, a commits
   call fails, a later page of a commits call fails, or the event query fails. `update_branch` is
   never called, and the refusal code is `…_siblings_unreadable`, not `…_sibling_holding`.
8. **Served fact versus act.** `branch_update_withheld_for_sibling` is true exactly when the act
   would refuse `…_sibling_holding`; it is false when the act would refuse `…_siblings_unreadable`,
   and false whenever `branch_update_qualifies` is false. A read failure in the scan leaves the
   admission read answering, with the field false. On the lander side, an act refused with
   `…_sibling_holding` prints `deliberate` for the update line (it is in `_UPDATE_SELF_CLEARING`),
   and an act refused with `…_siblings_unreadable` prints `held`, a finding. The field-set pins on
   both response models are updated.
9. **Inert:** a sync-bot target is never withheld, and a sync-bot edited sibling never holds. A
   Dependabot target beside a holding Dependabot sibling is withheld.
10. **Landers, both of them:** the classifier returns `withheld` only when the key is `True`. The same
    answer with the key absent, or `False`, stays `held`. `{behind, checks_not_clean}` stays `held`
    with the key `True`; that case cannot be produced by the orchestrator, so it guards each lander
    on its own. An exception beside the key reads `exception`, not `withheld`. `withheld` is in
    `_NOT_A_FINDING` and in the report-order list, and the summary's counts still sum. Each mirror
    constant is pinned to the orchestrator's field name.
11. **Completeness of the three sets** (§6): the releasing set is spelled out as a literal in the
    test, and holding ∪ read-failure ∪ releasing equals every refusal-code constant both admission
    modules define, the three disjoint. A new code reds this test until it is classified; the
    runtime still treats an unlisted positive code as releasing.
12. **Add-a-term hygiene.** Every existing "qualifies → acts" fixture in
    `tests/services/test_estate_pr_branch_update.py`, `tests/services/test_inert_pr_branch_update.py`,
    and both landers' suites (`tests/estate_lander/`, `tests/inert_lander/`) must set up a repository
    with **no** edited sibling and readable commits. Without that, the new conjunct is true by
    accident and those controls stop testing the shared predicate. This is the
    add-a-term-to-a-conjunction rule in CLAUDE.md.

**Mutation controls, with every survivor reported:** delete the Dependabot-target condition; delete
outcome 1; make an unestablished target count as owned, and separately as edited; make an
unestablished sibling count as owned, and separately as edited; flip arm (a)'s author test; delete
arm (a)'s committer clause; delete the verified clause; delete arm (b), key it on "any event"
instead of the current head, or drop its age bound; drop the last-SHA check; stop paginating; drop
each member of §6's holding set in turn; move each read-failure code into "releasing" in turn; make
read failures release instead of withhold; refuse outcome 3 with the outcome-2 code; add
`…_siblings_unreadable` to `_UPDATE_SELF_CLEARING`; unconditionally subtract in either classifier;
key the served fact on `qualifies` alone; set the served fact true on outcome 3. Restore from git
after each mutation, set `PYTHONDONTWRITEBYTECODE=1`, and read the kills rather than the count.

**Build hazards: whole-repo gates a per-task loop will not see.**

- **`test_cross_boundary_vocabulary`.** The holding set and the read-failure set are
  module-level string collections tested with `in`. Each must be registered in
  `VOCABULARY_REGISTRY` (they mirror the refusal codes the admission modules raise and each lander
  classifies) or carry a justified `# not-a-vocabulary:` marker. Register them; an exemption that
  would read "a legitimate second copy" means the predicate is wrong.
- **`test_unreachable_guards`.** The new function needs a production caller: the two routes and the
  two acts. A test calling it does not count.
- **Route and response-schema pins move:** both response-model field-set pins, and any schema
  snapshot that lists admission response properties. No route is added, so the route inventories
  and the idempotency matrix should not move; if a route does change, they must.
- **The ws32/ws33 word guards.** Docstrings in `src/orchestrator/` must not use the bare words
  `dispatch`, `deploy` or `merges`, nor the multi-token sequences in `FORBIDDEN_SEQUENCES`. Check a
  docstring with the guard's own `_tokenize` and `_contains_sequence`, not with a grep.
- **The invariant scan** adds parametrized cases for any new module under `src/`; reconcile the
  collected count by node-id diff against a fetched `main`, not by arithmetic.

## 12. Deploy ordering

Build, then swap, then verify. There is **no migration**: the rule reads the existing `events` table
and adds no column. Verification reads `openapi.json` and asserts that
`branch_update_withheld_for_sibling` is present on **both** admission response schemas, since health,
digest, and revision label cannot see a change to the served surface.

**Either order is safe. Orchestrator first is preferred.**

- **New orchestrator, old lander.** The old lander ignores the new key. It calls the act for a
  withheld sibling, and the act refuses with `…_sibling_holding`, which the old lander's
  `_UPDATE_SELF_CLEARING` does not know. So it prints `held`, which is a finding. That is the safe
  direction, and it lasts only until the main tree pulls, which the activation helper does on the next
  scheduled pass.
- **Old orchestrator, new lander.** The key is absent, so the lander treats it as false and calls the
  act, and the old orchestrator freshens exactly as today. Nothing gets worse.

The landers run from the main tree's working copy, so merging is what deploys them (CLAUDE.md). No
`[project.scripts]` entry changes, so no `uv sync` is needed.

## 13. Residuals

- **Queue jumping** (inferred; measured 0 times, but its first step has been seen). The lane
  freshens X while an older sibling Y is conflicted by the landing that has just happened. Dependabot
  rebases Y about 2 minutes later (seen 4 times), so Y is fresh and still Dependabot-owned. At the
  next window Y is older, so it lands first, and if Y conflicts X, X is stuck. X then stops holding,
  because it is conflicted, and the queue moves on. X needs a user's recreate. The history contains
  no instance of this whole path, but under serialization more siblings stay owned by Dependabot and
  the first step becomes more common. Closing it was considered and declined (§6, the last two
  alternatives), with a named trigger to revisit.
- **A lost response on the update call.** If `update_branch` times out after GitHub has accepted the
  202, the act raises, the transaction rolls back, and no event is written. Arm (b) then has nothing
  to match, and arm (a) sees the App's commit only once GitHub delivers it, about 12 seconds later
  (measured once). The next sibling's act in the same pass is 3–4 seconds away (§2), inside that
  window, so a lost response followed immediately by another qualifying sibling reproduces the
  defect once. It needs a timeout on a call that normally answers at once. Writing the event before
  the call would close it and is rejected, because `_record` documents the opposite trade on purpose:
  a record of an act that then fails is false, while an act whose record is lost is recoverable.
- **Server-side rebase by a person** (§5): GitHub's "Update branch → rebase" probably produces
  commits indistinguishable from Dependabot's. Not measured; probe named in §5.
- **Any other landing conflicts the one edited branch.** A person's merge, a sync-bot landing, or a
  non-Dependabot commit to the default branch can all do it. Serialization cuts the lane's exposure
  from "every edited sibling" to one branch per repository. It does not make that one branch safe.
- **Dependabot's 30-day cutoff.** A withheld sibling more than 30 days old that gets conflicted by a
  landing will not be rebased by Dependabot, so it sits conflicted. That is a finding on its own line,
  and it needs a recreate or a close. A queue drains one pull request per window per repository, so
  only an unusual backlog reaches this, but pull requests can be old for other reasons.
- **`[dependabot skip]` is untested.** The documentation says a commit carrying that marker in its
  message lets Dependabot force-push over it, which would dissolve the ownership problem completely.
  Update-branch gives no control over the commit message. The endpoint that does, the repository
  commit-message endpoint for combining a base into a branch, is a different write act. It has not
  been probed, and naming it in `src/` would trip the ws33 word guard. It is recorded as a follow-up
  probe, run on a public disposable repository per CLAUDE.md, and is not part of this design.
- **A holding branch whose checks never finish stalls its repository** until a person acts:
  abandoned runs, a run in flight that never gets a runner or an approval, or mergeability that never
  resolves (§6). Reported as one `held` finding on that branch's own line.
- **The pre-existing edited population** in `brain` and `change-manager#93`, per §10.
- **Two edited branches after the queue moves past a red one**, per §6.
- **Scan cost grows with queue depth** (§5). Fine at N ≤ 5; cache per pass if queues grow.

## 14. The `stop-freshening` branch: what to keep

Start fresh from `origin/main`. Keep nothing.

| commit | what it does | disposition |
|---|---|---|
| `d966cc2` | moves the shared predicate's tests out of the act's test file | **Optional; recommended skip.** Its reason was that the act's file was about to be deleted. Under this design the act stays, so moving tests for a surviving module is churn. |
| `38746ab` | deletes the estate branch-update act | **Superseded.** This design keeps and narrows the act. |
| `f66125f` | stops serving estate `branch_update_qualifies` | **Superseded.** The field keeps its reader, the estate lander's update pass. |
| `632a388` | inert: withholds the update for `dependabot[bot]` altogether | **Superseded.** It stops all Dependabot freshening, which §2 shows leaves a behind branch waiting indefinitely. Its diff is still useful as a map of where the author is in scope at the inert act. |
| `b2f90d5` | drops `LandingRefused.code` from the estate lander | **Superseded, and it must not be carried over.** This design reads `.code` again (`_UPDATE_SELF_CLEARING` gains `…_sibling_holding`). |

Tear down the `stop-freshening` worktree, its test database `orchestrator_test_stopfresh`, and the
branch, locally and on the remote, once this design is accepted. The remote branch has no pull
request, so `delete_branch_on_merge` will not remove it.

## 15. Open questions

- **None of this changes standing authority.** No new write act, credential, or permission is
  introduced. The rule only ever does less than today's lane.
- **For Devon, only if he wants it:** whether to probe `[dependabot skip]` through the
  commit-message-capable endpoint (§13). If that probe works, it would be a different and larger
  design: a new write act that relies on a third party's documented escape hatch. That is why it is
  presented as a fork rather than decided here.
- **For Devon, separately and now:** whether to post the user `@dependabot recreate` commands in §10
  before tonight's window. HQ is asking.

## 16. Review disposition

The adversarial review of 2026-09-25 returned **accept with changes**. Each item, and what was done.

**Should-fix**

1. **Release on a sibling whose reads failed.** Accepted. §6 now has three sets, and a read-failure
   code in a sibling's answer reaches outcome 3, a withhold. The set is enumerated from both admission
   modules as one set for both lanes, and a completeness test spells out the releasing set as a
   literal so a new code reds CI until classified while runtime keeps releasing as the complement
   (§6, test 11).
2. **Served fact versus act on read failure.** Accepted. The served fact is true only for outcome 2,
   a positively observed holding sibling. Outcome 3 has its own code,
   `…_siblings_unreadable`, kept out of both landers' `_UPDATE_SELF_CLEARING`, so it stays a finding.
   Test 8 was rewritten to state both codes.
3. **Bound on arm (b).** Accepted. Ten minutes, derived in §5 from the measured ~12-second delivery
   and the 3–4-second spacing of acts in a pass, with the trade stated.
4. **Last commit SHA must equal the head.** Accepted. A mismatch is unestablished, which withholds on
   either side (§5; test 5).
5. **Non-Dependabot committer.** Accepted in a sharpened form. Measured on 63 Dependabot commits:
   all carry committer `web-flow`, verified. **The App's commits also carry `web-flow`**, so the
   committer cannot tell Dependabot from the App; it is added to catch the case the author cannot
   see, a person's local rewrite of a Dependabot commit, and paired with the signature to close the
   cheap spoof. GitHub's server-side rebase is named as a residual with a probe (§5).
6. **Day one.** Accepted. `brain#73` lands first, measured from the lander's order and log. The user
   `@dependabot recreate` mitigation on `brain#74`, `brain#75` and `change-manager#93` is recorded
   (§10).
7. **Data corrections.** Accepted after verification against `rows.json`. All four stuck pairs were
   freshened in one pass seconds apart (`change-manager#92`/`#93` at 09-22 06:15:33/:37 included).
   Arm (b) is needed for two of the four; arm (a) catches the other two. 29 of 72 is relabelled the
   strict upper bound, with the holding gate as the reason, a recomputation showing that excluding
   conflicted siblings moves it by zero, and a split by lane.

**Minor**

- **`in_flight` and `mergeability_unknown` can persist.** Added to §6's stall residuals and §13.
- **Pagination.** Both lists are paginated to the end; a failed or truncated page is unread (§5,
  test 7).
- **Lost response.** Named in §13 with its window (3–4 seconds against about 12 seconds) and the
  reason an event written before the call was rejected.
- **Cost and lock.** Stated in §5: about 12 extra calls per act, run under the advisory lock with the
  transaction open, about 2 × N scans per lander pass after a landing.
- **Inert reporting.** Decided the same for both landers: a new `withheld` status, not a finding,
  keyed on the served fact (§9). The first draft's reason for leaving the inert lander alone was
  undermined by the stall residuals and by the inert lander already having the same conditional
  suppression beside an exception.
- **Build hazards.** Added to §11: vocabulary registration, a production caller, schema pins, the
  word guards checked with the guard's own functions, the invariant scan.
- **`…_sibling_holding` in the self-clearing set only with a separate read-failure code.** Done
  (§9).

**Alternative (attack 8).** Recorded in §6 as considered and rejected, with its trade. An
"older sibling about to land" clause on top of the chosen rule was also considered and not adopted:
queue jumping has 0 occurrences, and the clause brings lander-order re-derivation and a stall with
it. The trigger to revisit is named.

**Where verification contradicted the review.**

- Item 5 assumed a committer test could "allow `web-flow` only where GitHub is committer for
  Dependabot's own commits". GitHub is committer (`web-flow`) for the App's update commits too, so
  that distinction does not exist. The committer test is still worth having, for a different reason
  (local rewrites), and the author remains what separates Dependabot from the App.
- Item 7 suggested the orchestrator events inflate 29 of 72 because orchestrator is an inert-lane
  repository. The rule applies to the inert act as well, so those events are in scope; the bound is
  loose because the replay has no holding gate, not because of which lane the events are in.
